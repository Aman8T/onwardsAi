"""The soft budget: one kv record per UTC day (shared by replicas), a bounded in-process count per session, and what happens
when the store misbehaves."""

from __future__ import annotations

import asyncio
from typing import Any

from tayyari.core.settings import SpeechSettings
from tayyari.speech.meter import USAGE_KEY_PREFIX, SpeechMeter
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeKvRepo, FakeRepositories
from tests.unit.speech.support import NOON, OTHER_SESSION, SESSION


def make(**settings: Any) -> tuple[SpeechMeter, FakeKvRepo, FakeClock]:
    clock = FakeClock(NOON)
    kv = FakeRepositories(clock).kv
    return SpeechMeter(kv, clock, SpeechSettings(**settings)), kv, clock


async def test_nothing_is_used_at_first_and_the_limits_are_the_settings() -> None:
    meter, kv, _ = make(
        stt_daily_seconds=100, tts_daily_chars=500, stt_session_seconds=40, tts_session_chars=200
    )
    left = await meter.remaining(SESSION)
    assert (left.stt_day, left.tts_day, left.stt_session, left.tts_session) == (100, 500, 40, 200)
    assert (left.stt_s, left.tts_chars) == (40, 200)  # the smaller of the day's and the session's
    assert kv.values == {}  # reading costs nothing and writes nothing


async def test_without_a_session_only_the_day_counts() -> None:
    meter, _, _ = make(stt_daily_seconds=100, stt_session_seconds=40)
    left = await meter.remaining(None)
    assert (left.stt_session, left.tts_session) == (None, None)
    assert left.stt_s == 100


async def test_a_charge_is_kept_for_the_day_in_one_kv_record() -> None:
    meter, kv, _ = make()
    await meter.charge(SESSION, stt_s=7, tts_chars=120)
    await meter.charge(SESSION, stt_s=3)
    assert meter.key() == f"{USAGE_KEY_PREFIX}2026-10-17" == "speech:usage:2026-10-17"
    assert kv.values[meter.key()] == {"stt_s": 10, "tts_chars": 120}


async def test_the_day_is_shared_between_replicas_and_sessions_are_not() -> None:
    first, kv, clock = make(stt_daily_seconds=50, stt_session_seconds=30)
    second = SpeechMeter(kv, clock, SpeechSettings(stt_daily_seconds=50, stt_session_seconds=30))
    await first.charge(SESSION, stt_s=20)
    left = await second.remaining(SESSION)
    assert left.stt_day == 30  # the other replica's seconds count against the day
    assert left.stt_session == 30  # but its session starts from zero here: the per-session cap is approximate


async def test_each_session_has_its_own_share_of_the_day() -> None:
    meter, _, _ = make(stt_daily_seconds=100, stt_session_seconds=30)
    await meter.charge(SESSION, stt_s=30)
    mine, theirs = await meter.remaining(SESSION), await meter.remaining(OTHER_SESSION)
    assert (mine.stt_session, mine.stt_s) == (0, 0)
    assert (theirs.stt_session, theirs.stt_day, theirs.stt_s) == (30, 70, 30)


async def test_a_new_utc_day_starts_from_zero() -> None:
    meter, kv, clock = make(stt_daily_seconds=100)
    await meter.charge(SESSION, stt_s=100)
    assert (await meter.remaining(None)).stt_day == 0
    clock.advance(12 * 3600)  # 00:00 UTC the next day
    assert (await meter.remaining(None)).stt_day == 100
    assert set(kv.values) == {
        "speech:usage:2026-10-17"
    }  # yesterday's record stays; nothing is shared across days
    await meter.charge(SESSION, stt_s=1)
    assert set(kv.values) == {"speech:usage:2026-10-17", "speech:usage:2026-10-18"}


async def test_what_is_left_never_goes_below_zero() -> None:
    meter, _, _ = make(stt_daily_seconds=10, tts_daily_chars=10, stt_session_seconds=10, tts_session_chars=10)
    await meter.charge(SESSION, stt_s=500, tts_chars=500)
    left = await meter.remaining(SESSION)
    assert (left.stt_day, left.tts_day, left.stt_session, left.tts_session) == (0, 0, 0, 0)


async def test_a_zero_charge_writes_nothing_and_a_negative_one_counts_as_zero() -> None:
    meter, kv, _ = make()
    await meter.charge(SESSION)
    await meter.charge(SESSION, stt_s=-5, tts_chars=-5)
    assert kv.values == {}


async def test_concurrent_charges_in_one_process_are_all_counted() -> None:
    meter, kv, _ = make()
    await asyncio.gather(*(meter.charge(SESSION, stt_s=1, tts_chars=2) for _ in range(25)))
    assert kv.values[meter.key()] == {"stt_s": 25, "tts_chars": 50}


async def test_session_counts_are_bounded_and_the_least_recent_is_forgotten_first() -> None:
    clock = FakeClock(NOON)
    kv = FakeRepositories(clock).kv
    meter = SpeechMeter(kv, clock, SpeechSettings(stt_session_seconds=30), max_sessions=3)
    for index in range(5):
        await meter.charge(f"{index:032x}", stt_s=10)
    assert len(meter) == 3
    forgotten = await meter.remaining(f"{0:032x}")
    kept = await meter.remaining(f"{4:032x}")
    assert forgotten.stt_session == 30  # dropped: it starts again from zero (the cap only loosens)
    assert kept.stt_session == 20


async def test_a_damaged_record_counts_as_empty() -> None:
    meter, kv, _ = make(stt_daily_seconds=100)
    for junk in ("text", 5, [1, 2], {"stt_s": "9", "tts_chars": -4}, {"stt_s": True}):
        kv.values[meter.key()] = junk
        left = await meter.remaining(None)
        assert left.stt_day == 100, junk


class BrokenKv:
    """A kv store whose reads (or writes) fail."""

    def __init__(self, *, reads: bool = True, writes: bool = True) -> None:
        self.reads, self.writes = reads, writes
        self.stored: dict[str, Any] = {}

    async def get(self, key: str) -> Any | None:
        if self.reads:
            raise ConnectionError("database is down")
        return self.stored.get(key)

    async def set(self, key: str, value: Any) -> None:
        if self.writes:
            raise ConnectionError("database is down")
        self.stored[key] = value

    async def get_or_create_secret(
        self, key: str, nbytes: int = 32
    ) -> bytes:  # pragma: no cover - not used here
        raise NotImplementedError


async def test_when_the_day_cannot_be_read_it_counts_as_used_up() -> None:
    meter = SpeechMeter(BrokenKv(), FakeClock(NOON), SpeechSettings())
    left = await meter.remaining(SESSION)
    assert (left.stt_day, left.tts_day) == (0, 0)  # the browser's own speech takes over: it costs nothing
    await meter.charge(SESSION, stt_s=5)  # and a charge does not raise


async def test_a_charge_that_cannot_be_written_is_dropped_without_failing_the_request() -> None:
    kv = BrokenKv(reads=False)
    meter = SpeechMeter(kv, FakeClock(NOON), SpeechSettings(stt_session_seconds=30))
    await meter.charge(SESSION, stt_s=5)
    assert kv.stored == {}
    assert (
        await meter.remaining(SESSION)
    ).stt_session == 25  # the session's own count is in memory and still kept
