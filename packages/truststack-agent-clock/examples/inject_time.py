"""Runnable example mirroring the README.

Run with::

    uv run python packages/truststack-agent-clock/examples/inject_time.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from agent_clock import ClockInjector, FrozenTimeSource, OpenAIAdapter


async def main() -> None:
    # Use a frozen source so the output is identical every run.
    frozen = FrozenTimeSource(datetime(2026, 6, 10, 13, 55, tzinfo=UTC))
    clock = ClockInjector(timezone="Asia/Dubai", time_source=frozen)

    print("=== render() ===")
    print(clock.render().model_dump_json(indent=2))

    print("\n=== inject() ===")
    print(clock.inject("What day is it tomorrow?"))

    print("\n=== ainject() ===")
    print(await clock.ainject("Schedule a call for next Friday."))

    print("\n=== OpenAIAdapter ===")
    adapter = OpenAIAdapter(clock)
    messages = [{"role": "user", "content": "When is my next meeting?"}]
    for message in adapter.inject(messages):
        print(message)

    # Auto-detected local timezone (no argument).
    print("\n=== auto-detected local timezone ===")
    local_clock = ClockInjector()
    print(local_clock.render().human_readable)


if __name__ == "__main__":
    asyncio.run(main())
