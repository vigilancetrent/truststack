"""truststack-agent-clock — inject trusted temporal context into every LLM call.

LLMs answer with the wrong date because the current date/time is never injected
into prompts. :class:`ClockInjector` renders a trusted "now" and prepends it to
prompts (or provider-shaped payloads via the adapters), so agents stop guessing.

Quickstart::

    from agent_clock import ClockInjector

    clock = ClockInjector(timezone="Asia/Dubai")
    print(clock.inject("What day is it tomorrow?"))
"""

from __future__ import annotations

from agent_clock.adapters import (
    AdapterRegistry,
    AnthropicAdapter,
    ClockAdapter,
    GenericAdapter,
    LangChainAdapter,
    OpenAIAdapter,
    registry,
)
from agent_clock.injector import ClockInjector
from agent_clock.middleware import clock_middleware
from agent_clock.models import TimeFormat, TrustedTime, Weekday
from agent_clock.sources import (
    CallableTimeSource,
    FrozenTimeSource,
    SystemTimeSource,
    TimeSource,
    resolve_timezone,
)

__version__ = "0.1.0"

__all__ = [
    "AdapterRegistry",
    "AnthropicAdapter",
    "CallableTimeSource",
    "ClockAdapter",
    "ClockInjector",
    "FrozenTimeSource",
    "GenericAdapter",
    "LangChainAdapter",
    "OpenAIAdapter",
    "SystemTimeSource",
    "TimeFormat",
    "TimeSource",
    "TrustedTime",
    "Weekday",
    "__version__",
    "clock_middleware",
    "registry",
    "resolve_timezone",
]
