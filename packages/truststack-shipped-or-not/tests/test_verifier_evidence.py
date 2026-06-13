"""Evidence-capture, audit-integration, and monitor tests for DeploymentVerifier.

Offline only: HTTP traffic is faked with ``pytest_httpx``; TLS extraction is
exercised by feeding the pure parser helpers synthetic ``ssl.getpeercert()``
dicts and by stubbing the network-stream ``ssl_object``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from shipped_or_not import (
    DeploymentStatus,
    DeploymentVerifier,
    InMemoryAuditStore,
    RetryPolicy,
    SqliteAuditStore,
    VerificationResult,
)
from shipped_or_not.models import TlsInfo
from shipped_or_not.verifier import (
    _elapsed_ms,
    _extract_tls,
    _join_url,
    _looks_like_ssl_error,
    _parse_cert_time,
    _parse_peer_cert,
    _rdn_to_str,
)

URL = "https://example.com"


def _fast_retry() -> RetryPolicy:
    return RetryPolicy(attempts=2, backoff_seconds=0.001, max_backoff=0.001)


# --------------------------------------------------------------------------- #
# Evidence capture
# --------------------------------------------------------------------------- #
async def test_redirect_chain_records_final_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=URL, status_code=302, headers={"location": "https://example.com/landing"}
    )
    httpx_mock.add_response(url="https://example.com/landing", status_code=200)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL)

    assert result.status is DeploymentStatus.SHIPPED
    assert result.final_url == "https://example.com/landing"


async def test_explicit_expect_status_mismatch(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL, expect_status=204)

    assert result.status is DeploymentStatus.UNVERIFIED
    assert any(c.name == "http_status" and not c.passed for c in result.checks)
    assert result.detail is not None and "http_status" in result.detail


async def test_plain_http_has_ssl_valid_none(httpx_mock: HTTPXMock) -> None:
    http_url = "http://plain.example.com"
    httpx_mock.add_response(url=http_url, status_code=200)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(http_url)

    assert result.ssl_valid is None
    assert result.tls is None


async def test_headers_and_elapsed_captured(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=URL,
        status_code=200,
        headers={
            "server": "nginx/1.25",
            "content-type": "application/json",
            "x-secret": "should-not-be-captured",
        },
    )
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL)

    assert result.headers["server"] == "nginx/1.25"
    assert result.headers["content-type"] == "application/json"
    assert "x-secret" not in result.headers
    assert result.elapsed_ms is not None and result.elapsed_ms >= 0.0


async def test_timeout_yields_unverified(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("read timed out"), is_reusable=True)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL)

    assert result.status is DeploymentStatus.UNVERIFIED
    assert result.detail is not None
    assert result.elapsed_ms is not None


async def test_generic_http_error_path(httpx_mock: HTTPXMock) -> None:
    # A non-connect, non-transport HTTPError (e.g. invalid response framing).
    httpx_mock.add_exception(httpx.RemoteProtocolError("bad framing"), is_reusable=True)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL)

    assert result.status is DeploymentStatus.UNVERIFIED
    assert result.detail is not None and "request failed" in result.detail
    assert any(c.name == "http" and not c.passed for c in result.checks)


async def test_unexpected_exception_path(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = DeploymentVerifier(retry=_fast_retry())

    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(verifier, "_request_with_retry", _boom)

    result = await verifier.verify(URL)

    assert result.status is DeploymentStatus.UNVERIFIED
    assert result.detail is not None and "unexpected error" in result.detail
    assert any(c.name == "error" and not c.passed for c in result.checks)


async def test_health_endpoint_transport_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    httpx_mock.add_exception(
        httpx.ConnectError("health unreachable"),
        url=f"{URL}/healthz",
        is_reusable=True,
    )
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL, health_path="/healthz")

    assert result.status is DeploymentStatus.UNVERIFIED
    assert result.health_passed is False
    assert any(c.name == "health" and not c.passed for c in result.checks)


# --------------------------------------------------------------------------- #
# Audit-store integration
# --------------------------------------------------------------------------- #
async def test_verify_persists_to_inmemory_audit(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    audit = InMemoryAuditStore()
    verifier = DeploymentVerifier(retry=_fast_retry(), audit_store=audit)

    await verifier.verify(URL)

    history = await verifier.history(URL)
    assert len(history) == 1
    assert history[0].status is DeploymentStatus.SHIPPED


async def test_verify_persists_to_sqlite_and_history(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    httpx_mock.add_response(url=URL, status_code=500)
    store = await SqliteAuditStore.connect()
    try:
        verifier = DeploymentVerifier(retry=_fast_retry(), audit_store=store)
        await verifier.verify(URL)
        await verifier.verify(URL)

        history = await verifier.history(URL)
        assert [r.response_code for r in history] == [200, 500]
        assert [r.status for r in history] == [
            DeploymentStatus.SHIPPED,
            DeploymentStatus.UNVERIFIED,
        ]
    finally:
        await store.close()


async def test_history_without_store_raises() -> None:
    verifier = DeploymentVerifier(retry=_fast_retry())
    with pytest.raises(RuntimeError, match="history requires an audit_store"):
        await verifier.history(URL)


async def test_persist_failure_is_swallowed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)

    class _BrokenStore:
        async def record(self, result: VerificationResult) -> None:
            raise OSError("disk full")

        async def history(self, url: str) -> list[VerificationResult]:
            return []

        async def all(self) -> list[VerificationResult]:
            return []

        async def close(self) -> None:
            return None

    verifier = DeploymentVerifier(retry=_fast_retry(), audit_store=_BrokenStore())

    # The real verdict must survive a broken audit backend.
    result = await verifier.verify(URL)
    assert result.status is DeploymentStatus.SHIPPED


# --------------------------------------------------------------------------- #
# monitor()
# --------------------------------------------------------------------------- #
async def test_monitor_fires_on_change_and_stops(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    httpx_mock.add_response(url=URL, status_code=200)
    httpx_mock.add_response(url=URL, status_code=500)
    verifier = DeploymentVerifier(retry=_fast_retry())

    changes: list[DeploymentStatus] = []

    async def on_change(result: VerificationResult) -> None:
        changes.append(result.status)

    last = await verifier.monitor(URL, interval=0.0, on_change=on_change, iterations=3)

    # First observation (SHIPPED) fires; second SHIPPED does not; third (UNVERIFIED) fires.
    assert changes == [DeploymentStatus.SHIPPED, DeploymentStatus.UNVERIFIED]
    assert last is not None and last.status is DeploymentStatus.UNVERIFIED


async def test_monitor_invokes_webhook_on_change(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200, is_reusable=True)
    verifier = DeploymentVerifier(retry=_fast_retry())

    on_change_calls: list[VerificationResult] = []
    notify_calls: list[VerificationResult] = []

    async def on_change(result: VerificationResult) -> None:
        on_change_calls.append(result)

    async def notify(result: VerificationResult) -> None:
        notify_calls.append(result)

    await verifier.monitor(URL, interval=0.0, on_change=on_change, iterations=2, notify=notify)

    # Status never changes after the first observation -> exactly one edge.
    assert len(on_change_calls) == 1
    assert len(notify_calls) == 1


async def test_monitor_notify_failure_is_swallowed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200, is_reusable=True)
    verifier = DeploymentVerifier(retry=_fast_retry())

    fired: list[VerificationResult] = []

    async def on_change(result: VerificationResult) -> None:
        fired.append(result)

    async def bad_notify(result: VerificationResult) -> None:
        raise RuntimeError("webhook down")

    # Must not raise despite the notify callback throwing.
    last = await verifier.monitor(
        URL, interval=0.0, on_change=on_change, iterations=1, notify=bad_notify
    )
    assert last is not None
    assert len(fired) == 1


async def test_monitor_is_cancellable(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200, is_reusable=True)
    verifier = DeploymentVerifier(retry=_fast_retry())

    async def on_change(result: VerificationResult) -> None:
        return None

    # Unbounded monitor with a real (small) interval, then cancel it.
    task = asyncio.create_task(verifier.monitor(URL, interval=10.0, on_change=on_change))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_monitor_zero_iterations_returns_none(httpx_mock: HTTPXMock) -> None:
    verifier = DeploymentVerifier(retry=_fast_retry())

    async def on_change(result: VerificationResult) -> None:
        return None

    last = await verifier.monitor(URL, interval=0.0, on_change=on_change, iterations=0)
    assert last is None


# --------------------------------------------------------------------------- #
# TLS extraction + helper parsers
# --------------------------------------------------------------------------- #
def test_parse_peer_cert_full() -> None:
    cert: dict[str, object] = {
        "issuer": ((("organizationName", "Test CA"),), (("commonName", "Test Root"),)),
        "subject": ((("commonName", "example.com"),),),
        "notAfter": "Jun  1 12:00:00 2030 GMT",
        "notBefore": "Jun  1 12:00:00 2020 GMT",
    }
    info = _parse_peer_cert(cert)
    assert isinstance(info, TlsInfo)
    assert info.issuer is not None and "organizationName=Test CA" in info.issuer
    assert info.subject == "commonName=example.com"
    assert info.not_after == datetime(2030, 6, 1, 12, 0, 0, tzinfo=UTC)
    assert info.not_before == datetime(2020, 6, 1, 12, 0, 0, tzinfo=UTC)


def test_rdn_to_str_handles_non_sequence() -> None:
    assert _rdn_to_str(None) is None
    assert _rdn_to_str("not-a-tuple") is None
    assert _rdn_to_str(((),)) is None  # empty relative -> no parts
    # Mixed garbage entries are skipped gracefully.
    assert _rdn_to_str((("bad",), (("k", "v"),))) == "k=v"


def test_parse_cert_time_handles_bad_input() -> None:
    assert _parse_cert_time(None) is None
    assert _parse_cert_time(12345) is None
    assert _parse_cert_time("not a date") is None
    parsed = _parse_cert_time("Jan  2 03:04:05 2026 GMT")
    assert parsed == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_looks_like_ssl_error() -> None:
    assert _looks_like_ssl_error(Exception("certificate verify failed")) is True
    assert _looks_like_ssl_error(Exception("[SSL] handshake failure")) is True
    assert _looks_like_ssl_error(Exception("hostname mismatch")) is True
    assert _looks_like_ssl_error(Exception("connection refused")) is False


def test_join_url_normalizes_slashes() -> None:
    assert _join_url("https://x.com/", "/healthz") == "https://x.com/healthz"
    assert _join_url("https://x.com", "healthz") == "https://x.com/healthz"
    assert _join_url("https://x.com///", "///healthz") == "https://x.com/healthz"


def test_elapsed_ms_is_non_negative() -> None:
    import time

    started = time.perf_counter()
    assert _elapsed_ms(started) >= 0.0


def test_extract_tls_no_extensions() -> None:
    class _Resp:
        extensions: dict[str, Any] = {}

    assert _extract_tls(_Resp()) is None  # type: ignore[arg-type]


def test_extract_tls_no_network_stream() -> None:
    class _Resp:
        extensions = {"other": object()}

    assert _extract_tls(_Resp()) is None  # type: ignore[arg-type]


def test_extract_tls_no_ssl_object() -> None:
    class _Stream:
        def get_extra_info(self, _name: str) -> Any:
            return None

    class _Resp:
        extensions = {"network_stream": _Stream()}

    assert _extract_tls(_Resp()) is None  # type: ignore[arg-type]


def test_extract_tls_empty_peer_cert() -> None:
    class _Ssl:
        def getpeercert(self) -> dict[str, object]:
            return {}

    class _Stream:
        def get_extra_info(self, _name: str) -> Any:
            return _Ssl()

    class _Resp:
        extensions = {"network_stream": _Stream()}

    assert _extract_tls(_Resp()) is None  # type: ignore[arg-type]


def test_extract_tls_full_path_returns_info() -> None:
    cert: dict[str, object] = {
        "issuer": ((("commonName", "Real CA"),),),
        "subject": ((("commonName", "site.example.com"),),),
        "notAfter": "Dec 31 23:59:59 2031 GMT",
    }

    class _Ssl:
        def getpeercert(self) -> dict[str, object]:
            return cert

    class _Stream:
        def get_extra_info(self, _name: str) -> Any:
            return _Ssl()

    class _Resp:
        extensions = {"network_stream": _Stream()}

    info = _extract_tls(_Resp())  # type: ignore[arg-type]
    assert info is not None
    assert info.issuer == "commonName=Real CA"
    assert info.not_after == datetime(2031, 12, 31, 23, 59, 59, tzinfo=UTC)
