from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from tayyari.contract import (
    HEARTBEAT,
    AnalysisEvent,
    AnalysisEventAdapter,
    ChatDoneEvent,
    ChatErrorEvent,
    ChatEvent,
    ChatEventAdapter,
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    PhaseEvent,
    Profile,
    ReaderEvent,
    ReadersEvent,
    ReaderState,
    SignalEvent,
    SynthEvent,
    ToolEvent,
    encode_event,
    ev_chat_done,
    ev_chat_error,
    ev_delta,
    ev_done,
    ev_error,
    ev_log,
    ev_phase,
    ev_reader,
    ev_readers,
    ev_signal,
    ev_synth,
    ev_tool,
)
from tayyari.core.limits import CHAT_TOOL_DETAIL_MAX_CHARS
from tayyari.legacy import types as legacy_types

READER_DICTS: list[dict[str, Any]] = [
    {
        "sourceId": "resume",
        "kind": "resume",
        "src": "riya-kapoor.pdf",
        "kindLabel": "Resume · PDF",
        "agent": "Resume reader",
        "status": "queued",
        "line": "Waiting for a free reader",
        "progress": 0,
    },
    {
        "sourceId": "github",
        "kind": "github",
        "src": "github.com/riya-codes",
        "kindLabel": "GitHub profile",
        "agent": "GitHub analyst",
        "status": "done",
        "line": "34 repos read",
        "progress": 100,
    },
]


def _reader() -> ReaderState:
    return ReaderState.from_dict(READER_DICTS[0])


def _every_analysis_event(sample_dict: dict[str, Any]) -> list[AnalysisEvent]:
    return [
        ev_readers(READER_DICTS),
        ev_reader(READER_DICTS[1]),
        ev_signal("React", "resume", "resume"),
        ev_log("Jev", "First read: Design and UX, early career", "resume"),
        ev_log("Sonnet", "Building the graph", "sonnet"),
        ev_log("Tayyari", "Starting"),
        ev_phase("reading", "Reading your sources"),
        ev_synth(0),
        ev_synth(2, 1234),
        ev_done(sample_dict),
        ev_error("Something went wrong"),
    ]


def _every_chat_event() -> list[ChatEvent]:
    return [
        ev_delta("Hello"),
        ev_tool("WebSearch", "remote design jobs"),
        ev_chat_done("0123456789abcdef"),
        ev_chat_error("Profile not found"),
    ]


# ───────────────────────── encoding ─────────────────────────


def test_encode_event_is_one_compact_json_line(sample_dict: dict[str, Any]) -> None:
    for ev in [*_every_analysis_event(sample_dict), *_every_chat_event()]:
        data = encode_event(ev)
        assert isinstance(data, bytes)
        assert data.endswith(b"\n")
        assert data.count(b"\n") == 1
        text = data.decode("utf-8")
        assert text == json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False) + "\n"


def test_encode_event_puts_type_first() -> None:
    assert encode_event(ev_error("x")) == b'{"type":"error","message":"x"}\n'
    assert encode_event(ev_chat_done("abc")) == b'{"type":"done","sessionId":"abc"}\n'


def test_encoded_analysis_events_parse_back_through_the_adapter(sample_dict: dict[str, Any]) -> None:
    for ev in _every_analysis_event(sample_dict):
        parsed = AnalysisEventAdapter.validate_json(encode_event(ev))
        assert type(parsed) is type(ev)
        assert parsed == ev


def test_encoded_chat_events_parse_back_through_the_adapter() -> None:
    for ev in _every_chat_event():
        parsed = ChatEventAdapter.validate_json(encode_event(ev))
        assert type(parsed) is type(ev)
        assert parsed == ev


def test_the_two_unions_resolve_shared_type_names_differently() -> None:
    assert isinstance(ChatEventAdapter.validate_json(b'{"type":"done","sessionId":"abc"}'), ChatDoneEvent)
    assert isinstance(ChatEventAdapter.validate_json(b'{"type":"error","message":"m"}'), ChatErrorEvent)
    assert isinstance(AnalysisEventAdapter.validate_json(b'{"type":"error","message":"m"}'), ErrorEvent)
    with pytest.raises(ValidationError):
        AnalysisEventAdapter.validate_json(b'{"type":"done","sessionId":"abc"}')  # no profile
    with pytest.raises(ValidationError):
        ChatEventAdapter.validate_json(b'{"type":"phase","phase":"done","label":"x"}')


def test_none_fields_are_omitted_on_the_wire() -> None:
    assert b'"kind"' not in encode_event(ev_log("Jev", "text"))
    assert b'"chars"' not in encode_event(ev_synth(1))
    assert encode_event(LogEvent(who="a", text="b", kind=None)) == b'{"type":"log","who":"a","text":"b"}\n'


def test_none_fields_are_omitted_inside_a_done_profile(sample_dict: dict[str, Any]) -> None:
    sample_dict.pop("goal", None)
    sample_dict["person"].pop("location", None)
    wire = json.loads(encode_event(ev_done(sample_dict)))
    assert "goal" not in wire["profile"]
    assert "location" not in wire["profile"]["person"]
    assert wire["profile"]["person"]["name"] == "Riya Kapoor"


def test_non_ascii_text_is_written_as_utf8_not_escaped() -> None:
    data = encode_event(ev_log("Jev", "Zoë 日本 — résumé ·"))
    assert "Zoë 日本 — résumé ·".encode() in data
    assert b"\\u" not in data


def test_line_breaking_characters_cannot_split_a_line() -> None:
    tricky = "a" + "".join(chr(c) for c in (0x0A, 0x0D, 0x85, 0x2028, 0x2029, 0x0B, 0x0C)) + "b"
    data = encode_event(ev_delta(tricky))
    text = data.decode("utf-8")
    assert len(text.splitlines()) == 1
    assert text.count("\n") == 1
    assert json.loads(text)["text"] == tricky


def test_lone_surrogates_do_not_break_the_stream() -> None:
    lone = "a" + chr(0xD800) + "b"
    data = encode_event(ev_log("Jev", lone))
    assert data.endswith(b"\n")
    assert json.loads(data.decode("utf-8"))["text"] == lone  # escaped, so the bytes are valid UTF-8


def test_heartbeat_is_a_blank_line() -> None:
    assert HEARTBEAT == b"\n"
    stream = HEARTBEAT + encode_event(ev_error("x")) + HEARTBEAT
    lines = [line for line in stream.decode().split("\n") if line.strip()]
    assert [json.loads(line)["type"] for line in lines] == ["error"]


# ───────────────────────── the unions ─────────────────────────


def test_events_carry_their_own_type_literal() -> None:
    assert ReadersEvent(readers=[]).type == "readers"
    assert ReaderEvent(reader=_reader()).type == "reader"
    assert SignalEvent(label="l", src="s", kind="notes").type == "signal"
    assert LogEvent(who="w", text="t").type == "log"
    assert PhaseEvent(phase="done", label="l").type == "phase"
    assert SynthEvent(step=3).type == "synth"
    assert ErrorEvent(message="m").type == "error"
    assert DeltaEvent(text="t").type == "delta"
    assert ToolEvent(name="WebSearch", detail="d").type == "tool"
    assert ChatDoneEvent(sessionId="s").type == "done"
    assert ChatErrorEvent(message="m").type == "error"


def test_a_missing_or_unknown_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AnalysisEventAdapter.validate_python({"message": "no type"})
    with pytest.raises(ValidationError):
        AnalysisEventAdapter.validate_python({"type": "nope"})
    with pytest.raises(ValidationError):
        ChatEventAdapter.validate_python({"type": "readers", "readers": []})


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "synth", "step": 4},
        {"type": "synth", "step": -1},
        {"type": "phase", "phase": "finished", "label": "x"},
        {"type": "signal", "label": "x", "src": "y", "kind": "twitter"},
        {"type": "log", "who": "x", "text": "y", "kind": "gpt"},
        {"type": "reader", "reader": {**READER_DICTS[0], "status": "paused"}},
        {"type": "readers", "readers": [{"sourceId": "resume"}]},
    ],
)
def test_invalid_payloads_are_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AnalysisEventAdapter.validate_python(payload)


def test_log_accepts_sonnet_and_every_source_kind() -> None:
    for kind in ("sonnet", "resume", "github", "lovable", "linkedin", "portfolio", "link", "notes"):
        parsed = AnalysisEventAdapter.validate_python({"type": "log", "who": "w", "text": "t", "kind": kind})
        assert isinstance(parsed, LogEvent)
        assert parsed.kind == kind


def test_unknown_keys_on_events_are_ignored() -> None:
    parsed = AnalysisEventAdapter.validate_python({"type": "error", "message": "m", "extra": 1})
    assert parsed == ErrorEvent(message="m")


# ───────────────────────── builders ─────────────────────────

LEGACY_TABLE: list[tuple[str, Callable[[], Any], Callable[[], dict[str, Any]]]] = [
    (
        "readers",
        lambda: ev_readers([ReaderState.from_dict(r) for r in READER_DICTS]),
        lambda: legacy_types.ev_readers(READER_DICTS),
    ),
    ("readers-from-dicts", lambda: ev_readers(READER_DICTS), lambda: legacy_types.ev_readers(READER_DICTS)),
    ("readers-empty", lambda: ev_readers([]), lambda: legacy_types.ev_readers([])),
    ("reader", lambda: ev_reader(_reader()), lambda: legacy_types.ev_reader(READER_DICTS[0])),
    ("reader-from-dict", lambda: ev_reader(READER_DICTS[1]), lambda: legacy_types.ev_reader(READER_DICTS[1])),
    (
        "signal",
        lambda: ev_signal("React", "resume", "resume"),
        lambda: legacy_types.ev_signal("React", "resume", "resume"),
    ),
    (
        "signal-github",
        lambda: ev_signal("34 repos", "github", "github"),
        lambda: legacy_types.ev_signal("34 repos", "github", "github"),
    ),
    ("log", lambda: ev_log("Tayyari", "Starting"), lambda: legacy_types.ev_log("Tayyari", "Starting")),
    (
        "log-kind",
        lambda: ev_log("Jev", "First read: Design and UX", "resume"),
        lambda: legacy_types.ev_log("Jev", "First read: Design and UX", "resume"),
    ),
    (
        "log-sonnet",
        lambda: ev_log("Sonnet", "Building the graph", "sonnet"),
        lambda: legacy_types.ev_log("Sonnet", "Building the graph", "sonnet"),
    ),
    ("log-unicode", lambda: ev_log("Jev", "Zoë 日本 ·"), lambda: legacy_types.ev_log("Jev", "Zoë 日本 ·")),
    ("log-empty-text", lambda: ev_log("Jev", ""), lambda: legacy_types.ev_log("Jev", "")),
    (
        "phase-reading",
        lambda: ev_phase("reading", "Reading"),
        lambda: legacy_types.ev_phase("reading", "Reading"),
    ),
    (
        "phase-synth",
        lambda: ev_phase("synthesizing", "Synthesizing"),
        lambda: legacy_types.ev_phase("synthesizing", "Synthesizing"),
    ),
    ("phase-done", lambda: ev_phase("done", "Done"), lambda: legacy_types.ev_phase("done", "Done")),
    ("synth-0", lambda: ev_synth(0), lambda: legacy_types.ev_synth(0)),
    ("synth-chars", lambda: ev_synth(2, 1234), lambda: legacy_types.ev_synth(2, 1234)),
    ("synth-zero-chars", lambda: ev_synth(1, 0), lambda: legacy_types.ev_synth(1, 0)),
    ("synth-none-chars", lambda: ev_synth(3, None), lambda: legacy_types.ev_synth(3, None)),
    (
        "error",
        lambda: ev_error("Something went wrong"),
        lambda: legacy_types.ev_error("Something went wrong"),
    ),
    ("error-empty", lambda: ev_error(""), lambda: legacy_types.ev_error("")),
]


@pytest.mark.parametrize(("name", "build", "legacy"), LEGACY_TABLE, ids=[row[0] for row in LEGACY_TABLE])
def test_builders_match_the_legacy_helpers(
    name: str, build: Callable[[], Any], legacy: Callable[[], dict[str, Any]]
) -> None:
    assert build().to_dict() == legacy()


def test_ev_done_matches_the_legacy_helper(sample_dict: dict[str, Any]) -> None:
    expected = legacy_types.ev_done(sample_dict)
    assert ev_done(sample_dict).to_dict() == expected
    assert ev_done(Profile.from_dict(sample_dict)).to_dict() == expected


def test_chat_builders_match_the_legacy_chat_dicts() -> None:
    assert ev_delta("Hi").to_dict() == {"type": "delta", "text": "Hi"}
    assert ev_tool("WebSearch", "jobs").to_dict() == {"type": "tool", "name": "WebSearch", "detail": "jobs"}
    assert ev_chat_done("abc").to_dict() == {"type": "done", "sessionId": "abc"}
    assert ev_chat_error("nope").to_dict() == {"type": "error", "message": "nope"}


def test_ev_tool_cuts_a_long_detail() -> None:
    long_detail = "x" * (CHAT_TOOL_DETAIL_MAX_CHARS + 50)
    assert ev_tool("WebFetch", long_detail).detail == "x" * CHAT_TOOL_DETAIL_MAX_CHARS
    assert ev_tool("WebFetch", "short").detail == "short"


def test_builders_return_the_typed_events() -> None:
    assert isinstance(ev_readers([]), ReadersEvent)
    assert isinstance(ev_reader(_reader()), ReaderEvent)
    assert isinstance(ev_signal("a", "b", "link"), SignalEvent)
    assert isinstance(ev_log("a", "b"), LogEvent)
    assert isinstance(ev_phase("done", "x"), PhaseEvent)
    assert isinstance(ev_synth(0), SynthEvent)
    assert isinstance(ev_error("x"), ErrorEvent)
    assert isinstance(ev_delta("x"), DeltaEvent)
    assert isinstance(ev_tool("a", "b"), ToolEvent)
    assert isinstance(ev_chat_done("x"), ChatDoneEvent)
    assert isinstance(ev_chat_error("x"), ChatErrorEvent)


def test_builders_validate_their_input() -> None:
    with pytest.raises(ValidationError):
        ev_reader({"sourceId": "resume"})
    with pytest.raises(ValidationError):
        ev_done({"id": "x"})
    with pytest.raises(ValidationError):
        ev_synth(7)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ev_signal("a", "b", "twitter")  # type: ignore[arg-type]


def test_ev_reader_is_a_snapshot_of_the_state() -> None:
    state = _reader()
    event = ev_reader(state)
    state.status = "reading"
    state.progress = 55
    assert event.reader.status == "queued"
    assert event.reader.progress == 0
    readers_event = ev_readers([state])
    state.line = "changed"
    assert readers_event.readers[0].line != "changed"


def test_ev_done_is_a_snapshot_of_the_profile(sample_dict: dict[str, Any]) -> None:
    profile = Profile.from_dict(sample_dict)
    event = ev_done(profile)
    profile.person.name = "Someone Else"
    profile.skills.clear()
    assert event.profile.person.name == "Riya Kapoor"
    assert len(event.profile.skills) == len(sample_dict["skills"])


def test_done_event_wraps_a_profile(sample_dict: dict[str, Any]) -> None:
    event = ev_done(sample_dict)
    assert isinstance(event, DoneEvent)
    assert event.profile.id == sample_dict["id"]
