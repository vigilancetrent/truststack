# Trust Stack for Agent Apps

> _Because agent failures are trust failures._

The infrastructure layer that makes AI agents **reliable, auditable, and accountable.**

---

## The thesis

Most agent failures are **not intelligence failures — they are trust failures.**

Agents hallucinate dates, claim deployments that never happened, duplicate tasks,
create duplicate entities, and mishandle authentication tokens. Each of these is a
recurring, *operational* failure that no amount of prompt engineering fully fixes.

The Trust Stack is a suite of small, independently installable Python libraries —
each one targeting a single real-world trust failure observed repeatedly in
production AI systems.

## The libraries

| Library | Trust problem it solves | Install |
|---------|-------------------------|---------|
| [agent-clock](api/truststack-agent-clock.md) | Agents answer with the wrong date because time isn't injected into prompts. | `pip install truststack-agent-clock` |
| [shipped-or-not](api/truststack-shipped-or-not.md) | Agents claim software is deployed when the deploy actually failed. | `pip install truststack-shipped-or-not` |
| [task-dedupe](api/truststack-task-dedupe.md) | The same task gets created from email, chat, and transcripts. | `pip install truststack-task-dedupe` |
| [entity-canon](api/truststack-entity-canon.md) | Name variants (`Jatin`/`Jhatin`/`Jatyn`) create duplicate entities. | `pip install truststack-entity-canon` |
| [meta-token-vault](api/truststack-meta-token-vault.md) | Every Meta/WhatsApp integration rebuilds token management from scratch. | `pip install truststack-meta-token-vault` |

Every library depends on **`truststack-core`** — the shared contract
(`health_check()` / `metrics()` / `version()`), structured logging, the trust event
bus, and OpenTelemetry helpers. See [Architecture](architecture.md).

## Design contract

Every Trust Stack component implements the same interface, so the whole suite can
be supervised, scraped, and audited uniformly:

```python
component.version()            # -> "1.2.0"
await component.health_check() # -> HealthStatus(state=HEALTHY, ...)
await component.metrics()      # -> ComponentMetrics(counters=..., gauges=...)
```

All libraries are **Python 3.11+, fully typed (mypy strict), Pydantic v2,
async-first**, ship structured logs and OpenTelemetry spans, and are tested to
**95%+ coverage**.

[Get started →](getting-started.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/vigilancetrent/truststack){ .md-button }
