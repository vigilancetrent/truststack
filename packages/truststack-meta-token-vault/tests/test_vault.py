"""Tests for the Meta token vault: storage, expiry, refresh, rotation, audit, RBAC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meta_token_vault import (
    Action,
    InMemoryTokenStore,
    Role,
    Token,
    Vault,
    check_permission,
)


def _make_token(app_id: str = "app-1", *, expires_in: float | None = 86400) -> Token:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=expires_in) if expires_in is not None else None
    return Token(value="secret-value", app_id=app_id, expires_at=expires_at)


async def test_store_and_get_active_in_memory() -> None:
    vault = Vault(store=InMemoryTokenStore())
    token = _make_token()
    await vault.store(token)

    active = await vault.get_active_token("app-1")
    assert active.id == token.id
    assert active.value == "secret-value"


async def test_get_active_missing_raises() -> None:
    vault = Vault()
    with pytest.raises(KeyError):
        await vault.get_active_token("unknown-app")


async def test_expiry_detection() -> None:
    now = datetime.now(UTC)
    expired = Token(value="v", app_id="app-1", expires_at=now - timedelta(seconds=1))
    assert expired.is_expired(now) is True
    assert expired.expires_in_seconds(now) is not None
    assert expired.expires_in_seconds(now) < 0  # type: ignore[operator]

    never = Token(value="v", app_id="app-1", expires_at=None)
    assert never.is_expired(now) is False
    assert never.expires_in_seconds(now) is None


async def test_expired_token_is_not_returned_as_active() -> None:
    vault = Vault()
    await vault.store(_make_token(expires_in=-10))
    with pytest.raises(KeyError):
        await vault.get_active_token("app-1")


async def test_auto_refresh_with_fake_refresher() -> None:
    refreshed_ids: list[str] = []

    async def fake_refresher(token: Token) -> Token:
        new = Token(
            value="refreshed-value",
            app_id=token.app_id,
            scopes=token.scopes,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        refreshed_ids.append(new.id)
        return new

    vault = Vault(refresher=fake_refresher, refresh_threshold_seconds=3600)
    # token expiring within the threshold should trigger refresh on read
    await vault.store(_make_token(expires_in=600))

    active = await vault.get_active_token("app-1")
    assert active.value == "refreshed-value"
    assert refreshed_ids == [active.id]


async def test_no_refresh_when_far_from_expiry() -> None:
    calls: list[str] = []

    async def fake_refresher(token: Token) -> Token:
        calls.append(token.id)
        return token

    vault = Vault(refresher=fake_refresher, refresh_threshold_seconds=60)
    await vault.store(_make_token(expires_in=86400))

    active = await vault.get_active_token("app-1")
    assert active.value == "secret-value"
    assert calls == []


async def test_rotation() -> None:
    async def fake_refresher(token: Token) -> Token:
        return Token(value="rotated", app_id=token.app_id, expires_at=token.expires_at)

    vault = Vault(refresher=fake_refresher)
    await vault.store(_make_token())

    rotated = await vault.rotate("app-1")
    assert rotated.value == "rotated"

    active = await vault.get_active_token("app-1")
    assert active.value == "rotated"


async def test_rotation_without_refresher_raises() -> None:
    vault = Vault()
    await vault.store(_make_token())
    with pytest.raises(RuntimeError):
        await vault.rotate("app-1")


async def test_audit_trail_growth() -> None:
    async def fake_refresher(token: Token) -> Token:
        return Token(value="r", app_id=token.app_id, expires_at=token.expires_at)

    vault = Vault(refresher=fake_refresher)
    assert await vault.audit_trail() == []

    await vault.store(_make_token())
    await vault.get_active_token("app-1")
    await vault.rotate("app-1")

    trail = await vault.audit_trail()
    actions = [entry.action for entry in trail]
    assert Action.STORE in actions
    assert Action.GET in actions
    assert Action.ROTATE in actions
    assert len(trail) >= 3


async def test_rbac_denies_viewer_store() -> None:
    vault = Vault()
    with pytest.raises(PermissionError):
        await vault.store(_make_token(), role=Role.VIEWER)


def test_rbac_check_permission_matrix() -> None:
    check_permission(Role.ADMIN, Action.ROTATE)
    check_permission(Role.VIEWER, Action.GET)
    with pytest.raises(PermissionError):
        check_permission(Role.VIEWER, Action.STORE)


async def test_alert_hook_fires_on_refresh_failure() -> None:
    alerts: list[tuple[str, str]] = []

    async def broken_refresher(token: Token) -> Token:
        raise ValueError("upstream down")

    def hook(action: str, exc: Exception) -> None:
        alerts.append((action, str(exc)))

    vault = Vault(refresher=broken_refresher, refresh_threshold_seconds=3600, alert_hook=hook)
    # token still valid but within threshold -> refresh attempted, fails, degrades
    await vault.store(_make_token(expires_in=600))

    active = await vault.get_active_token("app-1")
    assert active.value == "secret-value"  # degraded to existing valid token
    assert alerts and alerts[0][0] == "refresh"


async def test_health_degraded_with_noop_encryptor() -> None:
    vault = Vault()
    status = await vault.health_check()
    assert status.state.value == "degraded"


async def test_metrics_record_operations() -> None:
    vault = Vault()
    await vault.store(_make_token())
    await vault.get_active_token("app-1")
    snapshot = await vault.metrics()
    assert snapshot.counters.get("tokens.stored") == 1
    assert snapshot.counters.get("tokens.served") == 1


async def test_version() -> None:
    vault = Vault()
    assert vault.version() == "0.1.0"
