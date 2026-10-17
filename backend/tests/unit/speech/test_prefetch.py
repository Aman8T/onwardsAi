"""The voice made ahead: a line being made is shared by everyone who asks for it, and `SpeechService.prefetch` makes the lines an
interview will probably say next in the background, in order, a few at a time, within the budgets, without ever raising."""

from __future__ import annotations

import asyncio
import gc
from typing import Any

import pytest

from tayyari.core.errors import ServiceUnavailable
from tayyari.speech.engine import SpeechUnavailable
from tests.fakes.billing import captured_events
from tests.fakes.speech import MP3, FakeSpeechEngine
from tests.unit.speech.support import OTHER_SESSION, SESSION, make_rig

LINE = "Walk me through the Kitebird checkout rebuild. What did you change first, and why?"
PUSH = "When the cart update failed, what did the person see?"
FLOOR = "Which part of the checkout was yours, in one sentence?"
NEXT = "Your resume says checkout LCP went from 4.1s to 1.9s at Kitebird. How did you find what was slow?"


async def settle() -> None:
    """Let every task that can run, run, until each is waiting for something the test controls."""
    for _ in range(60):
        await asyncio.sleep(0)


def lines_of(n: int, size: int = 30) -> list[str]:
    """``n`` different lines of ``size`` characters."""
    return [f"Line {i:02d} ".ljust(size, "x") for i in range(n)]


# ───────────────────────── one line being made is made once ─────────────────────────


async def test_two_requests_for_a_line_being_made_share_one_engine_call() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine)
    first = asyncio.create_task(rig.service.speak(SESSION, LINE))
    second = asyncio.create_task(rig.service.speak(OTHER_SESSION, f"  {LINE.replace(' ', '  ')} "))
    await settle()
    assert (
        len(engine.spoken) == 1
    )  # the second one is waiting for the first one's line, not asking Sarvam again
    assert not first.done() and not second.done()
    hold.set()
    assert await first == MP3 and await second == MP3
    assert len(engine.spoken) == 1
    assert rig.used_today() == {"stt_s": 0, "tts_chars": len(LINE)}  # charged once
    assert await rig.service.speak(SESSION, LINE) == MP3 and len(engine.spoken) == 1  # and now from memory


async def test_a_different_pace_is_a_different_line() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine)
    normal = asyncio.create_task(rig.service.speak(SESSION, LINE))
    slower = asyncio.create_task(rig.service.speak(SESSION, LINE, 0.85))
    await settle()
    assert [(s.text, s.pace) for s in engine.spoken] == [(LINE, 1.0), (LINE, 0.85)]
    hold.set()
    await asyncio.gather(normal, slower)


async def test_a_failing_line_reaches_everyone_waiting_for_it_and_is_tried_again_next_time() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold, speak_error=SpeechUnavailable(detail="tts 429 rate_limit"))
    rig = make_rig(engine=engine)
    waiting = [asyncio.create_task(rig.service.speak(sid, LINE)) for sid in (SESSION, OTHER_SESSION)]
    await settle()
    hold.set()
    for task in waiting:
        with pytest.raises(SpeechUnavailable):
            await task
    assert len(engine.spoken) == 1
    engine.speak_error = None
    assert await rig.service.speak(SESSION, LINE) == MP3
    assert len(engine.spoken) == 2  # nothing was kept from the failure


async def test_a_browser_that_hangs_up_does_not_stop_the_line_others_are_waiting_for() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine)
    gone = asyncio.create_task(rig.service.speak(SESSION, LINE))
    stays = asyncio.create_task(rig.service.speak(OTHER_SESSION, LINE))
    await settle()
    gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gone
    hold.set()
    assert await stays == MP3
    assert len(engine.spoken) == 1
    assert await rig.service.speak(SESSION, LINE) == MP3 and len(engine.spoken) == 1  # it reached the cache


async def test_a_line_whose_only_requester_hung_up_still_ends_up_in_the_cache() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine)
    gone = asyncio.create_task(rig.service.speak(SESSION, LINE))
    await settle()
    gone.cancel()
    hold.set()
    await rig.service.drain()
    assert await rig.service.speak(SESSION, LINE) == MP3
    assert len(engine.spoken) == 1


async def test_a_line_being_made_is_joined_even_when_the_sessions_share_is_spent() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine, tts_session_chars=len(LINE))
    first = asyncio.create_task(rig.service.speak(SESSION, LINE))
    await settle()
    # SESSION has used up its share by now (the line is charged as it starts); a second request for the very line joins it
    second = asyncio.create_task(rig.service.speak(SESSION, LINE))
    await settle()
    hold.set()
    assert await first == MP3 and await second == MP3
    assert len(engine.spoken) == 1
    # a different line is a new charge, and the share is gone
    with pytest.raises(SpeechUnavailable):
        await rig.service.speak(SESSION, PUSH)


# ───────────────────────── prefetch ─────────────────────────


async def test_prefetch_then_speak_makes_one_engine_call() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine)
    assert rig.service.prefetch(SESSION, [LINE]) == 1
    await rig.service.drain()
    assert [s.text for s in engine.spoken] == [LINE]
    assert await rig.service.speak(SESSION, LINE) == MP3
    assert len(engine.spoken) == 1
    assert rig.used_today() == {"stt_s": 0, "tts_chars": len(LINE)}  # charged once, by the prefetch


async def test_a_request_for_a_line_being_prefetched_joins_it() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine)
    rig.service.prefetch(SESSION, [LINE])
    await settle()
    assert len(engine.spoken) == 1  # Sarvam is working on it
    asked = asyncio.create_task(rig.service.speak(SESSION, LINE))
    await settle()
    assert len(engine.spoken) == 1 and not asked.done()
    hold.set()
    assert await asked == MP3
    await rig.service.drain()
    assert len(engine.spoken) == 1


async def test_a_request_for_a_line_waiting_its_turn_is_not_held_up_by_the_queue() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine, tts_prefetch_concurrency=1)
    rig.service.prefetch(SESSION, [LINE, PUSH, FLOOR])
    await settle()
    assert [s.text for s in engine.spoken] == [LINE]  # the other two wait for the one place
    asked = asyncio.create_task(rig.service.speak(SESSION, FLOOR))  # a person is waiting for this one now
    await settle()
    assert [s.text for s in engine.spoken] == [LINE, FLOOR]
    hold.set()
    assert await asked == MP3
    await rig.service.drain()
    assert sorted(s.text for s in engine.spoken) == sorted(
        [LINE, PUSH, FLOOR]
    )  # and FLOOR was not made twice


async def test_prefetch_makes_the_lines_in_order_and_charges_each() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine, tts_prefetch_concurrency=1)
    assert rig.service.prefetch(SESSION, [LINE, PUSH, FLOOR, NEXT]) == 4
    await rig.service.drain()
    assert [s.text for s in engine.spoken] == [LINE, PUSH, FLOOR, NEXT]
    assert rig.used_today()["tts_chars"] == len(LINE) + len(PUSH) + len(FLOOR) + len(NEXT)
    for text in (LINE, PUSH, FLOOR, NEXT):
        assert await rig.service.speak(SESSION, text) == MP3
    assert len(engine.spoken) == 4


async def test_prefetch_leaves_alone_lines_already_made_being_made_or_listed_twice() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine)
    hold.set()
    await rig.service.speak(SESSION, LINE)  # made before
    hold.clear()
    started = rig.service.prefetch(SESSION, [PUSH])
    await settle()  # PUSH is being made
    scheduled = rig.service.prefetch(SESSION, [LINE, PUSH, f" {FLOOR} ", FLOOR, "", "   "])
    assert (started, scheduled) == (1, 1)
    hold.set()
    await rig.service.drain()
    assert [s.text for s in engine.spoken] == [LINE, PUSH, FLOOR]


async def test_prefetch_skips_lines_too_long_to_be_spoken() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine)
    assert rig.service.prefetch(SESSION, ["word " * 400, LINE]) == 1
    await rig.service.drain()
    assert [s.text for s in engine.spoken] == [LINE]


async def test_prefetch_uses_the_pace_the_session_last_asked_for() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine, tts_prefetch_concurrency=1)
    rig.service.prefetch(SESSION, [LINE])  # nothing asked for yet: the normal voice
    await rig.service.speak(SESSION, FLOOR, 0.85)  # this browser chose the slower voice
    rig.service.prefetch(SESSION, [PUSH])
    rig.service.prefetch(OTHER_SESSION, [NEXT])  # another session is not affected
    rig.service.prefetch(SESSION, [f"{PUSH} Again."], pace=1.5)  # said outright
    await rig.service.drain()
    paces = {s.text: s.pace for s in engine.spoken}
    assert paces == {LINE: 1.0, FLOOR: 0.85, PUSH: 0.85, NEXT: 1.0, f"{PUSH} Again.": 1.5}
    assert await rig.service.speak(SESSION, PUSH, 0.85) == MP3
    assert len(engine.spoken) == 5  # the slower voice's line was ready


# ───────────────────────── the budgets ─────────────────────────


async def test_prefetch_skips_a_line_the_days_budget_would_not_cover() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine, tts_daily_chars=50, tts_prefetch_concurrency=1)
    (first, second), short = lines_of(2), "Short one?"
    assert (
        rig.service.prefetch(SESSION, [first, second, short]) == 3
    )  # it does not look at the budget while scheduling
    await rig.service.drain()  # ... and nothing is raised for the line that does not fit
    assert [s.text for s in engine.spoken] == [
        first,
        short,
    ]  # 30 used, 20 left: the second 30 does not fit, 10 does
    assert rig.used_today()["tts_chars"] == 40


async def test_prefetch_skips_everything_once_the_day_is_used_up() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine, tts_daily_chars=60)
    await rig.service.meter.charge(OTHER_SESSION, tts_chars=60)  # another interview spent the day
    assert rig.service.prefetch(SESSION, lines_of(3)) == 3
    await rig.service.drain()
    assert engine.calls == 0
    assert rig.used_today()["tts_chars"] == 60


async def test_prefetch_skips_a_line_the_sessions_share_would_not_cover() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine, tts_session_chars=70, tts_prefetch_concurrency=1)
    mine = lines_of(4)
    rig.service.prefetch(SESSION, mine)
    rig.service.prefetch(OTHER_SESSION, ["Another one's line, 30 characters"])
    await rig.service.drain()
    made = [s.text for s in engine.spoken]
    assert made[:2] == mine[:2] and "Another one's line, 30 characters" in made
    assert mine[2] not in made and mine[3] not in made  # 60 used, 10 left
    assert len(made) == 3


async def test_prefetch_does_nothing_when_a_budget_is_switched_off_by_a_zero_cap() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine, tts_daily_chars=0)
    rig.service.prefetch(SESSION, [LINE])
    await rig.service.drain()
    assert engine.calls == 0


async def test_a_prefetch_does_not_use_the_last_credits_a_person_may_be_waiting_for() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine, tts_daily_chars=len(LINE) + 5)
    rig.service.prefetch(SESSION, [LINE, PUSH])
    await rig.service.drain()
    assert [s.text for s in engine.spoken] == [LINE]
    assert (
        await rig.service.speak(SESSION, PUSH) == MP3
    )  # a person asking still gets the credits that are left
    assert len(engine.spoken) == 2


# ───────────────────────── failures ─────────────────────────


@pytest.mark.parametrize(
    "error",
    [SpeechUnavailable(detail="tts 429 rate_limit_exceeded_error"), RuntimeError(f"it broke on: {LINE}")],
)
async def test_a_prefetch_with_a_failing_engine_never_raises_and_leaves_nothing_cached(
    error: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    unretrieved: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: unretrieved.append(context))
    engine = FakeSpeechEngine(speak_error=error)
    rig = make_rig(engine=engine)
    with captured_events(monkeypatch, "tayyari.speech.service") as logs:
        assert rig.service.prefetch(SESSION, [LINE, PUSH]) == 2
        await rig.service.drain()
        gc.collect()
        await settle()
    assert unretrieved == []  # no "exception was never retrieved" for a line nobody waited for
    assert len(engine.spoken) == 2
    failed = [e for e in logs if e["event"] == "speech_prefetch_failed"]
    assert len(failed) == 2 and all(set(e) >= {"chars", "reason"} for e in failed)
    assert all(LINE not in str(e) and PUSH not in str(e) for e in logs)  # sizes and reasons, never the text
    engine.speak_error = None
    assert await rig.service.speak(SESSION, LINE) == MP3
    assert len(engine.spoken) == 3  # the failed line was not kept


async def test_a_failed_prefetch_does_not_poison_the_next_one() -> None:
    engine = FakeSpeechEngine(speak_error=SpeechUnavailable())
    rig = make_rig(engine=engine)
    rig.service.prefetch(SESSION, [LINE])
    await rig.service.drain()
    engine.speak_error = None
    assert rig.service.prefetch(SESSION, [LINE]) == 1
    await rig.service.drain()
    assert len(engine.spoken) == 2
    assert await rig.service.speak(SESSION, LINE) == MP3 and len(engine.spoken) == 2


async def test_prefetch_with_bad_input_never_raises() -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine)
    mixed: Any = [LINE, None, 7, b"bytes", PUSH]
    assert rig.service.prefetch(SESSION, mixed) == 2  # what is not text is passed over
    assert rig.service.prefetch(SESSION, 5) == 0  # type: ignore[arg-type]
    await rig.service.drain()
    assert [s.text for s in engine.spoken] == [LINE, PUSH]


def test_prefetch_outside_an_event_loop_never_raises() -> None:
    rig = make_rig()
    assert rig.service.prefetch(SESSION, [LINE]) == 0


# ───────────────────────── concurrency ─────────────────────────


async def test_no_more_lines_are_made_at_a_time_than_the_cap() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine, tts_prefetch_concurrency=2)
    lines = lines_of(6)
    rig.service.prefetch(SESSION, lines)
    await settle()
    assert (engine.active, len(engine.spoken)) == (2, 2)
    assert [s.text for s in engine.spoken] == lines[:2]  # in order
    hold.set()
    await rig.service.drain()
    assert engine.peak == 2
    assert [s.text for s in engine.spoken] == lines


async def test_the_cap_is_shared_by_every_prefetch_in_the_process() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine, tts_prefetch_concurrency=3)
    rig.service.prefetch(SESSION, lines_of(3))
    rig.service.prefetch(OTHER_SESSION, [f"{t}!" for t in lines_of(3)])
    await settle()
    assert (engine.active, len(engine.spoken)) == (3, 3)
    hold.set()
    await rig.service.drain()
    assert engine.peak == 3 and len(engine.spoken) == 6


async def test_a_cap_of_one_makes_one_line_at_a_time() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine, tts_prefetch_concurrency=1)
    rig.service.prefetch(SESSION, lines_of(3))
    await settle()
    assert (engine.active, len(engine.spoken)) == (1, 1)
    hold.set()
    await rig.service.drain()
    assert engine.peak == 1


async def test_a_failed_line_gives_its_place_to_the_next() -> None:
    engine = FakeSpeechEngine(speak_error=SpeechUnavailable())
    rig = make_rig(engine=engine, tts_prefetch_concurrency=1)
    rig.service.prefetch(SESSION, lines_of(4))
    await rig.service.drain()
    assert len(engine.spoken) == 4  # every line was tried; none held the place


# ───────────────────────── when it is off ─────────────────────────


@pytest.mark.parametrize(
    "settings",
    [
        {"tts_prefetch": False},
        {"enabled": False},
        {"tts_cache_items": 0},
    ],
)
async def test_prefetch_is_off_when_the_settings_say_so(settings: dict[str, Any]) -> None:
    engine = FakeSpeechEngine()
    rig = make_rig(engine=engine, **settings)
    assert rig.service.prefetching is False
    assert rig.service.prefetch(SESSION, [LINE, PUSH]) == 0
    await rig.service.drain()
    assert engine.calls == 0


async def test_prefetch_is_off_without_an_engine() -> None:
    rig = make_rig(silent=True)
    assert rig.service.prefetching is False and rig.service.prefetching_followups is False
    assert rig.service.prefetch(SESSION, [LINE]) == 0
    await rig.service.drain()


async def test_followups_can_be_switched_off_alone() -> None:
    assert make_rig().service.prefetching_followups is True
    rig = make_rig(tts_prefetch_followups=False)
    assert rig.service.prefetching is True and rig.service.prefetching_followups is False


async def test_sharing_a_line_in_flight_does_not_need_the_prefetch_setting() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine, tts_prefetch=False)
    both = [asyncio.create_task(rig.service.speak(SESSION, LINE)) for _ in range(2)]
    await settle()
    hold.set()
    await asyncio.gather(*both)
    assert len(engine.spoken) == 1


# ───────────────────────── the tasks it starts ─────────────────────────


async def test_the_tasks_are_dropped_once_they_are_done() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine, tts_prefetch_concurrency=1)
    rig.service.prefetch(SESSION, lines_of(3))
    await settle()
    gc.collect()  # the service holds the tasks: a collection does not lose a line that is waiting for its place
    assert (
        len(engine.spoken) == 1 and len(rig.service._background) == 3 + 1
    )  # three waiting or making, one being made
    hold.set()
    await rig.service.drain()
    assert len(engine.spoken) == 3
    assert not rig.service._background and not rig.service._making and not rig.service._queued


async def test_closing_the_service_stops_what_is_in_progress() -> None:
    hold = asyncio.Event()
    engine = FakeSpeechEngine(hold=hold)
    rig = make_rig(engine=engine)
    rig.service.prefetch(SESSION, lines_of(5))
    await settle()
    await rig.service.aclose()
    assert not rig.service._background and not rig.service._making
    hold.set()
    await settle()
    await rig.service.aclose()  # twice is fine
    assert engine.active == 0
    engine.hold = None
    assert await rig.service.speak(SESSION, lines_of(1)[0]) == MP3  # and it still works for a new request


async def test_a_line_that_is_over_leaves_the_making_table_whatever_happened() -> None:
    engine = FakeSpeechEngine(speak_error=ServiceUnavailable())
    rig = make_rig(engine=engine)
    with pytest.raises(ServiceUnavailable):
        await rig.service.speak(SESSION, LINE)
    assert not rig.service._making and not rig.service._background
