from __future__ import annotations

import pytest

from truststack.observability import get_meter, get_tracer, traced


def test_get_tracer_and_meter() -> None:
    assert get_tracer("truststack.test") is not None
    assert get_meter("truststack.test") is not None


async def test_traced_returns_value() -> None:
    @traced()
    async def add(a: int, b: int) -> int:
        return a + b

    assert await add(2, 3) == 5


async def test_traced_records_and_reraises() -> None:
    @traced("boom_span")
    async def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await boom()


async def test_traced_preserves_metadata() -> None:
    @traced()
    async def documented() -> str:
        """A docstring."""
        return "ok"

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "A docstring."
    assert await documented() == "ok"
