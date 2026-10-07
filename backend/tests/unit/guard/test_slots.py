from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager

import pytest

from tayyari.core.errors import AppError, RateLimited
from tayyari.guard import slots
from tayyari.guard.slots import BUSY_RETRY_AFTER_S, StreamSlots
from tests.unit.api.helpers import RecordingLog

BUSY = "Tayyari is busy answering other chats. Please try again in a few seconds."


def make(per_key: int = 2, per_process: int = 24) -> StreamSlots:
    return StreamSlots(per_key, per_process, busy_message=BUSY)


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    recorder = RecordingLog()
    monkeypatch.setattr(slots, "log", recorder)
    return recorder


async def refused(pool: StreamSlots, key: str | None) -> RateLimited:
    with pytest.raises(RateLimited) as info:
        async with pool.acquire(key):
            raise AssertionError("must not get a slot")  # pragma: no cover
    return info.value


# ───────────────────────────── per client ─────────────────────────────


async def test_acquire_is_an_async_context_manager() -> None:
    pool = make()
    cm = pool.acquire("a")
    assert isinstance(cm, AbstractAsyncContextManager)
    async with cm:
        assert pool.active == 1
        assert pool.active_for("a") == 1
    assert pool.active == 0


async def test_a_client_gets_its_limit_and_the_next_stream_is_a_429() -> None:
    pool = make(per_key=2)
    async with pool.acquire("a"), pool.acquire("a"):
        err = await refused(pool, "a")
    assert isinstance(err, AppError)
    assert err.status == 429
    assert err.code == "rate_limited"
    assert err.user_message == BUSY
    assert err.retry_after_s == 10
    assert err.retry_after_header() == "10"
    assert BUSY_RETRY_AFTER_S == 10
    assert "client" in (err.detail or "")  # for the server log only


async def test_another_client_is_not_affected_by_a_full_one() -> None:
    pool = make(per_key=1)
    async with pool.acquire("a"):
        await refused(pool, "a")
        async with pool.acquire("b"):
            assert pool.active == 2
            assert pool.active_for("a") == pool.active_for("b") == 1


async def test_a_slot_is_free_again_after_the_block_ends() -> None:
    pool = make(per_key=1)
    for _ in range(5):
        async with pool.acquire("a"):
            await refused(pool, "a")
    assert pool.active == 0
    assert pool.active_for("a") == 0


async def test_a_finished_client_leaves_no_entry_behind() -> None:
    pool = make(per_key=2)
    for i in range(100):
        async with pool.acquire(f"client{i}"):
            pass
    assert pool._by_key == {}


async def test_clients_without_an_address_share_one_anonymous_bucket() -> None:
    pool = make(per_key=2)
    async with pool.acquire(None), pool.acquire(None):
        err = await refused(pool, None)
        assert err.status == 429
        async with pool.acquire("a"):  # a client with a known address has its own
            assert pool.active == 3


async def test_the_anonymous_bucket_is_not_a_real_key() -> None:
    pool = make(per_key=1)
    async with pool.acquire(None):
        async with pool.acquire(""):  # an empty string is a key like any other, not "unknown"
            assert pool.active == 2


# ───────────────────────────── per process ─────────────────────────────


async def test_the_process_limit_applies_across_clients() -> None:
    pool = make(per_key=5, per_process=3)
    async with pool.acquire("a"), pool.acquire("b"), pool.acquire("c"):
        err = await refused(pool, "d")
        assert err.status == 429
        assert err.user_message == BUSY
        assert err.retry_after_s == 10
        assert "process" in (err.detail or "")
        assert pool.active == 3
    async with pool.acquire("d"):
        assert pool.active == 1


async def test_a_refused_stream_leaves_every_counter_as_it_was() -> None:
    pool = make(per_key=1, per_process=2)
    async with pool.acquire("a"), pool.acquire("b"):
        before = (pool.active, dict(pool._by_key))
        await refused(pool, "a")  # the client limit
        await refused(pool, "c")  # the process limit
        assert (pool.active, dict(pool._by_key)) == before
    assert pool.active == 0
    assert pool._by_key == {}


async def test_refusing_does_not_wait() -> None:
    pool = make(per_key=1)
    async with pool.acquire("a"):
        # wait_for would raise TimeoutError if acquire queued; it must raise RateLimited at once
        with pytest.raises(RateLimited):
            await asyncio.wait_for(_enter(pool, "a"), timeout=0.05)


async def _enter(pool: StreamSlots, key: str | None) -> None:
    async with pool.acquire(key):
        pass  # pragma: no cover


async def test_a_refusal_is_logged_without_the_client_key(log: RecordingLog) -> None:
    pool = make(per_key=1)
    async with pool.acquire("secret-client-key"):
        await refused(pool, "secret-client-key")
    assert log.events("info") == ["stream_slots_full"]
    assert log.records[0][2]["scope"] == "client"
    assert "secret-client-key" not in str(log.records)


# ───────────────────────────── 0 switches a limit off ─────────────────────────────


async def test_zero_per_client_switches_the_client_limit_off() -> None:
    pool = make(per_key=0, per_process=10)
    holders = [pool.acquire("a") for _ in range(10)]
    for cm in holders:
        await cm.__aenter__()
    assert pool.active_for("a") == 10
    await refused(pool, "a")  # but the process limit still applies
    for cm in holders:
        await cm.__aexit__(None, None, None)
    assert pool.active == 0


async def test_zero_per_process_switches_the_process_limit_off() -> None:
    pool = make(per_key=2, per_process=0)
    holders = [pool.acquire(f"client{i}") for i in range(200)]
    for cm in holders:
        await cm.__aenter__()
    assert pool.active == 200
    await refused_twice(pool)
    for cm in holders:
        await cm.__aexit__(None, None, None)
    assert pool.active == 0


async def refused_twice(pool: StreamSlots) -> None:
    holders = [pool.acquire("one"), pool.acquire("one")]
    for cm in holders:
        await cm.__aenter__()
    await refused(pool, "one")  # the per-client limit is still on
    for cm in holders:
        await cm.__aexit__(None, None, None)


async def test_zero_for_both_means_no_limit_at_all() -> None:
    pool = make(per_key=0, per_process=0)
    holders = [pool.acquire("a") for _ in range(500)]
    for cm in holders:
        await cm.__aenter__()
    assert pool.active == 500
    for cm in holders:
        await cm.__aexit__(None, None, None)
    assert pool.active == 0
    assert pool._by_key == {}


def test_negative_limits_are_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        StreamSlots(-1, 5, busy_message=BUSY)
    with pytest.raises(ValueError, match="negative"):
        StreamSlots(1, -5, busy_message=BUSY)


# ───────────────────────────── always released ─────────────────────────────


async def test_the_slot_is_released_when_the_body_raises() -> None:
    pool = make(per_key=1, per_process=1)
    with pytest.raises(RuntimeError, match="boom"):
        async with pool.acquire("a"):
            raise RuntimeError("boom")
    assert pool.active == 0
    assert pool.active_for("a") == 0
    async with pool.acquire("a"):  # and it can be taken again
        pass


async def test_the_slot_is_released_when_the_task_is_cancelled() -> None:
    pool = make(per_key=1, per_process=1)
    started = asyncio.Event()

    async def stream() -> None:
        async with pool.acquire("a"):
            started.set()
            await asyncio.sleep(3600)

    task = asyncio.create_task(stream())
    await started.wait()
    assert pool.active == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.active == 0
    assert pool._by_key == {}


async def test_a_task_cancelled_twice_still_releases() -> None:
    pool = make(per_key=1)
    started = asyncio.Event()

    async def stream() -> None:
        async with pool.acquire("a"):
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                await asyncio.sleep(0)  # the clean-up itself is interrupted by a second cancel

    task = asyncio.create_task(stream())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.active == 0
    assert pool.active_for("a") == 0


async def test_a_body_that_leaves_through_return_or_break_releases() -> None:
    pool = make(per_key=1)

    async def returns_early() -> str:
        async with pool.acquire("a"):
            return "early"

    assert await returns_early() == "early"
    for _ in range(3):
        async with pool.acquire("a"):
            break
    assert pool.active == 0


# ───────────────────────────── concurrency ─────────────────────────────


async def test_concurrent_streams_are_counted_exactly_per_client() -> None:
    pool = make(per_key=5, per_process=1000)
    gate = asyncio.Event()
    decided = asyncio.Event()
    outcomes: list[bool] = []

    def record(granted: bool) -> None:
        outcomes.append(granted)
        if len(outcomes) == 100:
            decided.set()

    async def stream() -> None:
        try:
            async with pool.acquire("a"):
                record(True)
                await gate.wait()
        except RateLimited:
            record(False)

    tasks = [asyncio.create_task(stream()) for _ in range(100)]
    await decided.wait()
    assert outcomes.count(True) == 5
    assert outcomes.count(False) == 95
    assert pool.active == 5
    gate.set()
    await asyncio.gather(*tasks)
    assert pool.active == 0
    assert pool._by_key == {}


async def test_concurrent_streams_are_counted_exactly_per_process() -> None:
    pool = make(per_key=1, per_process=24)
    gate = asyncio.Event()
    decided = asyncio.Event()
    outcomes: list[bool] = []

    def record(granted: bool) -> None:
        outcomes.append(granted)
        if len(outcomes) == 200:
            decided.set()

    async def stream(i: int) -> None:
        try:
            async with pool.acquire(f"client{i}"):
                record(True)
                await gate.wait()
        except RateLimited:
            record(False)

    tasks = [asyncio.create_task(stream(i)) for i in range(200)]
    await decided.wait()
    assert outcomes.count(True) == 24
    assert pool.active == 24
    gate.set()
    await asyncio.gather(*tasks)
    assert pool.active == 0


async def test_slots_are_reused_as_streams_finish() -> None:
    pool = make(per_key=2, per_process=4)
    served = 0

    async def stream(i: int) -> None:
        nonlocal served
        async with pool.acquire(f"client{i % 4}"):
            await asyncio.sleep(0)
            served += 1

    for _ in range(10):
        await asyncio.gather(*(stream(i) for i in range(4)))
    assert served == 40
    assert pool.active == 0
