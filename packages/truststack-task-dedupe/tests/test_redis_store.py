"""RedisTaskStore tests driven by an offline fakeredis async client.

These run fully offline: ``fakeredis.aioredis.FakeRedis`` provides an in-process
implementation of the ``redis.asyncio`` surface the store exercises (HSET /
HGETALL / SADD / SISMEMBER via a transactional pipeline). No real Redis server
is contacted. The whole module is skipped if ``fakeredis`` is unavailable.
"""

from __future__ import annotations

import pytest

from task_dedupe import DedupeEngine, RedisTaskStore, Task

fakeredis = pytest.importorskip("fakeredis")


def _client() -> object:
    # Decode responses off so the store sees bytes (the production default for
    # redis.asyncio), exercising the bytes-decoding path in _decode_record.
    return fakeredis.aioredis.FakeRedis()


def test_constructing_without_url_or_client_raises() -> None:
    with pytest.raises(ValueError):
        RedisTaskStore()


async def test_add_and_all_roundtrip() -> None:
    store = RedisTaskStore(client=_client())
    await store.add("id-1", "fp-1", Task(title="ship release", due="next week", project="core"))
    await store.add("id-2", "fp-2", Task(title="write changelog"))

    records = await store.all()
    assert [r.id for r in records] == ["id-1", "id-2"]  # sorted by id
    first = next(r for r in records if r.id == "id-1")
    assert first.fingerprint == "fp-1"
    assert first.task.project == "core"
    assert first.task.due == "next week"
    await store.close()


async def test_all_on_empty_store_returns_empty_list() -> None:
    store = RedisTaskStore(client=_client())
    assert await store.all() == []
    await store.close()


async def test_records_are_sorted_by_id_deterministically() -> None:
    store = RedisTaskStore(client=_client())
    for i in (3, 1, 2):
        await store.add(f"id-{i}", f"fp-{i}", Task(title=f"task {i}"))
    records = await store.all()
    assert [r.id for r in records] == ["id-1", "id-2", "id-3"]
    await store.close()


async def test_has_fingerprint_tracks_membership() -> None:
    store = RedisTaskStore(client=_client())
    assert await store.has_fingerprint("fp-x") is False
    await store.add("id-1", "fp-x", Task(title="alpha"))
    assert await store.has_fingerprint("fp-x") is True
    assert await store.has_fingerprint("fp-missing") is False
    await store.close()


async def test_namespace_isolates_keys() -> None:
    shared = _client()
    a = RedisTaskStore(client=shared, namespace="ns-a")
    b = RedisTaskStore(client=shared, namespace="ns-b")
    await a.add("id-1", "fp-1", Task(title="only in a"))

    assert [r.task.title for r in await a.all()] == ["only in a"]
    assert await b.all() == []
    assert await b.has_fingerprint("fp-1") is False


async def test_re_adding_same_id_overwrites() -> None:
    store = RedisTaskStore(client=_client())
    await store.add("id-1", "fp-1", Task(title="first"))
    await store.add("id-1", "fp-1", Task(title="second"))
    records = await store.all()
    assert len(records) == 1
    assert records[0].task.title == "second"
    await store.close()


async def test_close_without_client_is_safe() -> None:
    # url given but no client created yet -> close() must not raise.
    store = RedisTaskStore(url="redis://localhost:6379/0")
    await store.close()


async def test_engine_dedupes_through_redis_store() -> None:
    engine = DedupeEngine(store=RedisTaskStore(client=_client()))
    first = await engine.check(Task(title="Send Q3 report to Dana", due="tomorrow"))
    assert first.duplicate is False
    assert first.existing_task_id is None
    second = await engine.check(Task(title="send the q3 report to dana", due="tomorrow"))
    assert second.duplicate is True
    assert second.existing_task_id is not None
