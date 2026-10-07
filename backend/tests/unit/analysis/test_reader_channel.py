"""`ReaderChannel`: reader cards are re-sent at most every 0.26 s, the last state always gets through."""

from __future__ import annotations

import asyncio

from tayyari.analysis.readers import CHANNEL_THROTTLE_S, ReaderChannel
from tayyari.contract import AnalysisEvent, ReaderEvent, ReaderState


def card() -> ReaderState:
    return ReaderState(
        sourceId="resume",
        kind="resume",
        src="cv.pdf",
        kindLabel="Resume · PDF",
        agent="Resume reader",
        status="queued",
        line="Queued",
        progress=0,
    )


class Time:
    """A hand-moved monotonic clock."""

    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def sent(events: list[AnalysisEvent]) -> list[ReaderState]:
    return [e.reader for e in events if isinstance(e, ReaderEvent)]


def make(throttle_s: float = CHANNEL_THROTTLE_S) -> tuple[ReaderChannel, list[AnalysisEvent], Time]:
    events: list[AnalysisEvent] = []
    now = Time()
    return ReaderChannel(card(), events.append, now=now, throttle_s=throttle_s), events, now


def test_the_throttle_is_0_26_seconds() -> None:
    assert CHANNEL_THROTTLE_S == 0.26


async def test_the_first_update_is_sent_at_once() -> None:
    ch, events, _ = make()
    ch.update(status="reading", progress=6)
    assert [(s.status, s.progress) for s in sent(events)] == [("reading", 6)]


async def test_updates_inside_the_throttle_window_are_merged_into_one_later_event() -> None:
    ch, events, now = make(throttle_s=0.05)
    ch.update(progress=1)
    now.t += 0.01
    ch.update(progress=2)
    now.t += 0.01
    ch.update(progress=3, line="Almost")
    assert [s.progress for s in sent(events)] == [1]  # held back
    await asyncio.sleep(0.1)
    assert [(s.progress, s.line) for s in sent(events)] == [(1, "Queued"), (3, "Almost")]


async def test_an_update_after_the_window_is_sent_immediately() -> None:
    ch, events, now = make(throttle_s=0.26)
    ch.update(progress=1)
    now.t += 0.3
    ch.update(progress=2)
    assert [s.progress for s in sent(events)] == [1, 2]


async def test_force_sends_now_and_cancels_the_pending_event() -> None:
    ch, events, now = make(throttle_s=0.05)
    ch.update(progress=1)
    now.t += 0.01
    ch.update(progress=2)  # pending
    ch.update(status="done", progress=100, force=True)
    assert [(s.status, s.progress) for s in sent(events)] == [("queued", 1), ("done", 100)]
    await asyncio.sleep(0.1)
    assert len(sent(events)) == 2  # the pending one was cancelled


async def test_close_drops_a_pending_event_and_everything_after() -> None:
    ch, events, now = make(throttle_s=0.05)
    ch.update(progress=1)
    now.t += 0.01
    ch.update(progress=2)  # pending
    ch.close()
    ch.update(progress=3, force=True)
    await asyncio.sleep(0.1)
    assert [s.progress for s in sent(events)] == [1]
    assert ch.state.progress == 3  # the state itself still changes; only sending stops


async def test_no_throttle_means_every_update_is_sent() -> None:
    ch, events, _ = make(throttle_s=0.0)
    for n in range(1, 6):
        ch.update(progress=n)
    assert [s.progress for s in sent(events)] == [1, 2, 3, 4, 5]


async def test_each_event_is_a_snapshot() -> None:
    ch, events, _ = make(throttle_s=0.0)
    ch.update(progress=10, line="first")
    ch.update(progress=20, line="second")
    assert [(s.progress, s.line) for s in sent(events)] == [(10, "first"), (20, "second")]


async def test_every_field_can_be_updated() -> None:
    ch, events, _ = make(throttle_s=0.0)
    ch.update(status="reading", line="L", progress=7, kind_label="Resume · DOCX", src="cv.docx")
    (state,) = sent(events)
    assert (state.status, state.line, state.progress, state.kindLabel, state.src) == (
        "reading",
        "L",
        7,
        "Resume · DOCX",
        "cv.docx",
    )
