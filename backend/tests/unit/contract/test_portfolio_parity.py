"""tayyari.contract.portfolio must match shared/portfolio.ts exactly (types, event union and kit constants).

Reuses the TypeScript reader and the shape comparison of test_ts_parity.py.
"""

from __future__ import annotations

import json
import re
from typing import Any, get_args

import pytest
from pydantic import BaseModel

from tayyari.contract import portfolio as pf
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, literal_type_of, model_shape, union_classes

PORTFOLIO_TS = REPO_ROOT / "shared" / "portfolio.ts"

if not PORTFOLIO_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(PORTFOLIO_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "PortfolioStyle": pf.PortfolioStyle,
    "BlockSpec": pf.BlockSpec,
    "GraphSpec": pf.GraphSpec,
    "PortfolioPrivacy": pf.PortfolioPrivacy,
    "DesignReason": pf.DesignReason,
    "PortfolioSpec": pf.PortfolioSpec,
    "SourceRef": pf.SourceRef,
    "Fact": pf.Fact,
    "CopySlot": pf.CopySlot,
    "ProofStat": pf.ProofStat,
    "PracticeStats": pf.PracticeStats,
    "PortfolioPerson": pf.PortfolioPerson,
    "PortfolioContent": pf.PortfolioContent,
    "GraphSkill": pf.GraphSkill,
    "GraphDomain": pf.GraphDomain,
    "GraphSnapshot": pf.GraphSnapshot,
    "PublishInfo": pf.PublishInfo,
    "Portfolio": pf.Portfolio,
    "PublicPortfolio": pf.PublicPortfolio,
    "PortfolioRequest": pf.PortfolioRequest,
    "SlotEdit": pf.SlotEdit,
    "PortfolioPatch": pf.PortfolioPatch,
}

LITERAL_ALIASES: dict[str, Any] = {
    "BlockKind": pf.BlockKind,
    "Composition": pf.Composition,
    "Family": pf.Family,
    "FieldShape": pf.FieldShape,
    "TypePairing": pf.TypePairing,
    "Corners": pf.Corners,
    "Density": pf.Density,
    "GraphVariant": pf.GraphVariant,
    "PortfolioSkin": pf.PortfolioSkin,
    "SlotOrigin": pf.SlotOrigin,
    "FactKind": pf.FactKind,
    "PublishState": pf.PublishState,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


@pytest.mark.parametrize("alias", sorted(LITERAL_ALIASES))
def test_literal_unions_match(alias: str) -> None:
    assert _ts().shape(alias) == ("lit", frozenset(get_args(LITERAL_ALIASES[alias])))


def test_event_union_matches() -> None:
    ts_members = _ts().union_members("PortfolioEvent")
    py_members = {literal_type_of(cls): cls for cls in union_classes(pf.PortfolioEvent)}
    assert set(py_members) == set(ts_members)
    problems = [
        f"[{t}] {line}"
        for t, shape in ts_members.items()
        for line in diff(shape, model_shape(py_members[t]), lenient=frozenset({"type"}))
    ]
    assert problems == []


# ───────────────────────── kit constants ─────────────────────────


def _ts_const(name: str) -> Any:
    """The value of ``export const NAME = {...}`` / ``= 1`` in shared/portfolio.ts, read as JSON-ish."""
    text = PORTFOLIO_TS.read_text(encoding="utf-8")
    m = re.search(rf"export const {name}\s*=\s*", text)
    assert m, f"{name} not found"
    rest = text[m.end() :]
    if rest[0] != "{":
        return json.loads(re.match(r"[^;\s]+", rest).group(0))  # type: ignore[union-attr]
    depth = 0
    for i, ch in enumerate(rest):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            body = rest[: i + 1]
            break
    body = re.sub(r"(\w+):", r'"\1":', body).replace("'", '"')
    body = re.sub(r",\s*([}\]])", r"\1", body)
    return json.loads(body)


def test_kit_version_matches() -> None:
    assert _ts_const("KIT_VERSION") == pf.KIT_VERSION


def test_block_variants_match() -> None:
    assert {k: tuple(v) for k, v in _ts_const("BLOCK_VARIANTS").items()} == pf.BLOCK_VARIANTS
    assert set(pf.BLOCK_VARIANTS) == set(get_args(pf.BlockKind))
    assert pf.BLOCK_VARIANTS["graph"] == get_args(pf.GraphVariant)


def test_family_hues_match() -> None:
    assert _ts_const("FAMILY_HUES") == pf.FAMILY_HUES
    assert set(pf.FAMILY_HUES) == set(get_args(pf.Family))


def test_slot_limits_match() -> None:
    assert _ts_const("SLOT_LIMITS") == pf.SLOT_LIMITS


def test_slot_limit_lookup() -> None:
    assert pf.slot_limit("tagline") == 90
    assert pf.slot_limit("exp:exp-1") == 300
    assert pf.slot_limit("proj:proj-2") == 200
    assert pf.slot_limit("exp") is None
    assert pf.slot_limit("exp:") is None
    assert pf.slot_limit("nope:x") is None


# ───────────────────────── spec rules ─────────────────────────


def _spec(blocks: list[tuple[str, str]]) -> pf.PortfolioSpec:
    return pf.PortfolioSpec(
        composition="signature",
        style=pf.PortfolioStyle(family="iris", field="dawn", type="studio", corners="soft", density="airy"),
        blocks=[pf.BlockSpec(id=k, kind=k, variant=v, visible=True, public=True) for k, v in blocks],  # type: ignore[arg-type]
        graph=pf.GraphSpec(hiddenDomainIds=[], stats=True),
        privacy=pf.PortfolioPrivacy(email=False, phone=False, grades=False),
        reasons=[],
    )


def test_a_valid_spec_has_no_problems() -> None:
    assert pf.spec_problems(_spec([("hero", "split"), ("graph", "peaks"), ("about", "lede")])) == []


@pytest.mark.parametrize(
    ("blocks", "expected"),
    [
        ([("hero", "split"), ("about", "lede")], "exactly one Skill Graph"),
        ([("hero", "split"), ("graph", "peaks"), ("graph", "halo")], "duplicate block ids"),
        ([("hero", "split"), ("about", "lede"), ("proof", "strip"), ("graph", "peaks")], "first three"),
        ([("graph", "peaks"), ("hero", "split")], "first block is the hero"),
        ([("hero", "split"), ("graph", "radar")], "unknown variant"),
    ],
)
def test_invalid_specs_are_named(blocks: list[tuple[str, str]], expected: str) -> None:
    assert any(expected in p for p in pf.spec_problems(_spec(blocks)))
