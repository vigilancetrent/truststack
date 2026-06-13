"""The :class:`ClockInjector` — the heart of :mod:`agent_clock`.

It renders a trusted moment in time and prepends it to prompts so an LLM always
knows the real "now" instead of hallucinating a stale training-cutoff date.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

from agent_clock.models import TimeFormat, TrustedTime, Weekday
from agent_clock.sources import SystemTimeSource, TimeSource, resolve_timezone
from truststack.core import BaseTrustComponent, HealthState, HealthStatus
from truststack.logging import get_logger
from truststack.observability import traced

if TYPE_CHECKING:
    from truststack.events import EventBus

_TRUSTED_BLOCK_HEADER = "Current trusted datetime:"
_USER_REQUEST_HEADER = "User requests:"
_ZERO = timedelta(0)


def _format_offset(offset_seconds: int) -> str:
    """Format a UTC offset in seconds as a signed ``+HH:MM`` string."""
    sign = "+" if offset_seconds >= 0 else "-"
    total_minutes = abs(offset_seconds) // 60
    hours, minutes = divmod(total_minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _zone_label(zone: tzinfo, moment: datetime) -> str:
    """Best-effort human label for ``zone`` (IANA key when available)."""
    key = getattr(zone, "key", None)
    if isinstance(key, str) and key:
        return key
    name = zone.tzname(moment)
    if name:
        return name
    return f"UTC{_format_offset(int((moment.utcoffset() or _ZERO).total_seconds()))}"


class ClockInjector(BaseTrustComponent):
    """Render trusted temporal context and inject it into prompts.

    :param timezone: IANA zone name (e.g. ``"Asia/Dubai"``). When ``None`` the
        system local timezone is auto-detected.
    :param time_source: source of the current instant. Defaults to
        :class:`~agent_clock.sources.SystemTimeSource`; pass a
        :class:`~agent_clock.sources.FrozenTimeSource` for deterministic output.
    :param event_bus: optional :class:`~truststack.events.EventBus`; when given,
        a ``clock.injected`` event is published on every injection.
    :param time_format: presentation options for the rendered time line. Defaults
        to :class:`~agent_clock.models.TimeFormat` (24-hour, no seconds), which
        keeps the output byte-identical to prior versions.
    """

    component_name = "agent-clock"
    component_version = "0.1.0"

    def __init__(
        self,
        timezone: str | None = None,
        time_source: TimeSource | None = None,
        *,
        event_bus: EventBus | None = None,
        time_format: TimeFormat | None = None,
    ) -> None:
        super().__init__()
        self._tz_request = timezone
        self._tzinfo: tzinfo = resolve_timezone(timezone)
        self._source: TimeSource = time_source or SystemTimeSource()
        self._bus = event_bus
        self._time_format: TimeFormat = time_format or TimeFormat()
        self._log = get_logger("agent_clock", component=self.component_name)
        # Derive the label without touching the time source: construction must
        # have no side effect on (and never fail because of) the clock source.
        self._log.info(
            "clock_initialised",
            timezone=getattr(self._tzinfo, "key", None) or str(self._tzinfo),
            auto_detected=timezone is None,
        )

    @property
    def timezone(self) -> tzinfo:
        """The resolved display timezone."""
        return self._tzinfo

    @property
    def time_format(self) -> TimeFormat:
        """The presentation options used to render the time line."""
        return self._time_format

    def render(self) -> TrustedTime:
        """Render the current trusted moment as a :class:`TrustedTime`.

        The source instant is normalised to UTC, then converted into the
        configured display zone for all human-facing fields.
        """
        instant = self._source.now()
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("TimeSource.now() must return a timezone-aware datetime")

        local = instant.astimezone(self._tzinfo)
        offset_delta = local.utcoffset() or _ZERO
        offset = _format_offset(int(offset_delta.total_seconds()))
        abbreviation = local.tzname() or "UTC"
        weekday = Weekday.from_datetime(local)

        date_line = f"{weekday.value} {local.strftime('%B')} {local.day} {local.year}"
        time_line = f"{self._time_format.format_time(local)} {abbreviation}"

        trusted = TrustedTime(
            human_readable=f"{date_line}\n{time_line}",
            date_line=date_line,
            time_line=time_line,
            utc_iso=local.astimezone(UTC).isoformat(),
            local_iso=local.isoformat(),
            timezone=_zone_label(self._tzinfo, local),
            utc_offset=offset,
            weekday=weekday,
            abbreviation=abbreviation,
            epoch=local.timestamp(),
        )
        self.registry.increment("renders")
        self.registry.set_gauge("last_epoch", trusted.epoch)
        return trusted

    def block(self, trusted: TrustedTime | None = None) -> str:
        """Return the formatted ``Current trusted datetime:`` block as a string."""
        t = trusted or self.render()
        return (
            f"{_TRUSTED_BLOCK_HEADER}\n"
            f"{t.date_line}\n"
            f"{t.time_line}\n"
            f"Timezone: {t.timezone}\n"
            f"UTC Offset: {t.utc_offset}"
        )

    @traced("agent_clock.inject")
    async def ainject(self, prompt: str) -> str:
        """Async variant of :meth:`inject`, also publishing a trust event."""
        trusted = self.render()
        result = self._compose(prompt, trusted)
        self.registry.increment("injections")
        self._log.info("time_injected", timezone=trusted.timezone, weekday=trusted.weekday.value)
        if self._bus is not None:
            from truststack.events import TrustEvent

            await self._bus.publish(
                TrustEvent(
                    name="clock.injected",
                    component=self.component_name,
                    data={"utc_iso": trusted.utc_iso, "timezone": trusted.timezone},
                )
            )
        return result

    def inject(self, prompt: str) -> str:
        """Prepend the trusted-time block to ``prompt`` and return the new prompt.

        The output is the block, a blank line, then ``User requests:`` followed by
        the original prompt.
        """
        trusted = self.render()
        result = self._compose(prompt, trusted)
        self.registry.increment("injections")
        self._log.info("time_injected", timezone=trusted.timezone, weekday=trusted.weekday.value)
        return result

    def _compose(self, prompt: str, trusted: TrustedTime) -> str:
        return f"{self.block(trusted)}\n\n{_USER_REQUEST_HEADER}\n{prompt}"

    async def _check_health(self) -> HealthStatus:
        try:
            self.render()
        except Exception as exc:  # pragma: no cover - defensive boundary
            return HealthStatus(
                component=self.component_name,
                state=HealthState.UNHEALTHY,
                detail=f"clock render failed: {exc}",
            )
        return HealthStatus(
            component=self.component_name,
            state=HealthState.HEALTHY,
            detail=f"zone={_zone_label(self._tzinfo, self._source.now())}",
        )


__all__ = ["ClockInjector"]
