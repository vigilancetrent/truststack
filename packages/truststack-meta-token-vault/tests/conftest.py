"""Shared fixtures and offline fakes for the meta-token-vault test suite.

These fixtures let the cloud/db backend tests run fully offline with no real
services or even the heavy client libraries installed. They register tiny fake
modules in :data:`sys.modules` so the lazily-imported symbols the stores need
(``botocore.exceptions.ClientError``, ``hvac.exceptions.InvalidPath``) resolve
to controllable stand-ins. The actual clients/pools are injected as mocks.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import pytest


class _FakeClientError(Exception):
    """Stand-in for ``botocore.exceptions.ClientError`` used by the AWS store."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeInvalidPath(Exception):
    """Stand-in for ``hvac.exceptions.InvalidPath`` used by the HashiCorp store."""


@pytest.fixture
def fake_botocore_client_error() -> Iterator[type[_FakeClientError]]:
    """Install a fake ``botocore.exceptions`` module exposing ``ClientError``.

    Restores any pre-existing module on teardown so a real botocore install is
    never clobbered.
    """
    saved_botocore = sys.modules.get("botocore")
    saved_exceptions = sys.modules.get("botocore.exceptions")
    botocore = types.ModuleType("botocore")
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = _FakeClientError  # type: ignore[attr-defined]
    botocore.exceptions = exceptions  # type: ignore[attr-defined]
    sys.modules["botocore"] = botocore
    sys.modules["botocore.exceptions"] = exceptions
    try:
        yield _FakeClientError
    finally:
        for name, saved in (
            ("botocore", saved_botocore),
            ("botocore.exceptions", saved_exceptions),
        ):
            if saved is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved


@pytest.fixture
def fake_hvac_invalid_path() -> Iterator[type[_FakeInvalidPath]]:
    """Install a fake ``hvac.exceptions`` module exposing ``InvalidPath``."""
    saved_hvac = sys.modules.get("hvac")
    saved_exceptions = sys.modules.get("hvac.exceptions")
    hvac = types.ModuleType("hvac")
    exceptions = types.ModuleType("hvac.exceptions")
    exceptions.InvalidPath = _FakeInvalidPath  # type: ignore[attr-defined]
    hvac.exceptions = exceptions  # type: ignore[attr-defined]
    sys.modules["hvac"] = hvac
    sys.modules["hvac.exceptions"] = exceptions
    try:
        yield _FakeInvalidPath
    finally:
        for name, saved in (("hvac", saved_hvac), ("hvac.exceptions", saved_exceptions)):
            if saved is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved
