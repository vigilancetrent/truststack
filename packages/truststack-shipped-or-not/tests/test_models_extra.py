"""Model-level edge tests: RetryPolicy jitter, TlsInfo, and report serialization."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from shipped_or_not.models import (
    CheckResult,
    DeploymentStatus,
    RetryPolicy,
    TlsInfo,
    VerificationResult,
)


# --------------------------------------------------------------------------- #
# RetryPolicy jitter
# --------------------------------------------------------------------------- #
def test_jitter_zero_means_deterministic_base() -> None:
    policy = RetryPolicy(attempts=5, backoff_seconds=1.0, max_backoff=8.0, jitter=0.0)
    assert policy.delay_for(2) == 1.0
    assert policy.delay_for(3) == 2.0


def test_jitter_with_injected_rng_is_deterministic() -> None:
    policy = RetryPolicy(attempts=5, backoff_seconds=2.0, max_backoff=64.0, jitter=0.5).with_rng(
        lambda: 0.0
    )
    # draw=0.0 -> factor = 1 + 0.5 * (0 - 1) = 0.5 -> base(3)=4.0 * 0.5 = 2.0
    assert policy.delay_for(3) == 2.0

    upper = policy.with_rng(lambda: 1.0)
    # draw~1.0 -> factor = 1 + 0.5 * (2 - 1) = 1.5 -> 4.0 * 1.5 = 6.0
    assert upper.delay_for(3) == 6.0


def test_jitter_midpoint_rng_returns_base() -> None:
    policy = RetryPolicy(attempts=5, backoff_seconds=1.0, max_backoff=64.0, jitter=0.3).with_rng(
        lambda: 0.5
    )
    # draw=0.5 -> factor = 1 + 0.3 * (1 - 1) = 1.0 -> base unchanged
    assert policy.delay_for(2) == 1.0


def test_jitter_is_clamped_to_max_backoff() -> None:
    policy = RetryPolicy(attempts=5, backoff_seconds=4.0, max_backoff=5.0, jitter=1.0).with_rng(
        lambda: 1.0
    )
    # base(2)=4.0, factor up to 2.0 -> 8.0, clamped to max_backoff=5.0
    assert policy.delay_for(2) == 5.0


def test_jitter_first_attempt_is_always_zero() -> None:
    policy = RetryPolicy(jitter=0.5).with_rng(lambda: 1.0)
    assert policy.delay_for(1) == 0.0


def test_jitter_uses_default_rng_when_unset() -> None:
    policy = RetryPolicy(attempts=3, backoff_seconds=1.0, max_backoff=8.0, jitter=0.2)
    delay = policy.delay_for(2)
    # base(2)=1.0 with +/-20% jitter from random.random()
    assert 0.8 <= delay <= 1.2


def test_with_rng_does_not_mutate_original() -> None:
    base = RetryPolicy(jitter=0.5)
    bound = base.with_rng(lambda: 0.0)
    assert base._rng is None
    assert bound._rng is not None


def test_rng_is_excluded_from_serialization() -> None:
    policy = RetryPolicy(jitter=0.5).with_rng(lambda: 0.0)
    dumped = policy.model_dump()
    assert "_rng" not in dumped
    assert "rng" not in dumped


# --------------------------------------------------------------------------- #
# TlsInfo
# --------------------------------------------------------------------------- #
def test_tls_info_to_report_with_dates() -> None:
    info = TlsInfo(
        issuer="CN=CA",
        subject="CN=example.com",
        not_after=datetime(2030, 1, 1, tzinfo=UTC),
        not_before=datetime(2020, 1, 1, tzinfo=UTC),
    )
    report = info.to_report()
    assert report["issuer"] == "CN=CA"
    assert report["not_after"] == "2030-01-01T00:00:00+00:00"
    assert report["not_before"] == "2020-01-01T00:00:00+00:00"


def test_tls_info_to_report_with_none_dates() -> None:
    info = TlsInfo()
    report = info.to_report()
    assert report["not_after"] is None
    assert report["not_before"] is None
    assert report["issuer"] is None


# --------------------------------------------------------------------------- #
# VerificationResult evidence + reporting
# --------------------------------------------------------------------------- #
def test_shipped_property() -> None:
    shipped = VerificationResult(status=DeploymentStatus.SHIPPED, url="https://x.com")
    unverified = VerificationResult(status=DeploymentStatus.UNVERIFIED, url="https://x.com")
    assert shipped.shipped is True
    assert unverified.shipped is False


def test_to_report_includes_full_evidence() -> None:
    result = VerificationResult(
        status=DeploymentStatus.SHIPPED,
        url="https://x.com",
        response_code=200,
        ssl_valid=True,
        health_passed=True,
        checks=[CheckResult(name="dns", passed=True, detail="resolved")],
        detail=None,
        final_url="https://x.com/landing",
        elapsed_ms=42.0,
        headers={"server": "nginx"},
        tls=TlsInfo(issuer="CN=CA", not_after=datetime(2030, 1, 1, tzinfo=UTC)),
    )
    report = result.to_report()
    assert report["final_url"] == "https://x.com/landing"
    assert report["elapsed_ms"] == 42.0
    assert report["headers"] == {"server": "nginx"}
    assert report["tls"]["issuer"] == "CN=CA"
    assert report["checks"][0]["name"] == "dns"
    # Fully JSON serializable.
    assert json.loads(json.dumps(report))["status"] == "shipped"


def test_to_report_with_no_tls() -> None:
    result = VerificationResult(status=DeploymentStatus.UNVERIFIED, url="http://x.com")
    report = result.to_report()
    assert report["tls"] is None
    assert report["headers"] == {}


def test_check_result_to_report() -> None:
    check = CheckResult(name="ssl", passed=False, detail="expired")
    assert check.to_report() == {"name": "ssl", "passed": False, "detail": "expired"}


def test_verification_result_model_validate_roundtrip() -> None:
    original = VerificationResult(
        status=DeploymentStatus.SHIPPED,
        url="https://x.com",
        response_code=200,
        tls=TlsInfo(issuer="CN=CA", not_after=datetime(2030, 1, 1, tzinfo=UTC)),
    )
    restored = VerificationResult.model_validate(original.to_report())
    assert restored.status is DeploymentStatus.SHIPPED
    assert restored.tls is not None
    assert restored.tls.issuer == "CN=CA"
