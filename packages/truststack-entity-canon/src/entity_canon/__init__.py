"""Trust Stack Entity Canon — canonicalize entities before insertion.

Combines fuzzy (``difflib``) and phonetic (Soundex + Metaphone) matching to
detect and block duplicate entity names like ``Jatin`` / ``Jhatin`` / ``Jatyn``.
"""

from __future__ import annotations

from .canonicalizer import Canonicalizer, bulk_import
from .models import (
    CanonicalEntity,
    ImportCounts,
    MatchMethod,
    MatchResult,
    MatchSignal,
)
from .phonetic import (
    metaphone,
    metaphone_equal,
    phonetic_agreement,
    phonetic_equal,
    soundex,
)
from .stores import (
    EntityStore,
    InMemoryEntityStore,
    PostgresEntityStore,
    SqliteEntityStore,
)

__version__ = "0.1.0"

__all__ = [
    "CanonicalEntity",
    "Canonicalizer",
    "EntityStore",
    "ImportCounts",
    "InMemoryEntityStore",
    "MatchMethod",
    "MatchResult",
    "MatchSignal",
    "PostgresEntityStore",
    "SqliteEntityStore",
    "__version__",
    "bulk_import",
    "metaphone",
    "metaphone_equal",
    "phonetic_agreement",
    "phonetic_equal",
    "soundex",
]
