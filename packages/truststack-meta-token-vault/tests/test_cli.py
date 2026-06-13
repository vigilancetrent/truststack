"""Tests for the meta-token-vault CLI."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meta_token_vault import SqliteTokenStore, Token
from meta_token_vault.cli import main


def _seed(db: Path) -> None:
    store = SqliteTokenStore(db)
    token = Token(
        value="seed-value",
        app_id="123",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    asyncio.run(store.put(token))


def test_cli_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "vault.db"
    _seed(db)
    rc = main(["--db", str(db), "list", "123"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "app=123" in out


def test_cli_list_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "vault.db"
    rc = main(["--db", str(db), "list", "999"])
    assert rc == 0
    assert "No tokens" in capsys.readouterr().out


def test_cli_get(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "vault.db"
    _seed(db)
    rc = main(["--db", str(db), "get", "123"])
    assert rc == 0
    assert "app=123" in capsys.readouterr().out


def test_cli_get_missing(tmp_path: Path) -> None:
    db = tmp_path / "vault.db"
    rc = main(["--db", str(db), "get", "404"])
    assert rc == 1


def test_cli_rotate_without_refresher(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "vault.db"
    _seed(db)
    rc = main(["--db", str(db), "rotate", "123"])
    assert rc == 2
    assert "Cannot rotate" in capsys.readouterr().err
