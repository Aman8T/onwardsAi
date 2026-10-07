from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
from starlette.requests import ClientDisconnect, Request

from tayyari.api import ndjson
from tayyari.api.ndjson import MEDIA_TYPE, NdjsonResponse
from tayyari.contract import (
    HEARTBEAT,
    AnalysisEvent,
    ChatEvent,
    encode_event,
    ev_chat_done,
    ev_chat_error,
    ev_delta,
    ev_done,
    ev_error,
    ev_log,
    ev_phase,
    ev_tool,
    profile_from_json,
)
from tayyari.core.limits import DISCONNECT_POLL_S, HEARTBEAT_S
from tayyari.llm.errors import BUSY_MESSAGE, SPEND_CEILING_MESSAGE, ClaudeRateLimited, SpendCeilingReached
from tests.unit.api.helpers import AsgiExchange, RecordingLog, eventually, http_scope

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample-profile.json"
GENERIC = "Something went wrong on the server."

Events = AsyncIterator[AnalysisEvent | ChatEvent]


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    recorder = RecordingLog()
    monkeypatch.setattr(ndjson, "log", recorder)
    return recorder


def build(
    events: Events, *, tag: str = "analyze", spec_version: str | None = None, **kwargs: float
) -> tuple[NdjsonResponse, AsgiExchange]:
    exchange = AsgiExchange(http_scope(path=f"/api/{tag}", spec_version=spec_version))
    request = Request(exchange.scope, exchange.receive)
    options = {"heartbeat_s": 0.05, "poll_s": 0.01, **kwargs}
    return NdjsonResponse(request, events, tag, **options), exchange


async def emit(*events: AnalysisEvent | ChatEvent) -> Events:
    for event in events:
        yield event


def lines(exchange: AsgiExchange) -> list[bytes]:
    return exchange.body_parts


# ───────────────────────────── the stream itself ─────────────────────────────


async def test_each_event_is_one_compact_line_and_the_headers_match_the_legacy_server(
    log: RecordingLog,
) -> None:
    events = [
        ev_log("Jev", "First read: design"),
        ev_phase("reading", "Reading"),
        ev_log("sonnet", "Thinking"),
    ]
    response, exchange = build(emit(*events))
    await exchange.run(response)

    assert lines(exchange) == [encode_event(e) for e in events]
    assert all(part.endswith(b"\n") and part.count(b"\n") == 1 for part in lines(exchange))
    assert exchange.complete
    assert exchange.status == 200
    assert exchange.headers["content-type"] == "application/x-ndjson; charset=utf-8" == MEDIA_TYPE
    assert exchange.headers["cache-control"] == "no-cache, no-transform"
    assert exchange.headers["x-accel-buffering"] == "no"
    assert log.records == []


async def test_chat_events_use_the_same_encoding() -> None:
    events = [ev_delta("Hel"), ev_tool("WebSearch", "salary data"), ev_delta("lo"), ev_chat_done("abc123")]
    response, exchange = build(emit(*events), tag="chat")
    await exchange.run(response)
    decoded = [json.loads(part) for part in lines(exchange)]
    assert decoded == [
        {"type": "delta", "text": "Hel"},
        {"type": "tool", "name": "WebSearch", "detail": "salary data"},
        {"type": "delta", "text": "lo"},
        {"type": "done", "sessionId": "abc123"},
    ]


async def test_events_arrive_in_order_and_unicode_is_not_escaped() -> None:
    events = [ev_delta(f"é日本 {i}") for i in range(300)]
    response, exchange = build(emit(*events))
    await exchange.run(response)
    assert [json.loads(p)["text"] for p in lines(exchange)] == [f"é日本 {i}" for i in range(300)]
    assert "日本".encode() in lines(exchange)[0]


async def test_the_defaults_come_from_core_limits() -> None:
    exchange = AsgiExchange(http_scope())
    response = NdjsonResponse(Request(exchange.scope, exchange.receive), emit(), "analyze")
    assert response.heartbeat_s == HEARTBEAT_S == 15.0
    assert response.poll_s == DISCONNECT_POLL_S == 1.0
    await response._lines.aclose()


async def test_an_iterator_without_aclose_works() -> None:
    class Plain:
        def __init__(self) -> None:
            self.items = [ev_delta("a"), ev_chat_done("s")]

        def __aiter__(self) -> Plain:
            return self

        async def __anext__(self) -> AnalysisEvent | ChatEvent:
            if not self.items:
                raise StopAsyncIteration
            return self.items.pop(0)

    response, exchange = build(Plain(), tag="chat")  # type: ignore[arg-type]
    await exchange.run(response)
    assert len(lines(exchange)) == 2


# ───────────────────────────── heartbeat ─────────────────────────────


async def test_a_quiet_stream_gets_blank_line_heartbeats_between_events() -> None:
    gate = asyncio.Event()

    async def slow() -> Events:
        yield ev_log("Jev", "start")
        await gate.wait()
        yield ev_log("sonnet", "finish")

    response, exchange = build(slow())
    task = exchange.run(response)
    await exchange.wait_for_parts(1)
    await eventually(lambda: lines(exchange).count(HEARTBEAT) >= 2)  # two quiet spells in a row
    gate.set()
    await task

    parts = lines(exchange)
    assert parts[0] == encode_event(ev_log("Jev", "start"))
    assert parts[-1] == encode_event(ev_log("sonnet", "finish"))
    assert set(parts[1:-1]) == {HEARTBEAT}
    assert HEARTBEAT == b"\n"
    assert exchange.complete


async def test_no_heartbeat_while_events_keep_flowing() -> None:
    async def steady() -> Events:
        for i in range(8):
            await asyncio.sleep(0.03)
            yield ev_delta(str(i))

    response, exchange = build(
        steady(), heartbeat_s=0.2
    )  # the stream lasts ~0.25 s but is never quiet for 0.2 s
    await exchange.run(response)
    assert HEARTBEAT not in lines(exchange)
    assert len(lines(exchange)) == 8


async def test_an_idle_stream_with_the_default_heartbeat_sends_nothing_early() -> None:
    gate = asyncio.Event()

    async def slow() -> Events:
        await gate.wait()
        yield ev_delta("late")

    response, exchange = build(slow(), heartbeat_s=HEARTBEAT_S)
    task = exchange.run(response)
    await asyncio.sleep(0.15)
    assert lines(exchange) == []
    gate.set()
    await task
    assert lines(exchange) == [encode_event(ev_delta("late"))]


# ───────────────────────────── the client goes away ─────────────────────────────


class Probe:
    """An event producer that reports how it ended."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.finished_normally = False

    async def events(self) -> Events:
        try:
            yield ev_log("Jev", "working")
            self.started.set()
            await asyncio.sleep(3600)
            yield ev_delta("never")
            self.finished_normally = True
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


@pytest.mark.parametrize("spec_version", [None, "2.4"])
async def test_a_disconnect_cancels_the_producer_and_is_logged_once(
    log: RecordingLog, spec_version: str | None
) -> None:
    # None: Starlette listens for http.disconnect itself (older ASGI servers); 2.4: only our poll can see it.
    probe = Probe()
    response, exchange = build(probe.events(), spec_version=spec_version)
    task = exchange.run(response)
    await exchange.wait_for_parts(1)
    await asyncio.wait_for(probe.started.wait(), 2)

    exchange.disconnect()
    await asyncio.wait_for(task, 2)
    await asyncio.wait_for(probe.cancelled.wait(), 2)

    assert probe.finished_normally is False
    assert log.events("info") == ["[analyze] cancelled by client"]
    assert log.events("error") == []
    assert response.finished or spec_version is None


@pytest.mark.parametrize("spec_version", [None, "2.4"])
@pytest.mark.parametrize(
    "error", [OSError("broken pipe"), ClientDisconnect()], ids=["OSError", "ClientDisconnect"]
)
async def test_a_failed_send_cancels_the_producer_without_raising(
    log: RecordingLog, spec_version: str | None, error: BaseException
) -> None:
    probe = Probe()
    response, exchange = build(probe.events(), tag="chat", spec_version=spec_version)
    exchange.send_error = error
    await asyncio.wait_for(exchange.run(response), 2)  # the response swallows the client hanging up

    await asyncio.wait_for(probe.cancelled.wait(), 2)
    assert log.events("info") == ["[chat] cancelled by client"]
    assert log.events("error") == []


async def test_the_server_cancelling_the_task_cancels_the_producer(log: RecordingLog) -> None:
    probe = Probe()
    response, exchange = build(probe.events(), spec_version="2.4")
    task = exchange.run(response)
    await asyncio.wait_for(probe.started.wait(), 2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(probe.cancelled.wait(), 2)
    assert log.events("info") == ["[analyze] cancelled by client"]


async def test_a_producer_that_ignores_nothing_is_closed_when_the_response_is_closed() -> None:
    closed = asyncio.Event()

    async def events() -> Events:
        try:
            yield ev_delta("a")
            await asyncio.sleep(3600)
        finally:
            closed.set()

    response, exchange = build(events(), spec_version="2.4")
    task = exchange.run(response)
    await exchange.wait_for_parts(1)
    exchange.disconnect()
    await asyncio.wait_for(task, 2)
    await asyncio.wait_for(closed.wait(), 2)


async def test_a_normal_end_is_not_reported_as_a_cancellation(log: RecordingLog) -> None:
    response, exchange = build(emit(ev_delta("x"), ev_chat_done("s")), tag="chat")
    await exchange.run(response)
    assert response.finished is True
    assert log.events() == []


async def test_nothing_is_left_running_after_a_disconnect() -> None:
    probe = Probe()
    before = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    response, exchange = build(probe.events())
    task = exchange.run(response)
    await asyncio.wait_for(probe.started.wait(), 2)
    exchange.disconnect()
    await asyncio.wait_for(task, 2)
    await asyncio.sleep(0)
    leftovers = {t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()} - before
    assert leftovers == set()


# ───────────────────────────── a producer that fails ─────────────────────────────


def error_lines(exchange: AsgiExchange) -> list[dict[str, str]]:
    return [json.loads(p) for p in lines(exchange) if json.loads(p)["type"] == "error"]


async def test_a_crash_becomes_a_generic_error_event_and_the_traceback_is_logged(log: RecordingLog) -> None:
    async def boom() -> Events:
        yield ev_log("Jev", "start")
        raise RuntimeError("secret database password in this message")

    response, exchange = build(boom())
    await exchange.run(response)

    assert error_lines(exchange) == [{"type": "error", "message": GENERIC}]
    assert b"secret" not in b"".join(lines(exchange))
    assert exchange.complete
    (level, event, fields) = log.records[0]
    assert (level, event) == ("error", "[analyze] stream failed")
    assert fields["exc_info"][0] is RuntimeError


async def test_an_app_error_in_the_stream_shows_its_own_user_message() -> None:
    async def over_budget() -> Events:
        raise SpendCeilingReached(3600)
        yield ev_delta("unreachable")  # pragma: no cover

    response, exchange = build(over_budget(), tag="chat")
    await exchange.run(response)
    assert error_lines(exchange) == [{"type": "error", "message": SPEND_CEILING_MESSAGE}]


async def test_a_claude_error_in_the_stream_shows_its_user_message() -> None:
    async def busy() -> Events:
        yield ev_delta("partial")
        raise ClaudeRateLimited(12.0, detail="provider says: org rate limit")

    response, exchange = build(busy(), tag="chat")
    await exchange.run(response)
    assert error_lines(exchange) == [{"type": "error", "message": BUSY_MESSAGE}]
    assert b"provider says" not in b"".join(lines(exchange))


@pytest.mark.parametrize(
    "terminal",
    [
        ev_error("already told them"),
        ev_chat_error("already told them"),
        ev_chat_done("s"),
        ev_done(profile_from_json(json.loads(SAMPLE.read_text(encoding="utf-8")))),
    ],
    ids=["error", "chat error", "chat done", "done"],
)
async def test_no_second_error_event_after_a_terminal_event(
    log: RecordingLog, terminal: AnalysisEvent | ChatEvent
) -> None:
    async def finish_then_crash() -> Events:
        yield ev_log("Jev", "start")
        yield terminal
        raise RuntimeError("cleanup failed")

    response, exchange = build(finish_then_crash())
    await exchange.run(response)

    decoded = [json.loads(p) for p in lines(exchange)]
    assert [d["type"] for d in decoded][-1] == terminal.type
    assert decoded.count({"type": "error", "message": GENERIC}) == 0
    assert [e for e in log.events("error")] == ["[analyze] stream failed"]  # logged, just not sent


async def test_a_crash_before_the_first_event_is_still_a_clean_stream() -> None:
    async def immediate() -> Events:
        raise ValueError("nope")
        yield ev_delta("unreachable")  # pragma: no cover

    response, exchange = build(immediate())
    await exchange.run(response)
    assert error_lines(exchange) == [{"type": "error", "message": GENERIC}]
    assert exchange.complete


async def test_many_events_then_a_crash_keep_their_order() -> None:
    async def events() -> Events:
        for i in range(50):
            yield ev_delta(str(i))
        raise RuntimeError("x")

    response, exchange = build(events(), tag="chat")
    await exchange.run(response)
    decoded: Sequence[dict[str, str]] = [json.loads(p) for p in lines(exchange)]
    assert [d["text"] for d in decoded[:50]] == [str(i) for i in range(50)]
    assert decoded[50]["type"] == "error"
