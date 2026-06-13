"""Time-source abstraction for :mod:`agent_clock`.

A :class:`TimeSource` is anything that can hand back a timezone-aware "now".
Decoupling the clock from :func:`datetime.now` makes injection deterministic in
tests (via :class:`FrozenTimeSource`) while the default
:class:`SystemTimeSource` reads the real wall clock.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from typing import Protocol, runtime_checkable


@runtime_checkable
class TimeSource(Protocol):
    """Protocol for objects that yield the current, timezone-aware instant."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware :class:`datetime`."""
        ...


class SystemTimeSource:
    """The default source: reads the real system clock in UTC.

    The returned value is always timezone-aware (UTC). The
    :class:`~agent_clock.injector.ClockInjector` converts it into its display zone.
    """

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenTimeSource:
    """A deterministic source that always returns a fixed instant.

    Intended for tests and reproducible rendering. The provided ``fixed`` value
    must be timezone-aware; a naive datetime is rejected to avoid ambiguous
    "now" semantics.
    """

    def __init__(self, fixed: datetime) -> None:
        if fixed.tzinfo is None or fixed.utcoffset() is None:
            raise ValueError("FrozenTimeSource requires a timezone-aware datetime")
        self._fixed: datetime = fixed

    @property
    def fixed(self) -> datetime:
        """The frozen instant this source always returns."""
        return self._fixed

    def advance(self, *, seconds: float) -> None:
        """Move the frozen instant forward (or backward) by ``seconds``."""
        from datetime import timedelta

        self._fixed = self._fixed + timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self._fixed


class CallableTimeSource:
    """A source backed by a user-supplied ``fetcher`` returning the current instant.

    This is the extension point for *trusted* external clocks — e.g. an NTP query,
    an HTTPS ``Date`` header, or a roughtime response. The library itself performs
    **no** network I/O: the caller owns the fetcher and is responsible for any
    transport. The fetcher is invoked on every :meth:`now` call so the value stays
    live.

    The fetcher must return a timezone-aware :class:`datetime`; a naive value is
    rejected with :class:`ValueError` to avoid ambiguous "now" semantics. This
    fails *toward distrust*: a misbehaving fetcher raises rather than silently
    substituting an unverified local clock.

    :param fetcher: zero-arg callable returning a tz-aware :class:`datetime`.
    """

    def __init__(self, fetcher: Callable[[], datetime]) -> None:
        if not callable(fetcher):  # pragma: no cover - defensive boundary
            raise TypeError("CallableTimeSource requires a callable fetcher")
        self._fetcher = fetcher

    def now(self) -> datetime:
        moment = self._fetcher()
        if not isinstance(moment, datetime):
            raise ValueError(
                f"CallableTimeSource fetcher must return a datetime, got {type(moment).__name__!r}"
            )
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("CallableTimeSource fetcher must return a timezone-aware datetime")
        return moment


def resolve_timezone(timezone: str | None) -> tzinfo:
    """Resolve ``timezone`` to a concrete :class:`~datetime.tzinfo`.

    ``None`` auto-detects the system local zone via
    ``datetime.now().astimezone().tzinfo``. A non-empty string is treated as an
    IANA name and resolved with :class:`zoneinfo.ZoneInfo` (requires ``tzdata``
    on Windows).
    """
    if timezone is None:
        local = datetime.now().astimezone().tzinfo
        if local is None:  # pragma: no cover - astimezone always populates tzinfo
            return UTC
        return local

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone!r}") from exc


__all__ = [
    "CallableTimeSource",
    "FrozenTimeSource",
    "SystemTimeSource",
    "TimeSource",
    "resolve_timezone",
]
