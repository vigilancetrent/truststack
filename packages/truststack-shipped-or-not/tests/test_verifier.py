from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from shipped_or_not import (
    DeploymentStatus,
    DeploymentVerifier,
    RetryPolicy,
    VerificationResult,
)
from truststack.core import ComponentMetrics, HealthState
from truststack.events import EventBus, TrustEvent

URL = "https://example.com"


def _fast_retry() -> RetryPolicy:
    # Keep retries quick and zero-delay so tests never sleep meaningfully.
    return RetryPolicy(attempts=2, backoff_seconds=0.001, max_backoff=0.001)


async def test_shipped_when_status_matches(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL)

    assert isinstance(result, VerificationResult)
    assert result.status is DeploymentStatus.SHIPPED
    assert result.response_code == 200
    assert result.ssl_valid is True
    assert all(check.passed for check in result.checks)


async def test_unverified_on_500(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=500)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL)

    assert result.status is DeploymentStatus.UNVERIFIED
    assert result.response_code == 500
    failed = {c.name for c in result.checks if not c.passed}
    assert "http_status" in failed


async def test_unverified_on_connection_error(httpx_mock: HTTPXMock) -> None:
    # Reusable: the connection fails on every retry attempt (a real outage).
    httpx_mock.add_exception(httpx.ConnectError("name resolution failed"), is_reusable=True)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL)

    assert result.status is DeploymentStatus.UNVERIFIED
    assert result.response_code is None
    assert result.detail is not None
    assert "connection failed" in result.detail


async def test_invalid_ssl_is_unverified(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(
        httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
        is_reusable=True,
    )
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL)

    assert result.status is DeploymentStatus.UNVERIFIED
    assert result.ssl_valid is False
    assert any(c.name == "ssl" and not c.passed for c in result.checks)


async def test_health_path_must_pass(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    httpx_mock.add_response(url=f"{URL}/healthz", status_code=503)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL, health_path="/healthz")

    assert result.status is DeploymentStatus.UNVERIFIED
    assert result.health_passed is False


async def test_health_path_passes(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    httpx_mock.add_response(url=f"{URL}/healthz", status_code=200)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL, health_path="/healthz")

    assert result.status is DeploymentStatus.SHIPPED
    assert result.health_passed is True


async def test_custom_expect_status(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=204)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(URL, expect_status=204)

    assert result.status is DeploymentStatus.SHIPPED


async def test_http_url_has_no_ssl_check(httpx_mock: HTTPXMock) -> None:
    http_url = "http://example.com"
    httpx_mock.add_response(url=http_url, status_code=200)
    verifier = DeploymentVerifier(retry=_fast_retry())

    result = await verifier.verify(http_url)

    assert result.status is DeploymentStatus.SHIPPED
    assert result.ssl_valid is None
    assert not any(c.name == "ssl" for c in result.checks)


async def test_metrics_recorded(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    httpx_mock.add_response(url=URL, status_code=500)
    verifier = DeploymentVerifier(retry=_fast_retry())

    await verifier.verify(URL)
    await verifier.verify(URL)

    m = await verifier.metrics()
    assert isinstance(m, ComponentMetrics)
    assert m.counters["verifications"] == 2
    assert m.counters["shipped"] == 1
    assert m.counters["unverified"] == 1


async def test_emits_shipped_event(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    bus = EventBus()
    received: list[TrustEvent] = []

    async def _record(event: TrustEvent) -> None:
        received.append(event)

    bus.subscribe("*", _record)
    verifier = DeploymentVerifier(retry=_fast_retry(), event_bus=bus)

    await verifier.verify(URL)

    assert len(received) == 1
    assert received[0].name == "deployment.shipped"
    assert received[0].data["status"] == "shipped"


async def test_emits_unverified_event(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=500)
    bus = EventBus()
    received: list[TrustEvent] = []

    async def _record(event: TrustEvent) -> None:
        received.append(event)

    bus.subscribe("deployment.unverified", _record)
    verifier = DeploymentVerifier(retry=_fast_retry(), event_bus=bus)

    await verifier.verify(URL)

    assert len(received) == 1
    assert received[0].name == "deployment.unverified"


async def test_retry_then_succeed(httpx_mock: HTTPXMock) -> None:
    # First transport attempt fails, second returns 200 -> SHIPPED.
    httpx_mock.add_exception(httpx.ConnectError("transient blip"))
    httpx_mock.add_response(url=URL, status_code=200)
    verifier = DeploymentVerifier(retry=RetryPolicy(attempts=3, backoff_seconds=0.001))

    result = await verifier.verify(URL)

    assert result.status is DeploymentStatus.SHIPPED


async def test_health_check_and_version() -> None:
    verifier = DeploymentVerifier()
    assert verifier.version() == "0.1.0"
    health = await verifier.health_check()
    assert health.state is HealthState.HEALTHY
    assert health.component == "shipped-or-not"


def test_to_report_is_json_serializable(httpx_mock: HTTPXMock) -> None:
    import json

    result = VerificationResult(status=DeploymentStatus.SHIPPED, url=URL, response_code=200)
    report = result.to_report()
    # round-trips without error
    assert json.loads(json.dumps(report))["status"] == "shipped"


def test_retry_policy_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy(attempts=5, backoff_seconds=1.0, max_backoff=4.0)
    assert policy.delay_for(1) == 0.0
    assert policy.delay_for(2) == 1.0
    assert policy.delay_for(3) == 2.0
    assert policy.delay_for(4) == 4.0
    assert policy.delay_for(5) == 4.0  # capped


def test_retry_policy_rejects_bad_max_backoff() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(backoff_seconds=2.0, max_backoff=1.0)
