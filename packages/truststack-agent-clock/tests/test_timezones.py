"""Timezone-correctness edge cases for ClockInjector rendering.

Covers fractional-offset zones (Asia/Kolkata +05:30, Asia/Kathmandu +05:45),
negative offsets, a DST spring-forward day in America/New_York, year/day
boundaries, naive-datetime rejection, and unknown-timezone failure toward a
clear ValueError.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_clock import ClockInjector, FrozenTimeSource, Weekday, resolve_timezone


def _render_at(timezone: str, instant: datetime) -> object:
    return ClockInjector(timezone=timezone, time_source=FrozenTimeSource(instant)).render()


def test_half_hour_offset_kolkata() -> None:
    # 12:00 UTC -> 17:30 IST (+05:30).
    instant = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    trusted = _render_at("Asia/Kolkata", instant)
    assert trusted.utc_offset == "+05:30"  # type: ignore[attr-defined]
    assert trusted.time_line.startswith("17:30")  # type: ignore[attr-defined]
    assert trusted.timezone == "Asia/Kolkata"  # type: ignore[attr-defined]


def test_three_quarter_hour_offset_kathmandu() -> None:
    # 12:00 UTC -> 17:45 NPT (+05:45).
    instant = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    trusted = _render_at("Asia/Kathmandu", instant)
    assert trusted.utc_offset == "+05:45"  # type: ignore[attr-defined]
    assert trusted.time_line.startswith("17:45")  # type: ignore[attr-defined]


def test_negative_offset_los_angeles_summer() -> None:
    # 12:00 UTC in June -> 05:00 PDT (-07:00).
    instant = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    trusted = _render_at("America/Los_Angeles", instant)
    assert trusted.utc_offset == "-07:00"  # type: ignore[attr-defined]
    assert trusted.time_line == "05:00 PDT"  # type: ignore[attr-defined]


def test_negative_offset_winter_standard_time() -> None:
    # 12:00 UTC in January -> 04:00 PST (-08:00).
    instant = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
    trusted = _render_at("America/Los_Angeles", instant)
    assert trusted.utc_offset == "-08:00"  # type: ignore[attr-defined]
    assert trusted.time_line == "04:00 PST"  # type: ignore[attr-defined]


def test_dst_spring_forward_before_transition() -> None:
    # 2026-03-08, clocks spring forward at 02:00 -> 03:00 in America/New_York.
    # 06:30 UTC is still EST (-05:00) -> 01:30 local.
    before = datetime(2026, 3, 8, 6, 30, 0, tzinfo=UTC)
    trusted = _render_at("America/New_York", before)
    assert trusted.utc_offset == "-05:00"  # type: ignore[attr-defined]
    assert trusted.time_line == "01:30 EST"  # type: ignore[attr-defined]


def test_dst_spring_forward_after_transition() -> None:
    # 07:30 UTC on the same day is now EDT (-04:00) -> 03:30 local (02:xx skipped).
    after = datetime(2026, 3, 8, 7, 30, 0, tzinfo=UTC)
    trusted = _render_at("America/New_York", after)
    assert trusted.utc_offset == "-04:00"  # type: ignore[attr-defined]
    assert trusted.time_line == "03:30 EDT"  # type: ignore[attr-defined]


def test_year_boundary_rolls_into_next_year() -> None:
    # 23:30 UTC on 2025-12-31 -> 03:30 on 2026-01-01 in Asia/Dubai (+04:00).
    instant = datetime(2025, 12, 31, 23, 30, 0, tzinfo=UTC)
    trusted = _render_at("Asia/Dubai", instant)
    assert trusted.date_line == "Thursday January 1 2026"  # type: ignore[attr-defined]
    assert trusted.weekday is Weekday.THURSDAY  # type: ignore[attr-defined]
    assert trusted.time_line == "03:30 +04"  # type: ignore[attr-defined]


def test_day_boundary_crosses_to_previous_day() -> None:
    # 02:00 UTC -> 21:00 the previous day in America/New_York (EST, -05:00).
    instant = datetime(2026, 1, 2, 2, 0, 0, tzinfo=UTC)
    trusted = _render_at("America/New_York", instant)
    assert trusted.date_line == "Thursday January 1 2026"  # type: ignore[attr-defined]
    assert trusted.time_line == "21:00 EST"  # type: ignore[attr-defined]


def test_leap_day_renders() -> None:
    # 2028-02-29 is a leap day (Tuesday).
    instant = datetime(2028, 2, 29, 8, 0, 0, tzinfo=UTC)
    trusted = _render_at("Asia/Dubai", instant)
    assert trusted.date_line == "Tuesday February 29 2028"  # type: ignore[attr-defined]


def test_utc_zone_abbreviation() -> None:
    instant = datetime(2026, 6, 10, 13, 55, 0, tzinfo=UTC)
    trusted = _render_at("UTC", instant)
    assert trusted.utc_offset == "+00:00"  # type: ignore[attr-defined]
    assert trusted.abbreviation == "UTC"  # type: ignore[attr-defined]
    assert trusted.time_line == "13:55 UTC"  # type: ignore[attr-defined]


def test_unknown_timezone_raises_clear_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown timezone"):
        ClockInjector(timezone="Mars/Olympus_Mons")


def test_resolve_unknown_timezone_message_includes_name() -> None:
    with pytest.raises(ValueError, match="Narnia/Lamppost"):
        resolve_timezone("Narnia/Lamppost")


def test_naive_instant_from_source_rejected() -> None:
    clock = ClockInjector(
        timezone="UTC", time_source=FrozenTimeSource(datetime(2026, 6, 10, 13, 55, 0, tzinfo=UTC))
    )
    clock._source = _NaiveSource()  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.render()


class _NaiveSource:
    def now(self) -> datetime:
        return datetime(2026, 6, 10, 13, 55, 0)
