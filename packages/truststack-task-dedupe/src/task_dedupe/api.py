"""Optional FastAPI surface for the deduplication engine.

The module imports cleanly without FastAPI installed; :func:`create_app` lazily
imports it and raises a clear error if the ``api`` extra is missing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .engine import DedupeEngine
from .models import DedupeResult, Task

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app(engine: DedupeEngine | None = None) -> FastAPI:
    """Build a FastAPI app exposing ``POST /check`` and ``GET /health``.

    Install the API extra first::

        pip install truststack-task-dedupe[api]
    """
    try:
        from fastapi import FastAPI
    except ImportError as exc:  # pragma: no cover - exercised only without extra
        raise RuntimeError(
            "FastAPI is required for the API. Install it with: "
            "pip install 'truststack-task-dedupe[api]'"
        ) from exc

    dedupe = engine if engine is not None else DedupeEngine()
    app = FastAPI(title="Trust Stack Task Dedupe", version=dedupe.version())

    @app.post("/check", response_model=DedupeResult)
    async def check(task: Task) -> DedupeResult:
        return await dedupe.check(task)

    @app.get("/health")
    async def health() -> dict[str, object]:
        status = await dedupe.health_check()
        return status.model_dump(mode="json")

    return app


__all__ = ["create_app"]
