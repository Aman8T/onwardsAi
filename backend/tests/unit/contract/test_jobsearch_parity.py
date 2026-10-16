"""tayyari.contract.jobsearch must match shared/jobsearch.ts exactly: the interfaces, the literal unions and the stream's
event union. The TypeScript file imports `Cite`, `Place`, `QuickFit`, `Salary` and `DiscoverWorkMode` from discover.ts, so
the two files are read together.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import jobsearch as js
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, literal_type_of, model_shape, union_classes

JOBSEARCH_TS = REPO_ROOT / "shared" / "jobsearch.ts"
DISCOVER_TS = REPO_ROOT / "shared" / "discover.ts"

if not (JOBSEARCH_TS.is_file() and DISCOVER_TS.is_file()):
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _own() -> TsTypes:
    return TsTypes(JOBSEARCH_TS.read_text(encoding="utf-8"))


def _ts() -> TsTypes:
    return TsTypes(DISCOVER_TS.read_text(encoding="utf-8") + "\n" + JOBSEARCH_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "TrailStep": js.TrailStep,
    "AskMet": js.AskMet,
    "AiJob": js.AiJob,
    "FunnelStep": js.FunnelStep,
    "JobSearchRequest": js.JobSearchRequest,
}

UNIONS = {
    "FoundVia": js.FoundVia,
    "AiFitWord": js.AiFitWord,
    "LevelWord": js.LevelWord,
    "TrailStage": js.TrailStage,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_own().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


@pytest.mark.parametrize("name", sorted(UNIONS))
def test_the_unions_match(name: str) -> None:
    assert _ts().shape(name) == ("lit", frozenset(get_args(UNIONS[name])))


def test_every_exported_literal_union_is_checked() -> None:
    assert set(_own().aliases) == set(UNIONS)


def test_the_event_union_matches() -> None:
    ts_members = _ts().union_members("JobSearchEvent")
    py_members = {literal_type_of(cls): cls for cls in union_classes(js.JobSearchEvent)}
    assert set(py_members) == set(ts_members)
    problems = [
        f"[{t}] {line}"
        for t, shape in ts_members.items()
        for line in diff(shape, model_shape(py_members[t]), lenient=frozenset({"type"}))
    ]
    assert problems == []


def test_the_request_refuses_an_overlong_city_or_id() -> None:
    assert js.JobSearchRequest.model_validate({"profileId": "abc"}).city is None
    with pytest.raises(ValidationError):
        js.JobSearchRequest.model_validate({"profileId": "abc", "city": "x" * 200})
    with pytest.raises(ValidationError):
        js.JobSearchRequest.model_validate({"profileId": "a" * 200})
