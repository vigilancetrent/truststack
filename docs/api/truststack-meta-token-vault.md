# API: truststack-meta-token-vault

Import name: `meta_token_vault`. The standard token-management layer for
Meta/WhatsApp apps: durable storage, scheduled rotation, expiry monitoring, a
pluggable refresh workflow, encryption at rest, RBAC, an audit trail,
failure-alert webhooks, and a dashboard API.

All public modules use `from __future__ import annotations`, are fully type-hinted
(mypy `--strict` clean), Pydantic v2 only, and async-first. Every optional
backend client library is imported lazily inside the method that uses it, so
`import meta_token_vault` succeeds with only the base dependency installed.

## Package exports

Exported from `meta_token_vault` (`__all__`):

`Action`, `AlertHook`, `AuditEntry`, `AwsSecretsManagerTokenStore`,
`AzureKeyVaultTokenStore`, `Encryptor`, `ExpiringCallback`, `FernetEncryptor`,
`HashiCorpVaultTokenStore`, `InMemoryTokenStore`, `NoopEncryptor`,
`PostgresTokenStore`, `Role`, `RotationPolicy`, `SqliteTokenStore`, `Token`,
`TokenExpiringError`, `TokenRefresher`, `TokenStore`, `Vault`, `__version__`,
`check_permission`, `is_allowed`.

The FastAPI factory `create_app` lives in `meta_token_vault.api` (extra `api`).

`__version__ == "0.1.0"`.

## Models (`meta_token_vault.models`)

### `Token` (Pydantic v2, frozen)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `id` | `str` | uuid4 hex | Unique token id (`default_factory`). |
| `value` | `str` | required | Secret material (encrypted at rest by encrypting stores). |
| `app_id` | `str` | required | Meta app id. |
| `scopes` | `list[str]` | `[]` | Granted scopes. |
| `issued_at` | `datetime` | now (UTC) | Issue time. |
| `expires_at` | `datetime \| None` | `None` | Expiry; `None` = never. |

Instances are immutable (`frozen=True`). Treat them as secrets.

Methods:

- `is_expired(now: datetime | None = None) -> bool` — `True` when `expires_at` is
  set and `now` (default: current UTC time) is at or after it. Always `False`
  when `expires_at is None`.
- `expires_in_seconds(now: datetime | None = None) -> float | None` — seconds
  until expiry (negative if already expired), or `None` when there is no expiry.

### `AuditEntry` (Pydantic v2, frozen)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `action` | `Action` | required | The auditable action. |
| `app_id` | `str` | required | Meta app id. |
| `token_id` | `str \| None` | `None` | Token affected, if any. |
| `at` | `datetime` | now (UTC) | When the action was recorded. |
| `actor` | `str` | `"system"` | Identity threaded through the operation. |

### Enums

- `Role` (`StrEnum`): `ADMIN`, `OPERATOR`, `VIEWER`.
- `Action` (`StrEnum`): `STORE`, `GET`, `ROTATE`, `REFRESH`, `EXPIRE`.

## Encryption (`meta_token_vault.encryption`)

`Encryptor` is a runtime-checkable `Protocol`:

- `encrypt(plaintext: str) -> str`
- `decrypt(ciphertext: str) -> str`

Implementations:

- `NoopEncryptor()` — pass-through; returns input unchanged. **DEV ONLY**; using
  it pushes `Vault` health to `DEGRADED`.
- `FernetEncryptor(key: str | bytes)` — symmetric authenticated encryption via
  `cryptography`'s Fernet (extra `fernet`; `cryptography` imported lazily).
  - `@staticmethod FernetEncryptor.generate_key() -> str` — a fresh URL-safe
    base64 key. Persist it securely; losing it makes ciphertext unrecoverable.

```python
from meta_token_vault import FernetEncryptor

enc = FernetEncryptor(FernetEncryptor.generate_key())
ct = enc.encrypt("EAAG...")
assert enc.decrypt(ct) == "EAAG..."
```

## Stores (`meta_token_vault.stores`)

`TokenStore` is a runtime-checkable `Protocol` (all methods async):

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| `put` | `put(token: Token) -> None` | Persist `token` (insert or replace by id). |
| `get_active` | `get_active(app_id: str) -> Token \| None` | Newest non-expired token for `app_id`, or `None`. |
| `all` | `all(app_id: str) -> list[Token]` | Every token for `app_id`, newest-issued first. |

`get_active` is defined consistently across backends as: of all non-expired
tokens for the app, return the one with the most recent `issued_at` (or `None`).

### Infrastructure-free backends

- `InMemoryTokenStore()` — thread-safe dict keyed by token id. Default; zero
  infra; data lost on restart.
- `SqliteTokenStore(path: str | Path, encryptor: Encryptor | None = None)` —
  durable stdlib `sqlite3`; all blocking calls run off-loop via
  `asyncio.to_thread`; a process-wide lock serialises writes; encrypts token
  values at rest. Schema created on construction. See the SQL schema doc.

### Production cloud / database backends

All encrypt token values at rest via the configured `Encryptor`, serialise the
remaining metadata as JSON, and import their client library lazily. Each accepts
a pre-built client/pool for offline testing.

#### `AwsSecretsManagerTokenStore` (extra `aws`)

```python
AwsSecretsManagerTokenStore(
    prefix: str = "meta-token-vault",
    encryptor: Encryptor | None = None,
    *,
    region_name: str | None = None,
    client: Any | None = None,   # pre-built boto3 secretsmanager client (tests)
)
```

Stores each token as a dedicated secret named `{prefix}/{app_id}/{token_id}`
whose `SecretString` is the encrypted, serialised token, tagged with
`meta_token_vault:app_id` and `meta_token_vault:managed`. `put` creates the secret
or, on `ResourceExistsException`, updates its value. `all` paginates
`list_secrets` filtered by the app-id tag and decodes each secret. `boto3`
imported lazily; blocking calls run via `asyncio.to_thread`.

#### `AzureKeyVaultTokenStore` (extra `azure`)

```python
AzureKeyVaultTokenStore(
    vault_url: str,
    encryptor: Encryptor | None = None,
    *,
    prefix: str = "mtv",
    credential: Any | None = None,  # defaults to DefaultAzureCredential
    client: Any | None = None,      # pre-built SecretClient (tests)
)
```

Stores each token as a secret named `{prefix}-{app_id}-{token_id}` (Key Vault
names allow only alphanumerics and dashes) with the encrypted payload as the
value and the app id in the tags. `all` lists secret properties, filters by the
`meta_token_vault_app_id` tag, and fetches each matching secret.
`azure-keyvault-secrets` and `azure-identity` imported lazily; the sync
`SecretClient` runs via `asyncio.to_thread`.

#### `HashiCorpVaultTokenStore` (extra `hvac`)

```python
HashiCorpVaultTokenStore(
    url: str | None = None,
    token: str | None = None,
    encryptor: Encryptor | None = None,
    *,
    mount_point: str = "secret",
    path_prefix: str = "meta-token-vault",
    client: Any | None = None,  # pre-built hvac.Client (tests)
)
```

Writes each token to KV v2 path `{path_prefix}/{app_id}/{token_id}` with the
encrypted payload under a `token` key. `all` lists the app directory (returning
`[]` on `InvalidPath`) and reads each version. `hvac` imported lazily; the sync
client runs via `asyncio.to_thread`.

#### `PostgresTokenStore` (extra `postgres`)

```python
PostgresTokenStore(
    dsn: str | None = None,
    encryptor: Encryptor | None = None,
    *,
    table: str = "meta_token_vault_tokens",  # must be a valid identifier
    pool: asyncpg.Pool | None = None,        # pre-built pool (tests); skips DSN
)
```

Stores tokens in a table (name validated as an identifier to prevent injection)
with the value column encrypted at rest. `asyncpg` imported lazily; the
connection pool is created on first use and the schema (`CREATE TABLE IF NOT
EXISTS` + an `app_id` index) is ensured once under an `asyncio.Lock`. `put` uses
`INSERT ... ON CONFLICT (id) DO UPDATE`. Raises `ValueError` for an invalid table
name, or when neither `dsn` nor `pool` is supplied.

## Rotation (`meta_token_vault.rotation`)

### `RotationPolicy(max_age_seconds: float)`

Age-based proactive-rotation policy. `max_age_seconds` must be positive
(`ValueError` otherwise).

- `age_seconds(token: Token, now: datetime | None = None) -> float` — seconds
  since `token.issued_at`.
- `is_due(token: Token, now: datetime | None = None) -> bool` — `True` once the
  token's age is at or beyond `max_age_seconds`.

## Type aliases (`meta_token_vault.vault`)

- `TokenRefresher = Callable[[Token], Awaitable[Token]]` — your refresh workflow:
  given an expiring token, return a fresh one.
- `AlertHook = Callable[[str, Exception], None] | Callable[[str, Exception], Awaitable[None]]`
  — sync or async (webhook) failure/expiry hook.
- `ExpiringCallback = Callable[[Token], None] | Callable[[Token], Awaitable[None]]`
  — sync or async callback invoked by `Vault.monitor` for each expiring token.

## `Vault(BaseTrustComponent)` (`meta_token_vault.vault`)

```python
Vault(
    store: TokenStore | None = None,                  # default InMemoryTokenStore
    encryptor: Encryptor | None = None,               # default NoopEncryptor (DEV)
    refresher: TokenRefresher | None = None,          # enables auto-refresh/rotate
    refresh_threshold_seconds: int = 3600,            # proactive-refresh window
    rotation_policy: RotationPolicy | None = None,    # drives rotate_due
    event_bus: EventBus | None = None,                # lifecycle events when set
    alert_hook: AlertHook | None = None,              # failure/expiry callback
)
```

`component_name = "meta-token-vault"`, `component_version = "0.1.0"`.

### Methods

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| `store` | `async store(token, *, role=Role.ADMIN, actor="system") -> None` | Persist + audit. On store failure fires the alert hook, logs, and re-raises. RBAC: `STORE`. |
| `get_active_token` | `async get_active_token(app_id, *, role=Role.VIEWER, actor="system") -> Token` | Return active token; auto-refreshes if within `refresh_threshold_seconds` and a refresher is set. RBAC: `GET`. |
| `rotate` | `async rotate(app_id, *, role=Role.OPERATOR, actor="system") -> Token` | Force a refresh of the current/most-recent token and persist it. Emits `token.refreshed`. RBAC: `ROTATE`. |
| `rotate_due` | `async rotate_due(app_id, *, role=Role.OPERATOR, actor="system", now=None) -> Token \| None` | Rotate only if the `RotationPolicy` says the active token is due; `None` otherwise. RBAC: `ROTATE`. |
| `monitor` | `async monitor(app_id, interval, on_expiring=None, *, iterations=None, threshold_seconds=None, actor="system") -> int` | Async, cancellable expiry-monitoring loop. Returns the number of detections. |
| `audit_trail` | `async audit_trail() -> list[AuditEntry]` | Snapshot copy of the trail (oldest first). |

Inherited from `BaseTrustComponent`: `version() -> str`,
`async health_check() -> HealthStatus`, `async metrics() -> ComponentMetrics`.

### Errors

| Raised by | Exception | When |
|-----------|-----------|------|
| any RBAC-gated method | `PermissionError` | `role` lacks the required `Action` (checked before any mutation). |
| `get_active_token` | `KeyError` | No active token and refresh unavailable. |
| `rotate` | `RuntimeError` | No `TokenRefresher` configured. |
| `rotate` | `KeyError` | No token (active or historical) to rotate. |
| `rotate_due` | `RuntimeError` | No `RotationPolicy` configured. |

### `monitor` semantics

Each tick reads the active token. It counts as a *detection* and triggers the
expiry path when the token is missing, expired, or within `threshold_seconds`
(default: `refresh_threshold_seconds`) of expiry. The expiry path records an
`EXPIRE` audit entry (actor `"monitor"`), increments `tokens.expiring_detected`,
emits `token.expired`, fires the alert hook with a `TokenExpiringError` (only when
a token exists), invokes `on_expiring` (awaited if a coroutine; its exceptions are
logged and swallowed), and auto-refreshes when a refresher is set. The loop runs
forever unless bounded by `iterations`, sleeps `interval` seconds between ticks,
and is cancellable via task cancellation (re-raises `CancelledError`).

### Auto-refresh and graceful degradation

`get_active_token` refreshes when the served token is within the threshold. If a
refresh raises, the alert hook fires and the error is logged; if the current token
is still valid it is served anyway (fail-soft), otherwise `token.expired` is
emitted and the error propagates (fail-toward-distrust).

### Health

`health_check()` returns `DEGRADED` while a `NoopEncryptor` is in use (secrets not
encrypted at rest), otherwise `HEALTHY`.

### Events (when `event_bus` is set)

- `token.refreshed` — after a successful refresh or rotation. `data={"app_id", "token_id"}`.
- `token.expired` — when no usable token can be served or an expiry is detected.

### Metrics counters

`tokens.stored`, `tokens.served`, `tokens.missing`, `tokens.refreshed`,
`tokens.rotated`, `tokens.expiring_detected`, `alerts.fired`, and `audit.<action>`
(one per recorded action).

### Alert hook

`alert_hook(action, exc)` fires on `store`/`refresh` failures and on `expire`
detected by `monitor`. It may be sync or an async webhook coroutine (awaited
automatically). Exceptions inside the hook are logged and swallowed so alerting
never breaks the vault. `alerts.fired` is incremented every time the alert path
runs, even when no hook is configured.

### `TokenExpiringError`

`TokenExpiringError(app_id: str, token: Token)` — raised internally and passed to
the alert hook to signal a detected expiry. Exposes `.app_id` and `.token`.

## RBAC (`meta_token_vault.rbac`)

- `is_allowed(role: Role, action: Action) -> bool`
- `check_permission(role: Role, action: Action) -> None` — raises
  `PermissionError` when the role may not perform the action.

Permission matrix:

| Role | `STORE` | `GET` | `ROTATE` | `REFRESH` | `EXPIRE` |
|------|:-------:|:-----:|:--------:|:---------:|:--------:|
| `ADMIN` | yes | yes | yes | yes | yes |
| `OPERATOR` | yes | yes | yes | yes | yes |
| `VIEWER` | no | yes | no | no | no |

## Dashboard API (`meta_token_vault.api`, extra `api`)

`create_app(vault: Vault, *, actor: str = "dashboard") -> fastapi.FastAPI` —
builds a FastAPI app (lazy `fastapi` import) exposing a read/operate dashboard.
The token `value` is never returned by any route. `actor` is recorded as the audit
actor for dashboard-initiated operations.

| Route | Method | Behaviour | Status codes |
|-------|--------|-----------|--------------|
| `/tokens/{app_id}` | GET | List non-secret token metadata for the app. | 200 |
| `/rotate/{app_id}` | POST | Force rotation (operator role). | 200; 403 RBAC denial; 409 no refresher; 404 no token |
| `/audit` | GET | The audit trail. | 200 |
| `/health` | GET | Component health status. | 200 |

The `/tokens` projection includes `id`, `app_id`, `scopes`, `issued_at`,
`expires_at`, `expired`, and `expires_in_seconds` — never `value`.

## CLI (`meta_token_vault.cli`)

Console script `meta-token-vault` operates against a local SQLite store:

```
meta-token-vault [--db PATH] list   <app_id>
meta-token-vault [--db PATH] get    <app_id>
meta-token-vault [--db PATH] rotate <app_id>
```

`--db` defaults to `meta_token_vault.db`. `get` exits non-zero when there is no
active token; `rotate` reports that no refresher is configured (it never calls
Meta's API) rather than reaching the network. `main(argv=None) -> int` is the
programmatic entry point.

---

Part of the **Trust Stack** — trustworthy infrastructure for AI agents.
