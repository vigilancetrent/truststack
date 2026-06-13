"""Tests for clock_middleware on sync and async LLM-call functions.

Covers signature preservation, positional vs keyword prompt detection across the
recognised keyword names, str and messages payloads, the fail-open path when no
injectable argument is present, type preservation, and that other arguments are
forwarded untouched.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_clock import ClockInjector, FrozenTimeSource, clock_middleware

DUBAI_INSTANT = datetime(2026, 6, 10, 13, 55, 0, tzinfo=UTC)
BLOCK = (
    "Current trusted datetime:\n"
    "Wednesday June 10 2026\n"
    "17:55 +04\n"
    "Timezone: Asia/Dubai\n"
    "UTC Offset: +04:00"
)


def _clock() -> ClockInjector:
    return ClockInjector(timezone="Asia/Dubai", time_source=FrozenTimeSource(DUBAI_INSTANT))


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


def test_sync_positional_str_injected() -> None:
    @clock_middleware(_clock())
    def call(prompt: str) -> str:
        return prompt

    out = call("Hello?")
    assert out == f"{BLOCK}\n\nUser requests:\nHello?"


def test_sync_keyword_prompt_injected() -> None:
    @clock_middleware(_clock())
    def call(*, prompt: str) -> str:
        return prompt

    assert call(prompt="Hi").startswith(BLOCK)


def test_sync_messages_keyword_preserves_list_shape() -> None:
    @clock_middleware(_clock())
    def call(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages

    out = call(messages=[{"role": "user", "content": "hi"}])
    assert isinstance(out, list)
    assert out[0] == {"role": "system", "content": BLOCK}
    assert out[1] == {"role": "user", "content": "hi"}


@pytest.mark.parametrize("key", ["input", "text"])
def test_sync_other_keyword_names_detected(key: str) -> None:
    @clock_middleware(_clock())
    def call(**kwargs: Any) -> str:
        return str(kwargs[key])

    assert call(**{key: "x"}).startswith(BLOCK)


def test_sync_forwards_other_args_untouched() -> None:
    @clock_middleware(_clock())
    def call(prompt: str, *, temperature: float, model: str) -> dict[str, Any]:
        return {"prompt": prompt, "temperature": temperature, "model": model}

    out = call("hi", temperature=0.2, model="gpt-4o")
    assert out["temperature"] == 0.2
    assert out["model"] == "gpt-4o"
    assert out["prompt"].startswith(BLOCK)


def test_sync_no_injectable_argument_passes_through() -> None:
    @clock_middleware(_clock())
    def call(*, temperature: float) -> float:
        return temperature

    assert call(temperature=0.9) == 0.9


def test_sync_first_positional_injectable_used() -> None:
    @clock_middleware(_clock())
    def call(a: int, b: str) -> str:
        return b

    # The int is not injectable; the str is the first injectable positional.
    out = call(5, "second")
    assert out == f"{BLOCK}\n\nUser requests:\nsecond"


def test_sync_preserves_signature_and_metadata() -> None:
    @clock_middleware(_clock())
    def call(prompt: str, temperature: float = 0.0) -> str:
        """Original docstring."""
        return prompt

    sig = inspect.signature(call)
    assert list(sig.parameters) == ["prompt", "temperature"]
    assert call.__doc__ == "Original docstring."
    assert call.__name__ == "call"


def test_sync_keyword_priority_prompt_over_messages() -> None:
    seen: dict[str, Any] = {}

    @clock_middleware(_clock())
    def call(*, prompt: str, messages: list[dict[str, Any]]) -> None:
        seen["prompt"] = prompt
        seen["messages"] = messages

    call(prompt="p", messages=[{"role": "user", "content": "m"}])
    # 'prompt' wins; messages stay untouched.
    assert seen["prompt"].startswith(BLOCK)
    assert seen["messages"] == [{"role": "user", "content": "m"}]


# --------------------------------------------------------------------------- #
# Async
# --------------------------------------------------------------------------- #


async def test_async_positional_str_injected() -> None:
    @clock_middleware(_clock())
    async def call(prompt: str) -> str:
        return prompt

    assert inspect.iscoroutinefunction(call)
    out = await call("Hello?")
    assert out == f"{BLOCK}\n\nUser requests:\nHello?"


async def test_async_messages_keyword() -> None:
    @clock_middleware(_clock())
    async def call(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages

    out = await call(messages=[{"role": "user", "content": "hi"}])
    assert out[0] == {"role": "system", "content": BLOCK}


async def test_async_no_injectable_passes_through() -> None:
    @clock_middleware(_clock())
    async def call(*, temperature: float) -> float:
        return temperature

    assert await call(temperature=0.5) == 0.5


async def test_async_forwards_other_args() -> None:
    @clock_middleware(_clock())
    async def call(prompt: str, *, top_p: float) -> dict[str, Any]:
        return {"prompt": prompt, "top_p": top_p}

    out = await call("hi", top_p=0.95)
    assert out["top_p"] == 0.95
    assert out["prompt"].startswith(BLOCK)


async def test_async_preserves_signature() -> None:
    @clock_middleware(_clock())
    async def call(prompt: str) -> str:
        return prompt

    assert list(inspect.signature(call).parameters) == ["prompt"]
    assert call.__name__ == "call"
