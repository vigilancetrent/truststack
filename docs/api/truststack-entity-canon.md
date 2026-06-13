# truststack-entity-canon — API

Import name: `entity_canon`.

Canonicalize incoming entity names against existing records, combining fuzzy
(`difflib`) and phonetic (Soundex + Metaphone) matching into a calibrated
confidence, and block duplicate insertions before they reach the database.

```python
from entity_canon import (
    CanonicalEntity,
    Canonicalizer,
    EntityStore,
    ImportCounts,
    InMemoryEntityStore,
    MatchMethod,
    MatchResult,
    MatchSignal,
    PostgresEntityStore,
    SqliteEntityStore,
    bulk_import,
    metaphone,
    metaphone_equal,
    phonetic_agreement,
    phonetic_equal,
    soundex,
)
```

`__version__` is `"0.1.0"`.

## Models

### `CanonicalEntity` (Pydantic v2, frozen)
| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | Stable identifier. |
| `name` | `str` | Canonical display name (validated non-blank). |
| `aliases` | `list[str]` | Alternate surface forms (blanks stripped on validation). |

- `surface_forms() -> list[str]` returns `[name, *aliases]` — every form used for
  matching.
- The `name` validator raises `ValueError("name must not be blank")` for empty or
  whitespace-only input.
- The `aliases` validator strips whitespace and drops empty entries.

### `MatchResult` (Pydantic v2, frozen)
| Field | Type | Notes |
|-------|------|-------|
| `match` | `str \| None` | Matched surface form (canonical name or alias). |
| `entity_id` | `str \| None` | Matched entity id. |
| `confidence` | `float` | `0.0`–`1.0` calibrated score (validated range). |
| `blocked` | `bool` | Insertion blocked as a duplicate. |
| `suggestion` | `str \| None` | Canonical name to use instead. |
| `method` | `MatchMethod` | `none`/`fuzzy`/`phonetic`/`fuzzy+phonetic`/`exact`. |
| `signals` | `list[MatchSignal]` | Which discrete signals fired. |
| `fuzzy_ratio` | `float` | Raw `SequenceMatcher` ratio for the winner, `[0, 1]`. |
| `phonetic_agreement` | `float` | Soundex+Metaphone agreement for the winner (`0.0`/`0.5`/`1.0`). |

A miss returns `MatchResult(confidence=0.0, method=MatchMethod.NONE)` with all
optional fields unset.

### `MatchMethod` (StrEnum)
`none`, `fuzzy`, `phonetic`, `fuzzy+phonetic`, `exact`. Describes the *kind* of the
winning match: `exact` for a normalized-equal hit, `fuzzy+phonetic` when both a
fuzzy ratio and phonetic agreement contributed, `phonetic` when only phonetics
fired, `fuzzy` otherwise, `none` for a miss.

### `MatchSignal` (StrEnum)
`exact`, `fuzzy`, `soundex`, `metaphone`. The *individual* signals that fired for
the winning candidate. `exact` short-circuits and is the only signal present on an
exact match. Otherwise `fuzzy` fires whenever the fuzzy ratio is above `0`,
`soundex` when Soundex codes agree, and `metaphone` when Metaphone codes agree.

### `ImportCounts` (Pydantic v2, frozen)
| Field | Type | Notes |
|-------|------|-------|
| `added` | `int` | New entities stored (`>= 0`). |
| `merged` | `int` | Folded into an existing duplicate as aliases (`>= 0`). |
| `skipped` | `int` | Invalid/unparseable rows (`>= 0`). |

`total` property returns `added + merged + skipped`.

## Stores

`EntityStore` is a runtime-checkable async `Protocol`:

```python
@runtime_checkable
class EntityStore(Protocol):
    async def add(self, entity: CanonicalEntity) -> None: ...
    async def all(self) -> list[CanonicalEntity]: ...
    async def get(self, entity_id: str) -> CanonicalEntity | None: ...
    async def delete(self, entity_id: str) -> bool: ...
```

`add` is upsert semantics: writing an entity whose `id` already exists replaces
it (this is how merges persist updated alias lists).

### `InMemoryEntityStore()`
Default backend. A dict guarded by an `asyncio.Lock`. Process-local; lost on exit.

### `SqliteEntityStore(path)`
Durable stdlib `sqlite3` store. Blocking calls run via `asyncio.to_thread` so they
never stall the event loop. A fresh short-lived connection is opened per
operation, keeping it safe to use across threads. The schema is created lazily on
first use (guarded by a one-time-init lock). Aliases are stored as a JSON-encoded
`TEXT` column. DDL: [`docs/schemas/truststack-entity-canon.sql`](../schemas/truststack-entity-canon.sql).

### `PostgresEntityStore(dsn, *, pool=None, min_size=1, max_size=10)`
Production `asyncpg` backend behind the `postgres` extra
(`pip install truststack-entity-canon[postgres]`).

- **Lazy everything.** `asyncpg` is imported and the connection pool is created
  only on first use, inside `_get_pool()`. Constructing the store performs no I/O
  and never imports `asyncpg` at module import time, so `import entity_canon`
  works without the extra.
- **Constructor validation.** Raises `ValueError("a dsn or an existing pool is
  required")` if both `dsn` is falsy and `pool` is `None`.
- **Injectable pool.** Pass `pool=` to supply a pre-built pool (e.g. an
  `AsyncMock` in tests, or a shared application pool). When injected, schema init
  is still performed lazily on first use.
- **Schema.** Created on first use via `CREATE TABLE IF NOT EXISTS`; guarded by a
  dedicated schema lock separate from the pool lock to avoid deadlock when
  `_ensure_schema` acquires the pool.
- **Storage.** Aliases stored as `JSONB`. `_row_to_entity` tolerates both `str`
  (text) and decoded-list representations of the `aliases` column.
- **Writes.** `INSERT ... ON CONFLICT (id) DO UPDATE SET name=..., aliases=...`
  for idempotent upserts.
- **Deletes.** Parse the `asyncpg` command tag (`"DELETE 1"`) to derive the row
  count; returns `False` on an unparseable tag.
- **Lifecycle.** `await store.close()` closes the pool (if one was created) and
  resets schema state so the store can be reused.

| Method | Signature |
|--------|-----------|
| `add` | `async (entity: CanonicalEntity) -> None` |
| `all` | `async () -> list[CanonicalEntity]` |
| `get` | `async (entity_id: str) -> CanonicalEntity \| None` |
| `delete` | `async (entity_id: str) -> bool` |
| `close` | `async () -> None` |

**Offline testing.** Build a fake pool whose `acquire()` is an async context
manager yielding an `AsyncMock` connection; stub `conn.execute`, `conn.fetch`, and
`conn.fetchrow`. Inject it via `PostgresEntityStore("", pool=fake_pool)` —
`asyncpg` is never imported and no server is contacted.

## `Canonicalizer(BaseTrustComponent)`

Constructor: `Canonicalizer(store=None, threshold=0.90, require_approval=False, event_bus=None)`.

- `store` defaults to `InMemoryEntityStore()`.
- `threshold` must be within `[0.0, 1.0]`, else `ValueError("threshold must be
  within [0.0, 1.0]")`.
- `component_name = "entity-canon"`, `component_version = "0.1.0"`.

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| `find` | `async (name: str) -> MatchResult` | Best candidate over names + aliases. Never blocks. Raises `ValueError` on blank/whitespace name. Increments `find_calls` (and `find_misses` on no match), sets `last_confidence` gauge. |
| `add` | `async (name: str, aliases=None) -> CanonicalEntity` | Register entity + aliases; generates a `uuid4().hex` id. Raises `ValueError` on blank name. Increments `entities_added`, `aliases_registered`. |
| `canonicalize` | `async (name: str) -> MatchResult` | Calls `find`. If `entity_id` is set and `confidence >= threshold`: in normal mode → `blocked=True`, `suggestion=canonical`, emits `entity.duplicate_blocked`, increments `duplicates_blocked`. In `require_approval` mode → `blocked=False`, `suggestion=canonical`, no event, increments `approvals_requested`. Always increments `duplicates_detected` on a confident match. |
| `get` | `async (entity_id: str) -> CanonicalEntity \| None` | Fetch by id. |
| `all` | `async () -> list[CanonicalEntity]` | List all stored entities. |
| `delete` | `async (entity_id: str) -> bool` | Remove by id; `True` if removed. Increments `entities_deleted`. |
| `bulk_import` | `async (entities: Iterable[CanonicalEntity \| dict]) -> ImportCounts` | Dedup-aware batch import → `{added, merged, skipped}`. Each candidate is validated (`CanonicalEntity.model_validate` for dicts); invalid rows are **skipped**. Confident duplicates (`>= threshold`) are **merged** — incoming name + new aliases folded into the existing entity — instead of inserted. New entities are **added**. |
| `import_csv` | `async (path, *, name_field="name", alias_field="aliases", alias_sep="\|") -> ImportCounts` | Stdlib-`csv` import (header row required; read off-loop via `asyncio.to_thread`). Aliases split on `alias_sep`. Blank-name rows are preserved as invalid so they count as **skipped**. IDs are auto-generated. Delegates to `bulk_import`. |

Module-level `bulk_import(canonicalizer, entities) -> ImportCounts` wraps the
method for functional call sites.

### Inherited Trust Stack contract
- `version() -> str` — returns `component_version`.
- `async health_check() -> HealthStatus` — calls `store.all()`; reports
  `HealthState.UNHEALTHY` with a `detail` if the store raises, else `HEALTHY`.
- `async metrics() -> ComponentMetrics` — counter/gauge snapshot.

### Events
On a hard block, an `entity.duplicate_blocked` `TrustEvent` is published on the
injected `EventBus` with `data = {query, canonical, entity_id, confidence}`. No
event is emitted in `require_approval` mode or when no bus is injected.

## Scoring

For each surface form of each stored entity:

1. **Exact** — if normalized (case-folded, whitespace-collapsed) strings are
   equal → `confidence = 1.0`, `method = exact`, `signals = [exact]`.
2. Otherwise compute `fuzzy = SequenceMatcher(normalized).ratio()` and
   `phonetic = phonetic_agreement(name, surface)`.
3. `confidence = fuzzy + 0.15 * phonetic`, clamped to `[0, 1]`. When `fuzzy == 0`
   (phonetic-only), `confidence` is additionally capped at `0.99`.
4. `signals`: `fuzzy` if `fuzzy > 0`; `soundex` if Soundex agrees; `metaphone`
   when Metaphone contributed.
5. `method`: `fuzzy+phonetic` when both fuzzy and phonetic fired, `phonetic` for
   phonetic-only, `fuzzy` otherwise.

The highest-confidence surface across all entities wins. Insertions block when the
winner's `confidence >= threshold` (default `0.90`). The boundary is inclusive: a
candidate scoring `0.89` is allowed, `0.90` is blocked.

**Worked example.** `"Jhatin"` vs stored `"Jatin"`: the strings are one inserted
character apart, so `fuzzy ≈ 0.83`, and their Soundex codes agree (`J350` — `H` is
a non-coded separator that does not reset adjacency) for a phonetic agreement of
at least `0.5`. The additive boost pushes `confidence ≈ 0.83 + 0.15 * phonetic`
above the `0.90` threshold → blocked, with `signals` including `fuzzy` and
`soundex` (and `metaphone` when the Metaphone codes also coincide).

## Metrics

Counters: `find_calls`, `find_misses`, `entities_added`, `entities_deleted`,
`entities_merged`, `aliases_registered`, `duplicates_detected`,
`duplicates_blocked`, `approvals_requested`. Gauge: `last_confidence`.

## REST API (`entity_canon.api`, `api` extra)

`create_app(canonicalizer: Canonicalizer | None = None) -> FastAPI`. The `fastapi`
import is lazy (inside `create_app`); calling it without the extra raises a
`RuntimeError` pointing at `pip install truststack-entity-canon[api]`. A default
in-memory `Canonicalizer` is created when none is passed. App title is
`"Trust Stack Entity Canon"`, version is `component.version()`.

| Method & path | Request | Response |
|---------------|---------|----------|
| `POST /find` | `FindRequest{name: str (min_length=1)}` | `MatchResult` — calls `canonicalize()`, so it honours `require_approval` mode (returns the suggestion without blocking). |
| `POST /entities` | `AddEntityRequest{name: str (min_length=1), aliases: [str]}` | `CanonicalEntity`, status `201`. |
| `GET /entities` | — | `list[CanonicalEntity]`. |
| `DELETE /entities/{entity_id}` | — | `204`; `404` (`HTTPException`) if the id is unknown. |
| `GET /health` | — | serialized `HealthStatus` (`model_dump(mode="json")`). |

Request models `FindRequest` and `AddEntityRequest` are exported from
`entity_canon.api` alongside `create_app`.

**Offline testing.** `create_app(canon)` returns a standard ASGI app; drive it
with `httpx.AsyncClient(transport=ASGITransport(app))` or FastAPI's `TestClient` —
no network or live server required.

## Phonetic helpers (`entity_canon.phonetic`)

Two pure-stdlib encoders ship by default; both transparently delegate to
`jellyfish` when the optional `phonetic` extra is installed (lazy import).

| Function | Returns | Notes |
|----------|---------|-------|
| `soundex(name) -> str` | American Soundex (4-char code, e.g. `"J350"`) | Empty string for input with no letters. |
| `metaphone(name) -> str` | Variable-length Metaphone code | Accent-folded (NFKD); handles digraphs `PH→F`, `SCH→SK`, `TH→0`, silent `GH`/`KN`/`GN`/`WR`, soft `C`/`G`. Empty string for input with no letters. |
| `phonetic_equal(a, b) -> bool` | Shared non-empty Soundex code | |
| `metaphone_equal(a, b) -> bool` | Shared non-empty Metaphone code | |
| `phonetic_agreement(a, b) -> float` | `1.0` both agree, `0.5` exactly one, `0.0` neither (or no letters) | Drives the additive boost in scoring. |

All encoders are deterministic, so correctness can be unit-tested offline against
known codes (e.g. `metaphone("Thompson") == "TMSN"`,
`soundex("Robert") == soundex("Rupert")`).
