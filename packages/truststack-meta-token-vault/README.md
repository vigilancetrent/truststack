# truststack-meta-token-vault

**The standard token-management layer for Meta/WhatsApp apps.**

[![Trust Stack](https://img.shields.io/badge/Trust%20Stack-component-1f6feb)](https://github.com/vigilancetrent/truststack)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Typed](https://img.shields.io/badge/typing-strict-success)](https://mypy.readthedocs.io/)

## The problem

Every Meta Graph API, WhatsApp Business, or Instagram integration ends up
rebuilding the same fragile plumbing by hand:

- **Where** to store the access token so it survives restarts.
- **When** to refresh a long-lived token before it silently expires and pages the
  on-call engineer at 3am.
- **How** to rotate credentials proactively to limit the blast radius of a leak.
- **How** to encrypt the secret at rest instead of leaving it in plaintext config.
- **Who** touched which token, and proving it after the fact for a compliance
  review.

Each team solves these the same way, badly, in a different codebase. When a token
expires unnoticed, message delivery stops and nobody knows why until customers
complain. When a token leaks, there is no rotation story and no audit trail.

`truststack-meta-token-vault` provides that layer **once** — as a first-class
Trust Stack component — with durable storage, scheduled rotation, expiry
monitoring, a pluggable refresh workflow, an immutable audit trail, secret
encryption at rest, role-based access control, failure-alert webhooks, and a
read/operate dashboard API.

It deliberately **never calls Meta's API for you**. You supply the refresh
workflow as an async callback; the vault owns the entire lifecycle around it
(storage, scheduling, retries-toward-distrust, observability, audit). This keeps
the library testable offline and free of any Meta SDK coupling.

## Install

```bash
uv add truststack-meta-token-vault
```

The base install has a single dependency (`truststack-core`) and ships the
in-memory and SQLite stores, RBAC, rotation, monitoring, and the audit trail.
Heavier features live behind extras so the package stays importable everywhere:

| Extra | Installs | Unlocks |
|-------|----------|---------|
| `fernet` | `cryptography` | `FernetEncryptor` — real symmetric encryption at rest. |
| `aws` | `boto3` | `AwsSecretsManagerTokenStore`. |
| `azure` | `azure-keyvault-secrets`, `azure-identity` | `AzureKeyVaultTokenStore`. |
| `hvac` | `hvac` | `HashiCorpVaultTokenStore`. |
| `postgres` | `asyncpg` | `PostgresTokenStore`. |
| `api` | `fastapi` | `meta_token_vault.api.create_app` dashboard. |

```bash
# Real encryption at rest (recommended for any non-dev use):
uv add "truststack-meta-token-vault[fernet]"

# A cloud backend plus the dashboard:
uv add "truststack-meta-token-vault[aws,api]"

# Everything:
uv add "truststack-meta-token-vault[fernet,aws,azure,hvac,postgres,api]"
```

Every backend client library is imported **lazily**, inside the methods that use
it. `import meta_token_vault` always succeeds with only the base dependency
installed — you only need an extra when you actually instantiate the backend it
powers.

## Quickstart

```python
import asyncio
from datetime import UTC, datetime, timedelta

from meta_token_vault import Role, Token, Vault


async def refresh_workflow(token: Token) -> Token:
    # Call YOUR Meta refresh logic here and return a fresh Token.
    return Token(
        value="new-long-lived-token",
        app_id=token.app_id,
        scopes=token.scopes,
        expires_at=datetime.now(UTC) + timedelta(days=60),
    )


async def main() -> None:
    vault = Vault(refresher=refresh_workflow, refresh_threshold_seconds=3600)

    await vault.store(
        Token(
            value="EAAGm0PX4ZCpsBA...",
            app_id="123456789",
            scopes=["whatsapp_business_messaging"],
            expires_at=datetime.now(UTC) + timedelta(minutes=30),  # near expiry
        ),
        role=Role.ADMIN,
    )

    # Auto-refreshes because it expires within the threshold.
    token = await vault.get_active_token("123456789")
    print(token.value)

    rotated = await vault.rotate("123456789", role=Role.OPERATOR)
    print(rotated.value)

    for entry in await vault.audit_trail():
        print(entry.action, entry.app_id, entry.actor, entry.at)


asyncio.run(main())
```

A runnable version lives in [`examples/quickstart.py`](examples/quickstart.py).

## Features

### Encryption at rest

`NoopEncryptor` is the default and is **DEV ONLY** — it stores secrets in
cleartext and pushes the vault's health to `DEGRADED`. Use `FernetEncryptor`
(needs the `fernet` extra) in any real environment:

```python
from meta_token_vault import FernetEncryptor, SqliteTokenStore, Vault

key = FernetEncryptor.generate_key()  # persist this key securely, e.g. in a KMS
enc = FernetEncryptor(key)
store = SqliteTokenStore("vault.db", encryptor=enc)
vault = Vault(store=store, encryptor=enc)
```

Every durable and cloud store encrypts the token **value** through the supplied
encryptor before it leaves the process; all other metadata is stored in
cleartext. The local schema is documented in
[`docs/schemas/truststack-meta-token-vault.sql`](../../docs/schemas/truststack-meta-token-vault.sql).

### Storage backends

Pick a `TokenStore` to match your infrastructure. All implement the same async
`put` / `get_active` / `all` contract, so they are fully interchangeable.

```python
from meta_token_vault import (
    AwsSecretsManagerTokenStore,
    AzureKeyVaultTokenStore,
    HashiCorpVaultTokenStore,
    InMemoryTokenStore,
    PostgresTokenStore,
    SqliteTokenStore,
    Vault,
)

# In-memory — default, zero infra, ideal for tests and ephemeral processes.
vault = Vault(store=InMemoryTokenStore())

# SQLite — durable local persistence on stdlib sqlite3 (off-loop via threads).
vault = Vault(store=SqliteTokenStore("vault.db", encryptor=enc))

# AWS Secrets Manager — one tagged secret per token (extra `aws`).
vault = Vault(store=AwsSecretsManagerTokenStore("meta-token-vault", enc, region_name="us-east-1"))

# Azure Key Vault — one tagged secret per token (extra `azure`).
vault = Vault(store=AzureKeyVaultTokenStore("https://my-vault.vault.azure.net", enc))

# HashiCorp Vault KV v2 (extra `hvac`).
vault = Vault(store=HashiCorpVaultTokenStore("http://127.0.0.1:8200", "s.token", enc))

# PostgreSQL via asyncpg — table is created on first use (extra `postgres`).
vault = Vault(store=PostgresTokenStore("postgres://user:pass@host/db", enc))
```

Cloud stores accept a pre-built `client`/`pool`/`credential` so they can be
exercised offline against mocked SDK clients (see the test suite for `moto`,
`MagicMock`, and `AsyncMock` patterns).

### Rotation policy

`RotationPolicy(max_age_seconds=...)` expresses *when* a token becomes due for
proactive rotation — usually stricter than its own hard expiry — and drives
`Vault.rotate_due`, which only rotates when the active token is actually due:

```python
from meta_token_vault import RotationPolicy, Vault

vault = Vault(
    refresher=refresh_workflow,
    rotation_policy=RotationPolicy(max_age_seconds=7 * 24 * 3600),  # weekly
)

# In a scheduler / cron loop:
rotated = await vault.rotate_due("123456789")  # None if not yet due
```

### Expiry monitoring

`Vault.monitor` runs an async, cancellable polling loop. On each tick it inspects
the active token; if it is missing, expired, or within the expiry window it
records an `EXPIRE` audit entry, emits a `token.expired` event, fires the alert
hook, invokes your `on_expiring` callback (awaited if it is a coroutine), and
auto-refreshes when a refresher is configured.

```python
async def on_expiring(token):
    print("about to expire:", token.app_id)

# Bounded for tests; omit `iterations` to run forever until cancelled.
detections = await vault.monitor("123456789", interval=30, on_expiring=on_expiring)

# Or run it as a background task and cancel cleanly on shutdown:
task = asyncio.create_task(vault.monitor("123456789", interval=30, on_expiring=on_expiring))
...
task.cancel()
```

### Alert / webhook hook

Pass `alert_hook=` to be notified on refresh/store failures and detected expiry.
The hook may be synchronous or an async webhook coroutine — it is awaited
automatically — and any exception it raises is logged and swallowed so alerting
can never break the vault itself.

```python
import httpx

async def webhook(action: str, exc: Exception) -> None:
    async with httpx.AsyncClient() as client:
        await client.post("https://hooks.example/alert", json={"action": action, "error": str(exc)})

vault = Vault(refresher=refresh_workflow, alert_hook=webhook)
```

### RBAC and the audit trail

A small `Role` enum (`ADMIN`, `OPERATOR`, `VIEWER`) gates each action. Pass
`role=` to vault operations; unauthorized actions raise `PermissionError` before
any state changes. Pass `actor=` to thread an identity through the immutable audit
trail.

| Role | store | get | rotate | refresh | expire |
|------|:-----:|:---:|:------:|:-------:|:------:|
| `ADMIN` | yes | yes | yes | yes | yes |
| `OPERATOR` | yes | yes | yes | yes | yes |
| `VIEWER` | no | yes | no | no | no |

```python
await vault.store(token, role=Role.ADMIN, actor="ci-bot")
await vault.get_active_token("123456789", role=Role.VIEWER, actor="dashboard")
await vault.rotate("123456789", role=Role.OPERATOR, actor="rotation-job")

# Denied — VIEWER may not store:
await vault.store(token, role=Role.VIEWER)  # raises PermissionError
```

### Dashboard API

The `api` extra adds a FastAPI factory that surfaces a read/operate dashboard over
a vault. Token `value` is never exposed by any endpoint.

```python
from meta_token_vault import Vault
from meta_token_vault.api import create_app

app = create_app(Vault(refresher=refresh_workflow))  # an ASGI app; serve with uvicorn
```

| Route | Method | Behaviour |
|-------|--------|-----------|
| `/tokens/{app_id}` | GET | List non-secret metadata for an app's tokens. |
| `/rotate/{app_id}` | POST | Force rotation. `409` without a refresher, `404` if no token, `403` on RBAC denial. |
| `/audit` | GET | The full audit trail. |
| `/health` | GET | Component health status. |

### CLI

```bash
meta-token-vault --db vault.db list 123456789
meta-token-vault --db vault.db get 123456789
meta-token-vault --db vault.db rotate 123456789
```

### Observability

`Vault` is a `BaseTrustComponent`, so it participates in the Trust Stack's shared
health, metrics, tracing, and event machinery:

- **Health:** `health_check()` reports `DEGRADED` while a `NoopEncryptor` is in
  use (secrets not encrypted at rest), otherwise `HEALTHY`.
- **Events** (when an `EventBus` is injected): `token.refreshed` after a
  successful refresh/rotation, `token.expired` when no usable token can be served.
- **Metrics counters:** `tokens.stored`, `tokens.served`, `tokens.missing`,
  `tokens.refreshed`, `tokens.rotated`, `tokens.expiring_detected`,
  `alerts.fired`, and `audit.<action>`.
- **Tracing:** public operations are wrapped with `@traced` spans.

## API

| Symbol | Kind | Purpose |
|--------|------|---------|
| `Vault` | class (`BaseTrustComponent`) | Orchestrates storage, refresh, rotation, monitoring, audit, RBAC, alerts. |
| `Token` | Pydantic model | Token + lifecycle metadata; `is_expired`, `expires_in_seconds`. |
| `AuditEntry` | Pydantic model | One audit record: action, app_id, token_id, at, actor. |
| `Role` / `Action` | enums | RBAC roles and auditable actions. |
| `TokenStore` | Protocol | `put` / `get_active` / `all`. |
| `InMemoryTokenStore` | class | Default store; zero infra. |
| `SqliteTokenStore` | class | Durable stdlib SQLite store with encryption at rest. |
| `AwsSecretsManagerTokenStore` | class | AWS Secrets Manager backend (extra `aws`). |
| `AzureKeyVaultTokenStore` | class | Azure Key Vault backend (extra `azure`). |
| `HashiCorpVaultTokenStore` | class | HashiCorp Vault KV v2 backend (extra `hvac`). |
| `PostgresTokenStore` | class | PostgreSQL/`asyncpg` backend (extra `postgres`). |
| `Encryptor` | Protocol | `encrypt` / `decrypt`. |
| `NoopEncryptor` | class | Pass-through (DEV ONLY). |
| `FernetEncryptor` | class | Symmetric encryption via `cryptography` (extra `fernet`). |
| `RotationPolicy` | class | Age-based rotation policy (`max_age_seconds`); drives `Vault.rotate_due`. |
| `TokenRefresher` | type alias | `Callable[[Token], Awaitable[Token]]` refresh workflow. |
| `AlertHook` | type alias | Sync or async failure/expiry callback. |
| `ExpiringCallback` | type alias | Sync or async callback invoked by `Vault.monitor`. |
| `TokenExpiringError` | exception | Signals a detected expiry to the alert hook. |
| `check_permission` / `is_allowed` | functions | RBAC checks. |
| `meta_token_vault.api.create_app` | function | FastAPI dashboard factory (extra `api`). |

Full reference: [`docs/api/truststack-meta-token-vault.md`](../../docs/api/truststack-meta-token-vault.md).

## Security notes

- Treat `Token` instances as secrets: in memory the `value` is held as-is.
- Never run production with `NoopEncryptor`; the `DEGRADED` health state is the
  vault telling you so.
- The dashboard and CLI never print or return a token `value`.
- RBAC checks run *before* any state mutation, so a denied call leaves the store
  and audit trail untouched.
- The audit trail is append-only within a process; persist it externally if you
  need durable, tamper-evident records.

---

Part of the **Trust Stack** — trustworthy infrastructure for AI agents.
