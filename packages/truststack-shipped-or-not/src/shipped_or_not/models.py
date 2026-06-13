"""Pydantic v2 request/result models and enums for deployment verification.

These models form the audit-grade contract: every claim that software is
"shipped" is backed by an explicit, JSON-serializable :class:`VerificationResult`
that records exactly which checks passed and which failed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DeploymentStatus(StrEnum):
    """Verdict for a deployment claim.

    ``SHIPPED`` is awarded only when *every* applicable check passes. Anything
    else — including any error — collapses to ``UNVERIFIED`` (fail toward
    distrust).
    """

    SHIPPED = "shipped"
    UNVERIFIED = "unverified"


class CheckResult(BaseModel):
    """The outcome of a single verification check (e.g. ``dns``, ``http``)."""

    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    detail: str | None = None

    def to_report(self) -> dict[str, Any]:
        """Return a JSON-serializable dict for this check."""
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


class RetryPolicy(BaseModel):
    """Exponential-backoff retry configuration for transient transport errors.

    The base delay before attempt ``n`` (1-indexed) is
    ``min(backoff_seconds * 2 ** (n - 2), max_backoff)``.

    When :attr:`jitter` is enabled the delay is multiplied by a random factor in
    ``[1 - jitter, 1 + jitter]``. The random source defaults to :func:`random.random`
    but a deterministic callable can be injected via :meth:`with_rng` so tests stay
    reproducible (the ``rng`` is *not* part of the serialized model).
    """

    model_config = ConfigDict(frozen=True)

    attempts: int = Field(default=3, ge=1, le=20)
    backoff_seconds: float = Field(default=0.5, gt=0.0)
    max_backoff: float = Field(default=8.0, gt=0.0)
    jitter: float = Field(default=0.0, ge=0.0, le=1.0)

    # A 0-arg callable returning a float in [0, 1). Excluded from (de)serialization
    # so the policy stays a plain, comparable, JSON-friendly model.
    _rng: Callable[[], float] | None = PrivateAttr(default=None)

    @field_validator("max_backoff")
    @classmethod
    def _max_ge_base(cls, value: float, info: Any) -> float:
        base = info.data.get("backoff_seconds")
        if base is not None and value < base:
            raise ValueError("max_backoff must be >= backoff_seconds")
        return value

    def with_rng(self, rng: Callable[[], float] | None) -> RetryPolicy:
        """Return a copy of this policy bound to ``rng`` for deterministic jitter.

        ``rng`` is a 0-argument callable returning a float in ``[0, 1)`` (the same
        contract as :func:`random.random`). Inject a fixed sequence in tests to make
        jittered delays exactly reproducible.
        """
        clone = self.model_copy()
        clone._rng = rng
        return clone

    def _base_delay(self, attempt: int) -> float:
        if attempt <= 1:
            return 0.0
        raw = self.backoff_seconds * (2.0 ** (attempt - 2))
        return min(raw, self.max_backoff)

    def delay_for(self, attempt: int) -> float:
        """Return the backoff delay (seconds) to wait *before* ``attempt``.

        ``attempt`` is 1-indexed; the first attempt always has zero delay. When
        :attr:`jitter` is non-zero the base exponential delay is scaled by a random
        factor in ``[1 - jitter, 1 + jitter]`` (drawn from the injected ``rng`` when
        present), and the result is clamped to ``[0, max_backoff]``.
        """
        base = self._base_delay(attempt)
        if base <= 0.0 or self.jitter <= 0.0:
            return base
        draw = self._rng() if self._rng is not None else _default_random()
        factor = 1.0 + self.jitter * (2.0 * draw - 1.0)
        jittered = base * factor
        return max(0.0, min(jittered, self.max_backoff))


def _default_random() -> float:
    import random

    return random.random()


class TlsInfo(BaseModel):
    """A subset of TLS certificate facts captured from the live connection.

    Populated best-effort from the peer certificate of an ``https`` request. All
    fields are optional because not every transport exposes the peer cert (e.g.
    mocked clients, or a plain ``http`` URL).
    """

    model_config = ConfigDict(frozen=True)

    issuer: str | None = None
    subject: str | None = None
    not_after: datetime | None = None
    not_before: datetime | None = None

    def to_report(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (datetimes as ISO-8601 strings)."""
        return {
            "issuer": self.issuer,
            "subject": self.subject,
            "not_after": self.not_after.isoformat() if self.not_after else None,
            "not_before": self.not_before.isoformat() if self.not_before else None,
        }


class VerificationResult(BaseModel):
    """Evidence-bearing result of verifying a single deployment claim."""

    model_config = ConfigDict(frozen=True)

    status: DeploymentStatus
    url: str
    response_code: int | None = None
    verified_at: datetime = Field(default_factory=_utcnow)
    ssl_valid: bool | None = None
    health_passed: bool | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    detail: str | None = None
    # --- Evidence captured from the live exchange (added; all optional) ---
    final_url: str | None = None
    elapsed_ms: float | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    tls: TlsInfo | None = None

    @property
    def shipped(self) -> bool:
        """``True`` only when the deployment was positively verified."""
        return self.status is DeploymentStatus.SHIPPED

    def to_report(self) -> dict[str, Any]:
        """Return a JSON-serializable audit report.

        Suitable for persistence to an audit log or emitting over the wire; all
        values are primitives (``datetime`` rendered as an ISO-8601 string).
        """
        return {
            "status": self.status.value,
            "url": self.url,
            "response_code": self.response_code,
            "verified_at": self.verified_at.isoformat(),
            "ssl_valid": self.ssl_valid,
            "health_passed": self.health_passed,
            "checks": [check.to_report() for check in self.checks],
            "detail": self.detail,
            "final_url": self.final_url,
            "elapsed_ms": self.elapsed_ms,
            "headers": dict(self.headers),
            "tls": self.tls.to_report() if self.tls is not None else None,
        }


__all__ = [
    "CheckResult",
    "DeploymentStatus",
    "RetryPolicy",
    "TlsInfo",
    "VerificationResult",
]
