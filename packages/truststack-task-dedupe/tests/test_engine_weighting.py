"""Engine behavior under different scorers, weights, and near-dup boundaries."""

from __future__ import annotations

import pytest

from task_dedupe import (
    DedupeEngine,
    DifflibScorer,
    HashingEmbeddingScorer,
    Task,
)


async def test_custom_scorer_is_used() -> None:
    scorer = HashingEmbeddingScorer()
    engine = DedupeEngine(scorer=scorer)
    assert engine.scorer is scorer


async def test_threshold_boundary_just_below_is_not_duplicate() -> None:
    # A high threshold makes a near-miss fall short and be stored as new.
    engine = DedupeEngine(threshold=0.99)
    await engine.check(Task(title="Prepare the quarterly board deck", project="ops"))
    result = await engine.check(Task(title="Prepare the monthly board deck", project="ops"))
    assert result.duplicate is False
    assert result.score < 0.99


async def test_low_threshold_collapses_loose_matches() -> None:
    engine = DedupeEngine(threshold=0.4)
    await engine.check(Task(title="Email the client about pricing", project="sales"))
    result = await engine.check(Task(title="Email the client about renewal", project="sales"))
    assert result.duplicate is True


async def test_weighting_lets_metadata_tip_a_near_duplicate() -> None:
    # With heavier metadata weighting, matching due+assignee+project pushes a
    # borderline title pair over the threshold.
    heavy_meta = DifflibScorer(
        title_weight=0.55, due_weight=0.2, assignee_weight=0.15, project_weight=0.1
    )
    engine = DedupeEngine(scorer=heavy_meta, threshold=0.85)
    a = Task(title="finalize launch plan", due="today", assignee="dana", project="launch")
    b = Task(title="finalize the launch plans", due="today", assignee="dana", project="launch")
    await engine.check(a)
    result = await engine.check(b)
    assert result.duplicate is True


async def test_same_title_different_project_not_duplicate_without_meta_boost() -> None:
    # Generic title alone (0.7 weight) cannot reach the 0.85 threshold.
    engine = DedupeEngine()
    await engine.check(Task(title="follow up", project="alpha"))
    result = await engine.check(Task(title="follow up", project="beta"))
    assert result.duplicate is False
    assert result.score == pytest.approx(0.7, abs=1e-6)


async def test_distinct_tasks_each_stored() -> None:
    engine = DedupeEngine()
    r1 = await engine.check(Task(title="Book flights to Berlin", project="travel"))
    r2 = await engine.check(Task(title="Refactor billing service", project="platform"))
    r3 = await engine.check(Task(title="Write onboarding guide", project="docs"))
    assert all(not r.duplicate for r in (r1, r2, r3))
    assert len({r.existing_task_id for r in (r1, r2, r3)}) == 1  # all None


async def test_result_exposes_fingerprint_inputs() -> None:
    engine = DedupeEngine()
    result = await engine.check(
        Task(title="Send the Q3 Report!", due="tomorrow", assignee="Dana", project="Finance")
    )
    parts = result.fingerprint_inputs
    assert parts is not None
    assert parts.title == "send q3 report"
    assert parts.assignee == "dana"
    assert parts.project == "finance"
    assert parts.due != "none"


async def test_fingerprint_inputs_present_on_duplicate_result() -> None:
    engine = DedupeEngine()
    await engine.check(Task(title="ship release", due="today"))
    result = await engine.check(Task(title="ship release", due="today"))
    assert result.duplicate is True
    assert result.fingerprint_inputs is not None
    assert result.fingerprint_inputs.title == "ship release"


async def test_hashing_scorer_engine_dedupes_exact_repeat() -> None:
    engine = DedupeEngine(scorer=HashingEmbeddingScorer())
    await engine.check(Task(title="renew the ssl certificate", project="infra"))
    result = await engine.check(Task(title="renew the ssl certificate", project="infra"))
    assert result.duplicate is True


async def test_threshold_zero_treats_any_stored_task_as_duplicate() -> None:
    engine = DedupeEngine(threshold=0.0)
    await engine.check(Task(title="anything"))
    result = await engine.check(Task(title="totally unrelated and different"))
    # Threshold 0 means even a zero-ish score >= threshold once a candidate exists.
    assert result.duplicate is True


async def test_first_task_never_duplicate_on_empty_store() -> None:
    engine = DedupeEngine(threshold=0.0)
    result = await engine.check(Task(title="only task"))
    assert result.duplicate is False
    assert result.existing_task_id is None


async def test_last_best_score_gauge_recorded() -> None:
    engine = DedupeEngine()
    await engine.check(Task(title="alpha"))
    await engine.check(Task(title="alpha"))
    metrics = await engine.metrics()
    assert metrics.gauges["last_best_score"] == pytest.approx(1.0, abs=1e-6)
