"""tayyari.contract.prepare must match shared/prepare.ts exactly: the interfaces, the literal union and the stream's event
union. The TypeScript file imports nothing, so it is read on its own.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import prepare as pr
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, literal_type_of, model_shape, union_classes

PREPARE_TS = REPO_ROOT / "shared" / "prepare.ts"

if not PREPARE_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(PREPARE_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "PrepareRequest": pr.PrepareRequest,
    "PrepareEvidence": pr.PrepareEvidence,
    "PrepareGap": pr.PrepareGap,
    "PrepareStrong": pr.PrepareStrong,
    "PrepareCited": pr.PrepareCited,
    "PreparePhase": pr.PreparePhase,
    "PrepareMove": pr.PrepareMove,
    "PrepareLink": pr.PrepareLink,
    "PrepareState": pr.PrepareState,
    "PreparePlan": pr.PreparePlan,
    "PrepareStateUpdate": pr.PrepareStateUpdate,
    "PrepareStateSaved": pr.PrepareStateSaved,
    "PrepareRepo": pr.PrepareRepo,
    "PrepareRepos": pr.PrepareRepos,
    "PrepareHeadlineRequest": pr.PrepareHeadlineRequest,
    "PrepareHeadline": pr.PrepareHeadline,
}

UNIONS = {"PrepareScope": pr.PrepareScope}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


@pytest.mark.parametrize("name", sorted(UNIONS))
def test_the_unions_match(name: str) -> None:
    assert _ts().shape(name) == ("lit", frozenset(get_args(UNIONS[name])))


def test_every_literal_union_is_checked() -> None:
    assert set(_ts().aliases) == set(UNIONS)


def test_the_event_union_matches() -> None:
    ts_members = _ts().union_members("PrepareEvent")
    py_members = {literal_type_of(cls): cls for cls in union_classes(pr.PrepareEvent)}
    assert set(py_members) == set(ts_members)
    problems = [
        f"[{t}] {line}"
        for t, shape in ts_members.items()
        for line in diff(shape, model_shape(py_members[t]), lenient=frozenset({"type"}))
    ]
    assert problems == []


def test_the_state_refuses_hours_out_of_range_and_runaway_lists() -> None:
    good = {"hoursPerWeek": 6, "moveIndex": 0, "done": [], "links": []}
    assert pr.PrepareState.model_validate(good).hoursPerWeek == pr.DEFAULT_HOURS_PER_WEEK
    for bad in (
        {**good, "hoursPerWeek": 0},
        {**good, "hoursPerWeek": 41},
        {**good, "moveIndex": 3},
        {**good, "done": ["m1:0:0"] * (pr.MAX_DONE + 1)},
        {**good, "done": ["x" * (pr.TASK_KEY_MAX_CHARS + 1)]},
    ):
        with pytest.raises(ValidationError):
            pr.PrepareState.model_validate(bad)


def test_the_request_refuses_overlong_ids() -> None:
    assert pr.PrepareRequest.model_validate({"profileId": "p", "roleId": "r"}).requirement is None
    with pytest.raises(ValidationError):
        pr.PrepareRequest.model_validate({"profileId": "p" * 65, "roleId": "r"})
    with pytest.raises(ValidationError):
        pr.PrepareRequest.model_validate({"profileId": "p", "roleId": "r" * 121})
