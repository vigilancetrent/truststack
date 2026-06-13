"""Similarity scoring between tasks.

A :class:`SimilarityScorer` Protocol keeps the title-comparison strategy
swappable; the metadata-blending logic (due-window / assignee / project boosts)
is shared by all built-in scorers via :class:`_BlendedScorer`.

Built-in scorers:

* :class:`DifflibScorer` (alias :class:`SequenceMatcherScorer`) — the stdlib
  default, using :func:`difflib.SequenceMatcher`.
* :class:`RapidFuzzScorer` — backed by ``rapidfuzz`` (optional ``semantic``
  extra, imported lazily) for a faster, fuzzier token ratio.
* :class:`HashingEmbeddingScorer` — a pure-stdlib hashing bag-of-words cosine
  similarity. Deterministic and fully offline, it approximates a lightweight
  embedding without any third-party dependency.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from difflib import SequenceMatcher
from typing import Protocol, runtime_checkable

from .fingerprint import normalize_due, normalize_title
from .models import Task


@runtime_checkable
class SimilarityScorer(Protocol):
    """Computes a 0..1 similarity between two tasks."""

    def score(self, a: Task, b: Task) -> float: ...


class _BlendedScorer:
    """Shared base: a title ratio blended with exact-match metadata boosts.

    The blended score is a weighted sum capped at 1.0:

    * ``title_weight`` (default 0.7): a 0..1 ratio on normalized titles, provided
      by the concrete subclass via :meth:`_title_ratio`.
    * the remaining weight is split across due-window / assignee / project,
      awarded only when both tasks specify a field and it matches exactly.

    Title alone can never reach the duplicate threshold without at least some
    metadata agreement, which keeps generic titles ("follow up") from colliding.
    """

    def __init__(
        self,
        *,
        title_weight: float = 0.7,
        due_weight: float = 0.12,
        assignee_weight: float = 0.1,
        project_weight: float = 0.08,
    ) -> None:
        for name, value in (
            ("title_weight", title_weight),
            ("due_weight", due_weight),
            ("assignee_weight", assignee_weight),
            ("project_weight", project_weight),
        ):
            if value < 0.0:
                msg = f"{name} must be non-negative, got {value}"
                raise ValueError(msg)
        total = title_weight + due_weight + assignee_weight + project_weight
        if not 0.999 <= total <= 1.001:
            msg = f"scorer weights must sum to 1.0, got {total}"
            raise ValueError(msg)
        self._title_weight = title_weight
        self._due_weight = due_weight
        self._assignee_weight = assignee_weight
        self._project_weight = project_weight

    def _title_ratio(self, a_title: str, b_title: str) -> float:
        """Return a 0..1 title similarity. Overridden by concrete scorers."""
        raise NotImplementedError

    def score(self, a: Task, b: Task) -> float:
        ratio = self._title_ratio(normalize_title(a.title), normalize_title(b.title))
        result = self._title_weight * ratio

        if normalize_due(a.due) == normalize_due(b.due) != "none":
            result += self._due_weight
        if a.assignee and b.assignee and a.assignee.strip().lower() == b.assignee.strip().lower():
            result += self._assignee_weight
        if a.project and b.project and a.project.strip().lower() == b.project.strip().lower():
            result += self._project_weight

        return min(result, 1.0)


class DifflibScorer(_BlendedScorer):
    """Title ratio via :func:`difflib.SequenceMatcher` plus metadata boosts.

    This is the default scorer and has no third-party dependencies.
    """

    def _title_ratio(self, a_title: str, b_title: str) -> float:
        return SequenceMatcher(None, a_title, b_title).ratio()


# Backwards-compatible public alias retained from v0.1.
SequenceMatcherScorer = DifflibScorer


class RapidFuzzScorer(_BlendedScorer):
    """Title ratio via ``rapidfuzz`` (optional ``semantic`` extra, lazy import).

    ``rapidfuzz`` is imported lazily inside :meth:`_title_ratio`, so importing
    this class never requires the extra. A clear :class:`RuntimeError` is raised
    on first use when the dependency is missing.

    ``token_sort_ratio`` is used so word-order differences ("report to dana" vs
    "dana report") still score highly.
    """

    def _title_ratio(self, a_title: str, b_title: str) -> float:
        try:
            from rapidfuzz import fuzz
        except ImportError as exc:  # pragma: no cover - exercised only without extra
            raise RuntimeError(
                "RapidFuzzScorer requires rapidfuzz. Install it with: "
                "pip install 'truststack-task-dedupe[semantic]'"
            ) from exc
        if not a_title and not b_title:
            return 1.0
        return float(fuzz.token_sort_ratio(a_title, b_title)) / 100.0


class HashingEmbeddingScorer(_BlendedScorer):
    """Pure-stdlib hashing bag-of-words cosine similarity for titles.

    Each normalized title token is hashed into one of ``dimensions`` buckets
    (the "hashing trick"); the resulting term-frequency vectors are compared with
    cosine similarity. This is fully deterministic and offline — a lightweight
    embedding stand-in that needs no model download or third-party library.
    """

    def __init__(
        self,
        *,
        dimensions: int = 256,
        title_weight: float = 0.7,
        due_weight: float = 0.12,
        assignee_weight: float = 0.1,
        project_weight: float = 0.08,
    ) -> None:
        super().__init__(
            title_weight=title_weight,
            due_weight=due_weight,
            assignee_weight=assignee_weight,
            project_weight=project_weight,
        )
        if dimensions < 1:
            msg = f"dimensions must be >= 1, got {dimensions}"
            raise ValueError(msg)
        self._dimensions = dimensions

    def _bucket(self, token: str) -> int:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % self._dimensions

    def _vector(self, title: str) -> dict[int, float]:
        vec: Counter[int] = Counter()
        for token in title.split(" "):
            if token:
                vec[self._bucket(token)] += 1
        return dict(vec)

    def _title_ratio(self, a_title: str, b_title: str) -> float:
        va = self._vector(a_title)
        vb = self._vector(b_title)
        if not va and not vb:
            return 1.0
        if not va or not vb:
            return 0.0
        dot = sum(weight * vb.get(bucket, 0.0) for bucket, weight in va.items())
        norm_a = math.sqrt(sum(w * w for w in va.values()))
        norm_b = math.sqrt(sum(w * w for w in vb.values()))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return max(0.0, min(1.0, dot / (norm_a * norm_b)))


__all__ = [
    "DifflibScorer",
    "HashingEmbeddingScorer",
    "RapidFuzzScorer",
    "SequenceMatcherScorer",
    "SimilarityScorer",
]
