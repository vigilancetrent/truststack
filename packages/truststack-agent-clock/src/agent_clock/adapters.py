"""Provider-agnostic prompt adapters for :mod:`agent_clock`.

Each adapter implements the :class:`ClockAdapter` protocol and injects a trusted
time block into a provider-shaped *payload* while preserving its structure.
Adapters operate purely on plain ``dict`` / ``list`` / ``str`` values, so they
need **none** of the provider SDKs installed — those are optional extras only
needed by the caller constructing real client requests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Protocol, runtime_checkable

from agent_clock.injector import ClockInjector


@runtime_checkable
class ClockAdapter(Protocol):
    """Injects trusted time into a provider-specific request payload.

    Implementations must return a value of the *same shape* they received,
    leaving the rest of the payload untouched.
    """

    def inject(self, payload: Any) -> Any:
        """Return ``payload`` with trusted temporal context woven in."""
        ...


class _BaseAdapter:
    """Shared construction logic for the concrete adapters."""

    def __init__(self, injector: ClockInjector) -> None:
        self._injector = injector

    def _system_text(self) -> str:
        return self._injector.block()


class OpenAIAdapter(_BaseAdapter):
    """Inject trusted time into an OpenAI-style ``messages`` list.

    Accepts either a raw ``list[dict]`` of messages or a full request ``dict``
    containing a ``"messages"`` key, and returns the same shape. A ``system``
    message holding the trusted-time block is prepended (or merged into the
    leading system message when one already exists).
    """

    def inject(self, payload: list[dict[str, Any]] | dict[str, Any]) -> Any:
        block = self._system_text()
        if isinstance(payload, dict):
            messages = list(payload.get("messages", []))
            payload = dict(payload)
            payload["messages"] = self._inject_messages(messages, block)
            return payload
        return self._inject_messages(list(payload), block)

    @staticmethod
    def _inject_messages(messages: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
        if messages and messages[0].get("role") == "system":
            head = dict(messages[0])
            existing = str(head.get("content", "")).strip()
            head["content"] = f"{block}\n\n{existing}" if existing else block
            return [head, *messages[1:]]
        return [{"role": "system", "content": block}, *messages]


class AnthropicAdapter(_BaseAdapter):
    """Inject trusted time into an Anthropic Messages-API request ``dict``.

    Anthropic carries the system prompt in a top-level ``"system"`` field (string
    or list of content blocks). The trusted-time block is prepended to it while
    ``messages`` are left untouched.
    """

    def inject(self, payload: dict[str, Any]) -> dict[str, Any]:
        block = self._system_text()
        result = dict(payload)
        system = result.get("system")
        if system is None or (isinstance(system, str) and not system.strip()):
            result["system"] = block
        elif isinstance(system, str):
            result["system"] = f"{block}\n\n{system}"
        elif isinstance(system, list):
            time_block = {"type": "text", "text": block}
            result["system"] = [time_block, *system]
        else:  # pragma: no cover - unexpected provider shape
            result["system"] = block
        return result


class LangChainAdapter(_BaseAdapter):
    """Inject trusted time into a LangChain-style prompt or message list.

    Supports three structures, returning each in kind:
      * a plain ``str`` prompt -> uses :meth:`ClockInjector.inject`;
      * a ``list`` of ``(role, content)`` tuples -> prepends a ``system`` tuple;
      * a ``list`` of message ``dict``s -> prepends a ``system`` dict.
    """

    def inject(
        self, payload: str | list[tuple[str, str]] | list[dict[str, Any]]
    ) -> str | list[tuple[str, str]] | list[dict[str, Any]]:
        if isinstance(payload, str):
            return self._injector.inject(payload)

        block = self._system_text()
        items = list(payload)
        if items and isinstance(items[0], tuple):
            tuples: list[tuple[str, str]] = [t for t in items if isinstance(t, tuple)]
            return [("system", block), *tuples]
        dicts: list[dict[str, Any]] = [d for d in items if isinstance(d, dict)]
        return [{"role": "system", "content": block}, *dicts]


class GenericAdapter(_BaseAdapter):
    """Best-effort adapter that injects trusted time into *arbitrary* payloads.

    Used when no provider-specific adapter fits. It inspects the payload shape and
    weaves the trusted-time block in without altering the payload type:

      * ``str`` -> :meth:`ClockInjector.inject` (block + ``User requests:``);
      * ``dict`` with ``"messages"`` -> prepend/merge a leading ``system`` message
        (same semantics as :class:`OpenAIAdapter`);
      * ``dict`` with ``"system"`` -> prepend to the system field (str or
        content-block list, same semantics as :class:`AnthropicAdapter`);
      * ``dict`` with ``"prompt"`` -> prefix the block onto the prompt string;
      * ``dict`` with none of the above -> add a ``"system"`` key with the block;
      * ``list`` of message ``dict``s -> prepend a ``system`` dict;
      * ``list`` of ``(role, content)`` tuples -> prepend a ``system`` tuple;
      * any other ``list`` -> prepend the block as a leading element.
    """

    def inject(self, payload: Any) -> Any:
        block = self._system_text()
        if isinstance(payload, str):
            return self._injector.inject(payload)
        if isinstance(payload, dict):
            return self._inject_dict(payload, block)
        if isinstance(payload, list):
            return self._inject_list(payload, block)
        # Unknown scalar shape: fall back to string injection of its repr-free str.
        return self._injector.inject(str(payload))

    def _inject_dict(self, payload: dict[str, Any], block: str) -> dict[str, Any]:
        result = dict(payload)
        if "messages" in result:
            messages = list(result.get("messages") or [])
            result["messages"] = OpenAIAdapter._inject_messages(messages, block)
            return result
        if "system" in result:
            return AnthropicAdapter(self._injector).inject(result)
        if "prompt" in result:
            existing = str(result.get("prompt", ""))
            result["prompt"] = f"{block}\n\n{existing}" if existing else block
            return result
        result["system"] = block
        return result

    @staticmethod
    def _inject_list(payload: list[Any], block: str) -> list[Any]:
        items = list(payload)
        if items and isinstance(items[0], tuple):
            return [("system", block), *items]
        if items and isinstance(items[0], dict):
            return [{"role": "system", "content": block}, *items]
        return [block, *items]


class AdapterRegistry:
    """A mutable mapping of provider name -> :class:`ClockAdapter` factory.

    Factories are callables taking a :class:`ClockInjector` and returning a
    :class:`ClockAdapter`. Names are matched case-insensitively. The module-level
    :data:`registry` instance is pre-populated with the built-in providers plus a
    ``"generic"`` fallback.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[ClockInjector], ClockAdapter]] = {}

    def register(
        self,
        name: str,
        factory: Callable[[ClockInjector], ClockAdapter],
        *,
        overwrite: bool = False,
    ) -> None:
        """Register ``factory`` under ``name`` (case-insensitive).

        :raises ValueError: if ``name`` is empty/blank, or already registered and
            ``overwrite`` is False.
        """
        key = name.strip().lower()
        if not key:
            raise ValueError("adapter name must be a non-empty string")
        if key in self._factories and not overwrite:
            raise ValueError(f"adapter {name!r} is already registered; pass overwrite=True")
        self._factories[key] = factory

    def get(self, name: str, injector: ClockInjector) -> ClockAdapter:
        """Build and return the adapter registered under ``name`` for ``injector``.

        :raises KeyError: if no adapter is registered under ``name``.
        """
        key = name.strip().lower()
        try:
            factory = self._factories[key]
        except KeyError as exc:
            available = ", ".join(self.available()) or "<none>"
            raise KeyError(
                f"no clock adapter registered for {name!r}; available: {available}"
            ) from exc
        return factory(injector)

    def available(self) -> list[str]:
        """Return the registered provider names, sorted alphabetically."""
        return sorted(self._factories)

    def unregister(self, name: str) -> None:
        """Remove the adapter registered under ``name`` (no error if absent)."""
        self._factories.pop(name.strip().lower(), None)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.strip().lower() in self._factories

    def __iter__(self) -> Iterator[str]:  # pragma: no cover - trivial passthrough
        return iter(self.available())


registry = AdapterRegistry()
registry.register("openai", OpenAIAdapter)
registry.register("anthropic", AnthropicAdapter)
registry.register("langchain", LangChainAdapter)
registry.register("generic", GenericAdapter)


__all__ = [
    "AdapterRegistry",
    "AnthropicAdapter",
    "ClockAdapter",
    "GenericAdapter",
    "LangChainAdapter",
    "OpenAIAdapter",
    "registry",
]
