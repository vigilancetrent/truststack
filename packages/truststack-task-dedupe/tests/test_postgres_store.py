"""PostgresTaskStore tests against an AsyncMock-simulated asyncpg pool.

No real Postgres is contacted. A fake pool whose ``acquire()`` returns an async
context manager yielding a fake connection lets us assert on the exact SQL the
store issues (DDL, upsert, select, fingerprint probe) and feed back fake rows.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from task_dedupe import PostgresTaskStore, StoredTask, Task


class _FakeConn:
    """Records every execute/fetch/fetchval call and returns canned results."""

    def __init__(self) -> None:
        self.execute = AsyncMock(return_value="OK")
        self.fetch = AsyncMock(return_value=[])
        self.fetchval = AsyncMock(return_value=None)


class _Acquire:
    """Async context manager mimicking ``pool.acquire()``."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.closed = False

    def acquire(self) -> _Acquire:
        return _Acquire(self._conn)

    async def close(self) -> None:
        self.closed = True


def _row(id: str, fingerprint: str, payload: Any) -> dict[str, Any]:
    return {"id": id, "fingerprint": fingerprint, "payload": payload}


def test_requires_dsn_or_pool() -> None:
    with pytest.raises(ValueError):
        PostgresTaskStore()


def test_rejects_invalid_table_identifier() -> None:
    with pytest.raises(ValueError):
        PostgresTaskStore("postgres://localhost", table="bad-name")
    with pytest.raises(ValueError):
        PostgresTaskStore("postgres://localhost", table="drop table; --")


async def test_add_issues_ddl_then_upsert() -> None:
    conn = _FakeConn()
    store = PostgresTaskStore(pool=_FakePool(conn))

    await store.add("id-1", "fp-1", Task(title="ship release", project="core"))

    # Two DDL statements on first init + one upsert.
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert any("CREATE TABLE IF NOT EXISTS tasks" in s for s in statements)
    assert any("idx_tasks_fingerprint" in s for s in statements)
    upsert = next(s for s in statements if "INSERT INTO tasks" in s)
    assert "ON CONFLICT (id) DO UPDATE" in upsert
    # The upsert is parameterized: id, fingerprint, payload-json passed as args.
    upsert_call = next(c for c in conn.execute.await_args_list if "INSERT INTO" in c.args[0])
    assert upsert_call.args[1] == "id-1"
    assert upsert_call.args[2] == "fp-1"
    # args[3] is the JSON payload passed as the $3::jsonb parameter.
    assert "ship release" in upsert_call.args[3]
    assert upsert_call.args[3] == Task(title="ship release", project="core").model_dump_json()


async def test_schema_initialized_only_once() -> None:
    conn = _FakeConn()
    store = PostgresTaskStore(pool=_FakePool(conn))
    await store.add("id-1", "fp-1", Task(title="a"))
    ddl_after_first = sum("CREATE TABLE" in c.args[0] for c in conn.execute.await_args_list)
    await store.add("id-2", "fp-2", Task(title="b"))
    ddl_after_second = sum("CREATE TABLE" in c.args[0] for c in conn.execute.await_args_list)
    assert ddl_after_first == 1
    assert ddl_after_second == 1


async def test_all_decodes_rows_with_str_payload() -> None:
    conn = _FakeConn()
    payload = Task(title="hello", due="next week").model_dump_json()
    conn.fetch.return_value = [_row("id-1", "fp-1", payload)]
    store = PostgresTaskStore(pool=_FakePool(conn))

    records = await store.all()
    assert isinstance(records[0], StoredTask)
    assert records[0].id == "id-1"
    assert records[0].task.title == "hello"
    assert records[0].task.due == "next week"
    select_sql = conn.fetch.await_args.args[0]
    assert "SELECT id, fingerprint, payload FROM tasks ORDER BY id" in select_sql


async def test_all_decodes_rows_with_dict_payload() -> None:
    # asyncpg may hand back JSONB already decoded into a dict.
    conn = _FakeConn()
    conn.fetch.return_value = [_row("id-2", "fp-2", {"title": "decoded", "due": None})]
    store = PostgresTaskStore(pool=_FakePool(conn))
    records = await store.all()
    assert records[0].task.title == "decoded"


async def test_all_decodes_rows_with_bytes_payload() -> None:
    conn = _FakeConn()
    payload = Task(title="bytes path").model_dump_json().encode("utf-8")
    conn.fetch.return_value = [_row("id-3", "fp-3", payload)]
    store = PostgresTaskStore(pool=_FakePool(conn))
    records = await store.all()
    assert records[0].task.title == "bytes path"


async def test_all_on_empty_returns_empty() -> None:
    store = PostgresTaskStore(pool=_FakePool(_FakeConn()))
    assert await store.all() == []


async def test_has_fingerprint_true_and_false() -> None:
    conn = _FakeConn()
    conn.fetchval.return_value = 1
    store = PostgresTaskStore(pool=_FakePool(conn))
    assert await store.has_fingerprint("fp-1") is True
    probe_sql = conn.fetchval.await_args.args[0]
    assert "WHERE fingerprint = $1 LIMIT 1" in probe_sql
    assert conn.fetchval.await_args.args[1] == "fp-1"

    conn.fetchval.return_value = None
    assert await store.has_fingerprint("missing") is False


async def test_custom_table_name_threaded_through_sql() -> None:
    conn = _FakeConn()
    store = PostgresTaskStore(pool=_FakePool(conn), table="dedupe_tasks")
    await store.add("id-1", "fp-1", Task(title="x"))
    statements = [c.args[0] for c in conn.execute.await_args_list]
    assert any("CREATE TABLE IF NOT EXISTS dedupe_tasks" in s for s in statements)
    assert any("idx_dedupe_tasks_fingerprint" in s for s in statements)
    assert any("INSERT INTO dedupe_tasks" in s for s in statements)


async def test_close_delegates_to_pool() -> None:
    pool = _FakePool(_FakeConn())
    store = PostgresTaskStore(pool=pool)
    await store.close()
    assert pool.closed is True


async def test_close_without_pool_is_safe() -> None:
    store = PostgresTaskStore("postgres://localhost/db")
    # No pool ever created -> close() is a no-op, must not raise.
    await store.close()


async def test_store_error_surfaces_to_engine_health() -> None:
    from task_dedupe import DedupeEngine

    conn = _FakeConn()
    conn.fetch.side_effect = RuntimeError("connection reset")
    engine = DedupeEngine(store=PostgresTaskStore(pool=_FakePool(conn)))
    health = await engine.health_check()
    # Fail toward distrust: a broken store reports UNHEALTHY, not silent success.
    assert health.ok is False
    assert "store unavailable" in (health.detail or "")
