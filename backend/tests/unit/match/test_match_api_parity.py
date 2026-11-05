"""tayyari.contract.match_api must match shared/match_api.ts exactly: the request bodies, the two streams' events, the
JSON answers, the error codes and the limits. The TypeScript file imports `Posting`, `Fact`, `ResumeVariant` and the rest
from match.ts, so the two files are read together.

Python has one union for both streams (`MatchStreamEvent`, where two members share ``type: "done"``); the TypeScript
splits it into the match stream and the resume stream, so each member is compared with the class that stream sends.
"""

from __future__ import annotations

import re
from typing import Any, get_args

import pytest
from pydantic import BaseModel

from tayyari.contract import match_api as api
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, literal_type_of, model_shape

MATCH_TS = REPO_ROOT / "shared" / "match.ts"
MATCH_API_TS = REPO_ROOT / "shared" / "match_api.ts"

if not (MATCH_TS.is_file() and MATCH_API_TS.is_file()):
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _own() -> TsTypes:
    return TsTypes(MATCH_API_TS.read_text(encoding="utf-8"))


def _ts() -> TsTypes:
    return TsTypes(MATCH_TS.read_text(encoding="utf-8") + "\n" + MATCH_API_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "MatchRequest": api.MatchRequest,
    "AnswerItem": api.AnswerItem,
    "AnswersRequest": api.AnswersRequest,
    "DecisionsRequest": api.DecisionsRequest,
    "MatchDetail": api.MatchDetail,
    "MatchListItem": api.MatchListItem,
    "MatchList": api.MatchList,
    "VariantResponse": api.VariantResponse,
    "CoverResponse": api.CoverResponse,
}

STREAMS: dict[str, list[type[BaseModel]]] = {
    "MatchStreamEvent": [
        api.MatchPostingEvent,
        api.MatchGatesEvent,
        api.MatchCoverageEvent,
        api.MatchRadarEvent,
        api.MatchQuestionsEvent,
        api.MatchDoneEvent,
        api.MatchErrorEvent,
    ],
    "ResumeStreamEvent": [api.ResumeVariantEvent, api.ResumeDoneEvent, api.MatchErrorEvent],
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_own().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


def test_the_error_codes_match() -> None:
    assert _ts().shape("MatchErrorCode") == ("lit", frozenset(get_args(api.MatchErrorCode)))
    assert set(_own().aliases) == {"MatchErrorCode"}


@pytest.mark.parametrize("stream", sorted(STREAMS))
def test_each_stream_s_events_match_the_classes_it_sends(stream: str) -> None:
    ts_members = _ts().union_members(stream)
    py_members = {literal_type_of(cls): cls for cls in STREAMS[stream]}
    assert set(ts_members) == set(py_members)
    problems = [
        f"[{t}] {line}"
        for t, shape in ts_members.items()
        for line in diff(shape, model_shape(py_members[t]), lenient=frozenset({"type"}))
    ]
    assert problems == []


def test_both_streams_together_are_every_event_python_sends() -> None:
    sent = set(get_args(api.MatchStreamEvent))
    assert sent == {cls for classes in STREAMS.values() for cls in classes}


def _ts_number(name: str) -> Any:
    found = re.search(rf"export const {name}\s*=\s*(\d+);", MATCH_API_TS.read_text(encoding="utf-8"))
    assert found, f"{name} not found"
    return int(found.group(1))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ANSWER_MAX_CHARS", api.ANSWER_MAX_CHARS),
        ("MAX_DECISIONS", api.MAX_DECISIONS),
        ("URL_MAX_CHARS", api.URL_MAX_CHARS),
    ],
)
def test_the_limits_match(name: str, value: int) -> None:
    assert _ts_number(name) == value
