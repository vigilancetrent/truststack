"""OpenTelemetry helpers for the Trust Stack.

These wrappers use the OpenTelemetry *API* only, so they are safe to import and
call even when no SDK/exporter is configured (they become no-ops). Install the
``otel-sdk`` extra and configure a provider to capture real telemetry.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, Tracer

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def get_tracer(name: str) -> Tracer:
    """Return an OpenTelemetry tracer for ``name`` (component import path)."""
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    """Return an OpenTelemetry meter for ``name``."""
    return metrics.get_meter(name)


def traced(span_name: str | None = None) -> Callable[[F], F]:
    """Decorate an async function so each call runs inside an OTEL span.

    Exceptions are recorded on the span and re-raised. With no SDK configured the
    span is a no-op and overhead is negligible.
    """

    def decorator(func: F) -> F:
        name = span_name or func.__qualname__
        tracer = trace.get_tracer(func.__module__)

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(name) as span:
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
                    raise

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["Span", "get_meter", "get_tracer", "traced"]
