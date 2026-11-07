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
    for name in ["ApplyStatus", "ApplicantDetails", "ApplicantProfile", "ApplicantProfileUpdate", "FilledField", "NeedsInput",
                 "ApplicationReview", "ApplicationEvent", "ApplicationSummary", "ApplicationDetail", "ApplicationList",
                 "CreateApplicationRequest", "NeedAnswer", "AnswerNeedsRequest", "ApproveRequest", "StageChangeRequest",
                 "EmailClassifyRequest", "EmailClassification", "MetricsRow", "FailureCount", "PlaybookSummary", "ApplyMetrics"]
}  # fmt: skip
UNIONS = ["ApplicationStage", "AtsKind", "ApplySource", "StageSource", "FieldKind", "FieldSource", "Confidence", "RunMode",
          "RunOutcome", "PlaybookStatus", "PlaybookOrigin", "EmailStage"]  # fmt: skip


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
