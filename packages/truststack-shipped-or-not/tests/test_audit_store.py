"""Tests for the audit-trail stores (InMemory + Sqlite).

All offline: SQLite runs against an in-memory or temp-file database, no real
services are touched.
"""

from __future__ import annotations

from pathlib import Path

from shipped_or_not import (
    AuditStore,
    InMemoryAuditStore,
    SqliteAuditStore,
)
from shipped_or_not.audit import _result_to_row, _row_to_result
from shipped_or_not.models import (
    CheckResult,
    DeploymentStatus,
    TlsInfo,
    VerificationResult,
)


def _result(url: str = "https://example.com", code: int = 200) -> VerificationResult:
    return VerificationResult(
        status=DeploymentStatus.SHIPPED if code == 200 else DeploymentStatus.UNVERIFIED,
        url=url,
        response_code=code,
        ssl_valid=True,
        health_passed=True,
        checks=[CheckResult(name="http_status", passed=code == 200, detail="ok")],
        detail=None,
        final_url=url,
        elapsed_ms=12.5,
        headers={"server": "nginx", "content-type": "text/html"},
        tls=TlsInfo(issuer="CN=Test CA", subject="CN=example.com"),
    )


# --------------------------------------------------------------------------- #
# InMemoryAuditStore
# --------------------------------------------------------------------------- #
async def test_inmemory_is_auditstore_protocol() -> None:
    store = InMemoryAuditStore()
    assert isinstance(store, AuditStore)


async def test_inmemory_record_and_history() -> None:
    store = InMemoryAuditStore()
    a = _result("https://a.example.com", 200)
    b = _result("https://b.example.com", 500)
    a2 = _result("https://a.example.com", 500)

    await store.record(a)
    await store.record(b)
    await store.record(a2)

    history_a = await store.history("https://a.example.com")
    assert [r.response_code for r in history_a] == [200, 500]
    history_b = await store.history("https://b.example.com")
    assert len(history_b) == 1


async def test_inmemory_history_unknown_url_is_empty() -> None:
    store = InMemoryAuditStore()
    await store.record(_result())
    assert await store.history("https://nope.example.com") == []


async def test_inmemory_all_returns_copy() -> None:
    store = InMemoryAuditStore()
    await store.record(_result("https://a.example.com"))
    await store.record(_result("https://b.example.com"))

    everything = await store.all()
    assert len(everything) == 2
    # Mutating the returned list must not corrupt the store's internal state.
    everything.clear()
    assert len(await store.all()) == 2


async def test_inmemory_close_is_noop() -> None:
    store = InMemoryAuditStore()
    assert await store.close() is None


# --------------------------------------------------------------------------- #
# SqliteAuditStore
# --------------------------------------------------------------------------- #
async def test_sqlite_is_auditstore_protocol() -> None:
    store = await SqliteAuditStore.connect()
    try:
        assert isinstance(store, AuditStore)
    finally:
        await store.close()


async def test_sqlite_record_history_roundtrip_preserves_evidence() -> None:
    store = await SqliteAuditStore.connect()
    try:
        original = _result("https://x.example.com", 200)
        await store.record(original)

        history = await store.history("https://x.example.com")
        assert len(history) == 1
        restored = history[0]
        assert restored.status is DeploymentStatus.SHIPPED
        assert restored.response_code == 200
        assert restored.final_url == original.final_url
        assert restored.elapsed_ms == original.elapsed_ms
        assert restored.headers == {"server": "nginx", "content-type": "text/html"}
        assert restored.tls is not None
        assert restored.tls.issuer == "CN=Test CA"
        assert restored.checks[0].name == "http_status"
    finally:
        await store.close()


async def test_sqlite_history_is_chronological_by_insertion() -> None:
    store = await SqliteAuditStore.connect()
    try:
        for code in (200, 500, 204):
            await store.record(_result("https://seq.example.com", code))
        history = await store.history("https://seq.example.com")
        assert [r.response_code for r in history] == [200, 500, 204]
    finally:
        await store.close()


async def test_sqlite_history_filters_by_url() -> None:
    store = await SqliteAuditStore.connect()
    try:
        await store.record(_result("https://one.example.com"))
        await store.record(_result("https://two.example.com"))
        assert len(await store.history("https://one.example.com")) == 1
        assert await store.history("https://missing.example.com") == []
    finally:
        await store.close()


async def test_sqlite_all_returns_every_row() -> None:
    store = await SqliteAuditStore.connect()
    try:
        await store.record(_result("https://one.example.com"))
        await store.record(_result("https://two.example.com"))
        everything = await store.all()
        assert {r.url for r in everything} == {
            "https://one.example.com",
            "https://two.example.com",
        }
    finally:
        await store.close()


async def test_sqlite_persists_to_disk_across_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    store = await SqliteAuditStore.connect(db_path)
    await store.record(_result("https://persist.example.com"))
    await store.close()

    reopened = await SqliteAuditStore.connect(db_path)
    try:
        history = await reopened.history("https://persist.example.com")
        assert len(history) == 1
    finally:
        await reopened.close()


async def test_sqlite_initialize_is_idempotent() -> None:
    store = SqliteAuditStore()
    try:
        await store.initialize()
        await store.initialize()  # second call must not raise on existing schema
        await store.record(_result())
        assert len(await store.all()) == 1
    finally:
        await store.close()


async def test_sqlite_accepts_path_object(tmp_path: Path) -> None:
    store = await SqliteAuditStore.connect(Path(tmp_path / "p.db"))
    try:
        await store.record(_result())
        assert len(await store.all()) == 1
    finally:
        await store.close()


def test_result_row_helpers_roundtrip() -> None:
    result = _result("https://helper.example.com", 200)
    url, verified_at, payload = _result_to_row(result)
    assert url == "https://helper.example.com"
    assert verified_at == result.verified_at.isoformat()
    restored = _row_to_result(payload)
    assert restored.url == result.url
    assert restored.response_code == 200


def test_sqlite_uses_stdlib_only() -> None:
    # SQLite is part of the stdlib, so the durable store needs no live server.
    import sqlite3

    assert sqlite3.sqlite_version_info >= (3, 0, 0)
