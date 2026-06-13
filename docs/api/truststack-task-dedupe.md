# truststack-task-dedupe — API

Import name: `task_dedupe`. Prevent duplicate task creation via intent
fingerprinting + similarity.

All modules are async-first, fully type-hinted (mypy `--strict` on `src/`),
Pydantic v2 only, and carry `from __future__ import annotations`. The package
imports with only its required dependencies (`truststack-core`); every optional
client (`fastapi`, `rapidfuzz`, `redis`, `asyncpg`) is imported lazily inside the
method that uses it.

## Public exports

Importable from the `task_dedupe` top-level package:

`DedupeEngine`, `DedupeResult`, `FingerprintParts`, `Task`,
`FingerprintInputs`, `fingerprint_inputs`, `fingerprint_task`, `normalize_due`,
`normalize_title`, `SimilarityScorer`, `DifflibScorer`, `SequenceMatcherScorer`,
`RapidFuzzScorer`, `HashingEmbeddingScorer`, `TaskStore`, `StoredTask`,
`InMemoryTaskStore`, `SqliteTaskStore`, `RedisTaskStore`, `PostgresTaskStore`.

`create_app` lives in the `task_dedupe.api` submodule (extra `api`).

## Models

### `Task`
Pydantic v2, `extra="forbid"`.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `title` | `str` | required | min length 1 |
| `due` | `str \| None` | `None` | relative phrase or ISO date |
| `assignee` | `str \| None` | `None` | |
| `project` | `str \| None` | `None` | |

### `DedupeResult`
Pydantic v2, frozen.

| Field | Type | Notes |
|-------|------|-------|
| `duplicate` | `bool` | True if a stored task meets the threshold |
| `existing_task_id` | `str \| None` | matched task id on a hit |
| `score` | `float` | best similarity in `[0, 1]` |
| `fingerprint` | `str` | 16 hex-char intent fingerprint |
| `fingerprint_inputs` | `FingerprintParts \| None` | normalized inputs that produced `fingerprint` |

### `FingerprintParts`
Pydantic v2, frozen. The normalized field values that composed the fingerprint.

| Field | Type | Notes |
|-------|------|-------|
| `title` | `str` | normalized title (stopwords removed) |
| `due` | `str` | coarse due-window bucket, or `"none"` |
| `assignee` | `str` | normalized assignee, or `""` |
| `project` | `str` | normalized project, or `""` |

## `DedupeEngine(BaseTrustComponent)`

```python
DedupeEngine(
    store: TaskStore | None = None,        # default InMemoryTaskStore
    threshold: float = 0.85,               # duplicate cutoff in [0, 1]
    event_bus: EventBus | None = None,     # optional TrustEvent sink
    scorer: SimilarityScorer | None = None # default SequenceMatcherScorer
)
```

- `component_name = "task-dedupe"`, `component_version = "0.1.0"`.
- Raises `ValueError` if `threshold` is outside `[0, 1]`.
- `async check(task: Task | dict) -> DedupeResult`: fingerprints the task,
  compares it against every stored task (exact fingerprint match short-circuits
  to score `1.0`); if the best score `>= threshold` returns `duplicate=True`
  without storing, else stores it (with a fresh `uuid4().hex` id) and returns
  `duplicate=False`. A `dict` is validated into a `Task` first.
- Read-only properties: `store -> TaskStore`, `threshold -> float`,
  `scorer -> SimilarityScorer`.
- Inherited: `version() -> str`, `async health_check() -> HealthStatus`,
  `async metrics() -> ComponentMetrics`.

### Health

`async health_check()` calls `store.all()`. It returns `HealthState.HEALTHY`
("N task(s) tracked") when the store responds, and `HealthState.UNHEALTHY`
("store unavailable: ...") if the store raises — so a degraded Redis/Postgres
backend surfaces through the standard Trust Stack supervision path.

### Events
On a duplicate hit (when an `event_bus` is provided) publishes a `TrustEvent`:

- `name = "task.duplicate_detected"`, `component = "task-dedupe"`
- `data = {existing_task_id, score, fingerprint, title}`

### Metrics
- counters: `checks_total`, `duplicates_detected`, `tasks_stored`
- gauges: `last_best_score`

## Stores

### `TaskStore` (Protocol)
- `async add(id: str, fingerprint: str, task: Task) -> None`
- `async all() -> list[StoredTask]`

`StoredTask` is a frozen dataclass: `id`, `fingerprint`, `task`.

| Implementation | Backing | Status |
|----------------|---------|--------|
| `InMemoryTaskStore()` | thread-safe list | default, stdlib |
| `SqliteTaskStore(path)` | stdlib `sqlite3` via `asyncio.to_thread` | shipping, stdlib |
| `RedisTaskStore(url=None, *, namespace="task_dedupe", client=None)` | `redis.asyncio` (lazy) | shipping, extra `redis` |
| `PostgresTaskStore(dsn=None, *, table="tasks", pool=None)` | `asyncpg` (lazy) | shipping, extra `postgres` |

`RedisTaskStore` stores records as a hash `{namespace}:tasks` (id → JSON,
`add()` is a single pipelined `HSET` + `SADD`) with a companion set
`{namespace}:fingerprints`; `all()` returns records sorted by id for stable
ordering. Pass a pre-built async `client` (e.g. `fakeredis.aioredis.FakeRedis`)
to use it without a server; requires `url` **or** `client` (else `ValueError`).
Extra methods: `async has_fingerprint(fingerprint) -> bool` (O(1) `SISMEMBER`),
`async close()` (releases the pool, no-op if a client was injected). Using a `url`
without the `redis` extra raises a clear `RuntimeError`.

`PostgresTaskStore` lazily creates an `asyncpg` pool (or accepts a pre-built
`pool`) and a `tasks` table (id TEXT / fingerprint TEXT / payload JSONB) on first
use, guarded by an `asyncio.Lock` so concurrent first calls initialize once.
`add()` is an idempotent `INSERT ... ON CONFLICT (id) DO UPDATE`; `all()` orders
by id. Requires `dsn` **or** `pool` (else `ValueError`); the `table` name is
validated as a Python identifier (else `ValueError`). Extra methods:
`async has_fingerprint(fingerprint) -> bool`, `async close()`. Using a `dsn`
without the `postgres` extra raises a clear `RuntimeError`.

## Similarity

### `SimilarityScorer` (Protocol)
- `score(a: Task, b: Task) -> float` in `[0, 1]`.

All built-in scorers share the same constructor weights and blend a `0..1` title
ratio with exact-match boosts for a shared due-window / assignee / project:

```python
Scorer(
    *,
    title_weight: float = 0.7,
    due_weight: float = 0.12,
    assignee_weight: float = 0.1,
    project_weight: float = 0.08,
)
```

Weight rules (enforced in the constructor, raising `ValueError` on violation):

- every weight must be `>= 0.0`;
- the four weights must sum to `1.0` (within a `±0.001` tolerance).

Scoring: `result = title_weight * title_ratio`, then `+= due_weight` when both
tasks have a due value mapping to the same bucket (`!= "none"`),
`+= assignee_weight` when both name a matching assignee (case-insensitive),
`+= project_weight` when both name a matching project (case-insensitive); the sum
is capped at `1.0`. Title alone therefore tops out at `title_weight`.

- `DifflibScorer(...)` — `difflib.SequenceMatcher` ratio on normalized titles.
  The default scorer; stdlib only. `SequenceMatcherScorer` is a kept alias.
- `RapidFuzzScorer(...)` — `rapidfuzz.fuzz.token_sort_ratio / 100` (extra
  `semantic`, imported lazily; raises a clear `RuntimeError` if missing). Two
  empty titles score `1.0`.
- `HashingEmbeddingScorer(*, dimensions=256, ...)` — pure-stdlib hashing
  bag-of-words cosine over title tokens. Each token is SHA-256-hashed into one of
  `dimensions` buckets (the hashing trick) and the term-frequency vectors are
  compared with cosine similarity. Deterministic and fully offline. `dimensions`
  must be `>= 1` (else `ValueError`); two empty titles score `1.0`, and an empty
  vs. non-empty title scores `0.0`.

## Fingerprinting helpers
- `normalize_title(title: str) -> str` — lowercase, strip punctuation, collapse
  whitespace, drop a compact stopword set. A title made entirely of
  stopwords/punctuation falls back to the punctuation-stripped lowercase form so
  two such titles still compare.
- `normalize_due(due: str | None, *, now: datetime | None = None) -> str` — map
  a due string to a coarse ISO week bucket (`YYYY-Www`). `now` pins the reference
  instant (defaults to `datetime.now(UTC)`) for determinism. Returns `"none"`
  for `None`/blank; unparseable values are lowercased/trimmed and returned
  verbatim so they still match each other.

  Recognized vocabulary (checked in this order — ISO dates, exact relative
  phrases, counted offsets, week/month phrases, then weekday names):

  | Input | Resolves to |
  |-------|-------------|
  | `YYYY-MM-DD` | week bucket of that date (invalid date → verbatim text) |
  | `today` / `tonight` / `eod` / `asap` / `now` / `immediately` | `now`'s week |
  | `tomorrow` / `tmrw` / `tmr` | `now + 1 day` |
  | `yesterday` | `now - 1 day` |
  | `day after tomorrow` / `overmorrow` | `now + 2 days` |
  | `day before yesterday` | `now - 2 days` |
  | `in N days` / `in N weeks` / `in N months` | offset forward (`month ≈ 30d`) |
  | `N days ago` / `N weeks ago` / `N months ago` | offset backward |
  | `this week` / `week` | `now`'s week |
  | `next week` | `now + 1 week` |
  | `last week` / `previous week` | `now - 1 week` |
  | `this month` / `month` | `now + 15 days` |
  | `next month` | `now + 30 days` |
  | `last month` / `previous month` | `now - 30 days` |
  | weekday name or 3-letter prefix (`monday`, `tue`, ...) | next matching weekday |
  | `next <weekday>` | the matching weekday one full week later |

- `FingerprintInputs` — frozen, slotted dataclass with fields `title`, `due`,
  `assignee`, `project`. Methods: `to_payload() -> str` (the `\x1f`-joined hashed
  payload) and `to_fingerprint() -> str` (16 hex-char SHA-256 of the payload).
- `fingerprint_inputs(task, *, now=None) -> FingerprintInputs` — the normalized
  inputs for `task` (assignee/project are lowercased & whitespace-collapsed, or
  `""` when absent).
- `fingerprint_task(task: Task, *, now: datetime | None = None) -> str` — 16
  hex-char SHA-256 over the normalized fields; equivalent to
  `fingerprint_inputs(task, now=now).to_fingerprint()`.

## HTTP API (extra `api`)
`task_dedupe.api.create_app(engine: DedupeEngine | None = None) -> FastAPI`
exposes `POST /check` (body: `Task`, response: `DedupeResult`) and `GET
/health`. FastAPI is imported lazily; the module imports without the extra.
