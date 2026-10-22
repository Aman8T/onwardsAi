"""The card's radar geometry is the app's: `tayyari.card.geometry` gives byte-identical paths to `src/lib/radar.ts` for 1 to 8
axes (the fixture is written by `scripts/radar-parity.ts`, which `npm run check` keeps current)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from tayyari.card import geometry as geo

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "card" / "radar-paths.json"
DATA: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = {case["n"]: case for case in DATA["cases"]}


def test_the_fixture_covers_one_to_eight_axes() -> None:
    assert sorted(CASES) == list(range(1, 9))


def test_the_geometry_is_the_apps() -> None:
    g = geo.radar_geom()
    assert DATA["geom"] == {"size": g.size, "cx": g.cx, "cy": g.cy, "r": g.r, "labelR": g.label_r}


@pytest.mark.parametrize("n", range(1, 9))
def test_polygon_and_rings_match(n: int) -> None:
    case, g = CASES[n], geo.radar_geom()
    assert geo.polygon_path(g, case["values"]) == case["polygon"]
    assert [geo.ring_path(g, n, level) for level in (0.25, 0.5, 0.75, 1)] == case["rings"]


@pytest.mark.parametrize("n", range(1, 9))
def test_the_leaf_matches(n: int) -> None:
    leaf = geo.radar_leaf(geo.radar_geom(), n)
    expected = CASES[n]["leaf"]
    if expected is None:
        assert leaf is None
    else:
        assert leaf is not None and (leaf.blade, leaf.stalk) == (expected["blade"], expected["stalk"])


@pytest.mark.parametrize("n", range(1, 9))
def test_the_label_anchors_match(n: int) -> None:
    g = geo.radar_geom()
    for i, want in enumerate(CASES[n]["labels"]):
        got = geo.label_anchor(g, i, n)
        assert got.side == want["side"]
        assert math.isclose(got.x, want["x"], abs_tol=1e-9) and math.isclose(got.y, want["y"], abs_tol=1e-9)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0"),
        (-0.04, "0"),
        (300.0, "300"),
        (113.45, "113.5"),
        (-0.05, "0"),
        (-1.25, "-1.2"),
        (12.34, "12.3"),
    ],
)
def test_numbers_are_written_as_javascript_writes_them(value: float, text: str) -> None:
    assert geo.fmt(value) == text


def test_an_empty_polygon_is_empty() -> None:
    assert geo.polygon_path(geo.radar_geom(), []) == ""
