"""Pydantic v2 request/result models for task deduplication."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Task(BaseModel):
    """A task as captured from email, chat, or a meeting transcript.

    ``due`` accepts natural-language phrases ("tomorrow", "next week") or ISO
    dates; the engine normalizes it into a coarse time bucket for fingerprinting.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, description="Human-readable task title.")
    due: str | None = Field(default=None, description="Due date or relative phrase.")
    assignee: str | None = Field(default=None, description="Person responsible.")
    project: str | None = Field(default=None, description="Owning project or workspace.")


class FingerprintParts(BaseModel):
    """The normalized field values that composed a task's fingerprint.

    Surfaced on :class:`DedupeResult` so callers can inspect exactly which
    intent signature was hashed (and which fields collapsed two tasks together).
    """

    model_config = ConfigDict(frozen=True)

    title: str = Field(description="Normalized title (stopwords removed).")
    due: str = Field(description="Coarse due-window bucket, or 'none'.")
    assignee: str = Field(description="Normalized assignee, or '' when absent.")
    project: str = Field(description="Normalized project, or '' when absent.")


class DedupeResult(BaseModel):
    """The outcome of a :meth:`DedupeEngine.check` call."""

    model_config = ConfigDict(frozen=True)

    duplicate: bool = Field(description="True if a sufficiently similar task already exists.")
    existing_task_id: str | None = Field(
        default=None, description="Id of the matched task when ``duplicate`` is True."
    )
    score: float = Field(ge=0.0, le=1.0, description="Best similarity score against stored tasks.")
    fingerprint: str = Field(description="Stable intent fingerprint of the checked task.")
    fingerprint_inputs: FingerprintParts | None = Field(
        default=None,
        description="Normalized inputs that produced ``fingerprint``.",
    )


__all__ = ["DedupeResult", "FingerprintParts", "Task"]
