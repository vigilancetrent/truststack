from __future__ import annotations

from truststack.events import EventBus, TrustEvent


async def test_publish_to_named_subscriber() -> None:
    bus = EventBus()
    seen: list[TrustEvent] = []

    async def handler(event: TrustEvent) -> None:
        seen.append(event)

    bus.subscribe("deployment.unverified", handler)
    await bus.publish(TrustEvent(name="deployment.unverified", component="shipped-or-not"))

    assert len(seen) == 1
    assert seen[0].component == "shipped-or-not"
    assert seen[0].event_id  # auto-generated


async def test_wildcard_subscriber_receives_all() -> None:
    bus = EventBus()
    count = 0

    async def handler(_: TrustEvent) -> None:
        nonlocal count
        count += 1

    bus.subscribe("*", handler)
    await bus.publish(TrustEvent(name="a", component="c"))
    await bus.publish(TrustEvent(name="b", component="c"))
    assert count == 2


async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    calls = 0

    async def handler(_: TrustEvent) -> None:
        nonlocal calls
        calls += 1

    unsub = bus.subscribe("x", handler)
    await bus.publish(TrustEvent(name="x", component="c"))
    unsub()
    await bus.publish(TrustEvent(name="x", component="c"))
    assert calls == 1


async def test_publish_with_no_handlers_is_noop() -> None:
    bus = EventBus()
    await bus.publish(TrustEvent(name="nobody", component="c"))  # must not raise


async def test_failing_handler_does_not_block_others() -> None:
    bus = EventBus()
    delivered = False

    async def bad(_: TrustEvent) -> None:
        raise RuntimeError("boom")

    async def good(_: TrustEvent) -> None:
        nonlocal delivered
        delivered = True

    bus.subscribe("e", bad)
    bus.subscribe("e", good)
    await bus.publish(TrustEvent(name="e", component="c"))
    assert delivered is True
