"""Pydantic v2 models and enums for the Meta token vault.

These models are the wire/contract surface of the vault: the :class:`Token`
itself, the :class:`AuditEntry` records that form the audit trail, and the small
:class:`Role`/:class:`Action` enums that drive role-based access control.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


class Role(StrEnum):
    """Coarse RBAC roles recognised by the vault."""

    #: Full control: store, read, rotate, refresh.
    ADMIN = "admin"
    #: May store, read, and trigger rotation/refresh but not administer.
    OPERATOR = "operator"
    #: Read-only access to active tokens and the audit trail.
    VIEWER = "viewer"


class Action(StrEnum):
    """Auditable actions performed against the vault."""

    STORE = "store"
    GET = "get"
    ROTATE = "rotate"
    REFRESH = "refresh"
    EXPIRE = "expire"


class Token(BaseModel):
    """A Meta/WhatsApp access token and its lifecycle metadata.

    ``value`` is the secret material. When persisted by an encrypting store it is
    encrypted at rest; in memory it is held as-is, so treat instances as secrets.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=_new_id)
    value: str
    app_id: str
    scopes: list[str] = Field(default_factory=list)
    issued_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        """Return ``True`` if the token has an expiry that is at or before ``now``."""
        if self.expires_at is None:
            return False
        return (now or _utcnow()) >= self.expires_at

    def expires_in_seconds(self, now: datetime | None = None) -> float | None:
        """Seconds until expiry (negative if already expired), or ``None`` if no expiry."""
        if self.expires_at is None:
            return None
        return (self.expires_at - (now or _utcnow())).total_seconds()


class AuditEntry(BaseModel):
    """An immutable record of a single vault operation."""

    model_config = ConfigDict(frozen=True)

    action: Action
    app_id: str
    token_id: str | None = None
    at: datetime = Field(default_factory=_utcnow)
    actor: str = "system"


__all__ = ["Action", "AuditEntry", "Role", "Token"]
