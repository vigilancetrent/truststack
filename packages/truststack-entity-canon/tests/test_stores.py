"""Store backends: InMemory, Sqlite, and Postgres (via a fake asyncpg pool)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from entity_canon import (
    CanonicalEntity,
    Canonicalizer,
    InMemoryEntityStore,
    PostgresEntityStore,
    SqliteEntityStore,
)
from entity_canon.stores import EntityStore

# ── InMemoryEntityStore ────────────────────────────────────────────────────


async def test_in_memory_store_roundtrip() -> None:
    store = InMemoryEntityStore()
    await store.add(CanonicalEntity(id="1", name="Jatin", aliases=["Jat"]))
    entities = await store.all()
    assert len(entities) == 1
    assert entities[0].name == "Jatin"
    assert entities[0].aliases == ["Jat"]


async def test_in_memory_replace_by_id() -> None:
    store = InMemoryEntityStore()
    await store.add(CanonicalEntity(id="1", name="Jatin"))
    await store.add(CanonicalEntity(id="1", name="Jatin Patel"))
    entities = await store.all()
    assert len(entities) == 1
    assert entities[0].name == "Jatin Patel"


async def test_in_memory_get_and_delete() -> None:
    store = InMemoryEntityStore()
    await store.add(CanonicalEntity(id="1", name="Jatin"))
    assert (await store.get("1")).name == "Jatin"  # type: ignore[union-attr]
    assert (await store.get("missing")) is None
    assert (await store.delete("1")) is True
    assert (await store.delete("1")) is False
    assert (await store.all()) == []


def test_in_memory_satisfies_protocol() -> None:
    assert isinstance(InMemoryEntityStore(), EntityStore)


# ── SqliteEntityStore ──────────────────────────────────────────────────────


async def test_sqlite_store_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "entities.db"
    store = SqliteEntityStore(db)
    await store.add(CanonicalEntity(id="1", name="Jatin", aliases=["Jat", "JT"]))
    await store.add(CanonicalEntity(id="2", name="Maria"))
    entities = {e.id: e for e in await store.all()}
    assert entities["1"].name == "Jatin"
    assert entities["1"].aliases == ["Jat", "JT"]
    assert entities["2"].name == "Maria"


async def test_sqlite_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "entities.db"
    await SqliteEntityStore(db).add(CanonicalEntity(id="1", name="Jatin"))
    reopened = SqliteEntityStore(db)
    entities = await reopened.all()
    assert len(entities) == 1
    assert entities[0].name == "Jatin"


async def test_sqlite_get_and_delete(tmp_path: Path) -> None:
    store = SqliteEntityStore(tmp_path / "e.db")
    await store.add(CanonicalEntity(id="1", name="Jatin"))
    assert (await store.get("1")).name == "Jatin"  # type: ignore[union-attr]
    assert (await store.get("missing")) is None
    assert (await store.delete("1")) is True
    assert (await store.delete("1")) is False


async def test_sqlite_replace_by_id(tmp_path: Path) -> None:
    store = SqliteEntityStore(tmp_path / "e.db")
    await store.add(CanonicalEntity(id="1", name="Jatin"))
    await store.add(CanonicalEntity(id="1", name="Renamed"))
    rows = await store.all()
    assert len(rows) == 1
    assert rows[0].name == "Renamed"


async def test_sqlite_unicode_aliases(tmp_path: Path) -> None:
    store = SqliteEntityStore(tmp_path / "e.db")
    await store.add(CanonicalEntity(id="1", name="José", aliases=["Renée"]))
    row = await store.get("1")
    assert row is not None
    assert row.name == "José"
    assert row.aliases == ["Renée"]


async def test_sqlite_schema_initialized_once(tmp_path: Path) -> None:
    store = SqliteEntityStore(tmp_path / "e.db")
    await store.all()  # triggers schema init
    assert store._initialized is True
    await store.all()  # second call must be a no-op for schema


async def test_sqlite_backed_canonicalizer(tmp_path: Path) -> None:
    canon = Canonicalizer(store=SqliteEntityStore(tmp_path / "c.db"))
    await canon.add("Jatin")
    result = await canon.canonicalize("Jhatin")
    assert result.blocked is True
    assert result.suggestion == "Jatin"


# ── PostgresEntityStore (fake asyncpg pool, no live DB) ─────────────────────
#
# ``fake_pg_pool`` and the helper fixtures come from ``conftest.py``. Sharing via
# fixtures (not cross-module imports) keeps these tests compatible with pytest's
# ``importlib`` import mode used by this monorepo.

MakeEntity = Callable[..., CanonicalEntity]
AliasesOf = Callable[[dict[str, Any]], list[str]]


async def test_postgres_construction_requires_dsn_or_pool() -> None:
    with pytest.raises(ValueError):
        PostgresEntityStore(dsn="")


async def test_postgres_injected_pool_no_io_on_construct(fake_pg_pool: Any) -> None:
    store = PostgresEntityStore(dsn="", pool=fake_pg_pool)  # empty dsn ok with pool
    # Constructing performs no I/O: nothing executed yet.
    assert fake_pg_pool.executed == []
    assert store._schema_ready is False


async def test_postgres_add_and_get(fake_pg_pool: Any, make_entity_fn: MakeEntity) -> None:
    store = PostgresEntityStore(dsn="postgresql://x", pool=fake_pg_pool)
    await store.add(make_entity_fn("1", "Jatin", "Jat", "JT"))
    # Schema DDL should have run exactly once on first use.
    assert any("CREATE TABLE" in q for q in fake_pg_pool.executed)
    fetched = await store.get("1")
    assert fetched is not None
    assert fetched.name == "Jatin"
    assert fetched.aliases == ["Jat", "JT"]


async def test_postgres_get_missing(fake_pg_pool: Any) -> None:
    store = PostgresEntityStore(dsn="postgresql://x", pool=fake_pg_pool)
    assert (await store.get("nope")) is None


async def test_postgres_all(fake_pg_pool: Any, make_entity_fn: MakeEntity) -> None:
    store = PostgresEntityStore(dsn="postgresql://x", pool=fake_pg_pool)
    await store.add(make_entity_fn("1", "Jatin"))
    await store.add(make_entity_fn("2", "Maria"))
    rows = {e.id: e for e in await store.all()}
    assert set(rows) == {"1", "2"}
    assert rows["2"].name == "Maria"


async def test_postgres_upsert_replaces(fake_pg_pool: Any, make_entity_fn: MakeEntity) -> None:
    store = PostgresEntityStore(dsn="postgresql://x", pool=fake_pg_pool)
    await store.add(make_entity_fn("1", "Jatin"))
    await store.add(make_entity_fn("1", "Renamed", "alt"))
    rows = await store.all()
    assert len(rows) == 1
    assert rows[0].name == "Renamed"
    assert rows[0].aliases == ["alt"]


async def test_postgres_delete(fake_pg_pool: Any, make_entity_fn: MakeEntity) -> None:
    store = PostgresEntityStore(dsn="postgresql://x", pool=fake_pg_pool)
    await store.add(make_entity_fn("1", "Jatin"))
    assert (await store.delete("1")) is True
    assert (await store.delete("1")) is False
    assert (await store.all()) == []


async def test_postgres_schema_created_once(fake_pg_pool: Any, make_entity_fn: MakeEntity) -> None:
    store = PostgresEntityStore(dsn="postgresql://x", pool=fake_pg_pool)
    await store.all()
    await store.all()
    await store.add(make_entity_fn("1", "X"))
    create_count = sum(1 for q in fake_pg_pool.executed if "CREATE TABLE" in q)
    assert create_count == 1
    assert store._schema_ready is True


async def test_postgres_row_to_entity_accepts_jsonb_text() -> None:
    # asyncpg may hand back JSONB as text; the codec-tolerant path must decode it.
    entity = PostgresEntityStore._row_to_entity(
        {"id": "1", "name": "Jatin", "aliases": '["Jat", "JT"]'}
    )
    assert entity.aliases == ["Jat", "JT"]


async def test_postgres_row_to_entity_accepts_list() -> None:
    entity = PostgresEntityStore._row_to_entity({"id": "1", "name": "Jatin", "aliases": ["Jat"]})
    assert entity.aliases == ["Jat"]


async def test_postgres_close(fake_pg_pool: Any, make_entity_fn: MakeEntity) -> None:
    store = PostgresEntityStore(dsn="postgresql://x", pool=fake_pg_pool)
    await store.add(make_entity_fn("1", "X"))
    await store.close()
    assert fake_pg_pool.closed is True
    assert store._pool is None
    assert store._schema_ready is False


async def test_postgres_close_without_pool_is_noop() -> None:
    store = PostgresEntityStore(dsn="postgresql://x")
    # No pool was ever created; close must not raise.
    await store.close()
    assert store._pool is None


async def test_postgres_aliases_serialized_as_json(
    fake_pg_pool: Any, make_entity_fn: MakeEntity, aliases_of_fn: AliasesOf
) -> None:
    store = PostgresEntityStore(dsn="postgresql://x", pool=fake_pg_pool)
    await store.add(make_entity_fn("1", "Jatin", "Jat"))
    # The stored column holds a JSON string, decoded back to a list here.
    assert aliases_of_fn(fake_pg_pool.rows["1"]) == ["Jat"]


async def test_postgres_canonicalizer_end_to_end(fake_pg_pool: Any) -> None:
    canon = Canonicalizer(store=PostgresEntityStore(dsn="postgresql://x", pool=fake_pg_pool))
    await canon.add("Jatin")
    result = await canon.canonicalize("Jhatin")
    assert result.blocked is True
    assert result.suggestion == "Jatin"


async def test_postgres_lazy_pool_creation(
    monkeypatch: pytest.MonkeyPatch,
    pg_pool_factory: Callable[[], Any],
    make_entity_fn: MakeEntity,
) -> None:
    # Without an injected pool, _get_pool must lazily import asyncpg and call
    # create_pool. We stub a fake asyncpg module so no real driver is needed.
    import sys
    import types
    from unittest.mock import AsyncMock

    fake_pool = pg_pool_factory()
    fake_asyncpg = types.ModuleType("asyncpg")
    fake_asyncpg.create_pool = AsyncMock(return_value=fake_pool)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncpg", fake_asyncpg)

    store = PostgresEntityStore(dsn="postgresql://localhost/db")
    await store.add(make_entity_fn("1", "Jatin"))
    fake_asyncpg.create_pool.assert_awaited_once()  # type: ignore[attr-defined]
    assert store._pool is fake_pool
