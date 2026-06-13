from __future__ import annotations

import truststack
from truststack.core import (
    BaseTrustComponent,
    ComponentMetrics,
    HealthState,
    HealthStatus,
    MetricRegistry,
    TrustComponent,
)


def test_health_status_ok_property() -> None:
    assert HealthStatus(component="x").ok is True
    assert HealthStatus(component="x", state=HealthState.DEGRADED).ok is False


def test_metric_registry_counts_and_gauges() -> None:
    reg = MetricRegistry()
    reg.increment("hits")
    reg.increment("hits", 4)
    reg.set_gauge("ratio", 0.5)
    counters, gauges = reg.snapshot()
    assert counters == {"hits": 5}
    assert gauges == {"ratio": 0.5}
    # snapshot returns copies, not live references
    counters["hits"] = 0
    assert reg.snapshot()[0]["hits"] == 5


class _Demo(BaseTrustComponent):
    component_name = "demo"
    component_version = "2.3.4"


async def test_base_component_defaults() -> None:
    demo = _Demo()
    assert demo.version() == "2.3.4"

    health = await demo.health_check()
    assert isinstance(health, HealthStatus)
    assert health.state is HealthState.HEALTHY
    assert health.component == "demo"


async def test_base_component_metrics_reflect_registry() -> None:
    demo = _Demo()
    demo.registry.increment("calls", 3)
    demo.registry.set_gauge("load", 1.5)
    m = await demo.metrics()
    assert isinstance(m, ComponentMetrics)
    assert m.version == "2.3.4"
    assert m.counters == {"calls": 3}
    assert m.gauges == {"load": 1.5}


async def test_custom_health_override() -> None:
    class Degraded(BaseTrustComponent):
        component_name = "degraded"
        component_version = "1.0.0"

        async def _check_health(self) -> HealthStatus:
            return HealthStatus(
                component=self.component_name,
                state=HealthState.DEGRADED,
                detail="backend slow",
            )

    health = await Degraded().health_check()
    assert health.state is HealthState.DEGRADED
    assert health.detail == "backend slow"


def test_runtime_checkable_protocol() -> None:
    assert isinstance(_Demo(), TrustComponent)


def test_package_exports_version() -> None:
    assert truststack.__version__ == "0.1.0"
    assert "TrustComponent" in truststack.__all__
