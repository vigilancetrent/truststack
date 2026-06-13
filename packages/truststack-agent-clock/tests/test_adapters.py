"""Tests for the provider-agnostic prompt adapters."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_clock import (
    AnthropicAdapter,
    ClockAdapter,
    ClockInjector,
    FrozenTimeSource,
    LangChainAdapter,
    OpenAIAdapter,
)

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


def test_openai_adapter_prepends_system_to_list() -> None:
    adapter = OpenAIAdapter(_clock())
    assert isinstance(adapter, ClockAdapter)
    out = adapter.inject([{"role": "user", "content": "hi"}])
    assert out[0] == {"role": "system", "content": BLOCK}
    assert out[1] == {"role": "user", "content": "hi"}


def test_openai_adapter_merges_existing_system() -> None:
    adapter = OpenAIAdapter(_clock())
    out = adapter.inject(
        [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "hi"},
        ]
    )
    assert out[0]["role"] == "system"
    assert out[0]["content"] == f"{BLOCK}\n\nYou are terse."
    assert len(out) == 2


def test_openai_adapter_full_request_dict() -> None:
    adapter = OpenAIAdapter(_clock())
    req = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    out = adapter.inject(req)
    assert out["model"] == "gpt-4o"
    assert out["messages"][0] == {"role": "system", "content": BLOCK}
    # Original input not mutated.
    assert req["messages"][0]["role"] == "user"


def test_anthropic_adapter_string_system() -> None:
    adapter = AnthropicAdapter(_clock())
    out = adapter.inject({"system": "Be helpful.", "messages": []})
    assert out["system"] == f"{BLOCK}\n\nBe helpful."


def test_anthropic_adapter_no_system() -> None:
    adapter = AnthropicAdapter(_clock())
    out = adapter.inject({"messages": [{"role": "user", "content": "hi"}]})
    assert out["system"] == BLOCK
    assert out["messages"][0]["content"] == "hi"


def test_anthropic_adapter_list_system_blocks() -> None:
    adapter = AnthropicAdapter(_clock())
    out = adapter.inject({"system": [{"type": "text", "text": "Be helpful."}]})
    assert out["system"][0] == {"type": "text", "text": BLOCK}
    assert out["system"][1] == {"type": "text", "text": "Be helpful."}


def test_langchain_adapter_string() -> None:
    adapter = LangChainAdapter(_clock())
    out = adapter.inject("Summarise this.")
    assert out == f"{BLOCK}\n\nUser requests:\nSummarise this."


def test_langchain_adapter_tuple_messages() -> None:
    adapter = LangChainAdapter(_clock())
    out = adapter.inject([("human", "hi")])
    assert out[0] == ("system", BLOCK)
    assert out[1] == ("human", "hi")


def test_langchain_adapter_dict_messages() -> None:
    adapter = LangChainAdapter(_clock())
    out = adapter.inject([{"role": "user", "content": "hi"}])
    assert out[0] == {"role": "system", "content": BLOCK}
    assert out[1] == {"role": "user", "content": "hi"}
