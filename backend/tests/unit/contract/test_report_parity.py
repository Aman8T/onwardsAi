"""tayyari.contract.report must match shared/report.ts exactly: the interfaces, the literal union and the limits."""

from __future__ import annotations

import re
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import report as rp
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

REPORT_TS = REPO_ROOT / "shared" / "report.ts"

if not REPORT_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(REPORT_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "ReportQuote": rp.ReportQuote,
    "ReportRole": rp.ReportRole,
    "ReportNearMiss": rp.ReportNearMiss,
    "ReportArea": rp.ReportArea,
    "ReportPerson": rp.ReportPerson,
    "ReportContent": rp.ReportContent,
    "ReportChoices": rp.ReportChoices,
    "ReportView": rp.ReportView,
    "ReportUpdate": rp.ReportUpdate,
    "ReportNewLink": rp.ReportNewLink,
}

UNIONS = {"ReportBlocked": rp.ReportBlocked}

LIMITS = [
    "REPORT_ROLES_MAX",
    "REPORT_QUOTES_PER_ROLE",
    "REPORT_MISSING_MAX",
    "REPORT_NEAR_MISSES_MAX",
    "REPORT_AREAS_MAX",
    "REPORT_QUOTE_MAX",
    "REPORT_HIDDEN_MAX",
    "SLUG_LEN",
]


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


@pytest.mark.parametrize("name", LIMITS)
def test_the_limits_match(name: str) -> None:
    found = re.search(rf"export const {name} = (\d+);", REPORT_TS.read_text(encoding="utf-8"))
    assert found is not None and int(found.group(1)) == getattr(rp, name)


def test_no_field_scores_the_person_against_others() -> None:
    """project-soul.md: no rank, percentile, percentage or match score anywhere in the contract."""
    fields = {name.lower() for model in INTERFACE_MODELS.values() for name in model.model_fields}
    assert not {f for f in fields if any(w in f for w in ("rank", "percent", "match", "top", "score"))} - {
        "score"
    }


def test_the_defaults_hide_employers_open_to_work_and_near_misses() -> None:
    d = rp.DEFAULT_CHOICES
    assert (d.showName, d.showOrgs, d.showQuotes, d.openToWork, d.showNearMisses) == (
        True,
        False,
        True,
        False,
        False,
    )
    assert d.hiddenQuotes == []


def test_an_update_bounds_its_hidden_quotes() -> None:
    ok = rp.ReportUpdate.model_validate(
        {
            "profileId": "p",
            "shared": True,
            "choices": {**rp.DEFAULT_CHOICES.model_dump(), "hiddenQuotes": ["a1"]},
        }
    )
    assert ok.choices.hiddenQuotes == ["a1"]
    base = rp.DEFAULT_CHOICES.model_dump()
    for bad in (
        {"profileId": "p", "shared": True, "choices": {**base, "hiddenQuotes": ["x"] * 13}},
        {"profileId": "p", "shared": True, "choices": {**base, "hiddenQuotes": ["x" * 17]}},
        {"profileId": "p" * 65, "shared": True, "choices": base},
    ):
        with pytest.raises(ValidationError):
            rp.ReportUpdate.model_validate(bad)
