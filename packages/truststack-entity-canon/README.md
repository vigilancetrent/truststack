# truststack-entity-canon

**Canonicalize entities BEFORE insertion** so misspellings and phonetic variants
never become duplicate records.

[![Trust Stack](https://img.shields.io/badge/Trust%20Stack-entity--canon-0b7285)](https://github.com/vigilancetrent/truststack)

## Problem

Free-text entity names drift. `Jatin`, `Jhatin`, and `Jatyn` are the same person,
but a naive `INSERT` creates three rows. Once those fractured identities exist,
every downstream system pays for it:

- **Joins silently under-count.** A `GROUP BY name` splits one person across rows.
- **Dedup jobs run forever.** Reconciling duplicates after the fact is O(n²) and
  never finishes cleanly.
- **Trust decisions operate on the wrong record.** An agent that reads "no prior
  history" for `Jhatin` misses everything filed under `Jatin`.

The cheapest place to fix this is **at the door** — before the row is ever
written. Entity Canon resolves an incoming name against the entities you already
have and tells you, with a calibrated confidence, whether it is a duplicate.

It combines two complementary signals:

- **Fuzzy** string similarity via `difflib.SequenceMatcher` — catches typos and
  transpositions (`Jatin` ↔ `Jatyn`).
- **Phonetic** matching via pure-stdlib **Soundex** *and* **Metaphone** — catches
  homophone misspellings that look different but sound the same (`Catherine` ↔
  `Kathryn`, `Philip` ↔ `Fillip`).

When a new name is confidently a duplicate, the insertion is **blocked** and the
canonical name is **suggested**. `MatchResult` is fully transparent: it exposes
which discrete signals fired (`exact`, `fuzzy`, `soundex`, `metaphone`) alongside
the raw `fuzzy_ratio` and `phonetic_agreement` that produced the score — so the
decision is auditable, never a black box.

Entity Canon **fails toward distrust**: a blank or whitespace-only name raises
rather than silently matching nothing, and a phonetic-only hit can never reach
full certainty (it is capped below `1.0`).

## Install

```bash
uv add truststack-entity-canon
```

Optional extras — each pulls in exactly one backend and nothing else:

```bash
uv add "truststack-entity-canon[phonetic]"   # jellyfish-backed Soundex/Metaphone
uv add "truststack-entity-canon[api]"        # FastAPI REST surface
uv add "truststack-entity-canon[postgres]"   # asyncpg-backed Postgres store
```

| Extra | Pulls in | Unlocks |
|-------|----------|---------|
| *(none)* | `truststack-core` only | In-memory + SQLite stores, stdlib Soundex + Metaphone |
| `phonetic` | `jellyfish>=1` | Battle-tested Soundex/Metaphone (transparent delegation) |
| `api` | `fastapi>=0.110` | `entity_canon.api.create_app()` REST surface |
| `postgres` | `asyncpg>=0.29` | `PostgresEntityStore` for production persistence |

The **default install needs zero external infrastructure**: matching is pure
stdlib, and entities live in memory or a local SQLite file. Optional dependencies
are imported **lazily inside the methods that use them**, so `import entity_canon`
succeeds with only the required deps installed — even if `asyncpg`, `fastapi`, or
`jellyfish` are absent.

## Usage

### Block duplicates at insertion time

```python
import asyncio
from entity_canon import Canonicalizer

async def main() -> None:
    canon = Canonicalizer(threshold=0.90)
    await canon.add("Jatin", aliases=["Jat"])

    result = await canon.canonicalize("Jhatin")
    assert result.blocked is True            # do NOT insert a new entity
    assert result.suggestion == "Jatin"      # use this canonical name instead
    assert result.confidence >= 0.90
    assert "fuzzy" in result.signals          # which signals fired is transparent

    fresh = await canon.canonicalize("Michael")
    assert fresh.blocked is False            # genuinely new -> safe to insert

asyncio.run(main())
```

### Inspect why a match fired

Every `MatchResult` carries the breakdown behind its score:

```python
res = await canon.find("Jhatin")             # find() never blocks
print(res.match)                # "Jatin" — the matched surface form
print(round(res.confidence, 3)) # calibrated score in [0, 1]
print(res.method)               # e.g. MatchMethod.FUZZY_PHONETIC
print(res.signals)              # which signals fired, e.g. [FUZZY, SOUNDEX]
print(res.fuzzy_ratio)          # raw SequenceMatcher ratio
print(res.phonetic_agreement)   # 0.0 / 0.5 / 1.0
```

### Human-approval mode

Set `require_approval=True` to never auto-block. Duplicates are surfaced with a
`suggestion` for a human to confirm, and no `entity.duplicate_blocked` event is
emitted — the request is *not* hard-blocked:

```python
canon = Canonicalizer(require_approval=True)
await canon.add("Jatin")
pending = await canon.canonicalize("Jhatin")
assert pending.blocked is False
assert pending.suggestion == "Jatin"         # surfaced for a human to approve
```

### Threshold tuning

`threshold` is the confidence cutoff for blocking (default `0.90`). It is
inclusive at the boundary — a candidate scoring exactly `0.90` is blocked, while
`0.89` is allowed through:

```python
strict = Canonicalizer(threshold=0.95)       # fewer blocks, fewer false positives
loose  = Canonicalizer(threshold=0.80)       # more blocks, catches more variants
```

### Durable SQLite store

```python
from entity_canon import Canonicalizer, SqliteEntityStore

canon = Canonicalizer(store=SqliteEntityStore("entities.db"))
```

The schema is created lazily on first use; blocking `sqlite3` calls run off the
event loop via `asyncio.to_thread`, so the store never stalls the loop.

### Postgres store (optional `postgres` extra)

```python
from entity_canon import Canonicalizer, PostgresEntityStore

store = PostgresEntityStore(
    "postgresql://user:pass@localhost/db",
    min_size=1,
    max_size=10,
)
canon = Canonicalizer(store=store)
# ... use the canonicalizer ...
await store.close()                          # release the pool on shutdown
```

`asyncpg` is imported lazily and the connection pool is created on first use, so
importing the package never requires Postgres to be installed or reachable.
Aliases are stored as `JSONB`; writes use `INSERT ... ON CONFLICT (id) DO UPDATE`
for idempotent upserts. A pre-built pool can be injected (handy for tests with an
`AsyncMock`):

```python
store = PostgresEntityStore("", pool=my_pool)   # bring-your-own pool
```

### Batch import

`bulk_import` is **dedup-aware**: each candidate is checked against existing
entities before it is written. Confident duplicates are *merged* (their name and
any new aliases folded into the existing record) instead of inserted as a second
row. Invalid rows (e.g. a blank name) are *skipped*, not fatal.

```python
from entity_canon import Canonicalizer

canon = Canonicalizer()
counts = await canon.bulk_import([
    {"id": "1", "name": "Jatin", "aliases": ["Jat"]},
    {"id": "2", "name": "Jhatin"},           # confident duplicate -> merged
    {"id": "3", "name": "   "},              # blank -> skipped
])
assert counts.added == 1
assert counts.merged == 1
assert counts.skipped == 1
assert counts.total == 3
```

Import directly from a CSV file (stdlib `csv`, header row required). Aliases are
read from an optional column joined by `|`:

```csv
name,aliases
Jatin,Jat|J
Catherine,Cathy|Kate
```

```python
csv_counts = await canon.import_csv("entities.csv")
# Override the column names / separator if your file differs:
await canon.import_csv("people.csv", name_field="full_name", alias_sep=",")
```

A module-level `bulk_import(canon, entities)` wrapper is also exported for
functional call sites.

### REST API (optional `api` extra)

```python
from entity_canon import Canonicalizer
from entity_canon.api import create_app

canon = Canonicalizer(require_approval=False)
app = create_app(canon)        # or create_app() for a default in-memory component
# uvicorn module:app
```

| Method & path | Body | Returns |
|---------------|------|---------|
| `POST /find` | `{"name": str}` | `MatchResult` (blocks duplicates; suggests in approval mode) |
| `POST /entities` | `{"name": str, "aliases": [str]}` | `CanonicalEntity` (`201`) |
| `GET /entities` | — | `list[CanonicalEntity]` |
| `DELETE /entities/{entity_id}` | — | `204`, or `404` if unknown |
| `GET /health` | — | serialized `HealthStatus` |

`POST /find` calls `canonicalize()`, so it honours `require_approval` mode: in
approval mode it returns `blocked=False` with a populated `suggestion` rather than
hard-blocking the caller.

### Events

When a duplicate is blocked, an `entity.duplicate_blocked` `TrustEvent` is
published on any injected `EventBus`:

```python
from truststack.events import EventBus

bus = EventBus()
canon = Canonicalizer(event_bus=bus)
# blocking a duplicate now emits entity.duplicate_blocked with
# {query, canonical, entity_id, confidence}
```

### Health, metrics, and versioning

`Canonicalizer` is a Trust Stack component, so it inherits the standard contract:

```python
print(canon.version())                       # "0.1.0"
status = await canon.health_check()          # UNHEALTHY if the store is unreachable
metrics = await canon.metrics()              # ComponentMetrics snapshot
```

Counters tracked: `find_calls`, `find_misses`, `entities_added`,
`entities_deleted`, `entities_merged`, `aliases_registered`,
`duplicates_detected`, `duplicates_blocked`, `approvals_requested`. Gauge:
`last_confidence`.

## API

| Symbol | Description |
|--------|-------------|
| `Canonicalizer(store=None, threshold=0.90, require_approval=False, event_bus=None)` | Trust Stack component; subclasses `BaseTrustComponent`. Raises `ValueError` if `threshold` is outside `[0, 1]`. |
| `await canon.find(name) -> MatchResult` | Best existing match over names + aliases; never blocks. Raises `ValueError` on blank name. |
| `await canon.add(name, aliases=...) -> CanonicalEntity` | Register a canonical entity + aliases. |
| `await canon.canonicalize(name) -> MatchResult` | Resolve and block duplicates (or suggest in approval mode). |
| `await canon.bulk_import(entities) -> ImportCounts` | Dedup-aware batch import → `{added, merged, skipped}`. |
| `await canon.import_csv(path, *, name_field, alias_field, alias_sep) -> ImportCounts` | Import from a CSV file (stdlib `csv`). |
| `await canon.get(id)` / `await canon.delete(id)` / `await canon.all()` | Fetch, remove, or list entities. |
| `bulk_import(canon, entities) -> ImportCounts` | Module-level wrapper over the method. |
| `CanonicalEntity(id, name, aliases=[])` | Pydantic v2 entity record (frozen); `surface_forms()` → `[name, *aliases]`. |
| `MatchResult(match, entity_id, confidence, blocked, suggestion, method, signals, fuzzy_ratio, phonetic_agreement)` | Pydantic v2 lookup result (frozen). |
| `ImportCounts(added, merged, skipped)` | Pydantic v2 batch outcome; `.total` property. |
| `MatchMethod` | StrEnum: `none` / `fuzzy` / `phonetic` / `fuzzy+phonetic` / `exact`. |
| `MatchSignal` | StrEnum: `exact` / `fuzzy` / `soundex` / `metaphone`. |
| `EntityStore` | Runtime-checkable async `Protocol`: `add`, `all`, `get`, `delete`. |
| `InMemoryEntityStore()` | Default in-process store (asyncio-lock guarded). |
| `SqliteEntityStore(path)` | Durable stdlib `sqlite3` store (off-loop via `asyncio.to_thread`). |
| `PostgresEntityStore(dsn, *, pool=None, min_size=1, max_size=10)` | Production `asyncpg` store behind the `postgres` extra (lazy import/pool); `await store.close()`. |
| `soundex(name)` / `metaphone(name)` | Phonetic encoders (stdlib, or `jellyfish` if installed). |
| `phonetic_equal(a, b)` / `metaphone_equal(a, b)` | Shared non-empty Soundex / Metaphone code. |
| `phonetic_agreement(a, b) -> float` | `1.0` both agree, `0.5` one, `0.0` none. |
| `canon.version()` / `health_check()` / `metrics()` | Inherited Trust Stack contract. |

Full API spec: [`docs/api/truststack-entity-canon.md`](../../docs/api/truststack-entity-canon.md).
Store DDL: [`docs/schemas/truststack-entity-canon.sql`](../../docs/schemas/truststack-entity-canon.sql).

## Matching model

```
confidence = fuzzy_ratio + 0.15 * phonetic_agreement
             (capped at 0.99 when fuzzy_ratio == 0, i.e. phonetic-only)
             (exact normalized match short-circuits to 1.0)
```

- **`fuzzy_ratio`** is `SequenceMatcher` over case-folded, whitespace-collapsed
  strings, in `[0, 1]`.
- **`phonetic_agreement`** is `0.0` / `0.5` / `1.0`: Soundex and Metaphone each
  contribute `0.5` when their codes match.
- The phonetic boost (`+0.15` max) **only nudges scores up** — it never pulls a
  strong fuzzy match down. This lets a homophone misspelling cross the line
  without letting phonetics alone fake certainty.
- A **phonetic-only** hit (zero fuzzy overlap) is capped at `0.99`, so it can
  surface as a suggestion but never auto-block at the default threshold without a
  human in the loop.
- Insertions are blocked when `confidence >= threshold` (default `0.90`). The
  boundary is inclusive: `0.89` passes, `0.90` blocks.

## Testing notes

The package is designed to be fully testable **offline, with no real services**:

- **Postgres** — inject an `AsyncMock` pool into `PostgresEntityStore(..., pool=mock)`;
  `asyncpg` is never imported.
- **Phonetic** — `soundex`/`metaphone` are deterministic and pure-stdlib by
  default, so correctness tests need no network.
- **REST API** — `create_app(canon)` returns a standard FastAPI app; drive it with
  `httpx.ASGITransport` / `TestClient`.

Unicode and accented names are NFKD-folded before encoding, so `José`, `Jose`, and
`JOSÉ` collapse to the same phonetic code.

---

Part of the **Trust Stack** — trustworthy primitives for AI agents.
