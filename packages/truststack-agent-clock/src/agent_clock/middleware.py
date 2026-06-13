"""Call-site middleware for :mod:`agent_clock`.

:func:`clock_middleware` produces a decorator that transparently injects trusted
temporal context into an LLM-call function's first prompt-shaped argument. It
works on both synchronous and ``async`` callables, preserves the wrapped
signature (via :func:`functools.wraps`), and never changes the *type* of the
argument it rewrites — a ``str`` stays a ``str``, a ``messages`` list stays a
list of the same shape.

The injected argument may be passed positionally or by keyword; the first of
``prompt``/``messages``/``input``/``text`` found in keyword arguments is rewritten,
otherwise the first positional argument is used.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from agent_clock.adapters import GenericAdapter
from agent_clock.injector import ClockInjector

F = TypeVar("F", bound=Callable[..., Any])

#: Keyword-argument names that are treated as the prompt-shaped argument, in
#: priority order, before falling back to the first positional argument.
_PROMPT_KEYS: tuple[str, ...] = ("prompt", "messages", "input", "text")


def _injectable(value: Any) -> bool:
    """Return True when ``value`` is a shape the :class:`GenericAdapter` rewrites."""
    return isinstance(value, (str, dict, list))


def clock_middleware(injector: ClockInjector) -> Callable[[F], F]:
    """Return a decorator that injects trusted time into an LLM-call function.

    The decorator detects whether the wrapped function is a coroutine function
    and returns a matching sync/async wrapper. It locates the prompt-shaped
    argument (see module docstring), runs it through a :class:`GenericAdapter`
    bound to ``injector``, and forwards everything else unchanged.

    If no injectable argument is found (e.g. the function takes only options),
    the call is passed through untouched — the middleware fails *open* on shape
    rather than corrupting an unrecognised payload.

    :param injector: the :class:`ClockInjector` whose trusted time is injected.
    """
    adapter = GenericAdapter(injector)

    def _rewrite_args(
        args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        # Prefer an explicit keyword argument naming the prompt.
        for key in _PROMPT_KEYS:
            if key in kwargs and _injectable(kwargs[key]):
                new_kwargs = dict(kwargs)
                new_kwargs[key] = adapter.inject(kwargs[key])
                return args, new_kwargs
        # Otherwise rewrite the first injectable positional argument.
        for index, value in enumerate(args):
            if _injectable(value):
                new_args = (*args[:index], adapter.inject(value), *args[index + 1 :])
                return new_args, kwargs
        return args, kwargs

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):
            async_func = cast(Callable[..., Awaitable[Any]], func)

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                new_args, new_kwargs = _rewrite_args(args, kwargs)
                return await async_func(*new_args, **new_kwargs)

            return cast(F, async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            new_args, new_kwargs = _rewrite_args(args, kwargs)
            return func(*new_args, **new_kwargs)

        return cast(F, sync_wrapper)

    return decorator


__all__ = ["clock_middleware"]
