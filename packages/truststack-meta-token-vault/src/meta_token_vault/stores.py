"""Token storage backends.

The :class:`TokenStore` protocol defines async persistence for tokens. Two
infrastructure-free backends are provided:

* :class:`InMemoryTokenStore` -- the default, ideal for tests and ephemeral use.
* :class:`SqliteTokenStore` -- durable local persistence on stdlib ``sqlite3``,
  driven off the event loop via ``asyncio.to_thread``, with token *values*
  encrypted at rest through an :class:`~meta_token_vault.encryption.Encryptor`.

Production cloud / database backends are also provided and fully implemented:

* :class:`AwsSecretsManagerTokenStore` -- AWS Secrets Manager (extra ``aws``).
* :class:`AzureKeyVaultTokenStore` -- Azure Key Vault (extra ``azure``).
* :class:`HashiCorpVaultTokenStore` -- HashiCorp Vault KV v2 (extra ``hvac``).
* :class:`PostgresTokenStore` -- PostgreSQL via ``asyncpg`` (extra ``postgres``).

Each cloud backend stores token *values* encrypted at rest using the configured
:class:`~meta_token_vault.encryption.Encryptor`, serialises the remaining token
metadata as JSON, and imports its heavy client library lazily so the package
remains importable with only its required dependencies installed.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .encryption import Encryptor, NoopEncryptor
from .models import Token

if TYPE_CHECKING:
    import asyncpg


@runtime_checkable
class TokenStore(Protocol):
    """Async persistence contract for tokens."""

    async def put(self, token: Token) -> None:
        """Persist ``token`` (insert or replace by id)."""
        ...

    async def get_active(self, app_id: str) -> Token | None:
        """Return the newest non-expired token for ``app_id``, or ``None``."""
        ...

    async def all(self, app_id: str) -> list[Token]:
        """Return every token for ``app_id`` ordered newest-issued first."""
        ...


def _select_active(tokens: list[Token], now: datetime | None = None) -> Token | None:
    """Pick the most recently issued, non-expired token from ``tokens``."""
    candidates = [t for t in tokens if not t.is_expired(now)]
    if not candidates:
        return None
    return max(candidates, key=lambda t: t.issued_at)


def _encode_token(token: Token, encryptor: Encryptor) -> dict[str, Any]:
    """Serialise ``token`` to a JSON-safe dict with the value encrypted at rest."""
    return {
        "id": token.id,
        "app_id": token.app_id,
        "value": encryptor.encrypt(token.value),
        "scopes": list(token.scopes),
        "issued_at": token.issued_at.isoformat(),
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
    }


def _decode_token(payload: dict[str, Any], encryptor: Encryptor) -> Token:
    """Reverse :func:`_encode_token`, decrypting the stored value."""
    expires_raw = payload.get("expires_at")
    return Token(
        id=str(payload["id"]),
        value=encryptor.decrypt(str(payload["value"])),
        app_id=str(payload["app_id"]),
        scopes=list(payload.get("scopes") or []),
        issued_at=datetime.fromisoformat(str(payload["issued_at"])),
        expires_at=datetime.fromisoformat(str(expires_raw)) if expires_raw else None,
    )


def _encode_token_json(token: Token, encryptor: Encryptor) -> str:
    """Serialise ``token`` to a JSON string with the value encrypted at rest."""
    return json.dumps(_encode_token(token, encryptor))


def _decode_token_json(raw: str, encryptor: Encryptor) -> Token:
    """Reverse :func:`_encode_token_json`."""
    return _decode_token(json.loads(raw), encryptor)


class InMemoryTokenStore:
    """Thread-safe in-memory token store. Zero external dependencies.

    Tokens are kept in a dict keyed by id. Suitable for tests and short-lived
    processes; data is lost on restart.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._tokens: dict[str, Token] = {}

    async def put(self, token: Token) -> None:
        with self._lock:
            self._tokens[token.id] = token

    async def get_active(self, app_id: str) -> Token | None:
        return _select_active(await self.all(app_id))

    async def all(self, app_id: str) -> list[Token]:
        with self._lock:
            tokens = [t for t in self._tokens.values() if t.app_id == app_id]
        return sorted(tokens, key=lambda t: t.issued_at, reverse=True)


class SqliteTokenStore:
    """Durable token store backed by stdlib ``sqlite3``.

    Token *values* are encrypted at rest using the supplied ``encryptor`` (default
    :class:`NoopEncryptor`, which is DEV ONLY). All blocking sqlite calls run in a
    worker thread via :func:`asyncio.to_thread` so the event loop is never blocked.

    A process-wide :class:`threading.Lock` serialises writes, keeping the store
    safe under concurrent ``put`` calls within a single process.
    """

    def __init__(self, path: str | Path, encryptor: Encryptor | None = None) -> None:
        self._path = str(path)
        self._encryptor: Encryptor = encryptor or NoopEncryptor()
        self._lock = Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    id          TEXT PRIMARY KEY,
                    app_id      TEXT NOT NULL,
                    value       TEXT NOT NULL,
                    scopes      TEXT NOT NULL,
                    issued_at   TEXT NOT NULL,
                    expires_at  TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_app_id ON tokens (app_id)")

    def _row_to_token(self, row: sqlite3.Row) -> Token:
        scopes = [s for s in str(row["scopes"]).split(",") if s]
        expires_raw = row["expires_at"]
        return Token(
            id=str(row["id"]),
            value=self._encryptor.decrypt(str(row["value"])),
            app_id=str(row["app_id"]),
            scopes=scopes,
            issued_at=datetime.fromisoformat(str(row["issued_at"])),
            expires_at=datetime.fromisoformat(str(expires_raw)) if expires_raw else None,
        )

    def _put_sync(self, token: Token) -> None:
        encrypted = self._encryptor.encrypt(token.value)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tokens (id, app_id, value, scopes, issued_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    app_id=excluded.app_id,
                    value=excluded.value,
                    scopes=excluded.scopes,
                    issued_at=excluded.issued_at,
                    expires_at=excluded.expires_at
                """,
                (
                    token.id,
                    token.app_id,
                    encrypted,
                    ",".join(token.scopes),
                    token.issued_at.isoformat(),
                    token.expires_at.isoformat() if token.expires_at else None,
                ),
            )

    def _all_sync(self, app_id: str) -> list[Token]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tokens WHERE app_id = ? ORDER BY issued_at DESC",
                (app_id,),
            ).fetchall()
        return [self._row_to_token(row) for row in rows]

    async def put(self, token: Token) -> None:
        await asyncio.to_thread(self._put_sync, token)

    async def get_active(self, app_id: str) -> Token | None:
        return _select_active(await self.all(app_id))

    async def all(self, app_id: str) -> list[Token]:
        return await asyncio.to_thread(self._all_sync, app_id)


class AwsSecretsManagerTokenStore:
    """Token store backed by AWS Secrets Manager (requires the ``aws`` extra).

    Each token is stored as a JSON document in a dedicated secret named
    ``{prefix}/{app_id}/{token_id}`` whose ``SecretString`` is the encrypted,
    serialised token. Listing tokens for an ``app_id`` filters secrets by the
    ``meta_token_vault:app_id`` resource tag.

    ``boto3`` is imported lazily so the package is importable without it. All
    blocking ``boto3`` calls run in a worker thread via :func:`asyncio.to_thread`.

    :param prefix: secret-name prefix / namespace (default ``meta-token-vault``).
    :param encryptor: at-rest encryptor for token values.
    :param region_name: optional AWS region passed to the client.
    :param client: a pre-built boto3 ``secretsmanager`` client (mainly for tests);
        when omitted one is created lazily on first use.
    """

    _SERVICE = "secretsmanager"
    _APP_TAG = "meta_token_vault:app_id"
    _MANAGED_TAG = "meta_token_vault:managed"

    def __init__(
        self,
        prefix: str = "meta-token-vault",
        encryptor: Encryptor | None = None,
        *,
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._prefix = prefix.rstrip("/")
        self._encryptor: Encryptor = encryptor or NoopEncryptor()
        self._region_name = region_name
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client(self._SERVICE, region_name=self._region_name)
        return self._client

    def _secret_name(self, app_id: str, token_id: str) -> str:
        return f"{self._prefix}/{app_id}/{token_id}"

    def _put_sync(self, token: Token) -> None:
        from botocore.exceptions import ClientError

        client = self._get_client()
        name = self._secret_name(token.app_id, token.id)
        payload = _encode_token_json(token, self._encryptor)
        tags = [
            {"Key": self._APP_TAG, "Value": token.app_id},
            {"Key": self._MANAGED_TAG, "Value": "true"},
        ]
        try:
            client.create_secret(Name=name, SecretString=payload, Tags=tags)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code != "ResourceExistsException":
                raise
            client.put_secret_value(SecretId=name, SecretString=payload)

    def _all_sync(self, app_id: str) -> list[Token]:
        client = self._get_client()
        paginator = client.get_paginator("list_secrets")
        pages = paginator.paginate(
            Filters=[{"Key": "tag-value", "Values": [app_id]}],
        )
        tokens: list[Token] = []
        for page in pages:
            for entry in page.get("SecretList", []):
                tag_map = {t["Key"]: t["Value"] for t in entry.get("Tags", [])}
                if tag_map.get(self._APP_TAG) != app_id:
                    continue
                detail = client.get_secret_value(SecretId=entry["ARN"])
                tokens.append(_decode_token_json(detail["SecretString"], self._encryptor))
        return sorted(tokens, key=lambda t: t.issued_at, reverse=True)

    async def put(self, token: Token) -> None:
        await asyncio.to_thread(self._put_sync, token)

    async def get_active(self, app_id: str) -> Token | None:
        return _select_active(await self.all(app_id))

    async def all(self, app_id: str) -> list[Token]:
        return await asyncio.to_thread(self._all_sync, app_id)


class AzureKeyVaultTokenStore:
    """Token store backed by Azure Key Vault secrets (requires the ``azure`` extra).

    Each token is stored as a secret named ``{prefix}-{app_id}-{token_id}`` whose
    value is the encrypted, serialised token. The ``app_id`` is also stored in the
    secret's tags so tokens for an app can be enumerated.

    ``azure-keyvault-secrets`` and ``azure-identity`` are imported lazily. The
    Azure SDK's ``SecretClient`` is synchronous, so blocking calls run in a worker
    thread via :func:`asyncio.to_thread`.

    :param vault_url: the Key Vault URL (e.g. ``https://my-vault.vault.azure.net``).
    :param encryptor: at-rest encryptor for token values.
    :param prefix: secret-name prefix (default ``mtv``); Key Vault secret names may
        only contain alphanumerics and dashes.
    :param credential: an Azure credential; defaults to ``DefaultAzureCredential``.
    :param client: a pre-built ``SecretClient`` (mainly for tests).
    """

    _APP_TAG = "meta_token_vault_app_id"
    _MANAGED_TAG = "meta_token_vault_managed"

    def __init__(
        self,
        vault_url: str,
        encryptor: Encryptor | None = None,
        *,
        prefix: str = "mtv",
        credential: Any | None = None,
        client: Any | None = None,
    ) -> None:
        self._vault_url = vault_url
        self._encryptor: Encryptor = encryptor or NoopEncryptor()
        self._prefix = prefix
        self._credential = credential
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            credential = self._credential or DefaultAzureCredential()
            self._client = SecretClient(vault_url=self._vault_url, credential=credential)
        return self._client

    def _secret_name(self, app_id: str, token_id: str) -> str:
        return f"{self._prefix}-{app_id}-{token_id}"

    def _put_sync(self, token: Token) -> None:
        client = self._get_client()
        name = self._secret_name(token.app_id, token.id)
        payload = _encode_token_json(token, self._encryptor)
        client.set_secret(
            name,
            payload,
            tags={self._APP_TAG: token.app_id, self._MANAGED_TAG: "true"},
        )

    def _all_sync(self, app_id: str) -> list[Token]:
        client = self._get_client()
        tokens: list[Token] = []
        for prop in client.list_properties_of_secrets():
            tags = prop.tags or {}
            if tags.get(self._APP_TAG) != app_id:
                continue
            secret = client.get_secret(prop.name)
            tokens.append(_decode_token_json(secret.value, self._encryptor))
        return sorted(tokens, key=lambda t: t.issued_at, reverse=True)

    async def put(self, token: Token) -> None:
        await asyncio.to_thread(self._put_sync, token)

    async def get_active(self, app_id: str) -> Token | None:
        return _select_active(await self.all(app_id))

    async def all(self, app_id: str) -> list[Token]:
        return await asyncio.to_thread(self._all_sync, app_id)


class HashiCorpVaultTokenStore:
    """Token store backed by HashiCorp Vault KV v2 (requires the ``hvac`` extra).

    Each token is written to ``{mount}/{path_prefix}/{app_id}/{token_id}`` as a
    KV v2 secret whose ``data`` holds the encrypted, serialised token document.
    Tokens for an app are enumerated by listing the ``{app_id}`` directory.

    ``hvac`` is imported lazily; its client is synchronous, so blocking calls run
    in a worker thread via :func:`asyncio.to_thread`.

    :param url: Vault server URL (e.g. ``http://127.0.0.1:8200``).
    :param token: Vault auth token.
    :param encryptor: at-rest encryptor for token values.
    :param mount_point: KV v2 secrets-engine mount (default ``secret``).
    :param path_prefix: path namespace under the mount (default ``meta-token-vault``).
    :param client: a pre-built ``hvac.Client`` (mainly for tests).
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        encryptor: Encryptor | None = None,
        *,
        mount_point: str = "secret",
        path_prefix: str = "meta-token-vault",
        client: Any | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._encryptor: Encryptor = encryptor or NoopEncryptor()
        self._mount_point = mount_point
        self._path_prefix = path_prefix.strip("/")
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import hvac

            self._client = hvac.Client(url=self._url, token=self._token)
        return self._client

    def _app_dir(self, app_id: str) -> str:
        return f"{self._path_prefix}/{app_id}"

    def _secret_path(self, app_id: str, token_id: str) -> str:
        return f"{self._app_dir(app_id)}/{token_id}"

    def _put_sync(self, token: Token) -> None:
        client = self._get_client()
        client.secrets.kv.v2.create_or_update_secret(
            path=self._secret_path(token.app_id, token.id),
            secret={"token": _encode_token_json(token, self._encryptor)},
            mount_point=self._mount_point,
        )

    def _all_sync(self, app_id: str) -> list[Token]:
        from hvac.exceptions import InvalidPath

        client = self._get_client()
        try:
            listing = client.secrets.kv.v2.list_secrets(
                path=self._app_dir(app_id),
                mount_point=self._mount_point,
            )
        except InvalidPath:
            return []
        keys = listing.get("data", {}).get("keys", [])
        tokens: list[Token] = []
        for key in keys:
            if key.endswith("/"):
                continue
            secret = client.secrets.kv.v2.read_secret_version(
                path=self._secret_path(app_id, key),
                mount_point=self._mount_point,
            )
            raw = secret["data"]["data"]["token"]
            tokens.append(_decode_token_json(raw, self._encryptor))
        return sorted(tokens, key=lambda t: t.issued_at, reverse=True)

    async def put(self, token: Token) -> None:
        await asyncio.to_thread(self._put_sync, token)

    async def get_active(self, app_id: str) -> Token | None:
        return _select_active(await self.all(app_id))

    async def all(self, app_id: str) -> list[Token]:
        return await asyncio.to_thread(self._all_sync, app_id)


class PostgresTokenStore:
    """Token store backed by PostgreSQL via ``asyncpg`` (requires ``postgres`` extra).

    Tokens are stored in a ``tokens`` table (configurable name) with the value
    column encrypted at rest by the configured encryptor. ``asyncpg`` is imported
    lazily and a connection pool is created on first use.

    :param dsn: PostgreSQL DSN (e.g. ``postgres://user:pass@host/db``).
    :param encryptor: at-rest encryptor for token values.
    :param table: table name (default ``meta_token_vault_tokens``).
    :param pool: a pre-built ``asyncpg`` pool (mainly for tests); when supplied the
        DSN is ignored and the pool is assumed already initialised.
    """

    def __init__(
        self,
        dsn: str | None = None,
        encryptor: Encryptor | None = None,
        *,
        table: str = "meta_token_vault_tokens",
        pool: asyncpg.Pool | None = None,
    ) -> None:
        if not table.isidentifier():
            raise ValueError(f"Invalid table name: {table!r}")
        self._dsn = dsn
        self._encryptor: Encryptor = encryptor or NoopEncryptor()
        self._table = table
        self._pool: asyncpg.Pool | None = pool
        self._initialised = pool is not None
        self._init_lock = asyncio.Lock()

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            import asyncpg

            if self._dsn is None:
                raise ValueError("PostgresTokenStore requires a dsn or a pool")
            self._pool = await asyncpg.create_pool(dsn=self._dsn)
        return self._pool

    async def _ensure_schema(self) -> None:
        if self._initialised:
            return
        async with self._init_lock:
            if self._initialised:
                return
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        id          TEXT PRIMARY KEY,
                        app_id      TEXT NOT NULL,
                        value       TEXT NOT NULL,
                        scopes      TEXT[] NOT NULL DEFAULT '{{}}',
                        issued_at   TIMESTAMPTZ NOT NULL,
                        expires_at  TIMESTAMPTZ
                    )
                    """
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._table}_app_id ON {self._table} (app_id)"
                )
            self._initialised = True

    def _row_to_token(self, row: Any) -> Token:
        return Token(
            id=str(row["id"]),
            value=self._encryptor.decrypt(str(row["value"])),
            app_id=str(row["app_id"]),
            scopes=list(row["scopes"] or []),
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
        )

    async def put(self, token: Token) -> None:
        await self._ensure_schema()
        pool = await self._get_pool()
        encrypted = self._encryptor.encrypt(token.value)
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table} (id, app_id, value, scopes, issued_at, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO UPDATE SET
                    app_id=excluded.app_id,
                    value=excluded.value,
                    scopes=excluded.scopes,
                    issued_at=excluded.issued_at,
                    expires_at=excluded.expires_at
                """,
                token.id,
                token.app_id,
                encrypted,
                list(token.scopes),
                token.issued_at,
                token.expires_at,
            )

    async def get_active(self, app_id: str) -> Token | None:
        return _select_active(await self.all(app_id))

    async def all(self, app_id: str) -> list[Token]:
        await self._ensure_schema()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {self._table} WHERE app_id = $1 ORDER BY issued_at DESC",
                app_id,
            )
        return [self._row_to_token(row) for row in rows]


__all__ = [
    "AwsSecretsManagerTokenStore",
    "AzureKeyVaultTokenStore",
    "HashiCorpVaultTokenStore",
    "InMemoryTokenStore",
    "PostgresTokenStore",
    "SqliteTokenStore",
    "TokenStore",
]
