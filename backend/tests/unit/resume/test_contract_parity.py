"""tayyari.contract.resume must match shared/resume.ts exactly: the interfaces, the literal unions and the constants.

The TypeScript file imports `Fact`, `LintReport` and `ResumeVariant` from match.ts, so the two files are read together
for shapes, and resume.ts alone for which names it declares. Reuses the TypeScript reader and the shape comparison of
tests/unit/contract/test_ts_parity.py.
"""

from __future__ import annotations

import re
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import resume as r
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

RESUME_TS = REPO_ROOT / "shared" / "resume.ts"
MATCH_TS = REPO_ROOT / "shared" / "match.ts"

if not (RESUME_TS.is_file() and MATCH_TS.is_file()):
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _own() -> TsTypes:
    return TsTypes(RESUME_TS.read_text(encoding="utf-8"))


def _ts() -> TsTypes:
    return TsTypes(MATCH_TS.read_text(encoding="utf-8") + "\n" + RESUME_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "ResumeSummary": r.ResumeSummary,
    "PathSlot": r.PathSlot,
    "ResumeList": r.ResumeList,
    "BulletMark": r.BulletMark,
    "ResumeDetail": r.ResumeDetail,
    "ResumeOp": r.ResumeOp,
    "ResumeOpsRequest": r.ResumeOpsRequest,
    "PathRequest": r.PathRequest,
    "Finding": r.Finding,
    "FindingAnswer": r.FindingAnswer,
    "FinaliseRequest": r.FinaliseRequest,
    "FinaliseReport": r.FinaliseReport,
    "FinaliseResponse": r.FinaliseResponse,
    "BlockEntry": r.BlockEntry,
    "ResumeBlock": r.ResumeBlock,
    "BlocksResponse": r.BlocksResponse,
    "GrammarProposal": r.GrammarProposal,
    "GrammarResponse": r.GrammarResponse,
    "LineWording": r.LineWording,
    "WordingsResponse": r.WordingsResponse,
    "FromMatchRequest": r.FromMatchRequest,
    "BlockWording": r.BlockWording,
    "BlockDisagreement": r.BlockDisagreement,
    "MergeItem": r.MergeItem,
    "MergeCounts": r.MergeCounts,
    "MergeReport": r.MergeReport,
    "UploadResponse": r.UploadResponse,
    "ResumeFile": r.ResumeFile,
    "ResumeFilesResponse": r.ResumeFilesResponse,
}

LITERAL_ALIASES: dict[str, Any] = {
    "ResumeKind": r.ResumeKind,
    "ResumeState": r.ResumeState,
    "ResumeOpKind": r.ResumeOpKind,
    "MoveDir": r.MoveDir,
    "MarkKind": r.MarkKind,
    "FindingKind": r.FindingKind,
    "FindingChoice": r.FindingChoice,
    "BlockEntryKind": r.BlockEntryKind,
    "MergeSection": r.MergeSection,
    "MergeDiffer": r.MergeDiffer,
    "ResumeFileOrigin": r.ResumeFileOrigin,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_own().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


@pytest.mark.parametrize("alias", sorted(LITERAL_ALIASES))
def test_literal_unions_match(alias: str) -> None:
    assert _ts().shape(alias) == ("lit", frozenset(get_args(LITERAL_ALIASES[alias])))


def test_every_literal_alias_has_a_mirror() -> None:
    assert set(_own().aliases) == set(LITERAL_ALIASES)


def _ts_const(name: str) -> int:
    found = re.search(rf"export const {name}\s*=\s*(\d+)\s*;", RESUME_TS.read_text(encoding="utf-8"))
    assert found, f"{name} not found"
    return int(found.group(1))


@pytest.mark.parametrize(
    ("ts_name", "py_value"),
    [
        ("RESUME_VERSION", r.RESUME_VERSION),
        ("MAX_RESUME_OPS", r.MAX_RESUME_OPS),
        ("MAX_LINE_CHARS", r.MAX_LINE_CHARS),
        ("MAX_WORDINGS_PER_LINE", r.MAX_WORDINGS_PER_LINE),
        ("MAX_RESUME_FILES", r.MAX_RESUME_FILES),
    ],
)
def test_constants_match(ts_name: str, py_value: int) -> None:
    assert _ts_const(ts_name) == py_value


def test_every_typescript_constant_is_checked() -> None:
    declared = set(re.findall(r"export const (\w+)\s*=", RESUME_TS.read_text(encoding="utf-8")))
    assert declared == {
        "RESUME_VERSION",
        "MAX_RESUME_OPS",
        "MAX_LINE_CHARS",
        "MAX_WORDINGS_PER_LINE",
        "MAX_RESUME_FILES",
    }


def test_the_checker_would_catch_a_drifted_field() -> None:
    """Known-bad: a model that lost an optional marker is reported, so a green run means the shapes really agree."""

    class Drifted(r.ResumeSummary):
        roleId: str  # type: ignore[assignment]  # noqa: N815  (required here, optional in TypeScript)

    assert diff(_ts().interface("ResumeSummary"), model_shape(Drifted)) == [
        "roleId: optional in TypeScript only"
    ]


def test_a_request_carries_at_most_forty_operations() -> None:
    op = {"op": "reset", "bulletId": "b-exp-1.1"}
    assert len(r.ResumeOpsRequest.model_validate({"ops": [op] * r.MAX_RESUME_OPS}).ops) == r.MAX_RESUME_OPS
    with pytest.raises(ValidationError):
        r.ResumeOpsRequest.model_validate({"ops": [op] * (r.MAX_RESUME_OPS + 1)})
    with pytest.raises(ValidationError):
        r.ResumeOpsRequest.model_validate({"ops": [{"op": "rewrite", "bulletId": "b-exp-1.1"}]})


def test_json_omits_unset_optionals_like_undefined_in_typescript() -> None:
    summary = r.ResumeSummary(
        id="r1",
        kind="upload",
        title="Resume",
        state="final",
        isDefault=True,
        changes=0,
        updatedAt="2026-11-10T09:00:00+00:00",
    )
    data = summary.to_dict()
    assert "roleId" not in data and "matchId" not in data and "finalisedAt" not in data
    assert r.ResumeSummary.from_dict(data) == summary
    assert r.FinaliseRequest.model_validate({}).answers is None
