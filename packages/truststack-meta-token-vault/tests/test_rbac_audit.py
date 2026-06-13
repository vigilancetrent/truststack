"""Tests for RBAC enforcement, actor threading through the audit trail, events,
and the failure alert hook (fail toward distrust)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meta_token_vault import (
    Action,
    Role,
    Token,
    Vault,
    is_allowed,
)
from meta_token_vault.rbac import check_permission


def _make_token(app_id: str = "app-1", *, expires_in: float | None = 86400) -> Token:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=expires_in) if expires_in is not None else None
    return Token(value="secret-value", app_id=app_id, expires_at=expires_at)


async def _refresher(token: Token) -> Token:
    return Token(value="rotated", app_id=token.app_id, expires_at=token.expires_at)


# ---------------------------------------------------------------------------
# RBAC permission matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        (Role.ADMIN, Action.STORE, True),
        (Role.ADMIN, Action.GET, True),
        (Role.ADMIN, Action.ROTATE, True),
        (Role.ADMIN, Action.REFRESH, True),
        (Role.ADMIN, Action.EXPIRE, True),
        (Role.OPERATOR, Action.STORE, True),
        (Role.OPERATOR, Action.ROTATE, True),
        (Role.VIEWER, Action.GET, True),
        (Role.VIEWER, Action.STORE, False),
        (Role.VIEWER, Action.ROTATE, False),
        (Role.VIEWER, Action.REFRESH, False),
    ],
)
def test_is_allowed_matrix(role: Role, action: Action, allowed: bool) -> None:
    assert is_allowed(role, action) is allowed


def test_check_permission_raises_for_denied() -> None:
    with pytest.raises(PermissionError, match="not permitted"):
        check_permission(Role.VIEWER, Action.STORE)


def test_is_allowed_unknown_role_denied() -> None:
    # Defensive: a role with no mapping is denied everything.
    class _Rogue:
        value = "rogue"

    assert is_allowed(_Rogue(), Action.GET) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RBAC enforcement on vault operations
# ---------------------------------------------------------------------------


async def test_store_denied_for_viewer() -> None:
    vault = Vault()
    with pytest.raises(PermissionError):
        await vault.store(_make_token(), role=Role.VIEWER)
    # Nothing persisted.
    assert await vault.audit_trail() == []


async def test_get_allowed_for_viewer() -> None:
    vault = Vault()
    await vault.store(_make_token())
    token = await vault.get_active_token("app-1", role=Role.VIEWER)
    assert token.value == "secret-value"


async def test_rotate_denied_for_viewer() -> None:
    vault = Vault(refresher=_refresher)
    await vault.store(_make_token())
    with pytest.raises(PermissionError):
        await vault.rotate("app-1", role=Role.VIEWER)


async def test_rotate_due_denied_for_viewer() -> None:
    from meta_token_vault import RotationPolicy

    vault = Vault(refresher=_refresher, rotation_policy=RotationPolicy(1))
    await vault.store(_make_token())
    with pytest.raises(PermissionError):
        await vault.rotate_due("app-1", role=Role.VIEWER)


async def test_operator_can_store_and_rotate() -> None:
    vault = Vault(refresher=_refresher)
    await vault.store(_make_token(), role=Role.OPERATOR)
    rotated = await vault.rotate("app-1", role=Role.OPERATOR)
    assert rotated.value == "rotated"


# ---------------------------------------------------------------------------
# Actor threading through the audit trail
# ---------------------------------------------------------------------------


async def test_actor_recorded_in_audit_for_store_and_get() -> None:
    vault = Vault()
    await vault.store(_make_token(), actor="alice")
    await vault.get_active_token("app-1", actor="bob")

    trail = await vault.audit_trail()
    by_action = {e.action: e for e in trail}
    assert by_action[Action.STORE].actor == "alice"
    assert by_action[Action.GET].actor == "bob"


async def test_actor_recorded_for_rotation() -> None:
    vault = Vault(refresher=_refresher)
    await vault.store(_make_token(), actor="alice")
    await vault.rotate("app-1", actor="carol")
    trail = await vault.audit_trail()
    rotate_entries = [e for e in trail if e.action == Action.ROTATE]
    assert rotate_entries and rotate_entries[-1].actor == "carol"


async def test_monitor_audit_actor_is_monitor() -> None:
    vault = Vault(refresh_threshold_seconds=3600)
    await vault.store(_make_token(expires_in=10))
    await vault.monitor("app-1", interval=0.001, iterations=1)
    trail = await vault.audit_trail()
    expire_entries = [e for e in trail if e.action == Action.EXPIRE]
    assert expire_entries and expire_entries[0].actor == "monitor"


async def test_audit_trail_returns_snapshot_copy() -> None:
    vault = Vault()
    await vault.store(_make_token())
    snapshot = await vault.audit_trail()
    snapshot.clear()
    # Mutating the returned list must not affect the vault's internal trail.
    assert len(await vault.audit_trail()) == 1


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def test_events_emitted_for_lifecycle() -> None:
    from truststack.events import EventBus, TrustEvent

    received: list[str] = []

    async def handler(event: TrustEvent) -> None:
        received.append(event.name)

    bus = EventBus()
    bus.subscribe("*", handler)

    vault = Vault(refresher=_refresher, event_bus=bus)
    await vault.store(_make_token())
    await vault.rotate("app-1")
    assert "token.refreshed" in received


async def test_missing_token_emits_expired_event() -> None:
    from truststack.events import EventBus, TrustEvent

    received: list[TrustEvent] = []

    async def handler(event: TrustEvent) -> None:
        received.append(event)

    bus = EventBus()
    bus.subscribe("token.expired", handler)
    vault = Vault(event_bus=bus)
    with pytest.raises(KeyError):
        await vault.get_active_token("missing")
    assert any(e.name == "token.expired" for e in received)


# ---------------------------------------------------------------------------
# Alert hook / fail toward distrust
# ---------------------------------------------------------------------------


async def test_alert_hook_fires_on_store_failure() -> None:
    alerts: list[tuple[str, str]] = []

    class _BrokenStore:
        async def put(self, token: Token) -> None:
            raise RuntimeError("disk full")

        async def get_active(self, app_id: str) -> Token | None:
            return None

        async def all(self, app_id: str) -> list[Token]:
            return []

    def hook(action: str, exc: Exception) -> None:
        alerts.append((action, str(exc)))

    vault = Vault(store=_BrokenStore(), alert_hook=hook)
    with pytest.raises(RuntimeError, match="disk full"):
        await vault.store(_make_token())
    assert alerts == [("store", "disk full")]


async def test_async_webhook_alert_hook_awaited_on_refresh_failure() -> None:
    fired: list[str] = []

    async def webhook(action: str, exc: Exception) -> None:
        fired.append(action)

    async def broken_refresher(token: Token) -> Token:
        raise ValueError("upstream down")

    vault = Vault(
        refresher=broken_refresher,
        refresh_threshold_seconds=3600,
        alert_hook=webhook,
    )
    await vault.store(_make_token(expires_in=600))
    active = await vault.get_active_token("app-1")
    assert active.value == "secret-value"  # degraded to existing valid token
    assert fired == ["refresh"]


async def test_alert_hook_failure_is_swallowed() -> None:
    async def broken_refresher(token: Token) -> Token:
        raise ValueError("upstream down")

    def hook(action: str, exc: Exception) -> None:
        raise RuntimeError("hook itself failed")

    vault = Vault(
        refresher=broken_refresher,
        refresh_threshold_seconds=3600,
        alert_hook=hook,
    )
    await vault.store(_make_token(expires_in=600))
    # A failing hook must not crash the operation.
    active = await vault.get_active_token("app-1")
    assert active.value == "secret-value"


async def test_rotate_propagates_refresher_failure() -> None:
    async def broken_refresher(token: Token) -> Token:
        raise ValueError("upstream down")

    vault = Vault(refresher=broken_refresher)
    await vault.store(_make_token())
    # rotate() uses _refresh_and_store directly; the refresher error propagates.
    with pytest.raises(ValueError, match="upstream down"):
        await vault.rotate("app-1")


async def test_auto_refresh_reraises_when_token_already_expired() -> None:
    # When auto-refresh fails and the token cannot degrade (already expired),
    # the error must propagate (fail toward distrust) and emit token.expired.
    from truststack.events import EventBus, TrustEvent

    received: list[TrustEvent] = []

    async def handler(event: TrustEvent) -> None:
        received.append(event)

    async def broken_refresher(token: Token) -> Token:
        raise ValueError("upstream down")

    bus = EventBus()
    bus.subscribe("token.expired", handler)
    vault = Vault(refresher=broken_refresher, event_bus=bus)
    expired = _make_token(expires_in=-1)
    with pytest.raises(ValueError, match="upstream down"):
        await vault._auto_refresh(expired, actor="system")
    assert any(e.name == "token.expired" for e in received)


def test_no_alert_hook_is_fine() -> None:
    # Constructing without an alert hook must not raise.
    vault = Vault()
    assert vault._alert_hook is None
