"""Tests for token stores and encryptors."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meta_token_vault import (
    AwsSecretsManagerTokenStore,
    FernetEncryptor,
    InMemoryTokenStore,
    NoopEncryptor,
    PostgresTokenStore,
    SqliteTokenStore,
    Token,
)
from meta_token_vault.stores import (
    AzureKeyVaultTokenStore,
    HashiCorpVaultTokenStore,
)

_HAS_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None


def _token(app_id: str = "app-1", *, value: str = "secret", expires_in: float = 86400) -> Token:
    return Token(
        value=value,
        app_id=app_id,
        scopes=["whatsapp_business_messaging"],
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
    )


async def test_in_memory_get_active_picks_newest() -> None:
    store = InMemoryTokenStore()
    old = _token(value="old")
    await store.put(old)
    new = _token(value="new")
    await store.put(new)

    active = await store.get_active("app-1")
    assert active is not None
    assert active.value == "new"
    assert len(await store.all("app-1")) == 2


async def test_in_memory_get_active_none_when_all_expired() -> None:
    store = InMemoryTokenStore()
    await store.put(_token(expires_in=-5))
    assert await store.get_active("app-1") is None


async def test_sqlite_round_trip_with_noop(tmp_path: Path) -> None:
    db = tmp_path / "vault.db"
    store = SqliteTokenStore(db, encryptor=NoopEncryptor())
    token = _token()
    await store.put(token)

    fetched = await store.get_active("app-1")
    assert fetched is not None
    assert fetched.id == token.id
    assert fetched.value == "secret"
    assert fetched.scopes == ["whatsapp_business_messaging"]


async def test_sqlite_upsert_replaces(tmp_path: Path) -> None:
    db = tmp_path / "vault.db"
    store = SqliteTokenStore(db)
    token = _token(value="first")
    await store.put(token)
    updated = Token(
        id=token.id,
        value="second",
        app_id=token.app_id,
        expires_at=token.expires_at,
    )
    await store.put(updated)
    rows = await store.all("app-1")
    assert len(rows) == 1
    assert rows[0].value == "second"


@pytest.mark.skipif(not _HAS_CRYPTOGRAPHY, reason="cryptography not installed")
async def test_sqlite_encrypts_value_at_rest(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "vault.db"
    enc = FernetEncryptor(FernetEncryptor.generate_key())
    store = SqliteTokenStore(db, encryptor=enc)
    await store.put(_token(value="topsecret"))

    # Raw row must not contain the plaintext.
    with sqlite3.connect(str(db)) as conn:
        raw_value = conn.execute("SELECT value FROM tokens").fetchone()[0]
    assert "topsecret" not in raw_value

    fetched = await store.get_active("app-1")
    assert fetched is not None
    assert fetched.value == "topsecret"


def test_noop_encryptor_round_trip() -> None:
    enc = NoopEncryptor()
    assert enc.decrypt(enc.encrypt("hello")) == "hello"


@pytest.mark.skipif(not _HAS_CRYPTOGRAPHY, reason="cryptography not installed")
def test_fernet_encryptor_round_trip() -> None:
    enc = FernetEncryptor(FernetEncryptor.generate_key())
    ciphertext = enc.encrypt("hello")
    assert ciphertext != "hello"
    assert enc.decrypt(ciphertext) == "hello"


def test_cloud_stores_construct_without_client_libraries() -> None:
    # Constructing a cloud store must not import or require its heavy client
    # library; clients are built lazily only when a method is actually called.
    assert AwsSecretsManagerTokenStore("prefix") is not None
    assert AzureKeyVaultTokenStore("https://v.vault.azure.net") is not None
    assert HashiCorpVaultTokenStore("http://localhost:8200", "tok") is not None
    assert PostgresTokenStore("postgres://localhost/db") is not None


def test_postgres_store_rejects_invalid_table_name() -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        PostgresTokenStore("postgres://localhost/db", table="bad table")
