"""tayyari.contract.card must match shared/card.ts exactly: the interfaces, the literal unions and the limits."""

from __future__ import annotations

import re
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import card as cd
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

CARD_TS = REPO_ROOT / "shared" / "card.ts"

if not CARD_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(CARD_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "CardArea": cd.CardArea,
    "CardSkill": cd.CardSkill,
    "CardRole": cd.CardRole,
    "CardSettings": cd.CardSettings,
    "CardView": cd.CardView,
    "CardUpdate": cd.CardUpdate,
    "CardNewLink": cd.CardNewLink,
}

UNIONS = {"CardTheme": cd.CardTheme, "CardSize": cd.CardSize, "CardBlocked": cd.CardBlocked}


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


@pytest.mark.parametrize(
    "name", ["CARD_SKILLS_MAX", "CARD_SKILLS_OFFERED", "CARD_AREAS_MAX", "CARD_AREAS_MIN_RADAR", "SLUG_LEN"]
)
def test_the_limits_match(name: str) -> None:
    found = re.search(rf"export const {name} = (\d+);", CARD_TS.read_text(encoding="utf-8"))
    assert found is not None and int(found.group(1)) == getattr(cd, name)


def test_no_field_is_named_for_a_score_of_the_person_against_others() -> None:
    """project-soul.md: no rank, percentile or match percentage anywhere in the contract."""
    fields = {name.lower() for model in INTERFACE_MODELS.values() for name in model.model_fields}
    assert not {f for f in fields if any(w in f for w in ("rank", "percent", "match", "top"))}


def test_an_update_names_at_most_three_short_skill_ids() -> None:
    ok = cd.CardUpdate.model_validate(
        {"profileId": "p", "shared": True, "skills": ["a", "b", "c"], "theme": "match"}
    )
    assert ok.roleId is None and ok.theme == "match"
    for bad in (
        {"profileId": "p", "shared": True, "skills": ["a", "b", "c", "d"], "theme": "light"},
        {"profileId": "p", "shared": True, "skills": ["x" * 65], "theme": "light"},
        {"profileId": "p", "shared": True, "skills": [], "theme": "sepia"},
        {"profileId": "p" * 65, "shared": True, "skills": [], "theme": "light"},
    ):
        with pytest.raises(ValidationError):
            cd.CardUpdate.model_validate(bad)
