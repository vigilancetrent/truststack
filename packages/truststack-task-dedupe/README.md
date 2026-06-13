# truststack-task-dedupe

**Prevent duplicate task creation via intent fingerprinting + similarity scoring.**

## The problem

AI agents ingest the *same* action item from multiple channels — an email thread,
a Slack message, and the transcript of the meeting where it was first raised —
and dutifully create the **same task three times**. Naïve string matching does
not help: "Send Q3 report to Dana by tomorrow", "send the q3 report to dana",
and "Q3 report → Dana (EOD tomorrow)" are byte-for-byte different yet express one
intent.

`truststack-task-dedupe` collapses these to a single intent. Each task is
normalized into an **intent fingerprint** — `title + due-window + assignee +
project` — and compared against previously seen tasks with a swappable
**similarity scorer**, so near-duplicates are caught *before* a new task is
created. It is async-first, Pydantic v2, and a first-class
[Trust Stack](https://github.com/vigilancetrent/truststack) component
(`health_check()`, `metrics()`, `TrustEvent` emission).

The library **fails toward distrust**: an exact fingerprint match is always
treated as a duplicate (score `1.0`), and the duplicate threshold is
conservative by default (`0.85`) so genuinely distinct tasks are never silently
merged.

## Install

```bash
pip install truststack-task-dedupe
```

The default install is **stdlib-only** (plus `truststack-core`): the in-memory
and SQLite stores, and all of the `difflib` / hashing-embedding scoring, need
zero external infrastructure and run fully offline.

Optional backend extras:

```bash
pip install "truststack-task-dedupe[api]"        # FastAPI POST /check + GET /health
pip install "truststack-task-dedupe[semantic]"   # RapidFuzzScorer (rapidfuzz)
pip install "truststack-task-dedupe[redis]"      # RedisTaskStore (redis.asyncio)
pip install "truststack-task-dedupe[postgres]"   # PostgresTaskStore (asyncpg)
```

Every optional client (`fastapi`, `rapidfuzz`, `redis`, `asyncpg`) is imported
**lazily** inside the method that needs it, so `import task_dedupe` always
succeeds with only the required dependencies installed. Using a feature without
its extra raises a clear `RuntimeError` telling you exactly what to install.

## Quickstart

```python
import asyncio
from task_dedupe import DedupeEngine, Task

async def main() -> None:
    engine = DedupeEngine(threshold=0.85)

    first = await engine.check(Task(title="Send Q3 report to Dana", due="tomorrow"))
    print(first.duplicate)   # False -> stored

    # Same intent, different wording, same due window:
    second = await engine.check(Task(title="send the q3 report to dana", due="tomorrow"))
    print(second.duplicate)          # True
    print(second.existing_task_id)   # id of the first task
    print(second.fingerprint_inputs) # FingerprintParts(title='send q3 report dana', due='2026-W25', ...)

asyncio.run(main())
```

`check()` accepts either a `Task` or a plain `dict` (validated for you). See
[`examples/basic_dedupe.py`](examples/basic_dedupe.py) for an event-bus
integration that fires `task.duplicate_detected` on every hit and prints the
component metrics.

## Inspecting *why* two tasks collapsed

`DedupeResult.fingerprint_inputs` exposes the exact normalized values that
produced the fingerprint, so you can audit which intent signature merged two
tasks:

```python
result = await engine.check(Task(title="Email Dana the Q3 report!!", due="tomorrow", assignee="me"))
print(result.fingerprint)            # e.g. "9f1c2a8e4b7d0a36"
print(result.fingerprint_inputs.title)     # "email dana q3 report"
print(result.fingerprint_inputs.due)       # "2026-W25"  (coarse ISO week bucket)
print(result.fingerprint_inputs.assignee)  # "me"
```

## Scorers

The title-comparison strategy is swappable via the `SimilarityScorer` Protocol.
All built-in scorers blend a `0..1` **title ratio** with exact-match **metadata
boosts** for a shared due-window, assignee, and project (see *Weighting* below).
Pass any scorer to the engine:

```python
from task_dedupe import DedupeEngine, HashingEmbeddingScorer

engine = DedupeEngine(scorer=HashingEmbeddingScorer())
```

| Scorer | Title ratio | Dependency |
|--------|-------------|------------|
| `DifflibScorer` (default, alias `SequenceMatcherScorer`) | `difflib.SequenceMatcher.ratio()` | stdlib only |
| `RapidFuzzScorer` | `rapidfuzz.fuzz.token_sort_ratio` (word-order tolerant) | extra `semantic` (lazy) |
| `HashingEmbeddingScorer` | hashing bag-of-words **cosine** similarity | stdlib only, deterministic, offline |

`HashingEmbeddingScorer` uses the *hashing trick*: each normalized title token is
hashed into one of `dimensions` (default `256`) buckets and the resulting
term-frequency vectors are compared with cosine similarity — a lightweight,
fully offline embedding stand-in that needs no model download.

```python
from task_dedupe import RapidFuzzScorer        # needs `[semantic]`
from task_dedupe import HashingEmbeddingScorer  # stdlib

engine = DedupeEngine(scorer=RapidFuzzScorer())
engine = DedupeEngine(scorer=HashingEmbeddingScorer(dimensions=512))
```

## Weighting

Every scorer accepts the four weights that compose the blended score; they must
be non-negative and **sum to 1.0**. Tune how much the title vs. the structured
metadata drives a match:

```python
from task_dedupe import DedupeEngine, DifflibScorer

# Trust the title less; require strong metadata agreement to call a duplicate.
scorer = DifflibScorer(
    title_weight=0.6,
    due_weight=0.2,
    assignee_weight=0.1,
    project_weight=0.1,
)
engine = DedupeEngine(scorer=scorer)
```

| Weight | Default | Awarded when |
|--------|---------|--------------|
| `title_weight` | `0.7` | always — multiplies the `0..1` title ratio |
| `due_weight` | `0.12` | both tasks have a due value **and** they map to the same week bucket |
| `assignee_weight` | `0.1` | both tasks name an assignee **and** they match (case-insensitive) |
| `project_weight` | `0.08` | both tasks name a project **and** they match (case-insensitive) |

Because title alone tops out at `title_weight`, a generic title like "follow up"
can never reach the default `0.85` threshold without at least some metadata
agreement — by design.

## Due-window normalization

`normalize_due` collapses an expanded vocabulary of relative phrases and ISO
dates into a **coarse, deterministic ISO year-week bucket** (`YYYY-Www`), so
"tomorrow", "next Tuesday", and `2026-06-16` land in the same grain when they
fall in the same week. Pass a reference `now` to make buckets fully
deterministic (essential for tests and reproducible fingerprints):

```python
from datetime import datetime, UTC
from task_dedupe import normalize_due

now = datetime(2026, 6, 13, tzinfo=UTC)   # a Saturday in ISO week 2026-W24
normalize_due("today", now=now)            # "2026-W24"
normalize_due("tomorrow", now=now)         # "2026-W24" (Sunday, still W24)
normalize_due("next week", now=now)        # "2026-W25"
normalize_due("in 10 days", now=now)       # "2026-W25"
normalize_due("next monday", now=now)      # "2026-W26"
normalize_due("2026-07-01", now=now)       # "2026-W27"
normalize_due(None)                         # "none"
```

Recognized forms: `today` / `tonight` / `tomorrow` / `yesterday` (and `eod`,
`asap`, `now`, `day after tomorrow`, `day before yesterday`); `this week` /
`next week` / `last week`; `this month` / `next month` / `last month`;
`in N days|weeks|months` and `N days|weeks|months ago`; weekday names (resolved
to the *next* matching weekday; `next <weekday>` always one full week later); and
`YYYY-MM-DD` ISO dates. Unparseable values are lowercased/trimmed and returned
verbatim so they still match each other.

## Storage backends

A `TaskStore` persists the `(id, fingerprint, task)` records the engine compares
against. Four backends ship, all implementing the same `TaskStore` Protocol
(`async add()`, `async all()`):

### In-memory (default)

```python
from task_dedupe import DedupeEngine, InMemoryTaskStore

engine = DedupeEngine(store=InMemoryTaskStore())   # also the default
```

Thread-safe, process-local, zero infrastructure.

### SQLite (durable, stdlib)

```python
from task_dedupe import DedupeEngine, SqliteTaskStore

engine = DedupeEngine(store=SqliteTaskStore("dedupe.db"))
```

Backed by stdlib `sqlite3`; all blocking calls run via `asyncio.to_thread`. DDL
is documented in
[`docs/schemas/truststack-task-dedupe.sql`](../../docs/schemas/truststack-task-dedupe.sql).

### Redis (extra `redis`)

```python
from redis.asyncio import Redis
from task_dedupe import DedupeEngine, RedisTaskStore

store = RedisTaskStore("redis://localhost:6379/0", namespace="task_dedupe")
# or inject a pre-built async client (e.g. fakeredis in tests):
store = RedisTaskStore(client=Redis.from_url("redis://localhost:6379/0"))

engine = DedupeEngine(store=store)
# ...
await store.close()   # release the connection pool when done
```

Records are stored as JSON in a hash `{namespace}:tasks` (id → record) with a
companion set `{namespace}:fingerprints` for O(1) `has_fingerprint()` checks.
`add()` is a single pipelined `HSET` + `SADD`.

### Postgres (extra `postgres`)

```python
from task_dedupe import DedupeEngine, PostgresTaskStore

store = PostgresTaskStore("postgresql://user:pass@localhost/db", table="tasks")
engine = DedupeEngine(store=store)
# ...
await store.close()   # close the asyncpg pool when done
```

Lazily creates an `asyncpg` pool and a `tasks` table (`id TEXT` / `fingerprint
TEXT` / `payload JSONB`) on first use. The `table` name is validated as a Python
identifier to keep the dynamically-built DDL/SQL safe. Provision the schema ahead
of time with [`docs/schemas/truststack-task-dedupe.sql`](../../docs/schemas/truststack-task-dedupe.sql).
A pre-built `pool` can be injected for testing.

## HTTP API (extra `api`)

```python
from task_dedupe.api import create_app

app = create_app()   # FastAPI app: POST /check (-> DedupeResult), GET /health
```

The `api` module imports cleanly without FastAPI installed; `create_app` imports
it lazily and raises a clear error if the `api` extra is missing.

## How it works

| Stage | Detail |
|-------|--------|
| **Title normalization** | lowercase, strip punctuation, collapse whitespace, drop a compact stopword set |
| **Due-window bucketing** | relative phrases + ISO dates collapse to a coarse ISO week bucket — no external date library |
| **Fingerprint** | SHA-256 over normalized `title + due-window + assignee + project`, truncated to 16 hex chars |
| **Similarity** | swappable scorer: a `0..1` title ratio blended with exact-match metadata boosts |
| **Decision** | exact fingerprint → duplicate (score `1.0`); else best score `>= threshold` → duplicate, not stored; otherwise stored as new |

## API reference

| Symbol | Description |
|--------|-------------|
| `DedupeEngine(store=None, threshold=0.85, event_bus=None, scorer=None)` | Trust Stack component. `await .check(task)` → `DedupeResult`. Properties: `.store`, `.threshold`, `.scorer` |
| `Task(title, due=None, assignee=None, project=None)` | Input task model (Pydantic v2, `extra="forbid"`) |
| `DedupeResult(duplicate, existing_task_id, score, fingerprint, fingerprint_inputs)` | Check outcome (frozen) |
| `FingerprintParts(title, due, assignee, project)` | Normalized inputs surfaced on `DedupeResult.fingerprint_inputs` |
| `TaskStore` (Protocol) | `async add(id, fingerprint, task)`, `async all() -> list[StoredTask]` |
| `StoredTask(id, fingerprint, task)` | Frozen stored record |
| `InMemoryTaskStore()` | Default in-process store (stdlib) |
| `SqliteTaskStore(path)` | Durable stdlib `sqlite3` store |
| `RedisTaskStore(url=None, *, namespace="task_dedupe", client=None)` | `redis.asyncio` store (extra `redis`). Adds `has_fingerprint()`, `close()` |
| `PostgresTaskStore(dsn=None, *, table="tasks", pool=None)` | `asyncpg` store (extra `postgres`). Adds `has_fingerprint()`, `close()` |
| `SimilarityScorer` (Protocol) | `score(a, b) -> float` in `[0, 1]` |
| `DifflibScorer` / `SequenceMatcherScorer` | Default `difflib` scorer (alias) |
| `RapidFuzzScorer` | `rapidfuzz` scorer (extra `semantic`, lazy) |
| `HashingEmbeddingScorer(*, dimensions=256, ...)` | Stdlib hashing bag-of-words cosine scorer |
| `fingerprint_task(task, *, now=None)` | 16 hex-char intent fingerprint |
| `fingerprint_inputs(task, *, now=None)` | `FingerprintInputs` (with `.to_payload()` / `.to_fingerprint()`) |
| `normalize_title(title)` / `normalize_due(due, *, now=None)` | Field normalizers |
| `task_dedupe.api.create_app(engine=None)` | FastAPI app factory (extra `api`) |

`DedupeEngine` subclasses `BaseTrustComponent`: `.version()`, `await
.health_check()`, and `await .metrics()` are available for uniform supervision.
On a duplicate hit (with an `event_bus`) it publishes a
`task.duplicate_detected` `TrustEvent`. Metrics: counters `checks_total`,
`duplicates_detected`, `tasks_stored`; gauge `last_best_score`.

Full spec: [`docs/api/truststack-task-dedupe.md`](../../docs/api/truststack-task-dedupe.md).
Postgres / SQLite DDL: [`docs/schemas/truststack-task-dedupe.sql`](../../docs/schemas/truststack-task-dedupe.sql).

---

*Part of the **Trust Stack** — composable trust primitives for AI agents.*
