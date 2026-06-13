# truststack-core

> Shared contract, structured logging, event bus, and OpenTelemetry helpers for the
> [Trust Stack for Agent Apps](https://github.com/vigilancetrent/truststack).
>
> _Because agent failures are trust failures._

Every Trust Stack library depends on `truststack-core`. You rarely install it
directly — but you build on its contract.

## Install

```bash
pip install truststack-core
```

## The contract

```python
from truststack.core import BaseTrustComponent, HealthState, HealthStatus

class MyCheck(BaseTrustComponent):
    component_name = "my-check"
    component_version = "1.0.0"

    async def _check_health(self) -> HealthStatus:
        return HealthStatus(component=self.component_name, state=HealthState.HEALTHY)

check = MyCheck()
check.version()              # "1.0.0"
await check.health_check()   # HealthStatus(state=HEALTHY)
await check.metrics()        # ComponentMetrics(counters=..., gauges=...)
check.registry.increment("requests")
```

## Structured logging

```python
from truststack.logging import configure_logging, get_logger, correlation_id

configure_logging(json=True)
log = get_logger("agent_clock", component="agent-clock")
correlation_id.set("req-123")
log.info("time_injected", timezone="Asia/Dubai")
# {"event": "time_injected", "component": "agent-clock", "correlation_id": "req-123", ...}
```

## Event bus

```python
from truststack.events import EventBus, TrustEvent

bus = EventBus()
bus.subscribe("deployment.unverified", my_handler)
await bus.publish(TrustEvent(name="deployment.unverified", component="shipped-or-not"))
```

## Observability

```python
from truststack.observability import traced

@traced("verify_deployment")
async def verify(url: str) -> bool:
    ...
```

Safe to use with no OpenTelemetry SDK configured (spans become no-ops). Install
`truststack-core[otel-sdk]` and configure a provider to export real telemetry.

## License

MIT
