"""Intent fingerprinting helpers: title normalization and due-window bucketing.

Everything here is stdlib-only so the package installs and runs offline. The
fingerprint is a stable hash over the normalized title plus the coarse due
window, assignee, and project, so semantically equivalent tasks collapse to the
same intent signature.

The :func:`normalize_due` parser understands an expanded vocabulary of relative
phrases (``today`` / ``tomorrow`` / ``yesterday`` / ``this week`` / ``next
week`` / ``next month`` / ``in N days`` / weekday names) plus explicit ISO
dates, all mapped to coarse, deterministic ISO year-week buckets. A reference
``now`` may be supplied for determinism in tests and reproducible fingerprints.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from .models import Task

# A compact English stopword set. Kept small and dependency-free; it removes the
# filler words that add noise to short task titles without changing intent.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "to",
        "for",
        "of",
        "on",
        "in",
        "with",
        "at",
        "by",
        "is",
        "are",
        "be",
        "this",
        "that",
        "please",
        "pls",
        "kindly",
        "we",
        "i",
        "should",
        "need",
        "needs",
        "must",
        "can",
        "could",
        "would",
        "will",
        "let",
        "us",
        "our",
        "your",
    }
)

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, and drop stopwords.

    Empty results (a title made entirely of stopwords/punctuation) fall back to
    the punctuation-stripped lowercase form so two such titles still compare.
    """
    lowered = title.lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    collapsed = _WS_RE.sub(" ", no_punct).strip()
    tokens = [t for t in collapsed.split(" ") if t and t not in _STOPWORDS]
    if not tokens:
        return collapsed
    return " ".join(tokens)


def _coarse_week_bucket(d: date) -> str:
    """ISO year-week label, e.g. ``2026-W24`` — the deduplication time grain."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# Relative phrases mapped to an offset (in days) from "today".
_RELATIVE_DAYS: dict[str, int] = {
    "today": 0,
    "tonight": 0,
    "tomorrow": 1,
    "tmrw": 1,
    "tmr": 1,
    "yesterday": -1,
    "eod": 0,
    "asap": 0,
    "now": 0,
    "immediately": 0,
    "day after tomorrow": 2,
    "overmorrow": 2,
    "day before yesterday": -2,
}

_WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
# "in N days" / "in N weeks" / "in N months" (with simple singular/plural).
_IN_N_RE = re.compile(r"^in\s+(\d{1,4})\s+(day|days|week|weeks|month|months)\b")
# "N days ago" / "N weeks ago" / "N months ago".
_N_AGO_RE = re.compile(r"^(\d{1,4})\s+(day|days|week|weeks|month|months)\s+ago\b")


def normalize_due(due: str | None, *, now: datetime | None = None) -> str:
    """Map a due string to a coarse ISO week bucket.

    Understands relative phrases (``today``, ``tomorrow``, ``yesterday``,
    ``this week``, ``next week``, ``last week``, ``next month``, ``this month``),
    counted offsets (``in N days/weeks/months``, ``N days/weeks/months ago``),
    weekday names (resolved to the *next* matching weekday), and ISO dates
    (``YYYY-MM-DD``). Unparseable values are lowercased/trimmed and returned
    verbatim so they still match each other. Returns ``"none"`` when ``due`` is
    absent or blank.

    ``now`` pins the reference instant so buckets are deterministic.
    """
    if due is None:
        return "none"

    ref = (now or datetime.now(UTC)).date()
    text = _WS_RE.sub(" ", due.strip().lower())
    if not text:
        return "none"

    iso_match = _ISO_DATE_RE.match(text)
    if iso_match:
        try:
            parsed = date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            return text
        return _coarse_week_bucket(parsed)

    # Multi-word relative phrases must be checked before single-word lookups so
    # "day after tomorrow" is not swallowed by the "tomorrow" weekday scan.
    if text in _RELATIVE_DAYS:
        return _coarse_week_bucket(ref + timedelta(days=_RELATIVE_DAYS[text]))

    in_match = _IN_N_RE.match(text)
    if in_match:
        return _coarse_week_bucket(_apply_offset(ref, int(in_match.group(1)), in_match.group(2), 1))

    ago_match = _N_AGO_RE.match(text)
    if ago_match:
        return _coarse_week_bucket(
            _apply_offset(ref, int(ago_match.group(1)), ago_match.group(2), -1)
        )

    if "this week" in text or text == "week":
        return _coarse_week_bucket(ref)
    if "next week" in text:
        return _coarse_week_bucket(ref + timedelta(weeks=1))
    if "last week" in text or "previous week" in text:
        return _coarse_week_bucket(ref - timedelta(weeks=1))
    if "next month" in text:
        return _coarse_week_bucket(ref + timedelta(days=30))
    if "last month" in text or "previous month" in text:
        return _coarse_week_bucket(ref - timedelta(days=30))
    if "this month" in text or text == "month":
        return _coarse_week_bucket(ref + timedelta(days=15))

    # Weekday names resolve to the next matching weekday. A bare or "this"
    # weekday name lands on the upcoming occurrence (today if it matches);
    # "next <weekday>" always lands one full week later for a stable bucket.
    for idx, name in enumerate(_WEEKDAYS):
        if name in text or text == name[:3]:
            delta = (idx - ref.weekday()) % 7
            if "next" in text:
                delta += 7
            return _coarse_week_bucket(ref + timedelta(days=delta))

    return text


def _apply_offset(ref: date, magnitude: int, unit: str, sign: int) -> date:
    """Shift ``ref`` by ``sign * magnitude`` of ``unit`` (day/week/month)."""
    if unit.startswith("day"):
        return ref + timedelta(days=sign * magnitude)
    if unit.startswith("week"):
        return ref + timedelta(weeks=sign * magnitude)
    # months -> approximate as 30-day grains; the bucket is coarse by design.
    return ref + timedelta(days=sign * magnitude * 30)


def _norm_field(value: str | None) -> str:
    if value is None:
        return ""
    return _WS_RE.sub(" ", value.strip().lower())


@dataclass(frozen=True, slots=True)
class FingerprintInputs:
    """The normalized field values that feed a task fingerprint.

    Exposed on :class:`~task_dedupe.models.DedupeResult` so callers can see
    exactly which intent signature collapsed two tasks together.
    """

    title: str
    due: str
    assignee: str
    project: str

    def to_payload(self) -> str:
        """Join the inputs with a unit-separator into the hashed payload."""
        return "\x1f".join((self.title, self.due, self.assignee, self.project))

    def to_fingerprint(self) -> str:
        """Hash the inputs into the 16-hex-char intent fingerprint."""
        return hashlib.sha256(self.to_payload().encode("utf-8")).hexdigest()[:16]


def fingerprint_inputs(task: Task, *, now: datetime | None = None) -> FingerprintInputs:
    """Compute the normalized fingerprint inputs for ``task``."""
    return FingerprintInputs(
        title=normalize_title(task.title),
        due=normalize_due(task.due, now=now),
        assignee=_norm_field(task.assignee),
        project=_norm_field(task.project),
    )


def fingerprint_task(task: Task, *, now: datetime | None = None) -> str:
    """Compute a stable 16-hex-char intent fingerprint for ``task``."""
    return fingerprint_inputs(task, now=now).to_fingerprint()


__all__ = [
    "FingerprintInputs",
    "fingerprint_inputs",
    "fingerprint_task",
    "normalize_due",
    "normalize_title",
]
