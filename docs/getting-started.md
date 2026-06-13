# Getting Started

## Install

Each library is independent — install only what you need. `truststack-core` is
pulled in automatically.

```bash
pip install truststack-agent-clock
pip install truststack-shipped-or-not
pip install truststack-task-dedupe
pip install truststack-entity-canon
pip install truststack-meta-token-vault
```

Production backends are optional extras, e.g.:

```bash
pip install 'truststack-task-dedupe[redis,postgres,semantic]'
pip install 'truststack-meta-token-vault[aws,azure,hvac,postgres,fernet]'
```

Requires **Python 3.11+**.

## Your first trust check

Inject trusted time into an LLM prompt with `agent-clock`:

```python
from agent_clock import ClockInjector

injector = ClockInjector(timezone="Asia/Dubai")
prompt = injector.inject("Summarize today's events")
# Current trusted datetime:
# Wednesday June 10 2026
# 17:55 +04
# Timezone: Asia/Dubai
# UTC Offset: +04:00
#
# User requests:
# Summarize today's events
```

Verify a deployment claim with `shipped-or-not`:

```python
from shipped_or_not import DeploymentVerifier

verifier = DeploymentVerifier()
result = await verifier.verify("https://app.example.com", health_path="/healthz")
assert result.status  # DeploymentStatus.SHIPPED only if URL + status + SSL + health all pass
```

Block a duplicate task with `task-dedupe`:

```python
from task_dedupe import DedupeEngine, Task

engine = DedupeEngine()
await engine.check(Task(title="Follow up with Anthropic", due="next week"))  # duplicate=False
result = await engine.check(Task(title="Follow up with Anthropic", due="next week"))
assert result.duplicate is True  # existing_task_id set
```

## The shared contract

Every component exposes the same supervised interface:

```python
from agent_clock import ClockInjector

c = ClockInjector(timezone="UTC")
c.version()              # "0.1.0"
await c.health_check()   # HealthStatus(state=HEALTHY, ...)
await c.metrics()        # ComponentMetrics(counters=..., gauges=...)
```

Build your own component on `truststack-core`:

```python
from truststack.core import BaseTrustComponent, HealthStatus, HealthState

class MyCheck(BaseTrustComponent):
    component_name = "my-check"
    component_version = "1.0.0"

    async def _check_health(self) -> HealthStatus:
        return HealthStatus(component=self.component_name, state=HealthState.HEALTHY)
```

See the [library reference](api/truststack-agent-clock.md) for the full API of each
package, and [Architecture](architecture.md) for how they fit together.

## Develop the suite

This is a [uv](https://docs.astral.sh/uv/) workspace.

```bash
uv sync --all-packages
uv run pytest --cov
uv run ruff check . && uv run mypy packages
```
