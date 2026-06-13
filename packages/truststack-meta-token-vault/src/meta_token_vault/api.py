"""FastAPI dashboard API for the Meta token vault.

:func:`create_app` builds a small read/operate dashboard over a :class:`Vault`:

* ``GET  /tokens/{app_id}`` -- list non-secret metadata for an app's tokens.
* ``POST /rotate/{app_id}`` -- force a rotation (requires a refresher).
* ``GET  /audit``           -- the audit trail.
* ``GET  /health``          -- the component health status.

``fastapi`` is imported lazily inside :func:`create_app` so the package remains
importable without it; install the ``api`` extra to use the dashboard. Token
*values* are never exposed by these endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import Role, Token
from .vault import Vault

if TYPE_CHECKING:
    from fastapi import FastAPI


def _token_public(token: Token) -> dict[str, Any]:
    """Project a token to its non-secret, JSON-safe fields (no ``value``)."""
    return {
        "id": token.id,
        "app_id": token.app_id,
        "scopes": list(token.scopes),
        "issued_at": token.issued_at.isoformat(),
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "expired": token.is_expired(),
        "expires_in_seconds": token.expires_in_seconds(),
    }


def create_app(vault: Vault, *, actor: str = "dashboard") -> FastAPI:
    """Build a FastAPI app exposing a read/operate dashboard over ``vault``.

    :param vault: the :class:`Vault` instance to surface.
    :param actor: audit actor recorded for dashboard-initiated operations.
    """
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Meta Token Vault Dashboard", version=vault.component_version)

    @app.get("/tokens/{app_id}")
    async def list_tokens(app_id: str) -> dict[str, Any]:
        tokens = await vault._store.all(app_id)
        return {"app_id": app_id, "tokens": [_token_public(t) for t in tokens]}

    @app.post("/rotate/{app_id}")
    async def rotate_token(app_id: str) -> dict[str, Any]:
        try:
            rotated = await vault.rotate(app_id, role=Role.OPERATOR, actor=actor)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
        return {"app_id": app_id, "token": _token_public(rotated)}

    @app.get("/audit")
    async def audit() -> dict[str, Any]:
        trail = await vault.audit_trail()
        return {
            "entries": [
                {
                    "action": entry.action.value,
                    "app_id": entry.app_id,
                    "token_id": entry.token_id,
                    "at": entry.at.isoformat(),
                    "actor": entry.actor,
                }
                for entry in trail
            ]
        }

    @app.get("/health")
    async def health() -> dict[str, Any]:
        status = await vault.health_check()
        return {
            "component": status.component,
            "state": status.state.value,
            "ok": status.ok,
            "detail": status.detail,
            "checked_at": status.checked_at.isoformat(),
        }

    return app


__all__ = ["create_app"]
