"""Tests for the optional FastAPI surface."""

from __future__ import annotations

from fastapi.testclient import TestClient

from task_dedupe.api import create_app


def test_dedupe_api_check_and_health() -> None:
    client = TestClient(create_app())

    first = client.post("/check", json={"title": "Follow up with Anthropic", "due": "next week"})
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    again = client.post("/check", json={"title": "Follow up with Anthropic", "due": "next week"})
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["existing_task_id"] is not None

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["state"] == "healthy"
