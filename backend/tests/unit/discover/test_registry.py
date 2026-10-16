"""The employer registry: the format, its validation, and the file shipped with the package."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tayyari.discover import places
from tayyari.discover.errors import RegistryError
from tayyari.discover.registry import DEFAULT_PATH, load_registry, parse_registry
from tests.fakes.discover import DATA, fixture_registry


def valid() -> dict[str, Any]:
    return json.loads((DATA / "registry.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def problem_of(data: Any) -> str:
    with pytest.raises(RegistryError) as raised:
        parse_registry(data)
    return str(raised.value)


def test_the_fixture_registry_loads() -> None:
    registry = fixture_registry()
    assert registry.version == 1 and registry.verified_at == "2026-10-16"
    assert len(registry.employers) == 9
    kestrel = registry.get("kestrel")
    assert kestrel is not None
    assert (kestrel.name, kestrel.ats, kestrel.board) == ("Kestrel Pay", "greenhouse", "kestrelpay")
    assert [(o.city, o.locality) for o in kestrel.offices] == [("Bengaluru", "Koramangala"), ("Pune", None)]
    assert registry.get("nope") is None and "kestrel" in registry.ids


def test_a_locality_is_safe_only_when_the_employer_lists_exactly_one_office_in_the_city() -> None:
    registry = fixture_registry()
    kestrel = registry.get("kestrel")
    northwind = registry.get("northwind")
    quill = registry.get("quill")
    assert kestrel is not None and northwind is not None and quill is not None
    assert kestrel.sole_locality("Bengaluru") == "Koramangala"
    assert kestrel.sole_locality("Pune") is None  # an office with no locality
    assert northwind.sole_locality("Bengaluru") is None  # two offices there
    assert quill.sole_locality("Bengaluru") is None
    assert kestrel.sole_locality("Mumbai") is None  # no office there


def test_the_registry_shipped_with_the_package_is_valid_and_uses_our_place_names() -> None:
    if not DEFAULT_PATH.is_file():
        pytest.skip("the employer registry is written by another agent and is not in this checkout")
    registry = load_registry()
    assert registry.version == 1 and len(registry.employers) >= 1
    assert len(registry.ids) == len(registry.employers)  # ids are unique
    for employer in registry.employers:
        assert employer.ats in ("greenhouse", "lever")
        assert employer.careers_url.startswith("https://")
        for office in employer.offices:
            city = places.city_by_name(office.city)
            assert city is not None and city.name == office.city
            if office.locality:
                assert places.locality_in(city.name, office.locality) is not None


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda d: d.update(version=2), "version must be 1"),
        (lambda d: d["employers"][0].update(id="Bad Id"), "id must be a lowercase slug"),
        (lambda d: d["employers"][1].update(id="kestrel"), "id is used twice"),
        (lambda d: d["employers"][0].update(ats="ashby"), "ats must be one of greenhouse, lever"),
        (lambda d: d["employers"][0].update(board="../etc/passwd"), "board must be a board token"),
        (lambda d: d["employers"][0].update(board=""), "board must be a board token"),
        (
            lambda d: d["employers"][0].update(careersUrl="javascript:alert(1)"),
            "careersUrl must be an http(s) link",
        ),
        (lambda d: d["employers"][0].update(name=" "), "name is needed"),
        (
            lambda d: d["employers"][0].update(offices=[{"city": "Atlantis"}]),
            "'Atlantis' is not one of our cities",
        ),
        (
            lambda d: d["employers"][0].update(offices=[{"city": "Bangalore"}]),
            "'Bangalore' is not one of our cities",
        ),
        (
            lambda d: d["employers"][0].update(offices=[{"city": "Bengaluru", "locality": "Hebbel"}]),
            "'Hebbel' is not one of Bengaluru's",
        ),
        (
            lambda d: d["employers"][0].update(offices=[{"city": "Pune", "locality": "HSR Layout"}]),
            "not one of Pune's",
        ),
        (lambda d: d["employers"][0].update(offices="Pune"), "offices must be a list"),
        (
            lambda d: d["employers"][0].update(indiaJobsAtVerify=-1),
            "indiaJobsAtVerify must be a whole number",
        ),
        (lambda d: d["employers"][0].update(families="eng"), "families must be a list of text"),
    ],
)
def test_a_bad_registry_is_refused_with_what_is_wrong(edit: Any, expected: str) -> None:
    data = copy.deepcopy(valid())
    edit(data)
    assert expected in problem_of(data)


def test_every_problem_is_listed_at_once() -> None:
    data = valid()
    data["employers"][0]["ats"] = "ashby"
    data["employers"][1]["board"] = "a b"
    message = problem_of(data)
    assert "employers[0] (kestrel)" in message and "employers[1] (northwind)" in message


@pytest.mark.parametrize("data", [[], "x", None, {"version": 1}, {"version": 1, "employers": {}}])
def test_a_registry_of_the_wrong_shape_is_refused(data: Any) -> None:
    assert problem_of(data)


def test_a_missing_or_unreadable_file_is_a_registry_error(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="cannot read"):
        load_registry(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(RegistryError, match="not valid JSON"):
        load_registry(broken)
