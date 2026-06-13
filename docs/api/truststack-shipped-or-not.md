# API — truststack-shipped-or-not

Import name: `shipped_or_not`. Verify deployment claims with evidence; a claim is
`SHIPPED` only when every applicable check passes, otherwise `UNVERIFIED`
(fail toward distrust).

All public models are Pydantic v2 and **frozen** (immutable, hashable). Every
module uses `from __future__ import annotations` and full type hints satisfying
`mypy --strict`.

## Public surface

Exported from `shipped_or_not`:

| Symbol | Kind |
|--------|------|
| `DeploymentStatus` | enum |
| `CheckResult` | model |
| `RetryPolicy` | model |
| `TlsInfo` | model |
| `VerificationResult` | model |
| `AuditStore` | Protocol |
| `InMemoryAuditStore` | store |
| `SqliteAuditStore` | store |
| `DeploymentVerifier` | component |
| `OnChange` | type alias |
| `Notify` | type alias |
| `__version__` | `str` |

## Enums

### `DeploymentStatus(StrEnum)`

| Member | Value |
|--------|-------|
| `SHIPPED` | `"shipped"` |
| `UNVERIFIED` | `"unverified"` |

`SHIPPED` is awarded only when *every* applicable check passes. Any error,
timeout, invalid certificate, or unexpected status collapses to `UNVERIFIED`.

## Type aliases

| Alias | Definition | Used by |
|-------|------------|---------|
| `OnChange` | `Callable[[VerificationResult], Awaitable[None]]` | `monitor(on_change=...)` |
| `Notify` | `Callable[[VerificationResult], Awaitable[None]]` | `monitor(notify=...)` |

## Models (Pydantic v2, frozen)

### `CheckResult`

The outcome of a single verification check.

| Field | Type | Notes |
|-------|------|-------|
| `name` | `str` | check identifier — one of `dns`, `ssl`, `http_status`, `health`, `http`, `error` |
| `passed` | `bool` | |
| `detail` | `str \| None` | human-readable explanation |

- `to_report() -> dict[str, Any]` — `{"name", "passed", "detail"}`.

The set of checks present on a result depends on the path taken:

| Check | Emitted when |
|-------|--------------|
| `dns` | a response was received (`passed=True`) **or** a non-TLS connect error occurred (`passed=False`) |
| `ssl` | the URL is `https` — `passed=True` on a successful handshake, `passed=False` on a TLS/cert connect error |
| `http_status` | a response was received; `passed` iff `status == expect_status` |
| `health` | `health_path` was provided; `passed` iff the endpoint returns `200` |
| `http` | a non-connect `httpx.HTTPError` (e.g. a protocol/timeout error) was raised |
| `error` | any other unexpected exception was raised |

### `RetryPolicy`

Exponential-backoff retry configuration for transient transport errors.

| Field | Type | Default | Constraint |
|-------|------|---------|------------|
| `attempts` | `int` | `3` | `1 <= n <= 20` |
| `backoff_seconds` | `float` | `0.5` | `> 0` |
| `max_backoff` | `float` | `8.0` | `> 0`, `>= backoff_seconds` |
| `jitter` | `float` | `0.0` | `0 <= j <= 1` |

Constructing a policy where `max_backoff < backoff_seconds` raises a Pydantic
`ValidationError` (`"max_backoff must be >= backoff_seconds"`).

**Methods**

- `delay_for(attempt: int) -> float` — backoff to wait *before* a 1-indexed
  `attempt`. Base delay is `min(backoff_seconds * 2 ** (attempt - 2), max_backoff)`;
  the first attempt (and any `attempt <= 1`) is `0.0`. When `jitter > 0` the base
  delay is multiplied by `1 + jitter * (2 * draw - 1)` (a factor in
  `[1 - jitter, 1 + jitter]`) and clamped to `[0, max_backoff]`.
- `with_rng(rng: Callable[[], float] | None) -> RetryPolicy` — returns a copy of
  the policy bound to a deterministic random source (a 0-argument callable
  returning a float in `[0, 1)`, same contract as `random.random`) so jittered
  delays are reproducible in tests. The bound `rng` is a `PrivateAttr` and is
  **excluded** from (de)serialization and equality.

**Example (deterministic jitter)**

```python
draws = iter([0.0, 1.0])
policy = RetryPolicy(backoff_seconds=0.5, jitter=0.2).with_rng(lambda: next(draws))
policy.delay_for(2)   # 0.5 * (1 + 0.2*(2*0.0 - 1)) = 0.40
policy.delay_for(3)   # base = 1.0; 1.0 * (1 + 0.2*(2*1.0 - 1)) = 1.20
```

### `TlsInfo`

Best-effort subset of the peer certificate, captured from the live connection.
All fields optional (absent for `http` URLs, mocked transports, or any parse
failure — degradation never corrupts the verdict).

| Field | Type |
|-------|------|
| `issuer` | `str \| None` |
| `subject` | `str \| None` |
| `not_after` | `datetime \| None` |
| `not_before` | `datetime \| None` |

- `to_report() -> dict[str, Any]` — datetimes rendered as ISO-8601 strings (or
  `None`).

`issuer` / `subject` are flattened from the `ssl.getpeercert()` RDN sequence
into a comma-joined `key=value` string (e.g. `"C=US, O=Let's Encrypt, CN=R3"`).
`not_after` / `not_before` are parsed from the OpenSSL timestamp format and
stamped UTC.

### `VerificationResult`

Evidence-bearing result of verifying a single deployment claim.

| Field | Type | Notes |
|-------|------|-------|
| `status` | `DeploymentStatus` | the verdict |
| `url` | `str` | the URL that was claimed live |
| `response_code` | `int \| None` | root-URL HTTP status, `None` if no response |
| `verified_at` | `datetime` (UTC) | defaults to now |
| `ssl_valid` | `bool \| None` | `True`/`False` for `https`; `None` for `http` |
| `health_passed` | `bool \| None` | `None` when `health_path` was not provided |
| `checks` | `list[CheckResult]` | per-check evidence |
| `detail` | `str \| None` | failure summary when not shipped |
| `final_url` | `str \| None` | URL after following redirects |
| `elapsed_ms` | `float \| None` | wall-clock time for the exchange (ms, rounded to 3dp) |
| `headers` | `dict[str, str]` | captured subset of response headers (lower-cased keys) |
| `tls` | `TlsInfo \| None` | best-effort TLS facts |

**Captured headers** (when present): `server`, `content-type`, `content-length`,
`date`, `cache-control`, `x-powered-by`, `via`, `strict-transport-security`,
`location`.

- `shipped` (property) -> `bool` — `True` iff `status is DeploymentStatus.SHIPPED`.
- `to_report() -> dict[str, Any]` — flat, JSON-serializable audit report
  (`verified_at` and all `tls` timestamps rendered as ISO-8601). Round-trips
  losslessly via `VerificationResult.model_validate(report)`.

## Audit stores

### `AuditStore` (Protocol, runtime-checkable)

Persistence contract for verification results. Any object implementing these
four coroutines satisfies the protocol (and passes `isinstance(obj, AuditStore)`).

- `async record(result: VerificationResult) -> None` — persist a single result.
- `async history(url: str) -> list[VerificationResult]` — all results for `url`,
  oldest first.
- `async all() -> list[VerificationResult]` — every result across all URLs,
  oldest first.
- `async close() -> None` — release any held resources.

### `InMemoryAuditStore`

Process-local, thread-safe list-backed store. Insertion order is chronological
(each result carries its own `verified_at` and is appended on arrival). Ideal
for tests and ephemeral runs. `close()` is a no-op.

```python
store = InMemoryAuditStore()
await store.record(result)
await store.history("https://example.com")
```

### `SqliteAuditStore`

Durable store over stdlib `sqlite3`. The full `to_report()` JSON is stored in a
`payload` column with `url` / `verified_at` denormalized into indexed columns.
All blocking `sqlite3` calls run via `asyncio.to_thread`, keeping the async API
non-blocking. A single connection (`check_same_thread=False`) is reused under a
lock. Schema: see `docs/schemas/truststack-shipped-or-not.sql` (mirrors
`shipped_or_not.audit._SCHEMA`).

```python
SqliteAuditStore(path: str | Path = ":memory:")   # build directly
await store.initialize()                            # idempotent schema creation

await SqliteAuditStore.connect(path=":memory:")     # build + create schema (classmethod)
```

| Method | Behaviour |
|--------|-----------|
| `connect(path=":memory:")` (classmethod) | construct and `initialize()` |
| `initialize()` | create the table + index `IF NOT EXISTS` (idempotent) |
| `record(result)` | `INSERT` one row off-thread, then commit |
| `history(url)` | `SELECT ... WHERE url = ? ORDER BY id ASC` |
| `all()` | `SELECT ... ORDER BY id ASC` |
| `close()` | close the underlying connection |

`history` / `all` rehydrate each row by validating the stored JSON back into a
`VerificationResult`, so persisted results round-trip exactly.

## Component

### `DeploymentVerifier(BaseTrustComponent)`

```python
DeploymentVerifier(
    retry: RetryPolicy | None = None,        # default RetryPolicy()
    timeout: float = 10.0,                    # per-request timeout (seconds)
    event_bus: EventBus | None = None,        # optional TrustEvent sink
    audit_store: AuditStore | None = None,    # optional durable persistence
)
```

Class attributes: `component_name = "shipped-or-not"`, `component_version = "0.1.0"`.

**Methods**

- `async verify(url: str, health_path: str | None = None, expect_status: int = 200) -> VerificationResult`
  — verify a deployment claim and return evidence. Wrapped with
  `@traced("shipped_or_not.verify")`.
- `async history(url: str) -> list[VerificationResult]` — replay persisted
  results (oldest first). Requires `audit_store`; raises `RuntimeError`
  (`"history requires an audit_store to be configured"`) otherwise.
- `async monitor(url, interval, on_change, *, iterations=None, health_path=None, expect_status=200, notify=None) -> VerificationResult | None`
  — re-verify on an interval; see below.

**Verdict rule.** `SHIPPED` iff: host resolves, HTTP status `== expect_status`,
TLS certificate valid (for `https`), and — if `health_path` is given — the
health endpoint returns `200`. Any failed check, error, timeout, or invalid
certificate yields `UNVERIFIED`, with `detail` summarising the cause:

| Situation | `status` | `detail` shape | `ssl_valid` |
|-----------|----------|----------------|-------------|
| all checks pass | `SHIPPED` | `None` | `True` (https) / `None` (http) |
| one or more checks fail | `UNVERIFIED` | `"failed checks: http_status, health"` | check-dependent |
| TLS/cert connect error (`https`) | `UNVERIFIED` | `"SSL certificate invalid: ..."` | `False` |
| non-TLS connect error (DNS/refused) | `UNVERIFIED` | `"connection failed: ..."` | unchanged |
| other `httpx.HTTPError` (e.g. timeout) | `UNVERIFIED` | `"request failed: ..."` | unchanged |
| unexpected exception | `UNVERIFIED` | `"unexpected error: ..."` | unchanged |

A `health_path` request that itself raises `httpx.HTTPError` records a failed
`health` check rather than aborting the whole verification.

**Retry.** Only transport-level failures (`httpx.ConnectError`,
`httpx.ConnectTimeout`, `httpx.TransportError`) are retried with exponential
backoff per `RetryPolicy`. An HTTP response — even a `500` — is a verifiable
verdict and is **not** retried. `verify_retry` is logged on each retry.

**Persistence.** When an `audit_store` is configured, every result is recorded
in `_finalize`. Audit failures are logged (`audit_persist_failed`) and swallowed
— a broken audit backend never fabricates or suppresses a verdict.

**Monitoring (`monitor`).**

- Awaits `on_change(result)` whenever `status` differs from the previously
  observed status, **including the first observation** (baseline).
- When provided, `notify(result)` is awaited on the same change edges, **after**
  `on_change`, and is wrapped so a failing webhook can never break the loop
  (`monitor_notify_failed` is logged on error).
- Runs `iterations` times when given (sleeping `interval` seconds *between*
  checks, not after the last), otherwise loops until the surrounding task is
  cancelled. `asyncio.CancelledError` is propagated cleanly.
- Returns the last `VerificationResult`, or `None` if zero iterations ran.
- `monitor_change` is logged on each verdict flip.

**Observability.**

- Metrics on `self.registry`: `verifications` (every call), `shipped`,
  `unverified`.
- Events (if `event_bus` provided): `deployment.shipped` /
  `deployment.unverified`; payload is `VerificationResult.to_report()`.
- `verify` is wrapped with `@traced("shipped_or_not.verify")`.
- `_check_health()` (the component's own health) always reports `HEALTHY`.
- Inherits `version()`, `health_check()`, `metrics()` from `BaseTrustComponent`.

**Example (full)**

```python
import asyncio
from shipped_or_not import DeploymentVerifier, RetryPolicy, SqliteAuditStore
from truststack.events import EventBus

async def main() -> None:
    store = await SqliteAuditStore.connect("audit.db")
    verifier = DeploymentVerifier(
        retry=RetryPolicy(attempts=5, jitter=0.2),
        timeout=5.0,
        event_bus=EventBus(),
        audit_store=store,
    )

    result = await verifier.verify("https://example.com", health_path="/healthz")
    if not result.shipped:
        print("NOT shipped:", result.detail)

    history = await verifier.history("https://example.com")
    print(f"{len(history)} prior verdicts on record")
    await store.close()

asyncio.run(main())
```

## CLI

```
shipped-or-not verify <url> [--health PATH] [--expect-status N] [--timeout S] [--json]
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--health PATH` | `None` | health endpoint that must return `200` |
| `--expect-status N` | `200` | expected root-URL HTTP status |
| `--timeout S` | `10.0` | per-request timeout in seconds |
| `--json` | off | emit the full `to_report()` as indented JSON |

Exit code: **`0` if `SHIPPED`, `1` otherwise** — a drop-in trust gate for CI
pipelines and agent tool calls.
