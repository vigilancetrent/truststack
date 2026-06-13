"""Tests for the optional FastAPI surface (add, list, find, delete, health)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from entity_canon import Canonicalizer
from entity_canon.api import AddEntityRequest, FindRequest, create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_request_models_validate() -> None:
    assert FindRequest(name="x").name == "x"
    assert AddEntityRequest(name="x").aliases == []
    with pytest.raises(ValueError):
        FindRequest(name="")


def test_entity_api_add_find_health(client: TestClient) -> None:
    created = client.post("/entities", json={"name": "Jatin", "aliases": ["Jatyn"]})
    assert created.status_code == 201
    assert created.json()["name"] == "Jatin"

    found = client.post("/find", json={"name": "Jhatin"})
    assert found.status_code == 200
    body = found.json()
    assert body["match"] == "Jatin"
    assert body["blocked"] is True
    assert body["confidence"] >= 0.90

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["state"] == "healthy"


def test_find_validation_rejects_blank(client: TestClient) -> None:
    resp = client.post("/find", json={"name": ""})
    assert resp.status_code == 422


def test_find_miss_returns_unblocked(client: TestClient) -> None:
    resp = client.post("/find", json={"name": "Nobody"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is False
    assert body["entity_id"] is None
    assert body["confidence"] == 0.0


def test_list_entities(client: TestClient) -> None:
    assert client.get("/entities").json() == []
    client.post("/entities", json={"name": "Jatin"})
    client.post("/entities", json={"name": "Maria", "aliases": ["Mari"]})
    listed = client.get("/entities").json()
    assert {e["name"] for e in listed} == {"Jatin", "Maria"}
    maria = next(e for e in listed if e["name"] == "Maria")
    assert maria["aliases"] == ["Mari"]


def test_delete_entity(client: TestClient) -> None:
    created = client.post("/entities", json={"name": "Jatin"})
    entity_id = created.json()["id"]
    deleted = client.delete(f"/entities/{entity_id}")
    assert deleted.status_code == 204
    assert client.get("/entities").json() == []


def test_delete_unknown_entity_404(client: TestClient) -> None:
    resp = client.delete("/entities/does-not-exist")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


def test_approval_mode_find_returns_suggestion_without_blocking() -> None:
    component = Canonicalizer(require_approval=True)
    app = create_app(component)
    client = TestClient(app)

    client.post("/entities", json={"name": "Jatin"})
    resp = client.post("/find", json={"name": "Jhatin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is False
    assert body["suggestion"] == "Jatin"
    assert body["confidence"] >= 0.90


def test_create_app_uses_injected_component() -> None:
    import asyncio

    component = Canonicalizer()
    app = create_app(component)
    client = TestClient(app)
    client.post("/entities", json={"name": "Jatin"})
    # The injected component sees the entity directly (fresh loop, no clashes).
    entities = asyncio.run(component.all())
    assert len(entities) == 1


def test_app_metadata_version() -> None:
    app = create_app()
    assert app.title == "Trust Stack Entity Canon"
    assert app.version == "0.1.0"
