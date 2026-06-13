from __future__ import annotations

import json

import structlog

from truststack.logging import configure_logging, correlation_id, get_logger


def test_get_logger_emits_json(capsys) -> None:
    configure_logging(json=True, level="INFO")
    log = get_logger("test", component="core-test")
    log.info("hello", foo="bar")

    # Logs are emitted on stderr to keep stdout clean for data/CLI output.
    err = capsys.readouterr().err.strip()
    payload = json.loads(err)
    assert payload["event"] == "hello"
    assert payload["component"] == "core-test"
    assert payload["foo"] == "bar"
    assert payload["level"] == "info"


def test_correlation_id_woven_in(capsys) -> None:
    configure_logging(json=True)
    token = correlation_id.set("req-abc")
    try:
        get_logger("test").info("with_cid")
    finally:
        correlation_id.reset(token)

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["correlation_id"] == "req-abc"


def test_console_renderer_configures(capsys) -> None:
    configure_logging(json=False)
    get_logger("test").info("dev_mode")
    assert "dev_mode" in capsys.readouterr().err


def test_get_logger_autoconfigures(monkeypatch) -> None:
    # Force the "not configured yet" branch.
    monkeypatch.setattr("truststack.logging._configured", False)
    log = get_logger("auto")
    assert isinstance(log, structlog.stdlib.BoundLogger) or hasattr(log, "info")
