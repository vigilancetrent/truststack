"""Tests for time-source abstraction and timezone resolution."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

import pytest

from agent_clock import FrozenTimeSource, SystemTimeSource, TimeSource, resolve_timezone


def test_system_time_source_is_tz_aware() -> None:
    src = SystemTimeSource()
    assert isinstance(src, TimeSource)
    now = src.now()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None


def test_frozen_time_source_returns_fixed() -> None:
    fixed = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    src = FrozenTimeSource(fixed)
    assert src.now() == fixed
    assert src.fixed == fixed


def test_frozen_time_source_advance() -> None:
    src = FrozenTimeSource(datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
    src.advance(seconds=90)
    assert src.now() == datetime(2026, 1, 1, 0, 1, 30, tzinfo=UTC)


def test_frozen_time_source_rejects_naive() -> None:
    with pytest.raises(ValueError):
        FrozenTimeSource(datetime(2026, 1, 1, 0, 0))


def test_resolve_named_timezone() -> None:
    zone = resolve_timezone("Asia/Dubai")
    assert isinstance(zone, ZoneInfo)
    assert zone.key == "Asia/Dubai"


def test_resolve_unknown_timezone_raises() -> None:
    with pytest.raises(ValueError):
        resolve_timezone("Mars/Olympus_Mons")


def test_resolve_auto_detect_returns_tzinfo() -> None:
    zone = resolve_timezone(None)
    assert isinstance(zone, tzinfo)
    # The auto-detected zone must yield a concrete offset for "now".
    assert datetime.now(zone).utcoffset() is not None
