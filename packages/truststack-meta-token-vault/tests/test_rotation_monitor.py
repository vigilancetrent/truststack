"""Tests for rotation policy, scheduled rotation, and the expiry monitor loop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from meta_token_vault import (
    RotationPolicy,
    Token,
    Vault,
)


def _make_token(
    app_id: str = "app-1",
    *,
    value: str = "secret-value",
    expires_in: float | None = 86400,
    issued_at: datetime | None = None,
) -> Token:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=expires_in) if expires_in is not None else None
    extra = {"issued_at": issued_at} if issued_at is not None else {}
    return Token(value=value, app_id=app_id, expires_at=expires_at, **extra)


async def _refresher(token: Token) -> Token:
    return Token(
        value="rotated-value",
        app_id=token.app_id,
        scopes=token.scopes,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )


# ---------------------------------------------------------------------------
# RotationPolicy
# ---------------------------------------------------------------------------


def test_rotation_policy_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        RotationPolicy(0)
    with pytest.raises(ValueError, match="must be positive"):
        RotationPolicy(-5)


def test_rotation_policy_age_and_due() -> None:
    now = datetime.now(UTC)
    policy = RotationPolicy(max_age_seconds=3600)
    young = _make_token(issued_at=now - timedelta(seconds=10))
    old = _make_token(issued_at=now - timedelta(seconds=7200))

    assert policy.is_due(young, now) is False
    assert policy.is_due(old, now) is True
    assert policy.age_seconds(old, now) == pytest.approx(7200, abs=1)


def test_rotation_policy_due_at_exact_boundary() -> None:
    now = datetime.now(UTC)
    policy = RotationPolicy(max_age_seconds=100)
    boundary = _make_token(issued_at=now - timedelta(seconds=100))
    assert policy.is_due(boundary, now) is True


# ---------------------------------------------------------------------------
# rotate_due
# ---------------------------------------------------------------------------


async def test_rotate_due_rotates_when_old() -> None:
    policy = RotationPolicy(max_age_seconds=60)
    vault = Vault(refresher=_refresher, rotation_policy=policy)
    old = _make_token(issued_at=datetime.now(UTC) - timedelta(seconds=120))
    await vault.store(old)

    rotated = await vault.rotate_due("app-1")
    assert rotated is not None
    assert rotated.value == "rotated-value"


async def test_rotate_due_noop_when_young() -> None:
    policy = RotationPolicy(max_age_seconds=3600)
    vault = Vault(refresher=_refresher, rotation_policy=policy)
    await vault.store(_make_token())  # just issued

    assert await vault.rotate_due("app-1") is None


async def test_rotate_due_noop_when_no_active_token() -> None:
    policy = RotationPolicy(max_age_seconds=1)
    vault = Vault(refresher=_refresher, rotation_policy=policy)
    assert await vault.rotate_due("missing-app") is None


async def test_rotate_due_without_policy_raises() -> None:
    vault = Vault(refresher=_refresher)
    await vault.store(_make_token())
    with pytest.raises(RuntimeError, match="no RotationPolicy"):
        await vault.rotate_due("app-1")


async def test_rotate_due_respects_explicit_now() -> None:
    policy = RotationPolicy(max_age_seconds=100)
    vault = Vault(refresher=_refresher, rotation_policy=policy)
    issued = datetime.now(UTC)
    await vault.store(_make_token(issued_at=issued))

    # 'now' far in the future makes the token due even though it was just issued.
    future = issued + timedelta(seconds=500)
    rotated = await vault.rotate_due("app-1", now=future)
    assert rotated is not None


async def test_rotate_without_active_falls_back_to_recent() -> None:
    # rotate() should fall back to the most recent (even expired) token.
    vault = Vault(refresher=_refresher)
    await vault.store(_make_token(expires_in=-10))  # expired -> not "active"
    rotated = await vault.rotate("app-1")
    assert rotated.value == "rotated-value"


async def test_rotate_with_no_tokens_raises() -> None:
    vault = Vault(refresher=_refresher)
    with pytest.raises(KeyError, match="No token to rotate"):
        await vault.rotate("nothing")


# ---------------------------------------------------------------------------
# monitor loop
# ---------------------------------------------------------------------------


async def test_monitor_detects_expiring_token_bounded() -> None:
    detected: list[Token] = []

    async def on_expiring(token: Token) -> None:
        detected.append(token)

    vault = Vault(refresh_threshold_seconds=3600)
    await vault.store(_make_token(expires_in=600))  # within threshold

    count = await vault.monitor("app-1", interval=0.001, on_expiring=on_expiring, iterations=1)
    assert count == 1
    assert len(detected) == 1


async def test_monitor_sync_callback_invoked() -> None:
    seen: list[str] = []

    def on_expiring(token: Token) -> None:
        seen.append(token.id)

    vault = Vault(refresh_threshold_seconds=3600)
    await vault.store(_make_token(expires_in=10))
    count = await vault.monitor("app-1", interval=0.001, on_expiring=on_expiring, iterations=1)
    assert count == 1
    assert seen


async def test_monitor_no_detection_when_far_from_expiry() -> None:
    vault = Vault(refresh_threshold_seconds=60)
    await vault.store(_make_token(expires_in=86400))
    count = await vault.monitor("app-1", interval=0.001, iterations=3)
    assert count == 0


async def test_monitor_missing_token_counts_as_detection() -> None:
    vault = Vault()
    count = await vault.monitor("missing", interval=0.001, iterations=1)
    assert count == 1


async def test_monitor_auto_refreshes_when_refresher_present() -> None:
    vault = Vault(refresher=_refresher, refresh_threshold_seconds=3600)
    await vault.store(_make_token(expires_in=300))
    count = await vault.monitor("app-1", interval=0.001, iterations=1)
    assert count == 1
    active = await vault.get_active_token("app-1")
    assert active.value == "rotated-value"


async def test_monitor_respects_explicit_threshold() -> None:
    vault = Vault(refresh_threshold_seconds=10)
    await vault.store(_make_token(expires_in=500))
    # Default threshold (10s) wouldn't detect; an explicit large window does.
    count = await vault.monitor("app-1", interval=0.001, iterations=1, threshold_seconds=1000)
    assert count == 1


async def test_monitor_callback_error_is_swallowed() -> None:
    async def boom(token: Token) -> None:
        raise RuntimeError("callback exploded")

    vault = Vault(refresh_threshold_seconds=3600)
    await vault.store(_make_token(expires_in=10))
    # The loop must not propagate the callback failure.
    count = await vault.monitor("app-1", interval=0.001, on_expiring=boom, iterations=1)
    assert count == 1


async def test_monitor_is_cancellable() -> None:
    vault = Vault(refresh_threshold_seconds=60)
    await vault.store(_make_token(expires_in=86400))  # never detected -> loops forever

    task = asyncio.create_task(vault.monitor("app-1", interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_monitor_alert_hook_fires_on_expiry() -> None:
    alerts: list[str] = []

    async def hook(action: str, exc: Exception) -> None:
        alerts.append(action)

    vault = Vault(refresh_threshold_seconds=3600, alert_hook=hook)
    await vault.store(_make_token(expires_in=10))
    await vault.monitor("app-1", interval=0.001, iterations=1)
    assert "expire" in alerts


async def test_monitor_emits_event_on_expiry() -> None:
    from truststack.events import EventBus, TrustEvent

    events: list[TrustEvent] = []
    bus = EventBus()

    async def _handler(event: TrustEvent) -> None:
        events.append(event)

    bus.subscribe("token.expired", _handler)

    vault = Vault(refresh_threshold_seconds=3600, event_bus=bus)
    await vault.store(_make_token(expires_in=10))
    await vault.monitor("app-1", interval=0.001, iterations=1)
    # publish is awaited inside monitor; events captured synchronously by handler
    assert any(e.name == "token.expired" for e in events)
