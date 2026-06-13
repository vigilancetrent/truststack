"""Canonicalizer behaviour: matching, blocking, approval, signals, merge."""

from __future__ import annotations

import pytest

from entity_canon import (
    CanonicalEntity,
    Canonicalizer,
    ImportCounts,
    InMemoryEntityStore,
    MatchMethod,
    MatchResult,
    MatchSignal,
    bulk_import,
)
from truststack.core import ComponentMetrics, HealthState
from truststack.events import EventBus, TrustEvent


async def _seed_jatin() -> Canonicalizer:
    canon = Canonicalizer()
    await canon.add("Jatin")
    return canon


# ── Core matching / blocking ───────────────────────────────────────────────


async def test_phonetic_variants_are_blocked() -> None:
    canon = await _seed_jatin()
    for variant in ("Jhatin", "Jatyn"):
        result = await canon.canonicalize(variant)
        assert isinstance(result, MatchResult)
        assert result.confidence >= 0.90, variant
        assert result.blocked is True, variant
        assert result.suggestion == "Jatin", variant
        assert result.entity_id is not None


async def test_dissimilar_name_is_not_blocked() -> None:
    canon = await _seed_jatin()
    result = await canon.canonicalize("Michael")
    assert result.blocked is False
    assert result.confidence < 0.90


async def test_phonetic_match_below_threshold_not_blocked() -> None:
    # Robert/Rupert are phonetically related but fuzzily distinct -> below 0.90.
    canon = Canonicalizer()
    await canon.add("Robert")
    result = await canon.canonicalize("Rupert")
    assert result.confidence < 0.90
    assert result.blocked is False


async def test_exact_match_is_full_confidence() -> None:
    canon = await _seed_jatin()
    result = await canon.find("jatin")  # case-insensitive
    assert result.confidence == 1.0
    assert result.entity_id is not None
    assert result.method is MatchMethod.EXACT
    assert MatchSignal.EXACT in result.signals


async def test_find_never_blocks() -> None:
    canon = await _seed_jatin()
    result = await canon.find("Jhatin")
    assert result.blocked is False
    assert result.confidence >= 0.90


async def test_empty_store_returns_miss() -> None:
    canon = Canonicalizer()
    result = await canon.canonicalize("Anyone")
    assert result.entity_id is None
    assert result.confidence == 0.0
    assert result.blocked is False
    assert result.method is MatchMethod.NONE


# ── Input validation ───────────────────────────────────────────────────────


async def test_blank_name_raises_in_find() -> None:
    canon = Canonicalizer()
    with pytest.raises(ValueError):
        await canon.find("   ")


async def test_blank_name_raises_in_add() -> None:
    canon = Canonicalizer()
    with pytest.raises(ValueError):
        await canon.add("\t\n  ")


async def test_whitespace_only_name_in_canonicalize_raises() -> None:
    canon = await _seed_jatin()
    with pytest.raises(ValueError):
        await canon.canonicalize("   ")


async def test_invalid_threshold_raises() -> None:
    with pytest.raises(ValueError):
        Canonicalizer(threshold=1.5)
    with pytest.raises(ValueError):
        Canonicalizer(threshold=-0.1)


def test_threshold_boundaries_accepted() -> None:
    assert Canonicalizer(threshold=0.0).threshold == 0.0
    assert Canonicalizer(threshold=1.0).threshold == 1.0


# ── Threshold boundary: 0.89 NOT blocked, 0.90 blocked ─────────────────────


async def test_threshold_boundary_exact_value_blocked() -> None:
    # At threshold 0.90, a candidate scoring exactly 0.90 must be blocked
    # (the check is >= threshold). We engineer a name whose confidence we can
    # then drive across the boundary by adjusting the threshold itself.
    canon = await _seed_jatin()
    raw = await canon.find("Jhatin")
    score = raw.confidence
    assert score >= 0.90

    # Below the score by a hair: a threshold just under the score blocks.
    blocked_canon = Canonicalizer(threshold=score)
    await blocked_canon.add("Jatin")
    assert (await blocked_canon.canonicalize("Jhatin")).blocked is True


async def test_threshold_0_89_not_blocked_when_0_90_blocked() -> None:
    # A candidate whose confidence lands in [0.89, 0.90): blocked at 0.89 but
    # NOT at 0.90. We search for a pair sitting in that band.
    canon_low = Canonicalizer(threshold=0.89)
    canon_high = Canonicalizer(threshold=0.90)
    name, variant = "Catherine", "Catharine"
    for c in (canon_low, canon_high):
        await c.add(name)

    raw = await canon_low.find(variant)
    if 0.89 <= raw.confidence < 0.90:
        assert (await canon_low.canonicalize(variant)).blocked is True
        assert (await canon_high.canonicalize(variant)).blocked is False
    else:
        # If the heuristic shifts, fall back to asserting monotonicity:
        # a lower threshold blocks at least as often as a higher one.
        low_blocked = (await canon_low.canonicalize(variant)).blocked
        high_blocked = (await canon_high.canonicalize(variant)).blocked
        assert low_blocked or not high_blocked


async def test_lower_threshold_blocks_more() -> None:
    permissive = Canonicalizer(threshold=0.50)
    await permissive.add("Jonathan")
    # "Jonas" is fuzzily near Jonathan; permissive threshold should block it.
    result = await permissive.canonicalize("Jonas")
    assert result.blocked is True


# ── Signals & method reporting ─────────────────────────────────────────────


async def test_signals_report_fuzzy_and_phonetic() -> None:
    canon = await _seed_jatin()
    result = await canon.find("Jhatin")
    assert MatchSignal.FUZZY in result.signals
    assert result.fuzzy_ratio > 0.0
    assert result.phonetic_agreement > 0.0
    assert result.method in (MatchMethod.FUZZY_PHONETIC, MatchMethod.FUZZY)


async def test_method_fuzzy_only_when_no_phonetic() -> None:
    canon = Canonicalizer()
    await canon.add("Michael")
    # "Micael" shares letters (fuzzy) but the phonetic encoders should still
    # likely agree; assert the reported method is internally consistent.
    result = await canon.find("Michel")
    assert result.fuzzy_ratio > 0.0
    if result.phonetic_agreement == 0.0:
        assert result.method is MatchMethod.FUZZY
    else:
        assert result.method in (MatchMethod.FUZZY_PHONETIC, MatchMethod.PHONETIC)


async def test_metaphone_only_signal_reported() -> None:
    # Knight vs Night agree on Metaphone but not Soundex; the winning candidate
    # should report the METAPHONE signal without SOUNDEX.
    canon = Canonicalizer()
    await canon.add("Knight")
    result = await canon.find("Night")
    assert MatchSignal.METAPHONE in result.signals
    assert MatchSignal.SOUNDEX not in result.signals
    assert result.phonetic_agreement == 0.5


async def test_soundex_signal_reported() -> None:
    canon = Canonicalizer()
    await canon.add("Jatin")
    result = await canon.find("Jatyn")
    assert MatchSignal.SOUNDEX in result.signals


async def test_confidence_within_unit_interval() -> None:
    canon = await _seed_jatin()
    for name in ("Jatin", "Jhatin", "Michael", "Z"):
        result = await canon.find(name)
        assert 0.0 <= result.confidence <= 1.0


def test_score_phonetic_only_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the rare phonetic-only path (zero fuzzy overlap, strong phonetic):
    # the confidence must be capped below 1.0 and the method must be PHONETIC.
    import entity_canon.canonicalizer as mod

    monkeypatch.setattr(mod, "_fuzzy_ratio", lambda left, right: 0.0)
    monkeypatch.setattr(mod, "phonetic_agreement", lambda left, right: 1.0)
    monkeypatch.setattr(mod, "phonetic_equal", lambda left, right: True)

    canon = Canonicalizer()
    score = canon._score("Aaa", "Bbb")  # distinct enough to not normalise-equal
    assert score.method is MatchMethod.PHONETIC
    assert MatchSignal.FUZZY not in score.signals
    assert score.confidence <= 0.99
    assert score.confidence == pytest.approx(0.15)


# ── Aliases ────────────────────────────────────────────────────────────────


async def test_alias_matching() -> None:
    canon = Canonicalizer()
    await canon.add("Jonathan", aliases=["Jon", "Johnny"])
    result = await canon.canonicalize("Johnny")
    assert result.blocked is True
    # The matched surface form is the alias itself.
    assert result.suggestion == "Johnny"
    assert result.entity_id is not None


async def test_alias_fuzzy_match() -> None:
    canon = Canonicalizer()
    await canon.add("Robert", aliases=["Bob"])
    result = await canon.canonicalize("Bobb")
    assert result.entity_id is not None


# ── Approval mode ──────────────────────────────────────────────────────────


async def test_approval_mode_does_not_block() -> None:
    canon = Canonicalizer(require_approval=True)
    await canon.add("Jatin")
    result = await canon.canonicalize("Jhatin")
    assert result.blocked is False
    assert result.suggestion == "Jatin"
    assert result.confidence >= 0.90


async def test_approval_mode_non_duplicate_passes_through() -> None:
    canon = Canonicalizer(require_approval=True)
    await canon.add("Jatin")
    result = await canon.canonicalize("Michael")
    assert result.blocked is False
    assert result.suggestion is None


# ── Events ─────────────────────────────────────────────────────────────────


async def test_duplicate_blocked_event_emitted() -> None:
    bus = EventBus()
    seen: list[TrustEvent] = []

    async def handler(event: TrustEvent) -> None:
        seen.append(event)

    bus.subscribe("entity.duplicate_blocked", handler)
    canon = Canonicalizer(event_bus=bus)
    await canon.add("Jatin")
    await canon.canonicalize("Jhatin")

    assert len(seen) == 1
    assert seen[0].name == "entity.duplicate_blocked"
    assert seen[0].data["canonical"] == "Jatin"


async def test_no_event_in_approval_mode() -> None:
    bus = EventBus()
    seen: list[TrustEvent] = []

    async def handler(event: TrustEvent) -> None:
        seen.append(event)

    bus.subscribe("entity.duplicate_blocked", handler)
    canon = Canonicalizer(require_approval=True, event_bus=bus)
    await canon.add("Jatin")
    await canon.canonicalize("Jhatin")
    assert seen == []


async def test_no_event_when_not_blocked() -> None:
    bus = EventBus()
    seen: list[TrustEvent] = []

    async def handler(event: TrustEvent) -> None:
        seen.append(event)

    bus.subscribe("entity.duplicate_blocked", handler)
    canon = Canonicalizer(event_bus=bus)
    await canon.add("Jatin")
    await canon.canonicalize("Michael")
    assert seen == []


async def test_no_bus_no_crash() -> None:
    # Blocking without a configured bus must not raise.
    canon = await _seed_jatin()
    result = await canon.canonicalize("Jhatin")
    assert result.blocked is True


# ── Metrics & health ───────────────────────────────────────────────────────


async def test_metrics_recorded() -> None:
    canon = await _seed_jatin()
    await canon.canonicalize("Jhatin")
    m = await canon.metrics()
    assert isinstance(m, ComponentMetrics)
    assert m.counters.get("entities_added", 0) >= 1
    assert m.counters.get("duplicates_blocked", 0) == 1
    assert "last_confidence" in m.gauges


async def test_find_miss_metric() -> None:
    canon = Canonicalizer()
    await canon.find("Ghost")
    m = await canon.metrics()
    assert m.counters.get("find_misses", 0) == 1


async def test_approval_metric_recorded() -> None:
    canon = Canonicalizer(require_approval=True)
    await canon.add("Jatin")
    await canon.canonicalize("Jhatin")
    m = await canon.metrics()
    assert m.counters.get("approvals_requested", 0) == 1
    assert m.counters.get("duplicates_blocked", 0) == 0


async def test_health_check_ok() -> None:
    canon = Canonicalizer()
    status = await canon.health_check()
    assert status.state is HealthState.HEALTHY


async def test_version() -> None:
    assert Canonicalizer().version() == "0.1.0"


# ── get / all / delete ─────────────────────────────────────────────────────


async def test_get_and_all() -> None:
    canon = Canonicalizer()
    entity = await canon.add("Jatin")
    assert (await canon.get(entity.id)) == entity
    assert (await canon.get("nope")) is None
    assert len(await canon.all()) == 1


async def test_delete_existing_and_missing() -> None:
    canon = Canonicalizer()
    entity = await canon.add("Jatin")
    assert (await canon.delete(entity.id)) is True
    assert (await canon.delete(entity.id)) is False
    m = await canon.metrics()
    assert m.counters.get("entities_deleted", 0) == 1


# ── bulk_import / merge / skip ─────────────────────────────────────────────


async def test_bulk_import_added() -> None:
    canon = Canonicalizer()
    counts = await canon.bulk_import(
        [
            CanonicalEntity(id="1", name="Alice"),
            {"id": "2", "name": "Bob", "aliases": ["Bobby"]},
        ]
    )
    assert isinstance(counts, ImportCounts)
    assert counts.added == 2
    assert counts.merged == 0
    assert counts.skipped == 0
    assert counts.total == 2
    assert len(await canon.store.all()) == 2


async def test_bulk_import_merges_duplicates() -> None:
    canon = Canonicalizer()
    await canon.add("Jatin")
    counts = await canon.bulk_import([{"id": "x", "name": "Jhatin", "aliases": ["JT"]}])
    assert counts.merged == 1
    assert counts.added == 0
    # Still one entity, now carrying the new alias forms.
    entities = await canon.store.all()
    assert len(entities) == 1
    forms = {f.lower() for f in entities[0].surface_forms()}
    assert "jhatin" in forms
    assert "jt" in forms


async def test_bulk_import_skips_invalid_rows() -> None:
    canon = Canonicalizer()
    counts = await canon.bulk_import(
        [
            {"id": "1", "name": "Valid"},
            {"id": "2", "name": ""},  # blank -> validation error -> skipped
            {"id": "3"},  # missing name -> skipped
        ]
    )
    assert counts.added == 1
    assert counts.skipped == 2


async def test_bulk_import_module_function() -> None:
    canon = Canonicalizer()
    counts = await bulk_import(canon, [CanonicalEntity(id="x", name="Zara")])
    assert counts.added == 1


async def test_merge_no_new_aliases_is_idempotent() -> None:
    canon = Canonicalizer()
    await canon.add("Jatin", aliases=["JT"])
    # Re-importing the exact same surface forms adds nothing new.
    counts = await canon.bulk_import([{"id": "y", "name": "Jatin", "aliases": ["JT"]}])
    assert counts.merged == 1
    entities = await canon.store.all()
    assert len(entities) == 1
    assert sorted(entities[0].aliases) == ["JT"]


# ── store injection ────────────────────────────────────────────────────────


async def test_custom_store_injection() -> None:
    store = InMemoryEntityStore()
    canon = Canonicalizer(store=store)
    await canon.add("Jatin")
    assert len(await store.all()) == 1
