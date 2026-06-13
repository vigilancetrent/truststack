"""Structured, JSON-first logging for the Trust Stack, built on ``structlog``.

Usage::

    from truststack.logging import configure_logging, get_logger

    configure_logging()
    log = get_logger("agent_clock", component="agent-clock")
    log.info("time_injected", timezone="Asia/Dubai")
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any, cast

import structlog
from structlog.types import EventDict, WrappedLogger

#: Per-context correlation id, woven into every log line when set.
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_configured = False


def _add_correlation_id(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    cid = correlation_id.get()
    if cid is not None:
        event_dict.setdefault("correlation_id", cid)
    return event_dict


def configure_logging(*, level: int | str = logging.INFO, json: bool = True) -> None:
    """Configure ``structlog`` once for the whole process.

    :param level: standard library log level (int or name).
    :param json: emit JSON (production) when ``True``, else a colorized console
        renderer (local development).
    """
    global _configured

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level) if isinstance(level, str) else level
        ),
        # Logs go to stderr so stdout stays clean for data / CLI output.
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger, configuring defaults on first use.

    Extra keyword arguments (e.g. ``component="agent-clock"``) are bound to every
    line emitted by the returned logger.
    """
    if not _configured:
        configure_logging()
    logger = structlog.get_logger(name).bind(**initial_values)
    return cast("structlog.stdlib.BoundLogger", logger)


__all__ = ["configure_logging", "correlation_id", "get_logger"]
