"""Entity persistence backends.

The :class:`EntityStore` protocol defines the minimal contract. Two local,
zero-infrastructure implementations ship by default:

* :class:`InMemoryEntityStore` — a dict-backed store (default).
* :class:`SqliteEntityStore` — stdlib :mod:`sqlite3` run off the event loop via
  :func:`asyncio.to_thread`.

A production :class:`PostgresEntityStore` (``asyncpg``) lives behind the optional
``postgres`` extra. ``asyncpg`` is imported lazily inside the methods that use
it, so this module imports cleanly with only the required dependencies.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .models import CanonicalEntity

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncpg


@runtime_checkable
class EntityStore(Protocol):
    """Async storage contract for canonical entities."""

    async def add(self, entity: CanonicalEntity) -> None:
        """Persist (or replace) ``entity``."""
        ...

    async def all(self) -> list[CanonicalEntity]:
        """Return every stored entity."""
        ...

    async def get(self, entity_id: str) -> CanonicalEntity | None:
        """Return the entity with ``entity_id``, or ``None`` if absent."""
        ...

    async def delete(self, entity_id: str) -> bool:
        """Delete ``entity_id``; return ``True`` if a row was removed."""
        ...


class InMemoryEntityStore:
    """A process-local store backed by a dict. The default backend."""

    def __init__(self) -> None:
        self._entities: dict[str, CanonicalEntity] = {}
        self._lock = asyncio.Lock()

    async def add(self, entity: CanonicalEntity) -> None:
        async with self._lock:
            self._entities[entity.id] = entity

    async def all(self) -> list[CanonicalEntity]:
        async with self._lock:
            return list(self._entities.values())

    async def get(self, entity_id: str) -> CanonicalEntity | None:
        async with self._lock:
            return self._entities.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        async with self._lock:
            return self._entities.pop(entity_id, None) is not None


class SqliteEntityStore:
    """A durable store backed by stdlib :mod:`sqlite3`.

    Blocking sqlite calls are dispatched to a worker thread with
    :func:`asyncio.to_thread` so they never stall the event loop. A fresh,
    short-lived connection is opened per operation, which keeps the store safe
    to use across threads.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._initialized = False
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    id      TEXT PRIMARY KEY,
                    name    TEXT NOT NULL,
                    aliases TEXT NOT NULL DEFAULT '[]'
                )
                """
            )

    def _add_sync(self, entity: CanonicalEntity) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO entities (id, name, aliases) VALUES (?, ?, ?)",
                (entity.id, entity.name, json.dumps(entity.aliases)),
            )

    def _all_sync(self) -> list[CanonicalEntity]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name, aliases FROM entities").fetchall()
        return [self._row_to_entity(row) for row in rows]

    def _get_sync(self, entity_id: str) -> CanonicalEntity | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, aliases FROM entities WHERE id = ?",
                (entity_id,),
            ).fetchone()
        return self._row_to_entity(row) if row is not None else None

    def _delete_sync(self, entity_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
            return cur.rowcount > 0

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> CanonicalEntity:
        return CanonicalEntity(
            id=row["id"],
            name=row["name"],
            aliases=list(json.loads(row["aliases"])),
        )

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if not self._initialized:
                await asyncio.to_thread(self._init_schema)
                self._initialized = True

    async def add(self, entity: CanonicalEntity) -> None:
        await self._ensure_schema()
        await asyncio.to_thread(self._add_sync, entity)

    async def all(self) -> list[CanonicalEntity]:
        await self._ensure_schema()
        return await asyncio.to_thread(self._all_sync)

    async def get(self, entity_id: str) -> CanonicalEntity | None:
        await self._ensure_schema()
        return await asyncio.to_thread(self._get_sync, entity_id)

    async def delete(self, entity_id: str) -> bool:
        await self._ensure_schema()
        return await asyncio.to_thread(self._delete_sync, entity_id)


#: DDL applied lazily on first use by :class:`PostgresEntityStore`.
_POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS entities (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb
)
"""


class PostgresEntityStore:
    """Production Postgres-backed store using ``asyncpg``.

    Requires the optional ``postgres`` extra
    (``pip install truststack-entity-canon[postgres]``). The ``asyncpg`` import
    and connection-pool creation are lazy: the pool is created on first use, so
    constructing the store performs no I/O and never imports ``asyncpg`` at
    module import time.

    A caller may inject a pre-built pool (handy for testing with an
    ``AsyncMock``); otherwise one is created from ``dsn`` on first access.
    """

    def __init__(
        self,
        dsn: str,
        *,
        pool: asyncpg.Pool | None = None,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        if not dsn and pool is None:
            raise ValueError("a dsn or an existing pool is required")
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = pool
        self._min_size = min_size
        self._max_size = max_size
        # Schema is initialised lazily on first use, even when a pool is injected.
        self._schema_ready = False
        # Separate (non-reentrant) locks: one guards pool creation, the other
        # guards one-time schema init, so _ensure_schema can call _get_pool
        # without self-deadlocking on a shared lock.
        self._pool_lock = asyncio.Lock()
        self._schema_lock = asyncio.Lock()

    async def _get_pool(self) -> asyncpg.Pool:
        """Return the connection pool, creating it on first use."""
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    import asyncpg  # lazy: only when the postgres extra is installed

                    self._pool = await asyncpg.create_pool(
                        self._dsn,
                        min_size=self._min_size,
                        max_size=self._max_size,
                    )
        pool = self._pool
        if pool is None:  # pragma: no cover - defensive; create_pool never returns None
            raise RuntimeError("failed to create asyncpg pool")
        return pool

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        # Create the pool outside the schema lock to avoid lock nesting.
        pool = await self._get_pool()
        async with self._schema_lock:
            if self._schema_ready:
                return
            async with pool.acquire() as conn:
                await conn.execute(_POSTGRES_DDL)
            self._schema_ready = True

    @staticmethod
    def _row_to_entity(row: Any) -> CanonicalEntity:
        raw_aliases = row["aliases"]
        # asyncpg returns JSONB as text unless a codec is set; tolerate both.
        aliases = json.loads(raw_aliases) if isinstance(raw_aliases, str) else list(raw_aliases)
        return CanonicalEntity(id=row["id"], name=row["name"], aliases=list(aliases))

    async def add(self, entity: CanonicalEntity) -> None:
        await self._ensure_schema()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO entities (id, name, aliases)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    aliases = EXCLUDED.aliases
                """,
                entity.id,
                entity.name,
                json.dumps(entity.aliases),
            )

    async def all(self) -> list[CanonicalEntity]:
        await self._ensure_schema()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, name, aliases FROM entities")
        return [self._row_to_entity(row) for row in rows]

    async def get(self, entity_id: str) -> CanonicalEntity | None:
        await self._ensure_schema()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, aliases FROM entities WHERE id = $1",
                entity_id,
            )
        return self._row_to_entity(row) if row is not None else None

    async def delete(self, entity_id: str) -> bool:
        await self._ensure_schema()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM entities WHERE id = $1",
                entity_id,
            )
        # asyncpg returns a command tag like "DELETE 1"; trailing int is the count.
        try:
            return int(str(status).rsplit(" ", 1)[-1]) > 0
        except (ValueError, IndexError):  # pragma: no cover - defensive
            return False

    async def close(self) -> None:
        """Close the underlying connection pool if one was created."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._schema_ready = False


__all__ = [
    "EntityStore",
    "InMemoryEntityStore",
    "PostgresEntityStore",
    "SqliteEntityStore",
]
