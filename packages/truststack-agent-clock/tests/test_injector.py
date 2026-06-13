"""Tests for ClockInjector rendering, injection, and component contract."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from agent_clock import ClockInjector, FrozenTimeSource, TrustedTime, Weekday
from truststack.core import HealthState

# 13:55 UTC on 2026-06-10 == 17:55 in Asia/Dubai (UTC+04:00), a Wednesday.
DUBAI_INSTANT = datetime(2026, 6, 10, 13, 55, 0, tzinfo=UTC)

EXPECTED_BLOCK = (
    "Current trusted datetime:\n"
    "Wednesday June 10 2026\n"
    "17:55 +04\n"
    "Timezone: Asia/Dubai\n"
    "UTC Offset: +04:00"
)


def _dubai_clock() -> ClockInjector:
    return ClockInjector(timezone="Asia/Dubai", time_source=FrozenTimeSource(DUBAI_INSTANT))


def test_render_matches_expected_fields() -> None:
    trusted = _dubai_clock().render()
    assert isinstance(trusted, TrustedTime)
    assert trusted.date_line == "Wednesday June 10 2026"
    assert trusted.time_line == "17:55 +04"
    assert trusted.timezone == "Asia/Dubai"
    assert trusted.utc_offset == "+04:00"
    assert trusted.weekday is Weekday.WEDNESDAY
    assert trusted.abbreviation == "+04"
    assert trusted.utc_iso == "2026-06-10T13:55:00+00:00"
    assert trusted.human_readable == "Wednesday June 10 2026\n17:55 +04"


def test_block_matches_readme_example() -> None:
    assert _dubai_clock().block() == EXPECTED_BLOCK


def test_inject_prepends_block_and_user_request() -> None:
    result = _dubai_clock().inject("What day is it tomorrow?")
    assert result == f"{EXPECTED_BLOCK}\n\nUser requests:\nWhat day is it tomorrow?"


async def test_ainject_matches_sync_inject() -> None:
    clock = _dubai_clock()
    prompt = "Schedule a meeting for next Friday."
    assert await clock.ainject(prompt) == clock.inject(prompt)


async def test_metrics_count_injections() -> None:
    clock = _dubai_clock()
    clock.inject("a")
    await clock.ainject("b")
    snapshot = await clock.metrics()
    assert snapshot.counters["injections"] == 2
    assert snapshot.counters["renders"] >= 2
    assert snapshot.gauges["last_epoch"] == DUBAI_INSTANT.timestamp()


async def test_health_check_healthy() -> None:
    status = await _dubai_clock().health_check()
    assert status.state is HealthState.HEALTHY
    assert "Asia/Dubai" in (status.detail or "")


def test_version_and_component_contract() -> None:
    clock = _dubai_clock()
    assert clock.version() == "0.1.0"
    assert clock.component_name == "agent-clock"


def test_other_timezone_offset_and_abbrev() -> None:
    clock = ClockInjector(
        timezone="America/New_York",
        time_source=FrozenTimeSource(DUBAI_INSTANT),
    )
    trusted = clock.render()
    # 13:55 UTC in June -> EDT (-04:00), 09:55.
    assert trusted.utc_offset == "-04:00"
    assert trusted.time_line == "09:55 EDT"
    assert trusted.timezone == "America/New_York"


def test_naive_time_source_rejected() -> None:
    import pytest

    clock = ClockInjector(
        timezone="UTC",
        time_source=FrozenTimeSource(DUBAI_INSTANT),
    )
    # Swap in a deliberately broken naive source to hit the guard.
    clock._source = _NaiveSource()  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        clock.render()


class _NaiveSource:
    def now(self) -> datetime:
        return datetime(2026, 6, 10, 13, 55, 0)


def test_named_timezone_override_zoneinfo() -> None:
    clock = ClockInjector(timezone="Asia/Dubai", time_source=FrozenTimeSource(DUBAI_INSTANT))
    assert isinstance(clock.timezone, ZoneInfo)
