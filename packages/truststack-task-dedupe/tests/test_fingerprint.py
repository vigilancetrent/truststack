"""Tests for fingerprinting, title normalization, and due-window bucketing."""

from __future__ import annotations

from datetime import UTC, datetime

from task_dedupe import Task, fingerprint_task, normalize_due, normalize_title


def test_normalize_title_strips_punct_case_and_stopwords() -> None:
    assert normalize_title("Send the Q3 Report!!!") == "send q3 report"
    assert normalize_title("Please review  the PR.") == "review pr"


def test_normalize_title_all_stopwords_falls_back() -> None:
    # Made entirely of stopwords/punctuation -> falls back to cleaned form.
    assert normalize_title("the to of!!!") == "the to of"


def test_normalize_due_relative_phrases_share_buckets() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)  # Monday, ISO week 25
    assert normalize_due("today", now=now) == "2026-W25"
    assert normalize_due("tomorrow", now=now) == "2026-W25"
    assert normalize_due("this week", now=now) == "2026-W25"
    assert normalize_due("next week", now=now) == "2026-W26"


def test_normalize_due_iso_date() -> None:
    assert normalize_due("2026-06-15") == normalize_due("2026-06-16")  # same ISO week
    assert normalize_due("2026-06-15") != normalize_due("2026-07-15")


def test_normalize_due_none_and_garbage() -> None:
    assert normalize_due(None) == "none"
    assert normalize_due("   ") == "none"
    assert normalize_due("whenever") == "whenever"


def test_fingerprint_is_stable_and_intent_sensitive() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    a = Task(title="Send Q3 report to Dana!", due="tomorrow", assignee="dana")
    b = Task(title="send the q3 report to dana", due="today", assignee="dana")
    assert fingerprint_task(a, now=now) == fingerprint_task(b, now=now)

    c = Task(title="Completely different task", due="tomorrow", assignee="dana")
    assert fingerprint_task(a, now=now) != fingerprint_task(c, now=now)
