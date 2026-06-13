"""Runnable example mirroring the README.

Run with::

    python examples/basic_dedupe.py
"""

from __future__ import annotations

import asyncio

from task_dedupe import DedupeEngine, Task
from truststack.events import EventBus, TrustEvent


async def _on_duplicate(event: TrustEvent) -> None:
    print(
        f"[event] {event.name}: matched {event.data['existing_task_id']} "
        f"(score={event.data['score']:.2f})"
    )


async def main() -> None:
    bus = EventBus()
    bus.subscribe("task.duplicate_detected", _on_duplicate)
    engine = DedupeEngine(threshold=0.85, event_bus=bus)

    # First time we see this intent -> stored, not a duplicate.
    first = await engine.check(Task(title="Send Q3 report to Dana", due="tomorrow"))
    print(f"first : duplicate={first.duplicate} score={first.score:.2f} fp={first.fingerprint}")

    # Same intent from a chat transcript, phrased differently -> duplicate.
    second = await engine.check(Task(title="send the q3 report to dana", due="tomorrow"))
    print(
        f"second: duplicate={second.duplicate} score={second.score:.2f} "
        f"existing={second.existing_task_id}"
    )

    # A genuinely different task -> not a duplicate.
    third = await engine.check(Task(title="Refactor billing service", project="platform"))
    print(f"third : duplicate={third.duplicate} score={third.score:.2f}")

    metrics = await engine.metrics()
    print(f"metrics: {metrics.counters}")


if __name__ == "__main__":
    asyncio.run(main())
