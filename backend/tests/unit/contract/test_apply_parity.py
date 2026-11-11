"""tayyari.contract.apply must match shared/apply.ts exactly: every interface, the literal unions and the limits."""

from __future__ import annotations

import re
from typing import get_args

import pytest
from pydantic import BaseModel

from tayyari.contract import apply as ap
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

APPLY_TS = REPO_ROOT / "shared" / "apply.ts"

if not APPLY_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)

INTERFACES: dict[str, type[BaseModel]] = {
    name: getattr(ap, name)
    for name in ["ApplyStatus", "FilledField", "NeedsInput", "ApplicationReview", "ApplicationEvent", "ApplicationSummary",
                 "ApplicationDetail", "ApplicationList", "ApproveRequest", "StageChangeRequest", "EmailClassifyRequest",
                 "EmailClassification", "MetricsRow", "FailureCount", "PlaybookSummary", "ApplyMetrics", "ApplyCheckRequest",
                 "ApplyCheck", "StartApplicationRequest", "QuestionAnswer", "AnswerRequest", "AnswerResult",
                 "ResolveRequest", "CitedFact", "ReviewResume", "ResumeRef", "ScriptStep", "AlsoWaiting", "SavedAnswer", "BankQuestion",
                 "WorkAuthorization", "YourDetails", "SaveAnswerRequest", "FirstCheckRequest", "SelfIdRequest",
                 "StillTrueRequest", "ProgressSection", "ApplyProgress"]
}  # fmt: skip
UNIONS = ["ApplicationStage", "AtsKind", "ApplySource", "StageSource", "FieldKind", "FieldSource", "Confidence", "RunMode",
          "RunOutcome", "PlaybookStatus", "PlaybookOrigin", "EmailStage", "StopReason", "ResumeChoice", "CreditState",
          "ApplyBlockReason", "AnswerSource", "AnswerGroup", "ResolveOutcome", "ScriptOp", "ProgressState"]  # fmt: skip

LEGACY_NAMES = ["ApplicantDetails", "ApplicantProfile", "ApplicantProfileUpdate", "CreateApplicationRequest", "NeedAnswer",
                "AnswerNeedsRequest"]  # fmt: skip
"""The old branch's shapes: gone from both sides."""


def test_the_legacy_names_are_gone() -> None:
    text = APPLY_TS.read_text(encoding="utf-8")
    for name in LEGACY_NAMES:
        assert not hasattr(ap, name) and f"interface {name} " not in text
    assert not {"needs_user", "ready_to_submit", "applied"} & set(get_args(ap.ApplicationStage))
    assert "agent" not in get_args(ap.FieldSource) and "ai" not in get_args(ap.RunMode)
    assert not {"anonymous", "agents"} & set(ap.ApplyStatus.model_fields)


def test_selectors_and_question_keys_are_required() -> None:
    assert ap.FilledField.model_fields["selector"].is_required()
    assert ap.NeedsInput.model_fields["questionKey"].is_required()


def test_every_union_is_listed() -> None:
    """A literal alias exported by the TypeScript file but missing from UNIONS would never be compared."""
    assert set(_ts().aliases) == set(UNIONS)


def test_never_stored_keys_are_python_only_and_not_a_limit() -> None:
    assert (
        isinstance(ap.NEVER_STORED_KEYS, frozenset)
        and {"pan", "aadhaar", "signature"} <= ap.NEVER_STORED_KEYS
    )
    assert "NEVER_STORED_KEYS" not in APPLY_TS.read_text(encoding="utf-8")
    assert not any(n.startswith("APPLY_") and not isinstance(getattr(ap, n), int) for n in ap.__all__)


def _ts() -> TsTypes:
    return TsTypes(APPLY_TS.read_text(encoding="utf-8"))


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACES)


@pytest.mark.parametrize("name", sorted(INTERFACES))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACES[name])) == []


@pytest.mark.parametrize("name", UNIONS)
def test_unions_match(name: str) -> None:
    assert _ts().shape(name) == ("lit", frozenset(get_args(getattr(ap, name))))


@pytest.mark.parametrize("name", [n for n in ap.__all__ if n.startswith("APPLY_")])
def test_limits_match(name: str) -> None:
    found = re.search(rf"export const {name} = (\d+);", APPLY_TS.read_text(encoding="utf-8"))
    assert found is not None and int(found[1]) == getattr(ap, name)
