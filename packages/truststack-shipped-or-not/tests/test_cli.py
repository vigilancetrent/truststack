from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from shipped_or_not import cli

URL = "https://example.com"


def test_cli_shipped_exit_zero(httpx_mock: HTTPXMock, capsys: pytest.CaptureFixture[str]) -> None:
    httpx_mock.add_response(url=URL, status_code=200)

    exit_code = cli.main(["verify", URL])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SHIPPED" in out


def test_cli_unverified_exit_one(httpx_mock: HTTPXMock, capsys: pytest.CaptureFixture[str]) -> None:
    httpx_mock.add_response(url=URL, status_code=500)

    exit_code = cli.main(["verify", URL])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "UNVERIFIED" in out


def test_cli_json_output(httpx_mock: HTTPXMock, capsys: pytest.CaptureFixture[str]) -> None:
    httpx_mock.add_response(url=URL, status_code=200)

    exit_code = cli.main(["verify", URL, "--json"])

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert exit_code == 0
    assert payload["status"] == "shipped"
    assert payload["response_code"] == 200


def test_cli_health_flag(httpx_mock: HTTPXMock, capsys: pytest.CaptureFixture[str]) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    httpx_mock.add_response(url=f"{URL}/healthz", status_code=503)

    exit_code = cli.main(["verify", URL, "--health", "/healthz"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "UNVERIFIED" in out


def test_cli_via_monkeypatched_argv(
    httpx_mock: HTTPXMock,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    httpx_mock.add_response(url=URL, status_code=200)
    monkeypatch.setattr("sys.argv", ["shipped-or-not", "verify", URL])

    exit_code = cli.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SHIPPED" in out


def test_cli_requires_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0
