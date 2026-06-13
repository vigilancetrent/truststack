"""Tests for the DedupeEngine happy path and edge cases."""

from __future__ import annotations

from task_dedupe import DedupeEngine, DedupeResult, Task
from truststack.events import EventBus, TrustEvent


async def test_near_identical_titles_same_due_are_duplicate() -> None:
    engine = DedupeEngine()
    first = await engine.check(Task(title="Send Q3 report to Dana", due="tomorrow"))
    assert isinstance(first, DedupeResult)
    assert first.duplicate is False
    assert first.existing_task_id is None

    second = await engine.check(Task(title="send the q3 report to dana", due="tomorrow"))
    assert second.duplicate is True
    assert second.existing_task_id is not None
    assert second.score >= engine.threshold


async def test_different_tasks_are_not_duplicate() -> None:
    engine = DedupeEngine()
    await engine.check(Task(title="Book flights to Berlin", project="travel"))
    result = await engine.check(Task(title="Refactor billing service", project="platform"))
    assert result.duplicate is False
    assert result.existing_task_id is None


async def test_accepts_dict_input() -> None:
    engine = DedupeEngine()
    result = await engine.check({"title": "Review PR #42", "assignee": "sam"})
    assert result.duplicate is False
    assert result.fingerprint


async def test_exact_fingerprint_match_short_circuits() -> None:
    engine = DedupeEngine()
    task = Task(title="Pay invoice", due="2026-06-20", assignee="ops", project="finance")
    await engine.check(task)
    again = await engine.check(task)
    assert again.duplicate is True
    assert again.score == 1.0


async def test_emits_event_on_duplicate() -> None:
    bus = EventBus()
    captured: list[TrustEvent] = []
    bus.subscribe("task.duplicate_detected", lambda e: _collect(captured, e))

    engine = DedupeEngine(event_bus=bus)
    await engine.check(Task(title="Schedule standup", due="today"))
    await engine.check(Task(title="schedule standup", due="today"))

    assert len(captured) == 1
    assert captured[0].component == "task-dedupe"
    assert captured[0].data["existing_task_id"]


async def _collect(sink: list[TrustEvent], event: TrustEvent) -> None:
    sink.append(event)


async def test_metrics_and_health() -> None:
    engine = DedupeEngine()
    await engine.check(Task(title="alpha"))
    await engine.check(Task(title="alpha"))  # duplicate

    metrics = await engine.metrics()
    assert metrics.counters["checks_total"] == 2
    assert metrics.counters["duplicates_detected"] == 1
    assert metrics.counters["tasks_stored"] == 1

    health = await engine.health_check()
    assert health.ok
    assert "1 task" in (health.detail or "")


async def test_threshold_validation() -> None:
    import pytest

    with pytest.raises(ValueError):
        DedupeEngine(threshold=1.5)
