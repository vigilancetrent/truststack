"""truststack-core — the shared contract and utilities for the Trust Stack.

Submodules:

- :mod:`truststack.core` — the :class:`~truststack.core.TrustComponent` contract.
- :mod:`truststack.logging` — structured, JSON-first logging.
- :mod:`truststack.events` — the async trust event bus.
- :mod:`truststack.observability` — OpenTelemetry helpers.
"""

from __future__ import annotations

from truststack.core import (
    BaseTrustComponent,
    ComponentMetrics,
    HealthState,
    HealthStatus,
    MetricRegistry,
    TrustComponent,
)

__version__ = "0.1.0"

__all__ = [
    "BaseTrustComponent",
    "ComponentMetrics",
    "HealthState",
    "HealthStatus",
    "MetricRegistry",
    "TrustComponent",
    "__version__",
]
