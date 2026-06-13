"""Task storage backends.

A :class:`TaskStore` persists ``(id, fingerprint, task)`` records that the
engine compares new tasks against. Four backends ship:

* :class:`InMemoryTaskStore` — process-local, the default. Stdlib only.
* :class:`SqliteTaskStore` — durable, backed by stdlib ``sqlite3`` with blocking
  calls offloaded via :func:`asyncio.to_thread`. Stdlib only.
* :class:`RedisTaskStore` — backed by ``redis.asyncio`` (optional ``redis``
  extra, imported lazily).
* :class:`PostgresTaskStore` — backed by ``asyncpg`` (optional ``postgres``
  extra, imported lazily).

The Redis and Postgres clients are imported lazily inside the methods that use
them, so importing this module never requires the optional extras.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .models import Task

if TYPE_CHECKING:
    from redis.asyncio import Redis


@dataclass(frozen=True, slots=True)
class StoredTask:
    """An immutable stored task record."""

    id: str
    fingerprint: str
    task: Task


@runtime_checkable
class TaskStore(Protocol):
    """Append-only store of deduplication candidates."""

    async def add(self, id: str, fingerprint: str, task: Task) -> None: ...

    async def all(self) -> list[StoredTask]: ...


def _encode_record(id: str, fingerprint: str, task: Task) -> str:
    """Serialize a record to a stable JSON envelope for KV backends."""
    return json.dumps(
        {"id": id, "fingerprint": fingerprint, "payload": task.model_dump(mode="json")},
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_record(raw: str | bytes) -> StoredTask:
    """Inverse of :func:`_encode_record`."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    return StoredTask(
        id=str(data["id"]),
        fingerprint=str(data["fingerprint"]),
        task=Task.model_validate(data["payload"]),
    )


class InMemoryTaskStore:
    """Thread-safe in-process store. Default backend; needs no infrastructure."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[StoredTask] = []

    async def add(self, id: str, fingerprint: str, task: Task) -> None:
        with self._lock:
            self._records.append(StoredTask(id=id, fingerprint=fingerprint, task=task))

    async def all(self) -> list[StoredTask]:
        with self._lock:
            return list(self._records)


class SqliteTaskStore:
    """Durable store backed by stdlib ``sqlite3``.

    All blocking database work runs inside :func:`asyncio.to_thread`. A short-
    lived connection per operation (``check_same_thread=False``) keeps the store
    safe across the thread-pool workers used by ``to_thread``.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_fingerprint ON tasks (fingerprint)"
                )
                conn.commit()
            finally:
                conn.close()
            self._initialized = True

    def _add_sync(self, id: str, fingerprint: str, payload: str) -> None:
        self._ensure_schema()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO tasks (id, fingerprint, payload) VALUES (?, ?, ?)",
                (id, fingerprint, payload),
            )
            conn.commit()
        finally:
            conn.close()

    def _all_sync(self) -> list[StoredTask]:
        self._ensure_schema()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, fingerprint, payload FROM tasks ORDER BY rowid"
            ).fetchall()
        finally:
            conn.close()
        return [
            StoredTask(
                id=row["id"],
                fingerprint=row["fingerprint"],
                task=Task.model_validate(json.loads(row["payload"])),
            )
            for row in rows
        ]

    async def add(self, id: str, fingerprint: str, task: Task) -> None:
        payload = task.model_dump_json()
        await asyncio.to_thread(self._add_sync, id, fingerprint, payload)

    async def all(self) -> list[StoredTask]:
        return await asyncio.to_thread(self._all_sync)


class RedisTaskStore:
    """Redis-backed store using ``redis.asyncio`` (lazy import).

    Records are stored as JSON strings in a hash keyed ``{namespace}:tasks`` with
    the task id as the field, so :meth:`add` is a single ``HSET`` and :meth:`all`
    a single ``HGETALL``. A secondary set ``{namespace}:fingerprints`` is
    maintained for O(1) fingerprint-existence checks via :meth:`has_fingerprint`.

    Pass either a connection ``url`` (``redis://...``) or a pre-built async
    ``client`` (e.g. a ``fakeredis.aioredis.FakeRedis`` for offline tests).
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        namespace: str = "task_dedupe",
        client: Redis | None = None,
    ) -> None:
        if url is None and client is None:
            msg = "RedisTaskStore requires either a url or a client"
            raise ValueError(msg)
        self._url = url
        self._namespace = namespace
        self._client: Redis | None = client

    @property
    def _tasks_key(self) -> str:
        return f"{self._namespace}:tasks"

    @property
    def _fingerprints_key(self) -> str:
        return f"{self._namespace}:fingerprints"

    async def _get_client(self) -> Redis:
        if self._client is not None:
            return self._client
        try:
            from redis.asyncio import Redis as AsyncRedis
        except ImportError as exc:  # pragma: no cover - exercised only without extra
            raise RuntimeError(
                "RedisTaskStore requires redis. Install it with: "
                "pip install 'truststack-task-dedupe[redis]'"
            ) from exc
        assert self._url is not None  # guaranteed by __init__
        self._client = AsyncRedis.from_url(self._url)
        return self._client

    async def add(self, id: str, fingerprint: str, task: Task) -> None:
        client = await self._get_client()
        record = _encode_record(id, fingerprint, task)
        pipe = client.pipeline(transaction=True)
        pipe.hset(self._tasks_key, id, record)
        pipe.sadd(self._fingerprints_key, fingerprint)
        await pipe.execute()

    async def all(self) -> list[StoredTask]:
        client = await self._get_client()
        raw: dict[Any, Any] = await client.hgetall(self._tasks_key)
        records = [_decode_record(value) for value in raw.values()]
        # Stable ordering by id keeps results deterministic across calls.
        records.sort(key=lambda r: r.id)
        return records

    async def has_fingerprint(self, fingerprint: str) -> bool:
        """Return True if any stored task carries ``fingerprint`` (O(1))."""
        client = await self._get_client()
        return bool(await client.sismember(self._fingerprints_key, fingerprint))

    async def close(self) -> None:
        """Release the underlying connection pool, if one was created."""
        if self._client is not None:
            await self._client.aclose()


class PostgresTaskStore:
    """Postgres-backed store using ``asyncpg`` (lazy import).

    A connection pool is created lazily on first use. The ``tasks`` table mirrors
    the SQLite layout (id / fingerprint / payload JSONB) and is created on
    initialization. Pass either a ``dsn`` or a pre-built asyncpg ``pool`` (e.g. a
    mock pool for offline tests).
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        table: str = "tasks",
        pool: Any | None = None,
    ) -> None:
        if dsn is None and pool is None:
            msg = "PostgresTaskStore requires either a dsn or a pool"
            raise ValueError(msg)
        if not table.isidentifier():
            msg = f"table name must be a valid identifier, got {table!r}"
            raise ValueError(msg)
        self._dsn = dsn
        self._table = table
        self._pool: Any | None = pool
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _get_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        try:
            import asyncpg
        except ImportError as exc:  # pragma: no cover - exercised only without extra
            raise RuntimeError(
                "PostgresTaskStore requires asyncpg. Install it with: "
                "pip install 'truststack-task-dedupe[postgres]'"
            ) from exc
        assert self._dsn is not None  # guaranteed by __init__
        self._pool = await asyncpg.create_pool(dsn=self._dsn)
        return self._pool

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        id          TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL,
                        payload     JSONB NOT NULL
                    )
                    """
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._table}_fingerprint "
                    f"ON {self._table} (fingerprint)"
                )
            self._initialized = True

    async def add(self, id: str, fingerprint: str, task: Task) -> None:
        await self._ensure_schema()
        pool = await self._get_pool()
        payload = task.model_dump_json()
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table} (id, fingerprint, payload)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (id) DO UPDATE
                    SET fingerprint = EXCLUDED.fingerprint,
                        payload = EXCLUDED.payload
                """,
                id,
                fingerprint,
                payload,
            )

    async def all(self) -> list[StoredTask]:
        await self._ensure_schema()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, fingerprint, payload FROM {self._table} ORDER BY id"
            )
        return [
            StoredTask(
                id=str(row["id"]),
                fingerprint=str(row["fingerprint"]),
                task=_payload_to_task(row["payload"]),
            )
            for row in rows
        ]

    async def has_fingerprint(self, fingerprint: str) -> bool:
        """Return True if any stored task carries ``fingerprint``."""
        await self._ensure_schema()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                f"SELECT 1 FROM {self._table} WHERE fingerprint = $1 LIMIT 1",
                fingerprint,
            )
        return value is not None

    async def close(self) -> None:
        """Close the connection pool, if one was created."""
        if self._pool is not None:
            await self._pool.close()


def _payload_to_task(payload: Any) -> Task:
    """Coerce an asyncpg JSONB column (str or already-decoded) into a Task."""
    if isinstance(payload, str | bytes | bytearray):
        return Task.model_validate_json(payload)
    return Task.model_validate(payload)


__all__ = [
    "InMemoryTaskStore",
    "PostgresTaskStore",
    "RedisTaskStore",
    "SqliteTaskStore",
    "StoredTask",
    "TaskStore",
]
