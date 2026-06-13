"""The :class:`Canonicalizer` Trust Stack component.

Combines fuzzy string similarity (``difflib.SequenceMatcher``) with phonetic
matching (Soundex + Metaphone) to find the best existing entity for an incoming
name, and blocks insertions that are duplicates of an existing canonical entity.
"""

from __future__ import annotations

import asyncio
import csv
import uuid
from collections.abc import Iterable
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple

from truststack.core import BaseTrustComponent, HealthState, HealthStatus
from truststack.events import EventBus, TrustEvent
from truststack.logging import get_logger
from truststack.observability import traced

from .models import (
    CanonicalEntity,
    ImportCounts,
    MatchMethod,
    MatchResult,
    MatchSignal,
)
from .phonetic import phonetic_agreement, phonetic_equal
from .stores import EntityStore, InMemoryEntityStore

#: Maximum additive boost contributed by full phonetic agreement (Soundex +
#: Metaphone). Scaled linearly by the phonetic-agreement score in [0, 1].
_PHONETIC_BOOST = 0.15
#: A match with no fuzzy overlap (phonetic-only) can never reach certainty.
_PHONETIC_CAP = 0.99

_log = get_logger("entity_canon", component="entity-canon")


def _normalize(name: str) -> str:
    """Lowercase, strip, and collapse internal whitespace for comparison."""
    return " ".join(name.strip().lower().split())


def _fuzzy_ratio(left: str, right: str) -> float:
    """Return the normalized ``SequenceMatcher`` ratio in ``[0.0, 1.0]``."""
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


class _Score(NamedTuple):
    confidence: float
    method: MatchMethod
    signals: tuple[MatchSignal, ...]
    fuzzy: float
    phonetic: float


class _Candidate(NamedTuple):
    entity: CanonicalEntity
    surface: str
    confidence: float
    method: MatchMethod
    signals: tuple[MatchSignal, ...]
    fuzzy: float
    phonetic: float


class Canonicalizer(BaseTrustComponent):
    """Resolve incoming names to canonical entities and block duplicates."""

    component_name = "entity-canon"
    component_version = "0.1.0"

    def __init__(
        self,
        store: EntityStore | None = None,
        threshold: float = 0.90,
        require_approval: bool = False,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__()
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be within [0.0, 1.0]")
        self.store: EntityStore = store if store is not None else InMemoryEntityStore()
        self.threshold = threshold
        self.require_approval = require_approval
        self._bus = event_bus

    def _score(self, name: str, surface: str) -> _Score:
        """Score ``name`` against a single ``surface`` form of an entity.

        Returns a calibrated confidence in ``[0, 1]`` plus the discrete signals
        that fired, the raw fuzzy ratio, and the phonetic-agreement score.
        """
        if _normalize(name) == _normalize(surface):
            return _Score(1.0, MatchMethod.EXACT, (MatchSignal.EXACT,), 1.0, 1.0)

        fuzzy = _fuzzy_ratio(name, surface)
        phonetic = phonetic_agreement(name, surface)

        signals: list[MatchSignal] = []
        if fuzzy > 0.0:
            signals.append(MatchSignal.FUZZY)
        if phonetic_equal(name, surface):
            signals.append(MatchSignal.SOUNDEX)
        # Metaphone fired iff phonetic agreement exceeds the Soundex-only share.
        if phonetic >= 1.0 or (phonetic > 0.0 and MatchSignal.SOUNDEX not in signals):
            signals.append(MatchSignal.METAPHONE)

        # Calibrated score: the fuzzy ratio is the base, and phonetic agreement
        # (Soundex + Metaphone) adds an additive boost of up to ``_PHONETIC_BOOST``
        # scaled by how strongly the encoders agree. This nudges homophone
        # misspellings over the line without ever pulling a strong fuzzy match
        # *down*. A phonetic-only hit (zero fuzzy overlap) is capped below 1.0.
        confidence = fuzzy + _PHONETIC_BOOST * phonetic
        if fuzzy == 0.0:
            confidence = min(confidence, _PHONETIC_CAP)
        confidence = max(0.0, min(confidence, 1.0))

        if MatchSignal.FUZZY in signals and phonetic > 0.0:
            method = MatchMethod.FUZZY_PHONETIC
        elif phonetic > 0.0:
            method = MatchMethod.PHONETIC
        else:
            method = MatchMethod.FUZZY

        return _Score(confidence, method, tuple(signals), fuzzy, phonetic)

    def _best_candidate(self, name: str, entities: Iterable[CanonicalEntity]) -> _Candidate | None:
        """Return the highest-scoring entity surface form, if any."""
        best: _Candidate | None = None
        for entity in entities:
            for surface in entity.surface_forms():
                score = self._score(name, surface)
                if best is None or score.confidence > best.confidence:
                    best = _Candidate(
                        entity=entity,
                        surface=surface,
                        confidence=score.confidence,
                        method=score.method,
                        signals=score.signals,
                        fuzzy=score.fuzzy,
                        phonetic=score.phonetic,
                    )
        return best

    @traced("entity_canon.find")
    async def find(self, name: str) -> MatchResult:
        """Return the best matching existing entity for ``name`` (never blocks)."""
        if not name.strip():
            raise ValueError("name must not be blank")

        self.registry.increment("find_calls")
        entities = await self.store.all()
        best = self._best_candidate(name, entities)

        if best is None:
            self.registry.increment("find_misses")
            return MatchResult(confidence=0.0, method=MatchMethod.NONE)

        self.registry.set_gauge("last_confidence", best.confidence)
        _log.info(
            "entity_find",
            query=name,
            match=best.surface,
            entity_id=best.entity.id,
            confidence=round(best.confidence, 4),
            method=best.method.value,
            signals=[s.value for s in best.signals],
        )
        return MatchResult(
            match=best.surface,
            entity_id=best.entity.id,
            confidence=best.confidence,
            blocked=False,
            suggestion=None,
            method=best.method,
            signals=list(best.signals),
            fuzzy_ratio=best.fuzzy,
            phonetic_agreement=best.phonetic,
        )

    @traced("entity_canon.add")
    async def add(self, name: str, aliases: list[str] | None = None) -> CanonicalEntity:
        """Register a new canonical entity (with optional aliases)."""
        if not name.strip():
            raise ValueError("name must not be blank")

        entity = CanonicalEntity(
            id=uuid.uuid4().hex,
            name=name.strip(),
            aliases=list(aliases or []),
        )
        await self.store.add(entity)
        self.registry.increment("entities_added")
        self.registry.increment("aliases_registered", len(entity.aliases))
        _log.info(
            "entity_added",
            entity_id=entity.id,
            name=entity.name,
            aliases=entity.aliases,
        )
        return entity

    @traced("entity_canon.canonicalize")
    async def canonicalize(self, name: str) -> MatchResult:
        """Resolve ``name``, blocking the insertion when it duplicates an entity.

        When the best candidate's confidence is ``>= threshold``:

        * In normal mode, ``blocked=True`` and ``suggestion`` holds the canonical
          name (the caller should NOT insert a new entity), and a
          ``entity.duplicate_blocked`` event is emitted.
        * In ``require_approval`` mode, ``blocked`` stays ``False`` but
          ``suggestion`` is still populated so a human can decide.
        """
        result = await self.find(name)
        is_duplicate = result.entity_id is not None and result.confidence >= self.threshold

        if not is_duplicate:
            return result

        suggestion = result.match
        self.registry.increment("duplicates_detected")

        if self.require_approval:
            self.registry.increment("approvals_requested")
            _log.info(
                "duplicate_pending_approval",
                query=name,
                suggestion=suggestion,
                confidence=round(result.confidence, 4),
            )
            return result.model_copy(update={"blocked": False, "suggestion": suggestion})

        self.registry.increment("duplicates_blocked")
        _log.info(
            "duplicate_blocked",
            query=name,
            suggestion=suggestion,
            entity_id=result.entity_id,
            confidence=round(result.confidence, 4),
        )
        await self._emit_blocked(name, result)
        return result.model_copy(update={"blocked": True, "suggestion": suggestion})

    async def get(self, entity_id: str) -> CanonicalEntity | None:
        """Return a stored entity by id, or ``None`` if it does not exist."""
        return await self.store.get(entity_id)

    async def all(self) -> list[CanonicalEntity]:
        """Return every stored canonical entity."""
        return await self.store.all()

    @traced("entity_canon.delete")
    async def delete(self, entity_id: str) -> bool:
        """Delete an entity by id. Returns ``True`` if a row was removed."""
        removed = await self.store.delete(entity_id)
        if removed:
            self.registry.increment("entities_deleted")
            _log.info("entity_deleted", entity_id=entity_id)
        return removed

    async def bulk_import(
        self,
        entities: Iterable[CanonicalEntity | dict[str, object]],
    ) -> ImportCounts:
        """Import many entities, deduplicating against existing records.

        For each candidate the canonical name is checked against current
        entities. The outcome is one of:

        * **added** — no confident match; the entity is stored.
        * **merged** — a confident duplicate exists; the candidate's name and any
          new aliases are folded into the existing entity instead of inserting a
          second row.
        * **skipped** — the row is invalid (e.g. blank name) and cannot be parsed.
        """
        added = merged = skipped = 0
        for raw in entities:
            try:
                entity = (
                    raw if isinstance(raw, CanonicalEntity) else CanonicalEntity.model_validate(raw)
                )
            except Exception:
                skipped += 1
                continue

            existing = await self._find_duplicate_entity(entity.name)
            if existing is not None:
                await self._merge_into(existing, entity)
                merged += 1
                self.registry.increment("entities_merged")
            else:
                await self.store.add(entity)
                added += 1
                self.registry.increment("entities_added")
                self.registry.increment("aliases_registered", len(entity.aliases))

        counts = ImportCounts(added=added, merged=merged, skipped=skipped)
        _log.info("bulk_import", added=added, merged=merged, skipped=skipped)
        return counts

    async def import_csv(
        self,
        path: str | Path,
        *,
        name_field: str = "name",
        alias_field: str = "aliases",
        alias_sep: str = "|",
    ) -> ImportCounts:
        """Bulk-import entities from a CSV file using the stdlib :mod:`csv`.

        The CSV must have a header row. ``name_field`` is the required name
        column; ``alias_field`` (optional) holds aliases joined by ``alias_sep``.
        Rows with a blank name are skipped. IDs are generated automatically.
        """
        rows = await asyncio.to_thread(
            self._read_csv_rows, str(path), name_field, alias_field, alias_sep
        )
        return await self.bulk_import(rows)

    @staticmethod
    def _read_csv_rows(
        path: str,
        name_field: str,
        alias_field: str,
        alias_sep: str,
    ) -> list[dict[str, object]]:
        parsed: list[dict[str, object]] = []
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                name = (row.get(name_field) or "").strip()
                if not name:
                    # Preserve the row so bulk_import counts it as skipped.
                    parsed.append({"id": uuid.uuid4().hex, "name": "", "aliases": []})
                    continue
                raw_aliases = row.get(alias_field) or ""
                aliases = [a.strip() for a in raw_aliases.split(alias_sep) if a.strip()]
                parsed.append({"id": uuid.uuid4().hex, "name": name, "aliases": aliases})
        return parsed

    async def _find_duplicate_entity(self, name: str) -> CanonicalEntity | None:
        """Return an existing entity that ``name`` confidently duplicates."""
        if not name.strip():
            return None
        entities = await self.store.all()
        best = self._best_candidate(name, entities)
        if best is not None and best.confidence >= self.threshold:
            return best.entity
        return None

    async def _merge_into(self, existing: CanonicalEntity, incoming: CanonicalEntity) -> None:
        """Fold ``incoming`` aliases (and its name) into ``existing``."""
        known = {_normalize(s) for s in existing.surface_forms()}
        new_aliases = list(existing.aliases)
        for form in incoming.surface_forms():
            if _normalize(form) not in known:
                new_aliases.append(form)
                known.add(_normalize(form))
        if new_aliases != existing.aliases:
            updated = existing.model_copy(update={"aliases": new_aliases})
            await self.store.add(updated)
            self.registry.increment("aliases_registered", len(new_aliases) - len(existing.aliases))

    async def _emit_blocked(self, name: str, result: MatchResult) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            TrustEvent(
                name="entity.duplicate_blocked",
                component=self.component_name,
                data={
                    "query": name,
                    "canonical": result.match,
                    "entity_id": result.entity_id,
                    "confidence": result.confidence,
                },
            )
        )

    async def _check_health(self) -> HealthStatus:
        try:
            await self.store.all()
        except Exception as exc:  # pragma: no cover - defensive boundary
            return HealthStatus(
                component=self.component_name,
                state=HealthState.UNHEALTHY,
                detail=f"store unavailable: {exc}",
            )
        return HealthStatus(component=self.component_name, state=HealthState.HEALTHY)


async def bulk_import(
    canonicalizer: Canonicalizer,
    entities: Iterable[CanonicalEntity | dict[str, object]],
) -> ImportCounts:
    """Module-level convenience wrapper over :meth:`Canonicalizer.bulk_import`."""
    return await canonicalizer.bulk_import(entities)


__all__ = ["Canonicalizer", "bulk_import"]
