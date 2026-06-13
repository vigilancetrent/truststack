"""Tests for the SimilarityScorer implementations and metadata blending."""

from __future__ import annotations

import pytest

from task_dedupe import (
    DifflibScorer,
    HashingEmbeddingScorer,
    RapidFuzzScorer,
    SequenceMatcherScorer,
    SimilarityScorer,
    Task,
)


def test_sequence_matcher_is_difflib_alias() -> None:
    assert SequenceMatcherScorer is DifflibScorer


@pytest.mark.parametrize("scorer", [DifflibScorer(), HashingEmbeddingScorer()])
def test_scorers_satisfy_protocol(scorer: SimilarityScorer) -> None:
    assert isinstance(scorer, SimilarityScorer)


@pytest.mark.parametrize("scorer", [DifflibScorer(), HashingEmbeddingScorer()])
def test_identical_tasks_score_high(scorer: SimilarityScorer) -> None:
    a = Task(title="Send Q3 report to Dana", due="tomorrow", assignee="dana", project="fin")
    b = Task(title="send the q3 report to dana", due="tomorrow", assignee="dana", project="fin")
    assert scorer.score(a, b) >= 0.85


@pytest.mark.parametrize("scorer", [DifflibScorer(), HashingEmbeddingScorer()])
def test_distinct_tasks_score_low(scorer: SimilarityScorer) -> None:
    a = Task(title="Book flights to Berlin", project="travel")
    b = Task(title="Refactor billing service", project="platform")
    assert scorer.score(a, b) < 0.5


def test_metadata_boosts_only_when_both_present_and_equal() -> None:
    scorer = DifflibScorer()
    base = Task(title="generic title")
    # No metadata on either -> only the title contributes (weight 0.7).
    title_only = scorer.score(base, Task(title="generic title"))
    # Matching due + assignee + project add their full weights.
    full = scorer.score(
        Task(title="generic title", due="today", assignee="x", project="p"),
        Task(title="generic title", due="today", assignee="x", project="p"),
    )
    assert full > title_only
    assert full == pytest.approx(1.0, abs=1e-6)


def test_due_boost_skipped_when_due_is_none() -> None:
    scorer = DifflibScorer()
    # Both have no due -> normalize_due is "none" on both, no boost awarded.
    s = scorer.score(Task(title="abc"), Task(title="abc"))
    assert s == pytest.approx(0.7, abs=1e-6)


def test_assignee_case_insensitive_match() -> None:
    scorer = DifflibScorer()
    s = scorer.score(
        Task(title="task", assignee="Dana"),
        Task(title="task", assignee="  dana "),
    )
    # 0.7 title + 0.1 assignee.
    assert s == pytest.approx(0.8, abs=1e-6)


def test_score_is_capped_at_one() -> None:
    scorer = DifflibScorer()
    a = Task(title="x", due="today", assignee="a", project="p")
    assert scorer.score(a, a) <= 1.0


# --- weight validation ---------------------------------------------------


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match=r"sum to 1\.0"):
        DifflibScorer(title_weight=0.5, due_weight=0.5, assignee_weight=0.5, project_weight=0.5)


def test_weights_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        DifflibScorer(title_weight=-0.1, due_weight=0.4, assignee_weight=0.4, project_weight=0.3)


def test_custom_weights_shift_blend() -> None:
    # Title-only scorer: metadata never contributes.
    scorer = DifflibScorer(
        title_weight=1.0, due_weight=0.0, assignee_weight=0.0, project_weight=0.0
    )
    s = scorer.score(
        Task(title="same", due="today", assignee="a", project="p"),
        Task(title="same", due="today", assignee="a", project="p"),
    )
    assert s == pytest.approx(1.0, abs=1e-6)
    # Differing titles get no metadata rescue.
    s2 = scorer.score(
        Task(title="alpha beta", due="today", assignee="a"),
        Task(title="totally other", due="today", assignee="a"),
    )
    assert s2 < 0.5


# --- HashingEmbeddingScorer specifics ------------------------------------


def test_hashing_scorer_rejects_bad_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        HashingEmbeddingScorer(dimensions=0)


def test_hashing_scorer_is_deterministic() -> None:
    a = Task(title="send q3 report")
    b = Task(title="send q3 report")
    s1 = HashingEmbeddingScorer().score(a, b)
    s2 = HashingEmbeddingScorer().score(a, b)
    # Deterministic, and an identical *title* with no matching metadata caps at
    # the title weight (0.7) by design — only full-task matches reach 1.0.
    assert s1 == s2 == pytest.approx(0.7, abs=1e-6)


def test_hashing_scorer_identical_full_task_reaches_one() -> None:
    a = Task(title="send q3 report", due="2026-06-10", assignee="dana", project="finance")
    b = Task(title="send q3 report", due="2026-06-10", assignee="dana", project="finance")
    assert HashingEmbeddingScorer().score(a, b) == pytest.approx(1.0, abs=1e-6)


def test_hashing_scorer_empty_titles_match() -> None:
    scorer = HashingEmbeddingScorer()
    # Titles that normalize to empty token sets compare as identical (1.0).
    s = scorer._title_ratio("", "")
    assert s == 1.0


def test_hashing_scorer_one_empty_title_scores_zero() -> None:
    scorer = HashingEmbeddingScorer()
    assert scorer._title_ratio("", "something") == 0.0
    assert scorer._title_ratio("something", "") == 0.0


def test_hashing_scorer_word_overlap_in_range() -> None:
    scorer = HashingEmbeddingScorer()
    r = scorer._title_ratio("send report dana", "send report sam")
    assert 0.0 < r < 1.0


def test_hashing_scorer_small_dimensions_still_bounded() -> None:
    scorer = HashingEmbeddingScorer(dimensions=2)
    r = scorer._title_ratio("alpha beta gamma", "delta epsilon zeta")
    assert 0.0 <= r <= 1.0


# --- RapidFuzzScorer (optional extra) ------------------------------------


def test_rapidfuzz_scorer_when_available() -> None:
    pytest.importorskip("rapidfuzz")
    scorer = RapidFuzzScorer()
    a = Task(title="report to dana")
    b = Task(title="dana report")  # word-order swap -> token_sort_ratio is high
    assert scorer._title_ratio("report to dana", "dana report") > 0.8
    assert 0.0 <= scorer.score(a, b) <= 1.0


def test_rapidfuzz_empty_titles_match_when_available() -> None:
    pytest.importorskip("rapidfuzz")
    assert RapidFuzzScorer()._title_ratio("", "") == 1.0


def test_rapidfuzz_missing_dependency_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate rapidfuzz being absent regardless of the real environment.
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "rapidfuzz" or name.startswith("rapidfuzz."):
            raise ImportError("no rapidfuzz")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="rapidfuzz"):
        RapidFuzzScorer()._title_ratio("a", "b")


def test_base_scorer_title_ratio_not_implemented() -> None:
    from task_dedupe.similarity import _BlendedScorer

    with pytest.raises(NotImplementedError):
        _BlendedScorer()._title_ratio("a", "b")
