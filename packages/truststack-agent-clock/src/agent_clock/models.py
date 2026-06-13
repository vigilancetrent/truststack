"""Pydantic v2 models and enums for :mod:`agent_clock`.

The central model is :class:`TrustedTime`, a frozen snapshot of a single moment
rendered in both a human-friendly form and machine-readable ISO-8601 / UTC form.
It is what gets serialised into prompts so the LLM can reason about "now" without
guessing.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TimeFormat(BaseModel):
    """Presentation options for how the trusted time line is rendered.

    The defaults reproduce the historical, byte-identical output of
    :class:`~agent_clock.injector.ClockInjector` (``"HH:MM <abbrev>"`` on a
    24-hour clock with no seconds), so existing callers see no change. Opt in to
    a 12-hour clock or seconds by constructing a non-default instance.
    """

    model_config = ConfigDict(frozen=True)

    hour_clock: Literal[12, 24] = Field(
        default=24,
        description="Clock convention: 24-hour (default) or 12-hour with AM/PM.",
    )
    show_seconds: bool = Field(
        default=False,
        description="When True, include ``:SS`` in the rendered time line.",
    )

    @property
    def is_default(self) -> bool:
        """True when this format matches the historical default rendering."""
        return self.hour_clock == 24 and not self.show_seconds

    def format_time(self, moment: datetime) -> str:
        """Render the clock portion of ``moment`` (without the zone abbreviation).

        ``moment`` must already be in the desired display zone. The result is the
        bare time, e.g. ``"17:55"``, ``"17:55:09"``, ``"05:55 PM"``, or
        ``"05:55:09 PM"`` depending on the configured options.
        """
        if self.hour_clock == 24:
            pattern = "%H:%M:%S" if self.show_seconds else "%H:%M"
            return moment.strftime(pattern)
        pattern = "%I:%M:%S %p" if self.show_seconds else "%I:%M %p"
        return moment.strftime(pattern)


class Weekday(StrEnum):
    """Days of the week, ordered to match :meth:`datetime.weekday` (Monday == 0)."""

    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"
    SUNDAY = "Sunday"

    @classmethod
    def from_datetime(cls, moment: datetime) -> Weekday:
        """Return the :class:`Weekday` for ``moment`` (uses ``Monday == 0``)."""
        return tuple(cls)[moment.weekday()]


class TrustedTime(BaseModel):
    """An immutable, fully-rendered view of a single trusted moment in time.

    All fields are pre-computed so consumers never have to re-derive formatting,
    and so the exact same value can be logged, emitted as an event, and embedded
    in a prompt.
    """

    model_config = ConfigDict(frozen=True)

    human_readable: str = Field(
        description="Multi-line human form, e.g. 'Wednesday June 10 2026\\n17:55 +04'.",
    )
    date_line: str = Field(description="Date portion only, e.g. 'Wednesday June 10 2026'.")
    time_line: str = Field(description="Time portion only, e.g. '17:55 +04'.")
    utc_iso: str = Field(description="The same instant in UTC as an ISO-8601 string.")
    local_iso: str = Field(description="The instant in the configured zone as ISO-8601.")
    timezone: str = Field(description="IANA/zone label, e.g. 'Asia/Dubai' or 'UTC+04:00'.")
    utc_offset: str = Field(description="Offset from UTC, e.g. '+04:00'.")
    weekday: Weekday = Field(description="Day of week for the local instant.")
    abbreviation: str = Field(description="Zone abbreviation, e.g. '+04', 'UTC'.")
    epoch: float = Field(description="POSIX timestamp (seconds since the Unix epoch).")


__all__ = ["TimeFormat", "TrustedTime", "Weekday"]
