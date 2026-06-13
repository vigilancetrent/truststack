"""Durable audit trail for verification verdicts.

Every :class:`~shipped_or_not.models.VerificationResult` a verifier produces can be
persisted to an :class:`AuditStore`, giving an after-the-fact, queryable record of
*when* a URL was claimed shipped and *what evidence* backed that verdict. This is
the difference between "the agent said it deployed" and "here is the timestamped
proof, replayable on demand".

Two implementations ship here:

* :class:`InMemoryAuditStore` — a process-local list, ideal for tests and ephemeral
  runs.
* :class:`SqliteAuditStore` — a durable on-disk (or in-memory) SQLite store. All
  blocking ``sqlite3`` calls run in a worker thread via :func:`asyncio.to_thread`
  so the event loop is never blocked.

Both honour the :class:`AuditStore` protocol so callers can swap freely.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from shipped_or_not.models import VerificationResult


@runtime_checkable
class AuditStore(Protocol):
    """Persistence contract for verification results.

    Implementations record every result and can replay the history for a URL in
    chronological (oldest-first) order.
    """

    async def record(self, result: VerificationResult) -> None:
        """Persist a single verification ``result``."""
        ...

    async def history(self, url: str) -> list[VerificationResult]:
        """Return all recorded results for ``url``, oldest first."""
        ...

    async def all(self) -> list[VerificationResult]:
        """Return every recorded result across all URLs, oldest first."""
        ...

    async def close(self) -> None:
        """Release any held resources (connections, files)."""
        ...


class InMemoryAuditStore:
    """A process-local, thread-safe :class:`AuditStore` backed by a list.

    Insertion order is preserved, which is also chronological order because each
    result carries its own ``verified_at`` and is appended as it arrives.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: list[VerificationResult] = []

    async def record(self, result: VerificationResult) -> None:
        with self._lock:
            self._results.append(result)

    async def history(self, url: str) -> list[VerificationResult]:
        with self._lock:
            return [r for r in self._results if r.url == url]

    async def all(self) -> list[VerificationResult]:
        with self._lock:
            return list(self._results)

    async def close(self) -> None:
        return None


def _result_to_row(result: VerificationResult) -> tuple[str, str, str]:
    """Flatten a result into the (url, verified_at, payload-json) row tuple."""
    return (
        result.url,
        result.verified_at.isoformat(),
        json.dumps(result.to_report()),
    )


def _row_to_result(payload: str) -> VerificationResult:
    """Reconstruct a :class:`VerificationResult` from its stored JSON report.

    The report is the lossless ``to_report()`` shape, so :meth:`model_validate`
    round-trips it directly (Pydantic coerces ISO-8601 strings back to datetimes).
    """
    data: dict[str, Any] = json.loads(payload)
    return VerificationResult.model_validate(data)


class SqliteAuditStore:
    """A durable :class:`AuditStore` on top of stdlib :mod:`sqlite3`.

    The full audit report is stored as JSON in a single ``payload`` column, with
    ``url`` and ``verified_at`` denormalized into indexed columns for fast history
    queries. All blocking SQLite work is dispatched to a thread via
    :func:`asyncio.to_thread`, keeping the async API non-blocking.

    A single connection is reused (``check_same_thread=False``) and guarded by a
    lock, since every access already funnels through one worker thread at a time.
    Use :func:`SqliteAuditStore.connect` to construct (it creates the schema), or
    construct directly and call :meth:`initialize` before first use.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialized = False

    @classmethod
    async def connect(cls, path: str | Path = ":memory:") -> SqliteAuditStore:
        """Create a store and ensure its schema exists."""
        store = cls(path)
        await store.initialize()
        return store

    async def initialize(self) -> None:
        """Create the table and index if they do not already exist."""
        await asyncio.to_thread(self._create_schema)

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._initialized = True

    async def record(self, result: VerificationResult) -> None:
        row = _result_to_row(result)
        await asyncio.to_thread(self._insert, row)

    def _insert(self, row: tuple[str, str, str]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO verifications (url, verified_at, payload) VALUES (?, ?, ?)",
                row,
            )
            self._conn.commit()

    async def history(self, url: str) -> list[VerificationResult]:
        rows = await asyncio.to_thread(self._select_by_url, url)
        return [_row_to_result(payload) for payload in rows]

    def _select_by_url(self, url: str) -> list[str]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT payload FROM verifications WHERE url = ? ORDER BY id ASC",
                (url,),
            )
            return [str(r["payload"]) for r in cursor.fetchall()]

    async def all(self) -> list[VerificationResult]:
        rows = await asyncio.to_thread(self._select_all)
        return [_row_to_result(payload) for payload in rows]

    def _select_all(self) -> list[str]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT payload FROM verifications ORDER BY id ASC",
            )
            return [str(r["payload"]) for r in cursor.fetchall()]

    async def close(self) -> None:
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        with self._lock:
            self._conn.close()


# Kept in sync with docs/schemas/truststack-shipped-or-not.sql.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verifications_url ON verifications (url);
"""


__all__ = [
    "AuditStore",
    "InMemoryAuditStore",
    "SqliteAuditStore",
]
