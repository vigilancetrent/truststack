"""Tests for the AdapterRegistry and the GenericAdapter payload shapes.

Covers registry register/get/available/unregister/contains/overwrite semantics,
the pre-populated built-ins, unknown-name failure, plus GenericAdapter handling
of every supported payload shape (str, messages dict, system dict, prompt dict,
keyless dict, dict-message list, tuple-message list, bare list, and a scalar
fallback).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agent_clock import (
    AdapterRegistry,
    AnthropicAdapter,
    ClockAdapter,
    ClockInjector,
    FrozenTimeSource,
    GenericAdapter,
    LangChainAdapter,
    OpenAIAdapter,
)
from agent_clock import registry as default_registry

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
# AdapterRegistry
# --------------------------------------------------------------------------- #


def test_default_registry_has_builtins() -> None:
    assert default_registry.available() == ["anthropic", "generic", "langchain", "openai"]


def test_default_registry_get_returns_correct_types() -> None:
    clock = _clock()
    assert isinstance(default_registry.get("openai", clock), OpenAIAdapter)
    assert isinstance(default_registry.get("anthropic", clock), AnthropicAdapter)
    assert isinstance(default_registry.get("langchain", clock), LangChainAdapter)
    assert isinstance(default_registry.get("generic", clock), GenericAdapter)


def test_registry_get_is_case_insensitive_and_strips() -> None:
    adapter = default_registry.get("  OpenAI  ", _clock())
    assert isinstance(adapter, OpenAIAdapter)


def test_registry_get_unknown_raises_keyerror_with_available() -> None:
    reg = AdapterRegistry()
    reg.register("openai", OpenAIAdapter)
    with pytest.raises(KeyError) as exc:
        reg.get("does-not-exist", _clock())
    assert "openai" in str(exc.value)
    assert "does-not-exist" in str(exc.value)


def test_registry_get_unknown_on_empty_registry_says_none() -> None:
    reg = AdapterRegistry()
    with pytest.raises(KeyError, match="<none>"):
        reg.get("anything", _clock())


def test_registry_register_and_build_custom() -> None:
    reg = AdapterRegistry()
    reg.register("custom", GenericAdapter)
    assert "custom" in reg
    built = reg.get("custom", _clock())
    assert isinstance(built, ClockAdapter)


def test_registry_register_blank_name_raises() -> None:
    reg = AdapterRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register("   ", GenericAdapter)


def test_registry_register_duplicate_without_overwrite_raises() -> None:
    reg = AdapterRegistry()
    reg.register("openai", OpenAIAdapter)
    with pytest.raises(ValueError, match="already registered"):
        reg.register("OpenAI", AnthropicAdapter)


def test_registry_register_overwrite_replaces() -> None:
    reg = AdapterRegistry()
    reg.register("p", OpenAIAdapter)
    reg.register("p", AnthropicAdapter, overwrite=True)
    assert isinstance(reg.get("p", _clock()), AnthropicAdapter)


def test_registry_unregister_removes() -> None:
    reg = AdapterRegistry()
    reg.register("p", OpenAIAdapter)
    reg.unregister("P")
    assert "p" not in reg
    assert reg.available() == []


def test_registry_unregister_absent_is_noop() -> None:
    reg = AdapterRegistry()
    reg.unregister("ghost")  # must not raise
    assert reg.available() == []


def test_registry_contains_rejects_non_string() -> None:
    reg = AdapterRegistry()
    reg.register("openai", OpenAIAdapter)
    assert 123 not in reg
    assert "openai" in reg


def test_registry_available_is_sorted() -> None:
    reg = AdapterRegistry()
    reg.register("zeta", GenericAdapter)
    reg.register("alpha", GenericAdapter)
    assert reg.available() == ["alpha", "zeta"]


# --------------------------------------------------------------------------- #
# GenericAdapter — every payload shape
# --------------------------------------------------------------------------- #


def test_generic_str_uses_inject() -> None:
    out = GenericAdapter(_clock()).inject("Summarise this.")
    assert out == f"{BLOCK}\n\nUser requests:\nSummarise this."


def test_generic_dict_messages_prepends_system() -> None:
    out = GenericAdapter(_clock()).inject({"messages": [{"role": "user", "content": "hi"}]})
    assert out["messages"][0] == {"role": "system", "content": BLOCK}
    assert out["messages"][1] == {"role": "user", "content": "hi"}


def test_generic_dict_messages_merges_existing_system() -> None:
    out = GenericAdapter(_clock()).inject(
        {"messages": [{"role": "system", "content": "Be terse."}]}
    )
    assert out["messages"][0]["content"] == f"{BLOCK}\n\nBe terse."


def test_generic_dict_system_string() -> None:
    out = GenericAdapter(_clock()).inject({"system": "Be helpful."})
    assert out["system"] == f"{BLOCK}\n\nBe helpful."


def test_generic_dict_system_list_blocks() -> None:
    out = GenericAdapter(_clock()).inject({"system": [{"type": "text", "text": "Hi"}]})
    assert out["system"][0] == {"type": "text", "text": BLOCK}


def test_generic_dict_prompt() -> None:
    out = GenericAdapter(_clock()).inject({"prompt": "Tell me a joke."})
    assert out["prompt"] == f"{BLOCK}\n\nTell me a joke."


def test_generic_dict_empty_prompt() -> None:
    out = GenericAdapter(_clock()).inject({"prompt": ""})
    assert out["prompt"] == BLOCK


def test_generic_dict_keyless_adds_system() -> None:
    out = GenericAdapter(_clock()).inject({"temperature": 0.7})
    assert out["system"] == BLOCK
    assert out["temperature"] == 0.7


def test_generic_dict_does_not_mutate_original() -> None:
    original: dict[str, Any] = {"messages": [{"role": "user", "content": "hi"}]}
    GenericAdapter(_clock()).inject(original)
    assert original["messages"][0]["role"] == "user"
    assert len(original["messages"]) == 1


def test_generic_list_of_dict_messages() -> None:
    out = GenericAdapter(_clock()).inject([{"role": "user", "content": "hi"}])
    assert out[0] == {"role": "system", "content": BLOCK}
    assert out[1] == {"role": "user", "content": "hi"}


def test_generic_list_of_tuples() -> None:
    out = GenericAdapter(_clock()).inject([("human", "hi")])
    assert out[0] == ("system", BLOCK)
    assert out[1] == ("human", "hi")


def test_generic_bare_list_prepends_block() -> None:
    out = GenericAdapter(_clock()).inject(["a", "b"])
    assert out == [BLOCK, "a", "b"]


def test_generic_empty_list_gets_block_first() -> None:
    out = GenericAdapter(_clock()).inject([])
    assert out == [BLOCK]


def test_generic_scalar_fallback_to_str_injection() -> None:
    out = GenericAdapter(_clock()).inject(42)
    assert out == f"{BLOCK}\n\nUser requests:\n42"


def test_generic_dict_messages_with_none_value() -> None:
    out = GenericAdapter(_clock()).inject({"messages": None})
    assert out["messages"] == [{"role": "system", "content": BLOCK}]
