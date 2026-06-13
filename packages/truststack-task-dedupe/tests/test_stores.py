"""Tests for the storage backends."""

from __future__ import annotations

import pytest

from task_dedupe import (
    DedupeEngine,
    InMemoryTaskStore,
    PostgresTaskStore,
    RedisTaskStore,
    SqliteTaskStore,
    Task,
)


async def test_in_memory_store_roundtrip() -> None:
    store = InMemoryTaskStore()
    await store.add("id-1", "fp-1", Task(title="hello"))
    records = await store.all()
    assert len(records) == 1
    assert records[0].id == "id-1"
    assert records[0].fingerprint == "fp-1"
    assert records[0].task.title == "hello"


async def test_sqlite_store_roundtrip(tmp_path: object) -> None:
    db_path = str(tmp_path / "dedupe.db")  # type: ignore[operator]
    store = SqliteTaskStore(db_path)
    await store.add("id-1", "fp-1", Task(title="ship release", due="next week", project="core"))
    records = await store.all()
    assert len(records) == 1
    rec = records[0]
    assert rec.id == "id-1"
    assert rec.task.project == "core"


async def test_sqlite_store_persists_across_instances(tmp_path: object) -> None:
    db_path = str(tmp_path / "dedupe.db")  # type: ignore[operator]
    store_a = SqliteTaskStore(db_path)
    await store_a.add("id-1", "fp-1", Task(title="persisted"))

    store_b = SqliteTaskStore(db_path)
    records = await store_b.all()
    assert [r.task.title for r in records] == ["persisted"]


async def test_engine_with_sqlite_store(tmp_path: object) -> None:
    db_path = str(tmp_path / "engine.db")  # type: ignore[operator]
    engine = DedupeEngine(store=SqliteTaskStore(db_path))
    first = await engine.check(Task(title="Update docs", assignee="lee"))
    assert first.duplicate is False
    second = await engine.check(Task(title="update docs", assignee="lee"))
    assert second.duplicate is True


def test_optional_stores_require_url_or_client() -> None:
    # Backends are now fully implemented; constructing one needs a target.
    with pytest.raises(ValueError):
        RedisTaskStore()
    with pytest.raises(ValueError):
        PostgresTaskStore()
    # A bad table name is rejected without any network access.
    with pytest.raises(ValueError):
        PostgresTaskStore("postgres://localhost", table="bad-name")
