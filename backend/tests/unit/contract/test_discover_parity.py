"""tayyari.contract.discover must match shared/discover.ts exactly (interfaces, optionality, the unions), and the query model
must accept what the web app sends and refuse what it must not.

Reuses the TypeScript reader and the shape comparison of test_ts_parity.py.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import discover
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

DISCOVER_TS = REPO_ROOT / "shared" / "discover.ts"

if not DISCOVER_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(DISCOVER_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "Cite": discover.Cite,
    "Reason": discover.Reason,
    "QuickFit": discover.QuickFit,
    "Place": discover.Place,
    "Salary": discover.Salary,
    "ListingCard": discover.ListingCard,
    "Group": discover.Group,
    "LensStop": discover.LensStop,
    "DiscoverArea": discover.DiscoverArea,
    "CompanyLead": discover.CompanyLead,
    "DiscoverCoverage": discover.DiscoverCoverage,
    "DiscoverResponse": discover.DiscoverResponse,
    "DiscoverCity": discover.DiscoverCity,
    "DiscoverCitiesResponse": discover.DiscoverCitiesResponse,
    "ListingDetail": discover.ListingDetail,
}

UNIONS = {
    "DiscoverSource": discover.DiscoverSource,
    "DiscoverWorkMode": discover.DiscoverWorkMode,
    "PlacePrecision": discover.PlacePrecision,
    "QuickFitWord": discover.QuickFitWord,
    "DiscoverLane": discover.DiscoverLane,
    "DiscoverReach": discover.DiscoverReach,
    "DiscoverBreadth": discover.DiscoverBreadth,
    "DiscoverOrder": discover.DiscoverOrder,
    "GroupKind": discover.GroupKind,
    "SourceConfidence": discover.SourceConfidence,
    "CityOrigin": discover.CityOrigin,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


def test_the_model_class_names_follow_the_interface_names() -> None:
    for name, cls in INTERFACE_MODELS.items():
        assert cls.__name__ == name


def test_the_inline_lane_counts_object_is_mirrored() -> None:
    shape = _ts().interface("DiscoverResponse")[1]["laneCounts"]
    assert diff(shape[1], model_shape(discover.LaneCounts)) == []


@pytest.mark.parametrize("name", sorted(UNIONS))
def test_the_unions_match(name: str) -> None:
    assert _ts().shape(name) == ("lit", frozenset(get_args(UNIONS[name])))


def test_every_exported_union_is_checked() -> None:
    text = DISCOVER_TS.read_text(encoding="utf-8")
    declared = {line.split()[2] for line in text.splitlines() if line.startswith("export type ")}
    assert declared == set(UNIONS)


def test_the_reach_and_breadth_tuples_follow_the_unions() -> None:
    assert discover.REACHES == get_args(discover.DiscoverReach) == ("3", "5", "10", "25", "city")
    assert discover.BREADTHS == get_args(discover.DiscoverBreadth) == ("role", "next", "stretch")


def test_optional_fields_leave_the_json_when_unset() -> None:
    card = discover.Place(label="Remote, India", precision="country")
    assert card.to_dict() == {"label": "Remote, India", "precision": "country"}
    coverage = discover.DiscoverCoverage(employers=3, listings=40)
    assert coverage.to_dict() == {"employers": 3, "listings": 40}


# ───────────────────────── the query ─────────────────────────


def test_the_query_defaults_are_whole_city_next_steps_near_ranked() -> None:
    parsed = discover.DiscoverQuery.model_validate({"profileId": "abc"})
    assert (parsed.reach, parsed.breadth, parsed.lane, parsed.order) == ("city", "next", "near", "ranked")
    assert (parsed.city, parsed.locality, parsed.otherCity) == (None, None, None)


def test_the_query_takes_what_the_web_app_sends() -> None:
    parsed = discover.DiscoverQuery.model_validate(
        {
            "profileId": "abc",
            "city": " Bengaluru ",
            "locality": "HSR Layout",
            "otherCity": "Pune",
            "reach": "10",
            "breadth": "stretch",
            "lane": "remote",
            "order": "posted",
            "ignored": "x",
        }
    )
    assert (parsed.city, parsed.locality, parsed.otherCity) == ("Bengaluru", "HSR Layout", "Pune")
    assert (parsed.reach, parsed.breadth, parsed.lane, parsed.order) == ("10", "stretch", "remote", "posted")


def test_a_blank_value_means_not_given() -> None:
    parsed = discover.DiscoverQuery.model_validate(
        {"profileId": "abc", "city": "  ", "locality": "", "reach": "", "lane": " "}
    )
    assert (parsed.city, parsed.locality, parsed.reach, parsed.lane) == (None, None, "city", "near")


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"profileId": ""},
        {"profileId": "a" * 97},
        {"profileId": "a", "reach": "7"},
        {"profileId": "a", "reach": "50 km"},
        {"profileId": "a", "breadth": "wide"},
        {"profileId": "a", "lane": "map"},
        {"profileId": "a", "order": "newest"},
        {"profileId": "a", "city": "x" * 81},
    ],
)
def test_a_bad_query_is_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        discover.DiscoverQuery.model_validate(bad)


@pytest.mark.parametrize(
    ("interface", "model", "old", "new", "expected"),
    [
        ("Place", discover.Place, "  km?: number;", "  km: number;", "km: optional in pydantic only"),
        ("ListingCard", discover.ListingCard, "  applyHost: string;", "", "applyHost: in pydantic but not in TypeScript"),
        ("ListingCard", discover.ListingCard, "  alsoFits: string[];", "  alsoFits: string[]; extra: string;", "extra: in TypeScript"),
        (
            "DiscoverResponse",
            discover.DiscoverResponse,
            "laneCounts: { near: number; remote: number; city: number };",
            "laneCounts: { near: number; remote: number };",
            "laneCounts.city: in pydantic but not in TypeScript",
        ),
    ],
)  # fmt: skip
def test_the_check_fires_when_the_typescript_drifts(
    interface: str, model: type[BaseModel], old: str, new: str, expected: str
) -> None:
    source = DISCOVER_TS.read_text(encoding="utf-8")
    assert old in source, old
    problems = diff(TsTypes(source.replace(old, new)).interface(interface), model_shape(model))
    assert any(expected in problem for problem in problems), problems


def test_the_check_fires_when_a_union_drifts() -> None:
    source = DISCOVER_TS.read_text(encoding="utf-8")
    old = "export type DiscoverReach = '3' | '5' | '10' | '25' | 'city';"
    assert old in source
    drifted = TsTypes(source.replace(old, "export type DiscoverReach = '3' | '5';"))
    assert drifted.shape("DiscoverReach") != ("lit", frozenset(get_args(discover.DiscoverReach)))
