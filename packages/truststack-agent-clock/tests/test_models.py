"""Tests for the Pydantic models, enums, and TimeFormat rendering.

Covers TimeFormat's four rendering permutations (24/12 hour x with/without
seconds), the byte-identical default, Weekday derivation, frozen immutability,
and the ClockInjector ``time_format`` wiring that drives the time line.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from agent_clock import ClockInjector, FrozenTimeSource, TimeFormat, TrustedTime, Weekday

# 17:55:09 local in Asia/Dubai (UTC+04:00) on a Wednesday.
DUBAI_INSTANT = datetime(2026, 6, 10, 13, 55, 9, tzinfo=UTC)
_DUBAI_LOCAL = DUBAI_INSTANT.astimezone(ZoneInfo("Asia/Dubai"))


def test_timeformat_default_is_24h_no_seconds() -> None:
    fmt = TimeFormat()
    assert fmt.hour_clock == 24
    assert fmt.show_seconds is False
    assert fmt.is_default is True


def test_timeformat_default_format_time() -> None:
    assert TimeFormat().format_time(_DUBAI_LOCAL) == "17:55"


def test_timeformat_24h_with_seconds() -> None:
    fmt = TimeFormat(show_seconds=True)
    assert fmt.is_default is False
    assert fmt.format_time(_DUBAI_LOCAL) == "17:55:09"


def test_timeformat_12h_no_seconds() -> None:
    fmt = TimeFormat(hour_clock=12)
    assert fmt.is_default is False
    assert fmt.format_time(_DUBAI_LOCAL) == "05:55 PM"


def test_timeformat_12h_with_seconds() -> None:
    fmt = TimeFormat(hour_clock=12, show_seconds=True)
    assert fmt.is_default is False
    assert fmt.format_time(_DUBAI_LOCAL) == "05:55:09 PM"


def test_timeformat_is_frozen() -> None:
    fmt = TimeFormat()
    with pytest.raises(ValidationError):
        fmt.hour_clock = 12  # type: ignore[misc]


def test_timeformat_rejects_invalid_hour_clock() -> None:
    with pytest.raises(ValidationError):
        TimeFormat(hour_clock=13)  # type: ignore[arg-type]


def test_injector_default_time_format_byte_identical() -> None:
    clock = ClockInjector(timezone="Asia/Dubai", time_source=FrozenTimeSource(DUBAI_INSTANT))
    # Default time_format must drop seconds even though the instant has them.
    assert clock.render().time_line == "17:55 +04"
    assert clock.time_format.is_default is True


def test_injector_with_seconds_format() -> None:
    clock = ClockInjector(
        timezone="Asia/Dubai",
        time_source=FrozenTimeSource(DUBAI_INSTANT),
        time_format=TimeFormat(show_seconds=True),
    )
    assert clock.render().time_line == "17:55:09 +04"


def test_injector_with_12h_format() -> None:
    clock = ClockInjector(
        timezone="Asia/Dubai",
        time_source=FrozenTimeSource(DUBAI_INSTANT),
        time_format=TimeFormat(hour_clock=12),
    )
    trusted = clock.render()
    assert trusted.time_line == "05:55 PM +04"
    assert trusted.date_line == "Wednesday June 10 2026"


def test_injector_12h_with_seconds_block() -> None:
    clock = ClockInjector(
        timezone="Asia/Dubai",
        time_source=FrozenTimeSource(DUBAI_INSTANT),
        time_format=TimeFormat(hour_clock=12, show_seconds=True),
    )
    block = clock.block()
    assert "05:55:09 PM +04" in block
    assert block.startswith("Current trusted datetime:")


def test_weekday_from_datetime_each_day() -> None:
    # 2026-06-08 is a Monday; iterate one week.
    base = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    expected = [
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
        Weekday.FRIDAY,
        Weekday.SATURDAY,
        Weekday.SUNDAY,
    ]
    for offset, day in enumerate(expected):
        moment = base.replace(day=8 + offset)
        assert Weekday.from_datetime(moment) is day


def test_weekday_str_enum_values() -> None:
    assert Weekday.MONDAY.value == "Monday"
    assert str(Weekday.SUNDAY) == "Sunday"


def test_trustedtime_is_frozen() -> None:
    trusted = ClockInjector(
        timezone="Asia/Dubai", time_source=FrozenTimeSource(DUBAI_INSTANT)
    ).render()
    assert isinstance(trusted, TrustedTime)
    with pytest.raises(ValidationError):
        trusted.date_line = "tampered"  # type: ignore[misc]


def test_trustedtime_epoch_matches_instant() -> None:
    instant = datetime(2026, 6, 10, 13, 55, 0, tzinfo=UTC)
    trusted = ClockInjector(timezone="Asia/Dubai", time_source=FrozenTimeSource(instant)).render()
    assert trusted.epoch == instant.timestamp()
