"""Tests for the FastAPI dashboard API exposed by ``create_app``."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from meta_token_vault import Token, Vault

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
pytest.importorskip("httpx", reason="httpx (TestClient transport) not installed")

from fastapi.testclient import TestClient  # noqa: E402

from meta_token_vault.api import create_app  # noqa: E402


def _make_token(app_id: str = "app-1", *, value: str = "secret-value") -> Token:
    return Token(
        value=value,
        app_id=app_id,
        scopes=["whatsapp_business_messaging"],
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )


async def _refresher(token: Token) -> Token:
    return Token(value="rotated", app_id=token.app_id, expires_at=token.expires_at)


@pytest.fixture
def seeded_vault() -> Vault:
    vault = Vault(refresher=_refresher)
    asyncio.run(vault.store(_make_token()))
    return vault


@pytest.fixture
def client(seeded_vault: Vault) -> Iterator[TestClient]:
    with TestClient(create_app(seeded_vault)) as test_client:
        yield test_client


def test_list_tokens_omits_secret_value(client: TestClient) -> None:
    resp = client.get("/tokens/app-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app_id"] == "app-1"
    assert len(body["tokens"]) == 1
    token = body["tokens"][0]
    assert "value" not in token
    assert token["scopes"] == ["whatsapp_business_messaging"]
    assert token["expired"] is False
    assert token["expires_in_seconds"] > 0


def test_list_tokens_empty_app(client: TestClient) -> None:
    resp = client.get("/tokens/unknown")
    assert resp.status_code == 200
    assert resp.json()["tokens"] == []


def test_rotate_succeeds(client: TestClient) -> None:
    resp = client.post("/rotate/app-1")
    assert resp.status_code == 200
    assert "value" not in resp.json()["token"]


def test_rotate_without_refresher_returns_409() -> None:
    vault = Vault()  # no refresher
    asyncio.run(vault.store(_make_token()))
    with TestClient(create_app(vault)) as client:
        resp = client.post("/rotate/app-1")
    assert resp.status_code == 409


def test_rotate_missing_app_returns_404() -> None:
    vault = Vault(refresher=_refresher)
    with TestClient(create_app(vault)) as client:
        resp = client.post("/rotate/nope")
    assert resp.status_code == 404


def test_audit_endpoint_lists_entries(client: TestClient) -> None:
    client.get("/tokens/app-1")
    client.post("/rotate/app-1")
    resp = client.get("/audit")
    assert resp.status_code == 200
    actions = [e["action"] for e in resp.json()["entries"]]
    assert "store" in actions
    assert "rotate" in actions
    # Dashboard-initiated rotation is attributed to the dashboard actor.
    actors = {e["actor"] for e in resp.json()["entries"]}
    assert "dashboard" in actors


def test_health_endpoint_reports_degraded_for_noop(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "meta-token-vault"
    assert body["state"] == "degraded"
    assert body["ok"] is False
    assert "checked_at" in body


def test_custom_actor_recorded_in_audit() -> None:
    vault = Vault(refresher=_refresher)
    asyncio.run(vault.store(_make_token()))
    with TestClient(create_app(vault, actor="ops-console")) as client:
        client.post("/rotate/app-1")
        entries = client.get("/audit").json()["entries"]
    assert any(e["actor"] == "ops-console" for e in entries)
