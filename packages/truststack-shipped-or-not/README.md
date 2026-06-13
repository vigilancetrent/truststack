# truststack-shipped-or-not

> Deployment claims must be **verified with evidence** — not taken on faith.
>
> _Because agent failures are trust failures._

`shipped-or-not` answers one question that AI agents and CI bots get wrong all
the time: **did it actually ship?** It turns a confident "✅ deployed!" into an
evidence-backed, replayable verdict.

---

## The problem

An AI agent finishes a task and reports `✅ deployed to https://app.example.com`.
In reality any of the following may be true:

- the URL returns `500` (the app crash-looped on boot);
- DNS does not resolve (the record was never created);
- the TLS certificate is **expired**, self-signed, or for the wrong host;
- the page loads but the `/healthz` endpoint reports the service is degraded;
- a redirect quietly sends users to a parked/holding page.

A confident sentence is **not** proof. Worse, downstream automation trusts that
sentence and compounds the failure. `shipped-or-not` replaces the claim with an
auditable `VerificationResult` carrying exactly which checks passed, which
failed, and the evidence behind each.

### Fail toward distrust

A deployment is marked **`SHIPPED` only when _every_ applicable check passes**:

| Check | Passes when | Applies to |
|-------|-------------|------------|
| `dns` | host resolves and is reachable (no transport error) | all URLs |
| `ssl` | the TLS certificate is valid | `https://` URLs |
| `http_status` | HTTP status `== expect_status` (default `200`) | all URLs |
| `health` | the health endpoint returns `200` | only when `health_path` is set |

Anything else — **any** error, timeout, invalid certificate, or unexpected
status — collapses to **`UNVERIFIED`** with a human-readable `detail`. There is
no "probably fine". Ambiguity is distrust.

---

## Install

```bash
pip install truststack-shipped-or-not
```

Runtime dependencies are deliberately tiny: `truststack-core` and
`httpx>=0.27`. The durable audit store uses only the **standard library**
(`sqlite3`), so it needs no extra install.

### Optional extras

| Extra | Installs | Enables |
|-------|----------|---------|
| _(none)_ | `truststack-core`, `httpx` | verification, monitoring, in-memory + SQLite audit, CLI |
| `dev` | test/lint tooling | local development |

```bash
pip install "truststack-shipped-or-not"          # everything you need at runtime
pip install "truststack-shipped-or-not[dev]"      # + tooling for contributors
```

> The package imports cleanly with only its required dependencies — `httpx` is
> imported **lazily** inside the methods that use it, and SQLite is stdlib, so
> `import shipped_or_not` never pulls in anything optional.

---

## Quickstart (SDK)

```python
import asyncio
from shipped_or_not import DeploymentVerifier

async def main() -> None:
    verifier = DeploymentVerifier()
    result = await verifier.verify("https://example.com", health_path="/healthz")

    print(result.status)        # DeploymentStatus.SHIPPED or .UNVERIFIED
    print(result.shipped)       # True only when positively verified
    print(result.to_report())   # flat, JSON-serializable audit report

asyncio.run(main())
```

`verify()` is async-first and `@traced`, so every call is an OpenTelemetry span.

---

## Quickstart (CLI)

```bash
shipped-or-not verify https://example.com --health /healthz
shipped-or-not verify https://example.com --expect-status 204 --timeout 5
shipped-or-not verify https://example.com --json
```

The exit code is the gate: **`0` when `SHIPPED`, `1` otherwise** — drop it
straight into a CI pipeline or an agent tool call.

```bash
# CI gate: only continue the pipeline if the deploy is provably live.
shipped-or-not verify "$DEPLOY_URL" --health /healthz || exit 1
```

---

## Evidence on every verdict

A verdict is only as trustworthy as the evidence behind it. Every
`VerificationResult` captures, in addition to the verdict and per-check results:

| Evidence | Field | Source |
|----------|-------|--------|
| Final URL after redirects | `final_url` | `response.url` after `follow_redirects=True` |
| Wall-clock latency | `elapsed_ms` | `time.perf_counter()` around the exchange |
| Response headers (subset) | `headers` | `server`, `content-type`, `content-length`, `date`, `cache-control`, `x-powered-by`, `via`, `strict-transport-security`, `location` |
| TLS certificate facts | `tls` | peer certificate of the live connection (best effort) |

```python
result = await verifier.verify("https://example.com")
print(result.final_url)      # e.g. "https://example.com/" after a 301 -> 200 chain
print(result.elapsed_ms)     # e.g. 142.318
print(result.headers)        # {"server": "...", "content-type": "text/html", ...}
print(result.tls)            # TlsInfo(issuer=..., not_after=..., ...) or None
```

`tls` is **best effort**: it is populated from the peer certificate of the live
TLS connection when the transport exposes it, and is `None` for plain `http://`
URLs or mocked transports. A parsing failure degrades to `None` — it never
corrupts the verdict.

For `https://` URLs, `ssl_valid` is `True` when the handshake succeeded and
`False` when the connection failed on a certificate/TLS error. For `http://`
URLs `ssl_valid` is `None` (not applicable).

---

## Durable audit trail

Persist every verdict to an `AuditStore` and replay it later as timestamped
evidence — the difference between _"the agent said it deployed"_ and _"here is
the proof, on demand"_.

```python
from shipped_or_not import DeploymentVerifier, SqliteAuditStore

store = await SqliteAuditStore.connect("audit.db")   # durable, on disk
verifier = DeploymentVerifier(audit_store=store)

await verifier.verify("https://example.com")
await verifier.verify("https://example.com")

history = await verifier.history("https://example.com")   # oldest first
for r in history:
    print(r.verified_at, r.status, r.elapsed_ms)

await store.close()
```

Two implementations ship, both honouring the `AuditStore` protocol so callers
can swap freely:

| Store | Backing | Use it for |
|-------|---------|-----------|
| `InMemoryAuditStore` | a process-local, thread-safe `list` | tests, ephemeral runs |
| `SqliteAuditStore` | stdlib `sqlite3` (file or `:memory:`) | durable, queryable history |

`SqliteAuditStore` runs **all** blocking `sqlite3` calls off the event loop via
`asyncio.to_thread`, stores the full `to_report()` JSON in a `payload` column,
and denormalizes `url` / `verified_at` into indexed columns for fast
`history()` lookups. The schema lives in
[`docs/schemas/truststack-shipped-or-not.sql`](../../docs/schemas/truststack-shipped-or-not.sql).

```python
# Construction options
store = SqliteAuditStore(":memory:")        # build directly...
await store.initialize()                     # ...then create the schema (idempotent)

store = await SqliteAuditStore.connect("audit.db")   # or one-shot connect()
```

Persistence **fails open toward the truth**: if the audit backend errors, the
failure is logged and swallowed so a broken store can never turn a real verdict
into a lie — `verify()` still returns the correct result.

---

## Scheduled re-verification (`monitor`)

Deployments rot. `monitor()` re-checks a URL on an interval and fires
`on_change` **only when the verdict flips** (the very first observation always
fires, establishing the baseline). It is fully cancellable and can be bounded
with `iterations`.

```python
async def on_change(result):
    print("verdict changed ->", result.status, "at", result.verified_at)

# Re-verify every 30s, up to 10 times, calling on_change only on a flip.
last = await verifier.monitor(
    "https://example.com",
    interval=30,
    on_change=on_change,
    iterations=10,
    health_path="/healthz",
)
```

Run it unbounded inside a task and cancel it cleanly:

```python
task = asyncio.create_task(verifier.monitor("https://example.com", 30, on_change))
...
task.cancel()   # honoured promptly; CancelledError is re-raised
```

### Optional webhook / notify callback

Pass `notify` to fan a status change out to a webhook, pager, or chat channel.
It is awaited on the same change edges, right after `on_change`, and is wrapped
so a failing webhook can never break the monitor loop:

```python
import httpx

async def webhook(result):
    async with httpx.AsyncClient() as client:
        await client.post("https://hooks.example.com/deploy", json=result.to_report())

await verifier.monitor("https://example.com", 30, on_change, notify=webhook)
```

---

## Retries with optional jitter

Only **transport-level** failures (connect / timeout / transport errors) are
retried with exponential backoff. An HTTP response — even a `500` — is a
verifiable verdict and is returned immediately, never retried.

```python
from shipped_or_not import DeploymentVerifier, RetryPolicy

policy = RetryPolicy(attempts=5, backoff_seconds=0.25, max_backoff=4.0, jitter=0.2)
verifier = DeploymentVerifier(retry=policy)
```

The base delay before the 1-indexed `attempt` is
`min(backoff_seconds * 2 ** (attempt - 2), max_backoff)`; the first attempt
always has zero delay. With `jitter > 0` the delay is scaled by a random factor
in `[1 - jitter, 1 + jitter]` and clamped to `[0, max_backoff]`, spreading out
retries to avoid thundering herds.

For **deterministic** jitter in tests, inject a random source via `with_rng`
(a 0-argument callable returning a float in `[0, 1)`, the same contract as
`random.random`). The `rng` is excluded from (de)serialization, so the policy
stays a plain, comparable, JSON-friendly model:

```python
draws = iter([0.0, 1.0, 0.5])
policy = RetryPolicy(jitter=0.2).with_rng(lambda: next(draws))
assert policy.delay_for(2) == 0.4   # 0.5 * (1 + 0.2 * (2*0.0 - 1)) = 0.4
```

---

## Trust events & observability

Inject a `truststack.events.EventBus` and every verdict is published as a
`TrustEvent` for downstream auditing:

```python
from truststack.events import EventBus

bus = EventBus()
verifier = DeploymentVerifier(event_bus=bus)
await verifier.verify("https://example.com")
# Emits TrustEvent(name="deployment.shipped" | "deployment.unverified",
#                  component="shipped-or-not", data=result.to_report())
```

The verifier is a first-class Trust Stack component:

- **Metrics** on `self.registry`: `verifications`, `shipped`, `unverified`.
- **Events**: `deployment.shipped` / `deployment.unverified` (payload is the
  full `to_report()`).
- **Tracing**: `verify()` is wrapped with `@traced("shipped_or_not.verify")`.
- **Contract**: inherits `version()`, `health_check()`, and `metrics()` from
  `BaseTrustComponent`; its own health check is always `HEALTHY` (the component
  is stateless and has no external dependency to degrade).

---

## JSON reports

`VerificationResult.to_report()` returns a flat, JSON-serializable dict suitable
for persisting to an audit log or emitting over the wire:

```json
{
  "status": "shipped",
  "url": "https://example.com",
  "response_code": 200,
  "verified_at": "2026-06-13T12:00:00+00:00",
  "ssl_valid": true,
  "health_passed": true,
  "checks": [
    {"name": "dns", "passed": true, "detail": "host resolved"},
    {"name": "ssl", "passed": true, "detail": "certificate valid"},
    {"name": "http_status", "passed": true, "detail": "got 200, expected 200"},
    {"name": "health", "passed": true, "detail": "https://example.com/healthz -> 200"}
  ],
  "detail": null,
  "final_url": "https://example.com/",
  "elapsed_ms": 142.318,
  "headers": {"server": "nginx", "content-type": "text/html"},
  "tls": {
    "issuer": "C=US, O=Let's Encrypt, CN=R3",
    "subject": "CN=example.com",
    "not_after": "2030-06-01T12:00:00+00:00",
    "not_before": "2030-03-01T12:00:00+00:00"
  }
}
```

All `datetime` values are rendered as ISO-8601 strings, and the report
round-trips losslessly back through `VerificationResult.model_validate(...)`
(which is exactly how `SqliteAuditStore` rehydrates history).

---

## Deliverables

| Capability | How |
|------------|-----|
| **CLI** | `shipped-or-not verify <url> [--health PATH] [--expect-status N] [--timeout S] [--json]` |
| **SDK** | `DeploymentVerifier.verify(...)` (async-first, `@traced`) |
| **Evidence** | `final_url`, `elapsed_ms`, `headers`, `tls` on every result |
| **Audit logs** | `TrustEvent` on an injected `EventBus`; durable `AuditStore` |
| **JSON reports** | `VerificationResult.to_report()` (round-trips losslessly) |
| **Monitoring** | `DeploymentVerifier.monitor(...)` + optional webhook |
| **Retries** | `RetryPolicy` (exponential backoff, optional deterministic jitter) |

---

## API

| Symbol | Kind | Notes |
|--------|------|-------|
| `DeploymentStatus` | `StrEnum` | `SHIPPED` (`"shipped"`), `UNVERIFIED` (`"unverified"`) |
| `CheckResult` | model (frozen) | `name`, `passed`, `detail`; `.to_report()` |
| `RetryPolicy` | model (frozen) | `attempts=3`, `backoff_seconds=0.5`, `max_backoff=8.0`, `jitter=0.0`; `.delay_for(n)`, `.with_rng(rng)` |
| `TlsInfo` | model (frozen) | `issuer`, `subject`, `not_after`, `not_before`; `.to_report()` |
| `VerificationResult` | model (frozen) | verdict + evidence; `.shipped`, `.to_report()` |
| `AuditStore` | Protocol (runtime-checkable) | `record`, `history`, `all`, `close` |
| `InMemoryAuditStore` | store | process-local, thread-safe |
| `SqliteAuditStore` | store | durable stdlib `sqlite3`; `.connect()`, `.initialize()` |
| `DeploymentVerifier` | `BaseTrustComponent` | `verify(...)`, `history(...)`, `monitor(...)` |
| `OnChange` | type alias | `Callable[[VerificationResult], Awaitable[None]]` |
| `Notify` | type alias | `Callable[[VerificationResult], Awaitable[None]]` |

Full reference: [`docs/api/truststack-shipped-or-not.md`](../../docs/api/truststack-shipped-or-not.md).

---

Part of the **Trust Stack for Agent Apps** — _because agent failures are trust failures._
