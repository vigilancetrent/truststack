"""Exhaustive due-window normalization with a fixed reference date.

The reference instant is pinned to Monday 2026-06-15 12:00 UTC (ISO week 25) so
every bucket is deterministic and the tests never depend on wall-clock time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from task_dedupe import normalize_due
from task_dedupe.fingerprint import _coarse_week_bucket

# Monday, ISO year-week 2026-W25.
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _bucket(offset_days: int) -> str:
    return _coarse_week_bucket((NOW + timedelta(days=offset_days)).date())


@pytest.mark.parametrize(
    ("phrase", "offset_days"),
    [
        ("today", 0),
        ("tonight", 0),
        ("eod", 0),
        ("asap", 0),
        ("now", 0),
        ("immediately", 0),
        ("tomorrow", 1),
        ("tmrw", 1),
        ("tmr", 1),
        ("yesterday", -1),
        ("day after tomorrow", 2),
        ("overmorrow", 2),
        ("day before yesterday", -2),
    ],
)
def test_relative_day_phrases(phrase: str, offset_days: int) -> None:
    assert normalize_due(phrase, now=NOW) == _bucket(offset_days)


def test_case_and_whitespace_insensitive() -> None:
    assert normalize_due("  ToMoRRoW  ", now=NOW) == _bucket(1)
    assert normalize_due("DAY   AFTER   TOMORROW", now=NOW) == _bucket(2)


def test_week_phrases() -> None:
    assert normalize_due("this week", now=NOW) == _bucket(0)
    assert normalize_due("week", now=NOW) == _bucket(0)
    assert normalize_due("next week", now=NOW) == _coarse_week_bucket(
        (NOW + timedelta(weeks=1)).date()
    )
    assert normalize_due("last week", now=NOW) == _coarse_week_bucket(
        (NOW - timedelta(weeks=1)).date()
    )
    assert normalize_due("previous week", now=NOW) == _coarse_week_bucket(
        (NOW - timedelta(weeks=1)).date()
    )


def test_month_phrases() -> None:
    assert normalize_due("next month", now=NOW) == _coarse_week_bucket(
        (NOW + timedelta(days=30)).date()
    )
    assert normalize_due("last month", now=NOW) == _coarse_week_bucket(
        (NOW - timedelta(days=30)).date()
    )
    assert normalize_due("previous month", now=NOW) == _coarse_week_bucket(
        (NOW - timedelta(days=30)).date()
    )
    assert normalize_due("this month", now=NOW) == _coarse_week_bucket(
        (NOW + timedelta(days=15)).date()
    )
    assert normalize_due("month", now=NOW) == _coarse_week_bucket((NOW + timedelta(days=15)).date())


@pytest.mark.parametrize(
    ("phrase", "offset_days"),
    [
        ("in 1 day", 1),
        ("in 3 days", 3),
        ("in 10 days", 10),
        ("in 1 week", 7),
        ("in 2 weeks", 14),
        ("in 1 month", 30),
        ("in 2 months", 60),
    ],
)
def test_in_n_offsets(phrase: str, offset_days: int) -> None:
    assert normalize_due(phrase, now=NOW) == _bucket(offset_days)


@pytest.mark.parametrize(
    ("phrase", "offset_days"),
    [
        ("1 day ago", -1),
        ("5 days ago", -5),
        ("1 week ago", -7),
        ("2 weeks ago", -14),
        ("1 month ago", -30),
        ("3 months ago", -90),
    ],
)
def test_n_ago_offsets(phrase: str, offset_days: int) -> None:
    assert normalize_due(phrase, now=NOW) == _bucket(offset_days)


def test_weekday_names_resolve_to_next_occurrence() -> None:
    # NOW is Monday (weekday 0). Wednesday is +2 days, still in week 25.
    assert normalize_due("wednesday", now=NOW) == _bucket(2)
    # Monday itself resolves to today (delta 0).
    assert normalize_due("monday", now=NOW) == _bucket(0)
    # Sunday is +6 days.
    assert normalize_due("sunday", now=NOW) == _bucket(6)


def test_weekday_abbreviations() -> None:
    # Three-letter abbreviation form ("wed") is accepted.
    assert normalize_due("wed", now=NOW) == _bucket(2)
    assert normalize_due("mon", now=NOW) == _bucket(0)


def test_next_weekday_lands_a_full_week_later() -> None:
    # "next wednesday" is the Wednesday of the following week (+2 + 7).
    assert normalize_due("next wednesday", now=NOW) == _bucket(9)
    # "next monday" -> +0 + 7.
    assert normalize_due("next monday", now=NOW) == _bucket(7)


def test_iso_date_buckets_by_week() -> None:
    # Two ISO dates in the same ISO week share a bucket.
    assert normalize_due("2026-06-15") == normalize_due("2026-06-16")
    # Different weeks differ.
    assert normalize_due("2026-06-15") != normalize_due("2026-07-15")
    # Bucket value matches the helper.
    assert normalize_due("2026-06-15") == _coarse_week_bucket(date(2026, 6, 15))


def test_iso_date_with_trailing_time_is_accepted() -> None:
    # Only the leading YYYY-MM-DD is matched; trailing time is ignored.
    assert normalize_due("2026-06-15T09:30:00") == _coarse_week_bucket(date(2026, 6, 15))


def test_invalid_iso_date_falls_back_to_verbatim() -> None:
    # A syntactically date-shaped but invalid date (month 13) returns the text.
    assert normalize_due("2026-13-40") == "2026-13-40"


def test_none_and_blank_are_none_bucket() -> None:
    assert normalize_due(None) == "none"
    assert normalize_due("") == "none"
    assert normalize_due("    ") == "none"


def test_unparseable_phrase_returns_normalized_text() -> None:
    assert normalize_due("whenever you can") == "whenever you can"
    # Verbatim path is lowercased and whitespace-collapsed so two match.
    assert normalize_due("  Some  DAY  ") == "some day"


def test_now_defaults_to_current_time_when_omitted() -> None:
    # Without an explicit now, the call still returns a well-formed week bucket.
    result = normalize_due("today")
    assert "-W" in result
    assert result == _coarse_week_bucket(datetime.now(UTC).date())
