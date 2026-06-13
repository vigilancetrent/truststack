"""Tests for CallableTimeSource, event-bus integration, and remaining paths.

Covers the user-supplied fetcher source (success and fail-toward-distrust error
paths), the SystemTimeSource liveness, FrozenTimeSource.advance backward, the
``clock.injected`` event published by :meth:`ClockInjector.ainject`, auto-detect
timezone, and the runtime-checkable TimeSource protocol.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from agent_clock import (
    CallableTimeSource,
    ClockInjector,
    FrozenTimeSource,
    SystemTimeSource,
    TimeSource,
)
from truststack.events import EventBus, TrustEvent

DUBAI_INSTANT = datetime(2026, 6, 10, 13, 55, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# CallableTimeSource
# --------------------------------------------------------------------------- #


def test_callable_time_source_returns_fetcher_value() -> None:
    fixed = datetime(2026, 6, 10, 13, 55, 0, tzinfo=UTC)
    src = CallableTimeSource(lambda: fixed)
    assert isinstance(src, TimeSource)
    assert src.now() == fixed


def test_callable_time_source_invoked_every_call() -> None:
    calls = {"n": 0}

    def fetcher() -> datetime:
        calls["n"] += 1
        return datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(seconds=calls["n"])

    src = CallableTimeSource(fetcher)
    first = src.now()
    second = src.now()
    assert calls["n"] == 2
    assert second > first


def test_callable_time_source_rejects_non_datetime() -> None:
    src = CallableTimeSource(lambda: "not-a-datetime")  # type: ignore[arg-type,return-value]
    with pytest.raises(ValueError, match="must return a datetime"):
        src.now()


def test_callable_time_source_rejects_naive_datetime() -> None:
    src = CallableTimeSource(lambda: datetime(2026, 1, 1, 0, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        src.now()


def test_callable_time_source_drives_injector() -> None:
    src = CallableTimeSource(lambda: DUBAI_INSTANT)
    clock = ClockInjector(timezone="Asia/Dubai", time_source=src)
    assert clock.render().time_line == "17:55 +04"


def test_callable_time_source_non_utc_offset_normalised() -> None:
    # Fetcher returns a non-UTC aware instant; injector should normalise it.
    aware = datetime(2026, 6, 10, 17, 55, 0, tzinfo=timezone(timedelta(hours=4)))
    clock = ClockInjector(timezone="Asia/Dubai", time_source=CallableTimeSource(lambda: aware))
    trusted = clock.render()
    assert trusted.time_line == "17:55 +04"
    assert trusted.utc_iso == "2026-06-10T13:55:00+00:00"


# --------------------------------------------------------------------------- #
# Other sources
# --------------------------------------------------------------------------- #


def test_system_time_source_changes_over_time() -> None:
    src = SystemTimeSource()
    a = src.now()
    b = src.now()
    assert a.tzinfo is UTC
    assert b >= a


def test_frozen_advance_backward() -> None:
    src = FrozenTimeSource(datetime(2026, 1, 1, 0, 1, 0, tzinfo=UTC))
    src.advance(seconds=-30)
    assert src.now() == datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Event bus integration
# --------------------------------------------------------------------------- #


async def test_ainject_publishes_clock_injected_event() -> None:
    bus = EventBus()
    received: list[TrustEvent] = []

    async def handler(event: TrustEvent) -> None:
        received.append(event)

    bus.subscribe("clock.injected", handler)
    clock = ClockInjector(
        timezone="Asia/Dubai",
        time_source=FrozenTimeSource(DUBAI_INSTANT),
        event_bus=bus,
    )
    await clock.ainject("What time is it?")

    assert len(received) == 1
    event = received[0]
    assert event.name == "clock.injected"
    assert event.component == "agent-clock"
    assert event.data["timezone"] == "Asia/Dubai"
    assert event.data["utc_iso"] == "2026-06-10T13:55:00+00:00"


async def test_ainject_without_bus_does_not_raise() -> None:
    clock = ClockInjector(timezone="Asia/Dubai", time_source=FrozenTimeSource(DUBAI_INSTANT))
    out = await clock.ainject("hi")
    assert out.startswith("Current trusted datetime:")


async def test_ainject_wildcard_subscriber_receives_event() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def handler(event: TrustEvent) -> None:
        seen.append(event.name)

    bus.subscribe("*", handler)
    clock = ClockInjector(
        timezone="UTC",
        time_source=FrozenTimeSource(DUBAI_INSTANT),
        event_bus=bus,
    )
    await clock.ainject("hi")
    assert seen == ["clock.injected"]


# --------------------------------------------------------------------------- #
# Auto-detect & component contract
# --------------------------------------------------------------------------- #


def test_auto_detected_timezone_renders() -> None:
    clock = ClockInjector(time_source=FrozenTimeSource(DUBAI_INSTANT))
    trusted = clock.render()
    # Auto-detected zone yields a concrete, parseable block.
    assert "Current trusted datetime:" in clock.block(trusted)


async def test_metrics_after_render_and_inject() -> None:
    clock = ClockInjector(timezone="UTC", time_source=FrozenTimeSource(DUBAI_INSTANT))
    clock.render()
    clock.inject("a")
    snapshot = await clock.metrics()
    assert snapshot.counters["injections"] == 1
    assert snapshot.counters["renders"] >= 2
    assert snapshot.gauges["last_epoch"] == DUBAI_INSTANT.timestamp()


def test_block_accepts_precomputed_trusted_time() -> None:
    clock = ClockInjector(timezone="UTC", time_source=FrozenTimeSource(DUBAI_INSTANT))
    trusted = clock.render()
    # Passing the same TrustedTime must yield the same block as rendering fresh.
    assert clock.block(trusted) == clock.block()


def test_time_source_protocol_runtime_check_rejects_non_source() -> None:
    class NotASource:
        pass

    assert not isinstance(NotASource(), TimeSource)
    assert isinstance(SystemTimeSource(), TimeSource)


def test_injector_exposes_resolved_timezone_property() -> None:
    clock = ClockInjector(timezone="Asia/Kolkata", time_source=FrozenTimeSource(DUBAI_INSTANT))
    assert getattr(clock.timezone, "key", None) == "Asia/Kolkata"


def test_dict_payload_through_callable_source_unused() -> None:
    # Defensive: ensure a fetcher returning a value of an unexpected type fails
    # before reaching the injector composition.
    src = CallableTimeSource(lambda: [1, 2, 3])  # type: ignore[arg-type,return-value]
    clock = ClockInjector(timezone="UTC", time_source=src)
    with pytest.raises(ValueError):
        clock.render()
