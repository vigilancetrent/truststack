<div align="center">

# Trust Stack for Agent Apps

### _Because agent failures are trust failures._

The infrastructure layer that makes AI agents **reliable, auditable, and accountable.**

[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Typed](https://img.shields.io/badge/typed-mypy%20strict-2563eb)](https://mypy-lang.org/)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-e92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)

</div>

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

| Package | Trust problem it solves | Import |
|---------|-------------------------|--------|
| [`truststack-agent-clock`](packages/agent-clock) | Agents answer with the wrong date because time isn't injected into prompts. | `agent_clock` |
| [`truststack-shipped-or-not`](packages/shipped-or-not) | Agents claim software is deployed when the deploy actually failed. | `shipped_or_not` |
| [`truststack-task-dedupe`](packages/task-dedupe) | The same task gets created from email, chat, and transcripts. | `task_dedupe` |
| [`truststack-entity-canon`](packages/entity-canon) | Name variants (`Jatin`/`Jhatin`/`Jatyn`) create duplicate entities. | `entity_canon` |
| [`truststack-meta-token-vault`](packages/meta-token-vault) | Every Meta/WhatsApp integration rebuilds token management from scratch. | `meta_token_vault` |

Shared foundation:

| Package | Provides |
|---------|----------|
| [`truststack-core`](packages/truststack-core) | The shared contract: `TrustComponent` (`health_check()`/`metrics()`/`version()`), structured logging (`truststack.logging`), the trust event bus (`truststack.events`), and OpenTelemetry helpers (`truststack.observability`). |

## Install

Each library is independent — install only what you need:

```bash
pip install truststack-agent-clock
pip install truststack-shipped-or-not
pip install truststack-task-dedupe
pip install truststack-entity-canon
pip install truststack-meta-token-vault
```

Every library depends on `truststack-core`, which is installed automatically.

## Design contract

Every Trust Stack component implements the same interface so they can be supervised,
scraped, and audited uniformly:

```python
component.version()            # -> "1.2.0"
await component.health_check() # -> HealthStatus(state=HEALTHY, ...)
await component.metrics()      # -> ComponentMetrics(counters=..., gauges=...)
```

All libraries are **Python 3.11+, fully typed (mypy strict), Pydantic v2,
async-first**, ship structured logs and OpenTelemetry spans, and target **95%+
test coverage**.

## Repository layout

```
truststack/
├── packages/
│   ├── truststack-core/      # shared contract + logging + events + observability
│   ├── agent-clock/          # truststack-agent-clock
│   ├── shipped-or-not/       # truststack-shipped-or-not
│   ├── task-dedupe/          # truststack-task-dedupe
│   ├── entity-canon/         # truststack-entity-canon
│   └── meta-token-vault/     # truststack-meta-token-vault
├── docs/{api,schemas}/       # API specs + database schemas
├── .github/workflows/        # CI matrix + PyPI publish
├── ARCHITECTURE.md
└── ROADMAP.md
```

## Development

This is a [uv](https://docs.astral.sh/uv/) workspace.

```bash
uv sync                                   # install all packages + dev tools
uv run pytest                             # run the whole suite
uv run pytest --cov --cov-report=term     # with coverage
uv run ruff check . && uv run mypy packages
```

## License

MIT — see [LICENSE](LICENSE).
