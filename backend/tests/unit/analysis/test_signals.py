"""Signal chips: deduped across sources, paced on the clock, cancellable."""

from __future__ import annotations

import asyncio

from tayyari.analysis.signals import SIGNAL_PACE_S, SignalStream
from tayyari.contract import SignalEvent
from tayyari.core.settings import Settings
from tests.fakes.clock import FakeClock
from tests.unit.analysis.support import make_run


def chips(events: list) -> list[tuple[str, str, str]]:  # type: ignore[type-arg]
    return [(e.label, e.src, e.kind) for e in events if isinstance(e, SignalEvent)]


async def test_chips_are_sent_in_order_with_their_detail_as_src(settings: Settings) -> None:
    run, events = make_run(settings)
    stream = SignalStream(run)
    await stream.emit(
        [{"label": "React", "detail": "14 repos"}, {"label": "Go", "detail": "2 repos"}], "github"
    )
    assert chips(events) == [("React", "14 repos", "github"), ("Go", "2 repos", "github")]


async def test_a_chip_is_dropped_when_its_loose_key_was_seen_before(settings: Settings) -> None:
    run, events = make_run(settings)
    stream = SignalStream(run)
    await stream.emit([{"label": "REST & GraphQL", "detail": "a"}], "resume")
    await stream.emit(
        [
            {"label": "rest-graphql", "detail": "b"},  # same loose key
            {"label": "  REST   &  graphql ", "detail": "c"},
            {"label": "Node.js", "detail": "d"},
            {"label": "node.js", "detail": "e"},
            {"label": "!!!", "detail": "no key at all"},
            {"label": "", "detail": "empty"},
        ],
        "github",
    )
    assert chips(events) == [("REST & GraphQL", "a", "resume"), ("Node.js", "d", "github")]


async def test_c_plus_plus_and_c_sharp_are_not_the_same_chip(settings: Settings) -> None:
    run, events = make_run(settings)
    await SignalStream(run).emit(
        [{"label": "C++", "detail": ""}, {"label": "C#", "detail": ""}, {"label": "C", "detail": ""}], "notes"
    )
    assert [label for label, _, _ in chips(events)] == ["C++", "C#", "C"]


async def test_each_sent_chip_is_followed_by_a_scaled_pause(make_settings) -> None:  # type: ignore[no-untyped-def]
    clock = FakeClock()
    run, events = make_run(make_settings(pacing_scale=2.0), clock=clock)
    await SignalStream(run).emit(
        [{"label": "A", "detail": ""}, {"label": "A", "detail": ""}, {"label": "B", "detail": ""}], "link"
    )
    assert len(chips(events)) == 2
    assert clock.sleeps == [SIGNAL_PACE_S * 2, SIGNAL_PACE_S * 2]  # the duplicate costs no pause


async def test_pacing_scale_zero_never_touches_the_clock(settings: Settings) -> None:
    clock = FakeClock()
    run, _ = make_run(settings, clock=clock)
    await SignalStream(run).emit([{"label": "A", "detail": ""}, {"label": "B", "detail": ""}], "link")
    assert clock.sleeps == []


async def test_spawned_batches_share_the_dedupe_set_and_settle(settings: Settings) -> None:
    run, events = make_run(settings)
    stream = SignalStream(run)
    stream.spawn([{"label": "A", "detail": "1"}, {"label": "B", "detail": "1"}], "resume")
    stream.spawn([{"label": "b", "detail": "2"}, {"label": "C", "detail": "2"}], "github")
    await stream.settle()
    assert sorted(label for label, _, _ in chips(events)) == ["A", "B", "C"]


async def test_a_batch_waits_for_the_one_it_follows(settings: Settings) -> None:
    run, events = make_run(settings)
    stream = SignalStream(run)
    gate = asyncio.Event()

    async def first() -> None:
        await gate.wait()

    blocker = asyncio.create_task(first())
    stream.spawn([{"label": "Later", "detail": ""}], "link", after=blocker)
    await asyncio.sleep(0.01)
    assert chips(events) == []
    gate.set()
    await stream.settle()
    assert [label for label, _, _ in chips(events)] == ["Later"]


async def test_a_batch_still_runs_when_the_one_it_follows_failed(settings: Settings) -> None:
    run, events = make_run(settings)
    stream = SignalStream(run)

    async def boom() -> None:
        raise RuntimeError("quick batch failed")

    failing = asyncio.create_task(boom())
    stream.spawn([{"label": "Still here", "detail": ""}], "link", after=failing)
    await stream.settle()
    assert [label for label, _, _ in chips(events)] == ["Still here"]


async def test_cancel_stops_pending_batches_and_waits_for_them(make_settings) -> None:  # type: ignore[no-untyped-def]
    run, events = make_run(make_settings(pacing_scale=1.0))
    stream = SignalStream(run)
    gate = asyncio.Event()

    async def blocked() -> None:
        await gate.wait()

    blocker = asyncio.create_task(blocked())
    task = stream.spawn([{"label": "Never", "detail": ""}], "link", after=blocker)
    await asyncio.sleep(0)
    await stream.cancel()
    assert task.cancelled()
    assert chips(events) == []
    assert blocker.cancelled()  # awaiting a task from a cancelled task cancels it too: nothing is left behind
