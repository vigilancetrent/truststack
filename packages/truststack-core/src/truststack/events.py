"""The Trust Stack event bus.

Trust decisions are emitted as :class:`TrustEvent` objects on an async
:class:`EventBus`, giving applications an observable side-channel for auditing
(e.g. ``deployment.unverified``, ``task.duplicate_detected``).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

EventHandler = Callable[["TrustEvent"], Awaitable[None]]


class TrustEvent(BaseModel):
    """Base class for all trust events.

    Subclass it for typed payloads, or use it directly with ``data``.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    component: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    """A minimal async publish/subscribe bus with wildcard subscriptions.

    Subscribe to an exact event name or ``"*"`` for all events. Handlers are
    invoked concurrently; a failing handler never blocks the others.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, name: str, handler: EventHandler) -> Callable[[], None]:
        """Register ``handler`` for ``name`` (or ``"*"``). Returns an unsubscribe fn."""
        self._handlers[name].append(handler)

        def _unsubscribe() -> None:
            if handler in self._handlers[name]:
                self._handlers[name].remove(handler)

        return _unsubscribe

    async def publish(self, event: TrustEvent) -> None:
        """Dispatch ``event`` to all matching handlers concurrently."""
        handlers = [*self._handlers.get(event.name, []), *self._handlers.get("*", [])]
        if not handlers:
            return
        await asyncio.gather(
            *(h(event) for h in handlers),
            return_exceptions=True,
        )


__all__ = ["EventBus", "EventHandler", "TrustEvent"]
