"""Pydantic v2 models and enums for entity canonicalization."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MatchMethod(StrEnum):
    """How a candidate match was scored."""

    NONE = "none"
    FUZZY = "fuzzy"
    PHONETIC = "phonetic"
    FUZZY_PHONETIC = "fuzzy+phonetic"
    EXACT = "exact"


class MatchSignal(StrEnum):
    """An individual signal that contributed to a match decision."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    SOUNDEX = "soundex"
    METAPHONE = "metaphone"


class CanonicalEntity(BaseModel):
    """A canonical entity record with its known aliases."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("aliases")
    @classmethod
    def _aliases_clean(cls, value: list[str]) -> list[str]:
        return [a for a in (alias.strip() for alias in value) if a]

    def surface_forms(self) -> list[str]:
        """Return the canonical name plus all aliases for matching."""
        return [self.name, *self.aliases]


class MatchResult(BaseModel):
    """Outcome of a lookup or canonicalization request."""

    model_config = ConfigDict(frozen=True)

    match: str | None = None
    entity_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    blocked: bool = False
    suggestion: str | None = None
    method: MatchMethod = MatchMethod.NONE
    #: Which discrete signals fired for the winning candidate.
    signals: list[MatchSignal] = Field(default_factory=list)
    #: Raw fuzzy ``SequenceMatcher`` ratio for the winning candidate, in [0, 1].
    fuzzy_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    #: Phonetic agreement (Soundex + Metaphone) for the winner, in [0, 1].
    phonetic_agreement: float = Field(default=0.0, ge=0.0, le=1.0)


class ImportCounts(BaseModel):
    """Outcome of a batch import operation."""

    model_config = ConfigDict(frozen=True)

    added: int = Field(default=0, ge=0)
    merged: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        """Total rows processed across all outcomes."""
        return self.added + self.merged + self.skipped


__all__ = [
    "CanonicalEntity",
    "ImportCounts",
    "MatchMethod",
    "MatchResult",
    "MatchSignal",
]
