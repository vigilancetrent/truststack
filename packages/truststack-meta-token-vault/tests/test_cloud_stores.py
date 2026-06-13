"""Offline tests for the production cloud / database token stores.

* AWS Secrets Manager is exercised against a real (mocked) service via
  ``moto``'s ``mock_aws``; skipped if ``moto``/``boto3`` are unavailable.
* Azure Key Vault, HashiCorp Vault, and Postgres are exercised with
  ``unittest.mock`` doubles that emulate the client/pool surface the stores
  use, so no live services or even the heavy client libraries are required.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from meta_token_vault import (
    AwsSecretsManagerTokenStore,
    AzureKeyVaultTokenStore,
    FernetEncryptor,
    HashiCorpVaultTokenStore,
    NoopEncryptor,
    PostgresTokenStore,
    Token,
)
from meta_token_vault.stores import _encode_token_json


def _token(
    app_id: str = "app-1",
    *,
    value: str = "secret",
    expires_in: float | None = 86400,
    issued_at: datetime | None = None,
) -> Token:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=expires_in) if expires_in is not None else None
    extra = {"issued_at": issued_at} if issued_at is not None else {}
    return Token(value=value, app_id=app_id, scopes=["a", "b"], expires_at=expires_at, **extra)


# ---------------------------------------------------------------------------
# AWS Secrets Manager via moto
# ---------------------------------------------------------------------------

moto = pytest.importorskip("moto", reason="moto/boto3 not installed")


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SECURITY_TOKEN",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.setenv(var, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


async def test_aws_put_get_active_round_trip(aws_credentials: None) -> None:
    from moto import mock_aws

    with mock_aws():
        store = AwsSecretsManagerTokenStore("mtv-test", region_name="us-east-1")
        token = _token(value="EAAG-top-secret")
        await store.put(token)

        active = await store.get_active("app-1")
        assert active is not None
        assert active.id == token.id
        assert active.value == "EAAG-top-secret"
        assert active.scopes == ["a", "b"]


async def test_aws_put_is_idempotent_upsert(aws_credentials: None) -> None:
    from moto import mock_aws

    with mock_aws():
        store = AwsSecretsManagerTokenStore("mtv-test", region_name="us-east-1")
        token = _token(value="first")
        await store.put(token)
        # Same id -> hits the ResourceExistsException -> put_secret_value branch.
        updated = Token(
            id=token.id, value="second", app_id=token.app_id, expires_at=token.expires_at
        )
        await store.put(updated)

        rows = await store.all("app-1")
        assert len(rows) == 1
        assert rows[0].value == "second"


async def test_aws_all_filters_by_app_tag(aws_credentials: None) -> None:
    from moto import mock_aws

    with mock_aws():
        store = AwsSecretsManagerTokenStore("mtv-test", region_name="us-east-1")
        await store.put(_token("app-1", value="one"))
        await store.put(_token("app-2", value="two"))

        rows1 = await store.all("app-1")
        rows2 = await store.all("app-2")
        assert [t.value for t in rows1] == ["one"]
        assert [t.value for t in rows2] == ["two"]
        assert await store.get_active("missing") is None


async def test_aws_all_orders_newest_first(aws_credentials: None) -> None:
    from moto import mock_aws

    now = datetime.now(UTC)
    with mock_aws():
        store = AwsSecretsManagerTokenStore("mtv-test", region_name="us-east-1")
        older = _token(value="older", issued_at=now - timedelta(hours=2))
        newer = _token(value="newer", issued_at=now)
        await store.put(older)
        await store.put(newer)
        rows = await store.all("app-1")
        assert [t.value for t in rows] == ["newer", "older"]


async def test_aws_unexpected_client_error_propagates(
    fake_botocore_client_error: type[Exception],
) -> None:
    client = MagicMock()
    client.create_secret.side_effect = fake_botocore_client_error("AccessDeniedException")
    store = AwsSecretsManagerTokenStore("p", client=client)
    with pytest.raises(Exception, match="AccessDeniedException"):
        await store.put(_token())


async def test_aws_existing_secret_falls_back_to_put_value(
    fake_botocore_client_error: type[Exception],
) -> None:
    client = MagicMock()
    client.create_secret.side_effect = fake_botocore_client_error("ResourceExistsException")
    store = AwsSecretsManagerTokenStore("p", client=client)
    await store.put(_token())
    client.put_secret_value.assert_called_once()


async def test_aws_with_fernet_encrypts_at_rest(aws_credentials: None) -> None:
    pytest.importorskip("cryptography")
    from moto import mock_aws

    with mock_aws():
        enc = FernetEncryptor(FernetEncryptor.generate_key())
        store = AwsSecretsManagerTokenStore("mtv-enc", encryptor=enc, region_name="us-east-1")
        token = _token(value="ultra-secret")
        await store.put(token)
        # Read the raw SecretString straight from the mocked service.
        raw = store._get_client().get_secret_value(
            SecretId=store._secret_name(token.app_id, token.id)
        )["SecretString"]
        assert "ultra-secret" not in raw
        fetched = await store.get_active("app-1")
        assert fetched is not None
        assert fetched.value == "ultra-secret"


# ---------------------------------------------------------------------------
# Azure Key Vault via MagicMock-patched SecretClient
# ---------------------------------------------------------------------------


class _FakeAzureSecret:
    def __init__(self, value: str, name: str) -> None:
        self.value = value
        self.name = name


class _FakeAzureProperties:
    def __init__(self, name: str, tags: dict[str, str]) -> None:
        self.name = name
        self.tags = tags


class _FakeAzureClient:
    """Minimal in-memory emulation of ``azure.keyvault.secrets.SecretClient``."""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}
        self._tags: dict[str, dict[str, str]] = {}

    def set_secret(self, name: str, value: str, *, tags: dict[str, str] | None = None) -> None:
        self._secrets[name] = value
        self._tags[name] = tags or {}

    def list_properties_of_secrets(self) -> list[_FakeAzureProperties]:
        return [_FakeAzureProperties(n, self._tags[n]) for n in self._secrets]

    def get_secret(self, name: str) -> _FakeAzureSecret:
        return _FakeAzureSecret(self._secrets[name], name)


async def test_azure_put_get_active_round_trip() -> None:
    client = _FakeAzureClient()
    store = AzureKeyVaultTokenStore("https://v.vault.azure.net", client=client)
    token = _token(value="azure-secret")
    await store.put(token)

    active = await store.get_active("app-1")
    assert active is not None
    assert active.value == "azure-secret"
    assert active.scopes == ["a", "b"]


async def test_azure_all_filters_by_tag_and_orders() -> None:
    now = datetime.now(UTC)
    client = _FakeAzureClient()
    store = AzureKeyVaultTokenStore("https://v.vault.azure.net", client=client)
    await store.put(_token("app-1", value="older", issued_at=now - timedelta(hours=1)))
    await store.put(_token("app-1", value="newer", issued_at=now))
    await store.put(_token("app-2", value="other"))

    rows = await store.all("app-1")
    assert [t.value for t in rows] == ["newer", "older"]
    assert await store.all("app-2") and (await store.all("app-2"))[0].value == "other"


async def test_azure_ignores_untagged_secret() -> None:
    client = _FakeAzureClient()
    # A secret with no matching tag must be skipped, not decoded.
    client.set_secret("foreign", "not-json", tags={})
    store = AzureKeyVaultTokenStore("https://v.vault.azure.net", client=client)
    assert await store.all("app-1") == []


async def test_azure_lazy_client_built_from_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    fake_client = _FakeAzureClient()
    captured: dict[str, Any] = {}

    identity_mod = types.ModuleType("azure.identity")
    identity_mod.DefaultAzureCredential = MagicMock(name="DefaultAzureCredential")  # type: ignore[attr-defined]
    secrets_mod = types.ModuleType("azure.keyvault.secrets")

    def _secret_client(*, vault_url: str, credential: Any) -> _FakeAzureClient:
        captured["vault_url"] = vault_url
        captured["credential"] = credential
        return fake_client

    secrets_mod.SecretClient = _secret_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "azure.identity", identity_mod)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", secrets_mod)

    store = AzureKeyVaultTokenStore("https://v.vault.azure.net")
    await store.put(_token(value="lazy"))
    assert captured["vault_url"] == "https://v.vault.azure.net"
    assert (await store.get_active("app-1")).value == "lazy"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# HashiCorp Vault via MagicMock-patched hvac client
# ---------------------------------------------------------------------------


def _make_hvac_client() -> MagicMock:
    client = MagicMock()
    store: dict[str, dict[str, Any]] = {}

    def create_or_update(*, path: str, secret: dict[str, Any], mount_point: str) -> None:
        store[path] = secret

    def list_secrets(*, path: str, mount_point: str) -> dict[str, Any]:
        prefix = path.rstrip("/") + "/"
        keys = [p[len(prefix) :] for p in store if p.startswith(prefix)]
        if not keys:
            from hvac.exceptions import InvalidPath

            raise InvalidPath(path)
        return {"data": {"keys": keys}}

    def read_secret_version(*, path: str, mount_point: str) -> dict[str, Any]:
        return {"data": {"data": store[path]}}

    client.secrets.kv.v2.create_or_update_secret.side_effect = create_or_update
    client.secrets.kv.v2.list_secrets.side_effect = list_secrets
    client.secrets.kv.v2.read_secret_version.side_effect = read_secret_version
    return client


async def test_hvac_put_get_active_round_trip(fake_hvac_invalid_path: type[Exception]) -> None:
    client = _make_hvac_client()
    store = HashiCorpVaultTokenStore(client=client)
    token = _token(value="hvac-secret")
    await store.put(token)

    active = await store.get_active("app-1")
    assert active is not None
    assert active.value == "hvac-secret"


async def test_hvac_all_orders_and_skips_subdirs(
    fake_hvac_invalid_path: type[Exception],
) -> None:
    now = datetime.now(UTC)
    client = _make_hvac_client()
    store = HashiCorpVaultTokenStore(client=client)
    await store.put(_token(value="older", issued_at=now - timedelta(hours=1)))
    await store.put(_token(value="newer", issued_at=now))

    # Inject a subdirectory-style key (ends with "/") that must be skipped.
    original = client.secrets.kv.v2.list_secrets.side_effect

    def with_subdir(*, path: str, mount_point: str) -> dict[str, Any]:
        result = original(path=path, mount_point=mount_point)
        result["data"]["keys"] = [*result["data"]["keys"], "nested/"]
        return result

    client.secrets.kv.v2.list_secrets.side_effect = with_subdir
    rows = await store.all("app-1")
    assert [t.value for t in rows] == ["newer", "older"]


async def test_hvac_all_returns_empty_on_invalid_path(
    fake_hvac_invalid_path: type[Exception],
) -> None:
    client = _make_hvac_client()
    store = HashiCorpVaultTokenStore(client=client)
    assert await store.all("never-written") == []
    assert await store.get_active("never-written") is None


async def test_hvac_lazy_client_built(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    captured: dict[str, Any] = {}
    fake_client = _make_hvac_client()

    hvac_mod = types.ModuleType("hvac")

    def _client(*, url: str | None, token: str | None) -> MagicMock:
        captured["url"] = url
        captured["token"] = token
        return fake_client

    hvac_mod.Client = _client  # type: ignore[attr-defined]
    exceptions_mod = types.ModuleType("hvac.exceptions")
    exceptions_mod.InvalidPath = type("InvalidPath", (Exception,), {})  # type: ignore[attr-defined]
    hvac_mod.exceptions = exceptions_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hvac", hvac_mod)
    monkeypatch.setitem(sys.modules, "hvac.exceptions", exceptions_mod)

    store = HashiCorpVaultTokenStore("http://127.0.0.1:8200", "root-token")
    await store.put(_token(value="lazy-hvac"))
    assert captured == {"url": "http://127.0.0.1:8200", "token": "root-token"}
    assert (await store.get_active("app-1")).value == "lazy-hvac"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Postgres via AsyncMock asyncpg pool / connection
# ---------------------------------------------------------------------------


class _FakeAcquire:
    """Async context manager returning a mock connection from ``pool.acquire()``."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        return None


def _make_pg_pool() -> tuple[MagicMock, AsyncMock]:
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=lambda: _FakeAcquire(conn))
    return pool, conn


async def test_postgres_put_executes_upsert() -> None:
    pool, conn = _make_pg_pool()
    store = PostgresTokenStore(pool=pool)
    token = _token(value="pg-secret")
    await store.put(token)

    conn.execute.assert_awaited()
    args = conn.execute.await_args.args
    assert "INSERT INTO meta_token_vault_tokens" in args[0]
    # Positional bind params: id, app_id, encrypted-value, scopes, issued_at, expires_at
    assert args[1] == token.id
    assert args[2] == token.app_id
    assert args[3] == "pg-secret"  # Noop encryptor passes through
    assert args[4] == ["a", "b"]


async def test_postgres_all_decodes_rows() -> None:
    pool, conn = _make_pg_pool()
    token = _token(value="pg-row")
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": token.id,
                "app_id": token.app_id,
                "value": "pg-row",
                "scopes": ["a", "b"],
                "issued_at": token.issued_at,
                "expires_at": token.expires_at,
            }
        ]
    )
    store = PostgresTokenStore(pool=pool)
    rows = await store.all("app-1")
    assert len(rows) == 1
    assert rows[0].value == "pg-row"
    assert rows[0].scopes == ["a", "b"]

    active = await store.get_active("app-1")
    assert active is not None and active.id == token.id


async def test_postgres_all_empty() -> None:
    pool, _ = _make_pg_pool()
    store = PostgresTokenStore(pool=pool)
    assert await store.all("app-1") == []
    assert await store.get_active("app-1") is None


async def test_postgres_row_with_null_scopes() -> None:
    pool, conn = _make_pg_pool()
    now = datetime.now(UTC)
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "abc",
                "app_id": "app-1",
                "value": "v",
                "scopes": None,
                "issued_at": now,
                "expires_at": None,
            }
        ]
    )
    store = PostgresTokenStore(pool=pool)
    rows = await store.all("app-1")
    assert rows[0].scopes == []
    assert rows[0].expires_at is None


async def test_postgres_with_fernet_encrypts_value() -> None:
    pytest.importorskip("cryptography")
    pool, conn = _make_pg_pool()
    enc = FernetEncryptor(FernetEncryptor.generate_key())
    store = PostgresTokenStore(pool=pool, encryptor=enc)
    await store.put(_token(value="cleartext"))
    stored_value = conn.execute.await_args.args[3]
    assert stored_value != "cleartext"
    assert enc.decrypt(stored_value) == "cleartext"


async def test_postgres_injected_pool_skips_schema_creation() -> None:
    # With a pre-built pool the store is considered initialised: no CREATE TABLE.
    pool, conn = _make_pg_pool()
    store = PostgresTokenStore(pool=pool)
    await store.all("app-1")
    executed_sql = [call.args[0] for call in conn.execute.await_args_list]
    assert not any("CREATE TABLE" in sql for sql in executed_sql)


async def test_postgres_lazy_pool_creation_and_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    pool, conn = _make_pg_pool()
    created: dict[str, Any] = {}

    async def _create_pool(*, dsn: str) -> MagicMock:
        created["dsn"] = dsn
        return pool

    asyncpg_mod = types.ModuleType("asyncpg")
    asyncpg_mod.create_pool = _create_pool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_mod)

    store = PostgresTokenStore("postgres://localhost/db")
    await store.put(_token(value="lazy-pg"))
    assert created["dsn"] == "postgres://localhost/db"
    executed_sql = [call.args[0] for call in conn.execute.await_args_list]
    assert any("CREATE TABLE IF NOT EXISTS" in sql for sql in executed_sql)
    # Schema is created only once across calls.
    await store.all("app-1")
    all_sql = [call.args[0] for call in conn.execute.await_args_list]
    create_calls = [sql for sql in all_sql if "CREATE TABLE" in sql]
    assert len(create_calls) == 1


async def test_postgres_requires_dsn_or_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    asyncpg_mod = types.ModuleType("asyncpg")
    asyncpg_mod.create_pool = AsyncMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_mod)

    store = PostgresTokenStore()  # no dsn, no pool
    with pytest.raises(ValueError, match="requires a dsn or a pool"):
        await store.all("app-1")


def test_postgres_custom_table_name_used() -> None:
    store = PostgresTokenStore("postgres://localhost/db", table="custom_tokens")
    assert store._table == "custom_tokens"


def test_encode_round_trips_token_without_expiry() -> None:
    token = _token(expires_in=None)
    raw = _encode_token_json(token, NoopEncryptor())
    assert '"expires_at": null' in raw
