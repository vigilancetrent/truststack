# Trust Stack — Roadmap

Semantic versioning across the suite. Each library versions independently; the
milestones below describe the shared trajectory.

## v1.0 — "Trust the basics" (GA)

Goal: every library production-ready, 95%+ coverage, published to PyPI.

- **truststack-core**: stable `TrustComponent` contract, structured logging, event
  bus, OpenTelemetry spans + metrics.
- **agent-clock**: timezone auto-detect + override, human/UTC rendering, prompt
  middleware, OpenAI + Anthropic + LangChain adapters, generic adapter protocol.
- **shipped-or-not**: HTTP + health-endpoint + SSL verification, retry policy,
  `SHIPPED`/`UNVERIFIED` verdicts, JSON audit reports, CLI.
- **task-dedupe**: intent fingerprinting, due-date normalization, in-memory + SQLite
  backends, FastAPI service.
- **entity-canon**: fuzzy + phonetic matching, confidence scoring, alias store,
  SQLite backend, blocking threshold, REST API.
- **meta-token-vault**: encrypted token storage, expiry monitoring, refresh
  workflow, audit trail, SQLite backend, CLI.

## v1.5 — "Trust at scale"

- Production storage backends: PostgreSQL (all), Redis (task-dedupe),
  AWS Secrets Manager / Azure Key Vault / HashiCorp Vault (meta-token-vault).
- **task-dedupe** & **entity-canon**: pluggable vector/embedding backends for
  semantic similarity; batch import tooling.
- **shipped-or-not**: scheduled re-verification, webhook notifications, evidence
  bundles (screenshots/headers).
- **meta-token-vault**: rotation schedules, failure-alert webhooks, role-based
  access controls, dashboard API.
- Shared OpenTelemetry dashboards + Grafana templates.

## v2.0 — "Trust as a platform"

- `truststack` meta-package installing the full suite.
- Unified Trust Gateway: one ASGI app exposing all checks behind a single API +
  admin dashboard.
- Policy engine: declarative trust policies ("block deploy claims without a passing
  health check") evaluated across libraries.
- Managed event sink + audit warehouse (export trust events to ClickHouse/BigQuery).
- Framework-native plugins (LangGraph checkpoints, CrewAI guards, AutoGen hooks).
