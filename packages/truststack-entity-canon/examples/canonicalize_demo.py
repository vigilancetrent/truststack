"""Runnable demo mirroring the README usage.

Run with::

    python examples/canonicalize_demo.py
"""

from __future__ import annotations

import asyncio

from entity_canon import Canonicalizer
from truststack.events import EventBus, TrustEvent


async def main() -> None:
    bus = EventBus()

    async def on_blocked(event: TrustEvent) -> None:
        print(f"  [event] {event.name}: {event.data}")

    bus.subscribe("entity.duplicate_blocked", on_blocked)

    canon = Canonicalizer(threshold=0.90, event_bus=bus)

    # Register a canonical entity with aliases.
    jatin = await canon.add("Jatin", aliases=["Jat"])
    print(f"Added canonical entity: {jatin.name} ({jatin.id})")

    # Misspelled variants get blocked and a canonical name is suggested.
    for variant in ("Jhatin", "Jatyn", "Michael"):
        result = await canon.canonicalize(variant)
        verdict = "BLOCKED" if result.blocked else "allowed"
        print(
            f"{variant:>8} -> {verdict:7} "
            f"confidence={result.confidence:.2f} "
            f"suggestion={result.suggestion!r}"
        )

    # Human-approval mode: never auto-blocks, just suggests.
    approver = Canonicalizer(require_approval=True)
    await approver.add("Jatin")
    pending = await approver.canonicalize("Jhatin")
    print(
        f"\n[approval mode] Jhatin -> blocked={pending.blocked} "
        f"suggestion={pending.suggestion!r} (human decides)"
    )

    metrics = await canon.metrics()
    print(f"\nmetrics: {metrics.counters}")


if __name__ == "__main__":
    asyncio.run(main())
