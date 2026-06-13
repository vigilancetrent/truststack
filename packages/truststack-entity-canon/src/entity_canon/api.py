"""Optional FastAPI surface for the canonicalizer.

The ``fastapi`` import is lazy (inside :func:`create_app`) so the package
imports cleanly without the ``api`` extra installed. Install it with
``pip install truststack-entity-canon[api]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from .canonicalizer import Canonicalizer
from .models import CanonicalEntity, MatchResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI


class FindRequest(BaseModel):
    """Request body for ``POST /find``."""

    name: str = Field(min_length=1)


class AddEntityRequest(BaseModel):
    """Request body for ``POST /entities``."""

    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)


def create_app(canonicalizer: Canonicalizer | None = None) -> FastAPI:
    """Build a FastAPI app exposing the entity-canon REST surface.

    Routes:

    * ``POST /find`` — resolve a name; blocks duplicates (or, when the component
      runs in ``require_approval`` mode, returns the suggestion without blocking).
    * ``POST /entities`` — register a new canonical entity.
    * ``GET /entities`` — list every stored entity.
    * ``DELETE /entities/{entity_id}`` — remove an entity (404 if unknown).
    * ``GET /health`` — Trust Stack health status.

    :param canonicalizer: an existing component to serve; a default in-memory
        one is created when omitted.
    """
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:  # pragma: no cover - exercised only without extra
        raise RuntimeError(
            "FastAPI is required for the REST API. "
            "Install it with: pip install truststack-entity-canon[api]"
        ) from exc

    component = canonicalizer if canonicalizer is not None else Canonicalizer()
    app = FastAPI(title="Trust Stack Entity Canon", version=component.version())

    @app.post("/find", response_model=MatchResult)
    async def find(request: FindRequest) -> MatchResult:
        # canonicalize() already honours require_approval mode: in approval mode
        # it returns blocked=False with a populated suggestion, so a human can
        # decide rather than the request being hard-blocked.
        return await component.canonicalize(request.name)

    @app.post("/entities", response_model=CanonicalEntity, status_code=201)
    async def add_entity(request: AddEntityRequest) -> CanonicalEntity:
        return await component.add(request.name, aliases=request.aliases)

    @app.get("/entities", response_model=list[CanonicalEntity])
    async def list_entities() -> list[CanonicalEntity]:
        return await component.all()

    @app.delete("/entities/{entity_id}", status_code=204)
    async def delete_entity(entity_id: str) -> None:
        removed = await component.delete(entity_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"entity {entity_id!r} not found")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        status = await component.health_check()
        return status.model_dump(mode="json")

    return app


__all__ = ["AddEntityRequest", "FindRequest", "create_app"]
