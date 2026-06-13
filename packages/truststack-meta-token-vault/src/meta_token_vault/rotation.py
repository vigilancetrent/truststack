"""Rotation policy for vault-managed tokens.

A :class:`RotationPolicy` expresses *when* a token becomes due for proactive
rotation, expressed as a maximum age (independent of, and usually stricter than,
the token's own hard expiry). The vault consults it to decide whether to rotate a
token before it is actually close to expiring -- a defence-in-depth posture that
limits the blast radius of a leaked credential.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import Token


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RotationPolicy:
    """Age-based rotation policy.

    :param max_age_seconds: a token is *due* for rotation once it has been issued
        for at least this many seconds. Must be positive.
    """

    def __init__(self, max_age_seconds: float) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self.max_age_seconds = float(max_age_seconds)

    def age_seconds(self, token: Token, now: datetime | None = None) -> float:
        """Return how many seconds ago ``token`` was issued."""
        return ((now or _utcnow()) - token.issued_at).total_seconds()

    def is_due(self, token: Token, now: datetime | None = None) -> bool:
        """Return ``True`` if ``token`` is at or beyond :attr:`max_age_seconds`."""
        return self.age_seconds(token, now) >= self.max_age_seconds


__all__ = ["RotationPolicy"]
