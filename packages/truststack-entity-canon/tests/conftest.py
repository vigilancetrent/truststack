"""Shared fixtures and fakes for the entity-canon test suite.

Everything here runs fully offline. The Postgres tests use a hand-rolled fake
``asyncpg`` pool/connection built from :class:`unittest.mock.AsyncMock` so no
real database is ever contacted.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from entity_canon import CanonicalEntity


class FakeAcquire:
    """Async context manager returned by ``pool.acquire()``.

    Mirrors asyncpg's ``pool.acquire()`` which is an async context manager
    yielding a connection. Both ``async with pool.acquire() as conn`` and the
    plain awaitable form used by some asyncpg versions are supported.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakePostgresPool:
    """A minimal in-memory stand-in for an ``asyncpg.Pool``.

    It backs a single ``entities`` table with a dict keyed by id, implements the
    exact SQL the :class:`PostgresEntityStore` issues, and records calls so tests
    can assert behaviour without a live server.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.closed = False
        self.executed: list[str] = []
        # The connection object the store interacts with.
        self.conn = MagicMock()
        self.conn.execute = AsyncMock(side_effect=self._execute)
        self.conn.fetch = AsyncMock(side_effect=self._fetch)
        self.conn.fetchrow = AsyncMock(side_effect=self._fetchrow)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)

    async def close(self) -> None:
        self.closed = True

    # NOTE: these side_effect callables are intentionally *synchronous*. They are
    # attached to ``AsyncMock`` instances, which provide the await machinery; a
    # sync side_effect's return value becomes the awaited result. Making them
    # ``async def`` would leave a coroutine un-awaited on some Python versions.
    def _execute(self, query: str, *args: Any) -> str:
        self.executed.append(query)
        normalized = " ".join(query.split())
        if normalized.startswith("CREATE TABLE"):
            return "CREATE TABLE"
        if normalized.startswith("INSERT INTO entities"):
            entity_id, name, aliases_json = args
            self.rows[entity_id] = {
                "id": entity_id,
                "name": name,
                "aliases": aliases_json,
            }
            return "INSERT 0 1"
        if normalized.startswith("DELETE FROM entities"):
            (entity_id,) = args
            existed = self.rows.pop(entity_id, None) is not None
            return f"DELETE {1 if existed else 0}"
        raise AssertionError(f"unexpected execute: {normalized!r}")

    def _fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return list(self.rows.values())

    def _fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        (entity_id,) = args
        return self.rows.get(entity_id)


@pytest.fixture
def fake_pg_pool() -> FakePostgresPool:
    """Return a fresh in-memory fake asyncpg pool."""
    return FakePostgresPool()


@pytest.fixture
def pg_pool_factory() -> Callable[[], FakePostgresPool]:
    """Return a factory that builds fresh fake asyncpg pools.

    Useful for tests that need to construct an additional pool (e.g. to stand in
    for ``asyncpg.create_pool``) beyond the one provided by ``fake_pg_pool``.
    """
    return FakePostgresPool


def make_entity(entity_id: str, name: str, *aliases: str) -> CanonicalEntity:
    """Build a :class:`CanonicalEntity` for tests.

    Exposed via the ``make_entity`` fixture below so it can be used under
    pytest's ``importlib`` import mode without cross-test-module imports.
    """
    return CanonicalEntity(id=entity_id, name=name, aliases=list(aliases))


def aliases_of(row: dict[str, Any]) -> list[str]:
    """Decode the JSON-encoded aliases column from a fake row."""
    raw = row["aliases"]
    return list(json.loads(raw)) if isinstance(raw, str) else list(raw)


@pytest.fixture
def make_entity_fn() -> Callable[..., CanonicalEntity]:
    """Fixture wrapper around :func:`make_entity`."""
    return make_entity


@pytest.fixture
def aliases_of_fn() -> Callable[[dict[str, Any]], list[str]]:
    """Fixture wrapper around :func:`aliases_of`."""
    return aliases_of


@pytest.fixture(autouse=True)
def _no_jellyfish(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the pure-stdlib phonetic path so tests are deterministic.

    ``jellyfish`` is an optional extra; if it happens to be installed in the
    environment the bundled stdlib encoders would be bypassed and the expected
    codes could differ. Setting ``sys.modules["jellyfish"] = None`` makes the
    lazy ``import jellyfish`` raise ``ImportError`` (the standard idiom), so the
    stdlib implementation is always exercised — without touching ``__import__``.
    """
    import sys

    monkeypatch.setitem(sys.modules, "jellyfish", None)
    yield
