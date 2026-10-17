"""tayyari.contract.interview must match shared/interview.ts exactly: the interfaces (field names, optionality, nested
shapes) and the literal unions. The TypeScript file imports nothing, so it is read on its own.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import interview as iv
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

INTERVIEW_TS = REPO_ROOT / "shared" / "interview.ts"

if not INTERVIEW_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(INTERVIEW_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "RoleSource": iv.RoleSource,
    "SlotSource": iv.SlotSource,
    "PlanSlot": iv.PlanSlot,
    "PlanRequest": iv.PlanRequest,
    "InterviewPlan": iv.InterviewPlan,
    "StartRequest": iv.StartRequest,
    "InterviewLine": iv.InterviewLine,
    "Turn": iv.Turn,
    "WorkTask": iv.WorkTask,
    "AssistantTurn": iv.AssistantTurn,
    "InterviewSession": iv.InterviewSession,
    "AnswerRequest": iv.AnswerRequest,
    "WorkSubmit": iv.WorkSubmit,
    "AssistantRequest": iv.AssistantRequest,
    "AssistantReply": iv.AssistantReply,
    "SessionRequest": iv.SessionRequest,
    "AgainRequest": iv.AgainRequest,
    "FactRequest": iv.FactRequest,
    "Quote": iv.Quote,
    "SayLine": iv.SayLine,
    "AvoidLine": iv.AvoidLine,
    "FixStep": iv.FixStep,
    "StoryParts": iv.StoryParts,
    "SlotResult": iv.SlotResult,
    "CheckResult": iv.CheckResult,
    "VerdictCounts": iv.VerdictCounts,
    "Debrief": iv.Debrief,
    "SessionView": iv.SessionView,
    "SessionSummary": iv.SessionSummary,
    "ClaimMark": iv.ClaimMark,
    "ClaimBest": iv.ClaimBest,
    "PracticeFact": iv.PracticeFact,
    "PracticeHistory": iv.PracticeHistory,
}

UNIONS = {
    "InterviewKind": iv.InterviewKind,
    "SlotKind": iv.SlotKind,
    "Verdict": iv.Verdict,
    "Rung": iv.Rung,
    "AnswerMode": iv.AnswerMode,
    "AnswerAction": iv.AnswerAction,
    "InterviewLength": iv.InterviewLength,
    "InterviewFocus": iv.InterviewFocus,
    "SessionStatus": iv.SessionStatus,
    "LineKind": iv.LineKind,
    "Speaker": iv.Speaker,
    "StoryPartName": iv.StoryPartName,
    "MaterialKind": iv.MaterialKind,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


def test_the_model_class_names_follow_the_interface_names() -> None:
    for name, cls in INTERFACE_MODELS.items():
        assert cls.__name__ == name


@pytest.mark.parametrize("name", sorted(UNIONS))
def test_the_unions_match(name: str) -> None:
    assert _ts().shape(name) == ("lit", frozenset(get_args(UNIONS[name])))


def test_every_exported_literal_union_is_checked() -> None:
    assert set(_ts().aliases) == set(UNIONS)


def test_the_inline_origin_union_is_the_models() -> None:
    assert set(get_args(iv.SourceOrigin)) == {"posting", "evidence"}


def test_the_verdicts_are_words_and_there_is_no_score_anywhere() -> None:
    """project-soul.md: counts, never a score. No field of the contract is named for a grade, rating or percentage."""
    assert set(get_args(iv.Verdict)) == {"defended", "shaky", "couldnt", "not_assessed"}
    fields = {name.lower() for model in INTERFACE_MODELS.values() for name in model.model_fields}
    assert not {
        f for f in fields if any(word in f for word in ("score", "grade", "rating", "percent", "hire"))
    }


def test_the_requests_refuse_overlong_text_and_ids() -> None:
    with pytest.raises(ValidationError):
        iv.AnswerRequest.model_validate(
            {"profileId": "p", "text": "x" * (iv.ANSWER_MAX_CHARS + 1), "mode": "typed"}
        )
    assert iv.AnswerRequest.model_validate(
        {"profileId": "p", "text": "x" * iv.ANSWER_MAX_CHARS, "mode": "speech"}
    )
    with pytest.raises(ValidationError):
        iv.WorkSubmit.model_validate({"profileId": "p", "work": "x" * (iv.WORK_MAX_CHARS + 1)})
    with pytest.raises(ValidationError):
        iv.WorkSubmit.model_validate(
            {"profileId": "p", "work": "w", "testNote": "x" * (iv.TEST_NOTE_MAX_CHARS + 1)}
        )
    with pytest.raises(ValidationError):
        iv.AssistantRequest.model_validate(
            {"profileId": "p", "message": "x" * (iv.ASSISTANT_MESSAGE_MAX_CHARS + 1)}
        )
    with pytest.raises(ValidationError):
        iv.AgainRequest.model_validate({"profileId": "p", "slotId": "s" * (iv.SLOT_ID_MAX_CHARS + 1)})
    with pytest.raises(ValidationError):
        iv.StartRequest.model_validate({"profileId": "a" * (iv.PROFILE_ID_MAX_CHARS + 1), "kind": "resume"})
    with pytest.raises(ValidationError):
        iv.StartRequest.model_validate(
            {"profileId": "p", "kind": "resume", "skip": [f"s{i}" for i in range(iv.MAX_SKIP + 1)]}
        )
    with pytest.raises(ValidationError):
        iv.PlanRequest.model_validate({"profileId": "p", "kind": "mock"})
    with pytest.raises(ValidationError):
        iv.RoleSource.model_validate({"runId": "r" * (iv.RUN_ID_MAX_CHARS + 1), "jobId": "j"})


def test_optional_fields_are_omitted_on_the_wire() -> None:
    plan = iv.InterviewPlan(kind="resume", title="Defend your resume", slots=[], minutes=12)
    assert plan.to_dict() == {"kind": "resume", "title": "Defend your resume", "slots": [], "minutes": 12}
    start = iv.StartRequest.model_validate(
        {"profileId": "p", "kind": "role", "role": {"runId": "r", "jobId": "j"}}
    )
    assert start.length is None and start.focus is None and start.skip is None


def test_a_drifted_typescript_file_is_caught() -> None:
    """Known-bad: an optional made required, a field renamed and a union member dropped each show up as a difference."""
    source = INTERVIEW_TS.read_text(encoding="utf-8")
    optional = TsTypes(source.replace("testNote?: string;", "testNote: string;"))
    assert diff(optional.interface("WorkSubmit"), model_shape(iv.WorkSubmit)) != []
    renamed = TsTypes(source.replace("  keptUntil: string;", "  keepUntil: string;"))
    assert diff(renamed.interface("Debrief"), model_shape(iv.Debrief)) != []
    narrowed = TsTypes(
        source.replace(
            "'defended' | 'shaky' | 'couldnt' | 'not_assessed'", "'defended' | 'shaky' | 'couldnt'"
        )
    )
    assert narrowed.shape("Verdict") != ("lit", frozenset(get_args(iv.Verdict)))
