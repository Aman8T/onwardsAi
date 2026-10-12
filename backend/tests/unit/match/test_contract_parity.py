"""tayyari.contract.match must match shared/match.ts exactly (interfaces, literal unions and constants).

Reuses the TypeScript reader and the shape comparison of tests/unit/contract/test_ts_parity.py.
"""

from __future__ import annotations

import json
import re
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import match as m
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

MATCH_TS = REPO_ROOT / "shared" / "match.ts"

if not MATCH_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(MATCH_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "FactRef": m.FactRef,
    "Fact": m.Fact,
    "Requirement": m.Requirement,
    "Gate": m.Gate,
    "Posting": m.Posting,
    "GateAnswer": m.GateAnswer,
    "Coverage": m.Coverage,
    "StatusCounts": m.StatusCounts,
    "RadarAxis": m.RadarAxis,
    "Radar": m.Radar,
    "Question": m.Question,
    "MatchResult": m.MatchResult,
    "LintItem": m.LintItem,
    "LintReport": m.LintReport,
    "Bullet": m.Bullet,
    "SkillItem": m.SkillItem,
    "SkillGroup": m.SkillGroup,
    "ResumeHeader": m.ResumeHeader,
    "ExperienceEntry": m.ExperienceEntry,
    "EducationEntry": m.EducationEntry,
    "Decision": m.Decision,
    "Change": m.Change,
    "ResumeVariant": m.ResumeVariant,
    "DecisionChoice": m.DecisionChoice,
    "CoverParagraph": m.CoverParagraph,
    "CoverNote": m.CoverNote,
}

LITERAL_ALIASES: dict[str, Any] = {
    "FactSource": m.FactSource,
    "MatchFactKind": m.MatchFactKind,
    "EvidenceTier": m.EvidenceTier,
    "RequirementTier": m.RequirementTier,
    "RequirementKind": m.RequirementKind,
    "Provenance": m.Provenance,
    "WorkMode": m.WorkMode,
    "GateVerdict": m.GateVerdict,
    "CoverageStatus": m.CoverageStatus,
    "MatchBand": m.MatchBand,
    "Confidence": m.Confidence,
    "BulletVerdict": m.BulletVerdict,
    "BulletStatus": m.BulletStatus,
    "DecisionKind": m.DecisionKind,
    "DecisionChoiceKey": m.DecisionChoiceKey,
    "ChangeKind": m.ChangeKind,
    "CoverKind": m.CoverKind,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


@pytest.mark.parametrize("alias", sorted(LITERAL_ALIASES))
def test_literal_unions_match(alias: str) -> None:
    assert _ts().shape(alias) == ("lit", frozenset(get_args(LITERAL_ALIASES[alias])))


def test_every_literal_alias_has_a_mirror() -> None:
    assert set(_ts().aliases) == set(LITERAL_ALIASES)


def _ts_const(name: str) -> Any:
    text = MATCH_TS.read_text(encoding="utf-8")
    found = re.search(rf"export const {name}\s*=\s*", text)
    assert found, f"{name} not found"
    rest = text[found.end() :]
    if rest[0] != "{":
        return json.loads(re.match(r"[^;\s]+", rest).group(0))  # type: ignore[union-attr]
    depth = 0
    for i, ch in enumerate(rest):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            body = rest[: i + 1]
            break
    body = re.sub(r"(\w+):", r'"\1":', body)
    body = re.sub(r",\s*([}\]])", r"\1", body)
    return json.loads(body)


@pytest.mark.parametrize(
    ("ts_name", "py_value"),
    [
        ("MATCH_VERSION", m.MATCH_VERSION),
        ("MAX_REQUIREMENTS", m.MAX_REQUIREMENTS),
        ("MAX_GATES", m.MAX_GATES),
        ("MAX_QUESTIONS", m.MAX_QUESTIONS),
        ("GATE_BLOCK_SEVERITY", m.GATE_BLOCK_SEVERITY),
        ("BAND_STRONG", m.BAND_STRONG),
        ("BAND_GOOD", m.BAND_GOOD),
        ("BAND_STRETCH", m.BAND_STRETCH),
        ("COVER_WORDS_MIN", m.COVER_WORDS_MIN),
        ("COVER_WORDS_MAX", m.COVER_WORDS_MAX),
        ("TIER_WEIGHTS", m.TIER_WEIGHTS),
    ],
)
def test_constants_match(ts_name: str, py_value: Any) -> None:
    assert _ts_const(ts_name) == py_value


def test_the_tier_weights_are_the_specified_ones() -> None:
    assert m.TIER_WEIGHTS == {"must": 1.0, "required": 0.8, "preferred": 0.5, "nice": 0.25}
    assert set(m.TIER_WEIGHTS) == set(get_args(m.RequirementTier))
    assert m.tier_weight("unheard-of") == 0.25


def test_a_fact_ref_takes_two_line_numbers_or_none() -> None:
    assert m.FactRef(source="resume", docId="resume", lines=[3, 4], quote="x" * 5).lines == [3, 4]
    assert m.FactRef(source="user", docId="q-1", quote="my words").lines is None
    for bad in ([3], [0, 2], [5, 2], [1, 2, 3]):
        with pytest.raises(ValidationError):
            m.FactRef(source="resume", docId="resume", lines=bad, quote="x" * 5)


def test_json_omits_unset_optionals_like_undefined_in_typescript() -> None:
    fact = m.Fact(
        id="f1",
        text="Built a thing",
        kind="accomplishment",
        refs=[m.FactRef(source="user", docId="q", quote="I built a thing")],
    )
    data = fact.to_dict()
    assert "evidenceTier" not in data
    assert "lines" not in data["refs"][0]
    assert m.Fact.from_dict(data) == fact
