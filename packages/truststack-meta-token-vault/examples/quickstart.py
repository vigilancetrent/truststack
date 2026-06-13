"""Runnable quickstart mirroring the README.

Run with::

    python examples/quickstart.py

Demonstrates storing a token, automatic refresh near expiry, rotation, RBAC,
event subscription, and reading the audit trail -- all on infrastructure-free
in-memory backends.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from meta_token_vault import Role, Token, Vault
from truststack.events import EventBus, TrustEvent


async def refresh_workflow(token: Token) -> Token:
    """Stand-in for your real Meta refresh call (do NOT hit the real API here)."""
    return Token(
        value=f"refreshed-{token.id[:6]}",
        app_id=token.app_id,
        scopes=token.scopes,
        expires_at=datetime.now(UTC) + timedelta(days=60),
    )


async def main() -> None:
    bus = EventBus()

    async def on_event(event: TrustEvent) -> None:
        print(f"[event] {event.name} -> {event.data}")

    bus.subscribe("*", on_event)

    def alert(action: str, exc: Exception) -> None:
        print(f"[alert] {action} failed: {exc}")

    vault = Vault(
        refresher=refresh_workflow,
        refresh_threshold_seconds=3600,
        event_bus=bus,
        alert_hook=alert,
    )

    now = datetime.now(UTC)

    # Store a token that is close to expiry so the next read auto-refreshes it.
    await vault.store(
        Token(
            value="EAAGm0PX4ZCpsBA...",
            app_id="123456789",
            scopes=["whatsapp_business_messaging"],
            expires_at=now + timedelta(minutes=30),
        ),
        role=Role.ADMIN,
    )

    active = await vault.get_active_token("123456789")
    print("active token value:", active.value)

    rotated = await vault.rotate("123456789", role=Role.OPERATOR)
    print("rotated token value:", rotated.value)

    print("\nhealth:", (await vault.health_check()).model_dump())
    print("metrics counters:", (await vault.metrics()).counters)

    print("\naudit trail:")
    for entry in await vault.audit_trail():
        print(f"  {entry.at.isoformat()}  {entry.action.value:8}  app={entry.app_id}")


if __name__ == "__main__":
    asyncio.run(main())
