"""The shared Trust Stack contract.

Every Trust Stack component implements :class:`TrustComponent` so the whole suite
can be supervised, scraped, and audited uniformly.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class HealthState(StrEnum):
    """Coarse health classification for a component."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthStatus(BaseModel):
    """Result of a component health check."""

    model_config = ConfigDict(frozen=True)

    component: str
    state: HealthState = HealthState.HEALTHY
    detail: str | None = None
    checked_at: datetime = Field(default_factory=_utcnow)

    @property
    def ok(self) -> bool:
        return self.state is HealthState.HEALTHY


class ComponentMetrics(BaseModel):
    """A point-in-time snapshot of a component's counters and gauges."""

    model_config = ConfigDict(frozen=True)

    component: str
    version: str
    counters: dict[str, int] = Field(default_factory=dict)
    gauges: dict[str, float] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=_utcnow)


class MetricRegistry:
    """A tiny thread-safe in-process counter/gauge store.

    Components use this to track operational metrics that are surfaced through
    :meth:`TrustComponent.metrics`. It is intentionally minimal; for export to a
    real backend, pair it with :mod:`truststack.observability`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> tuple[dict[str, int], dict[str, float]]:
        with self._lock:
            return dict(self._counters), dict(self._gauges)


@runtime_checkable
class TrustComponent(Protocol):
    """The interface every Trust Stack component exposes."""

    def version(self) -> str: ...

    async def health_check(self) -> HealthStatus: ...

    async def metrics(self) -> ComponentMetrics: ...


class BaseTrustComponent:
    """Convenience base implementing the boilerplate of :class:`TrustComponent`.

    Subclasses set :attr:`component_name` and :attr:`component_version`, then use
    :attr:`registry` to record metrics. Override :meth:`_check_health` to add
    component-specific health logic.
    """

    component_name: str = "trust-component"
    component_version: str = "0.0.0"

    def __init__(self) -> None:
        self.registry = MetricRegistry()

    def version(self) -> str:
        return self.component_version

    async def _check_health(self) -> HealthStatus:
        return HealthStatus(component=self.component_name, state=HealthState.HEALTHY)

    async def health_check(self) -> HealthStatus:
        return await self._check_health()

    async def metrics(self) -> ComponentMetrics:
        counters, gauges = self.registry.snapshot()
        return ComponentMetrics(
            component=self.component_name,
            version=self.component_version,
            counters=counters,
            gauges=gauges,
        )


__all__ = [
    "BaseTrustComponent",
    "ComponentMetrics",
    "HealthState",
    "HealthStatus",
    "MetricRegistry",
    "TrustComponent",
]
