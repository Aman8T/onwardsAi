"""tayyari.contract.discover must match shared/discover.ts exactly (interfaces, optionality, the unions).

Discover keeps the city list and the small vocabulary Search with AI reuses (`Cite`, `Place`, `QuickFit`, `Salary`,
`DiscoverWorkMode`). Reuses the TypeScript reader and the shape comparison of test_ts_parity.py.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel

from tayyari.contract import discover
from tayyari.core.paths import REPO_ROOT
from tayyari.discover import places
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
    "DiscoverCity": discover.DiscoverCity,
    "DiscoverCitiesResponse": discover.DiscoverCitiesResponse,
}

UNIONS = {
    "DiscoverWorkMode": discover.DiscoverWorkMode,
    "PlacePrecision": discover.PlacePrecision,
    "QuickFitWord": discover.QuickFitWord,
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


def test_every_exported_union_is_checked() -> None:
    text = DISCOVER_TS.read_text(encoding="utf-8")
    declared = {line.split()[2] for line in text.splitlines() if line.startswith("export type ")}
    assert declared == set(UNIONS)


def test_the_longest_place_name_a_client_may_send_fits_the_longest_of_ours() -> None:
    longest = max(
        len(name) for city in places.CITIES for name in [city.name, *(spot.name for spot in city.localities)]
    )
    assert longest <= discover.PLACE_NAME_MAX_CHARS


def test_optional_fields_leave_the_json_when_unset() -> None:
    place = discover.Place(label="Remote, India", precision="country")
    assert place.to_dict() == {"label": "Remote, India", "precision": "country"}
    reason = discover.Reason(text="You show React")
    assert reason.to_dict() == {"text": "You show React"}


@pytest.mark.parametrize(
    ("interface", "model", "old", "new", "expected"),
    [
        ("Place", discover.Place, "  km?: number;", "  km: number;", "km: optional in pydantic only"),
        ("QuickFit", discover.QuickFit, "  named: number;", "", "named: in pydantic but not in TypeScript"),
        ("QuickFit", discover.QuickFit, "  matched: number;", "  matched: number; extra: string;", "extra: in TypeScript"),
        ("DiscoverCity", discover.DiscoverCity, "  localities: string[];", "  localities: string;", "localities"),
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
    old = "export type PlacePrecision = 'locality' | 'city' | 'country';"
    assert old in source
    drifted = TsTypes(source.replace(old, "export type PlacePrecision = 'locality' | 'city';"))
    assert drifted.shape("PlacePrecision") != ("lit", frozenset(get_args(discover.PlacePrecision)))
