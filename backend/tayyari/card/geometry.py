"""The app's radar geometry in Python: `src/lib/radar.ts`, line for line, so the card draws the same shape as the graph.

A fixed 600 x 600 viewBox: centre 300,300, rim 200, labels at 236. Axis 0 sits at 12 o'clock and the rest follow clockwise in
the order given (never sorted by value). Paths are written with one decimal and ``-0`` normalised, exactly as the TypeScript
``fmt`` does, so a path here is byte-identical to the app's: `scripts/radar-parity.ts` writes the app's paths for 1 to 8 axes
into ``backend/tests/data/card/radar-paths.json`` and `tests/unit/card/test_geometry.py` compares.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

RADAR_VIEW: Final = 600

LabelSide = Literal["n", "ne", "e", "se", "s", "sw", "w", "nw"]
_SIDES: Final[tuple[LabelSide, ...]] = ("n", "ne", "e", "se", "s", "sw", "w", "nw")


@dataclass(frozen=True, slots=True)
class RadarGeom:
    size: float
    cx: float
    cy: float
    r: float
    """Radius of the 100 % ring."""
    label_r: float
    """Radius at which axis labels are anchored, outside the rim."""


@dataclass(frozen=True, slots=True)
class RadarLeaf:
    blade: str
    stalk: str


@dataclass(frozen=True, slots=True)
class LabelAnchor:
    x: float
    y: float
    side: LabelSide


def radar_geom() -> RadarGeom:
    """The one geometry every radar shares."""
    return RadarGeom(size=RADAR_VIEW, cx=RADAR_VIEW / 2, cy=RADAR_VIEW / 2, r=200, label_r=236)


def angle_of(i: int, n: int) -> float:
    """Angle of axis ``i`` of ``n`` in radians, clockwise from 12 o'clock (``n`` >= 1)."""
    return -math.pi / 2 + (i * 2 * math.pi) / n


def point_at(g: RadarGeom, i: int, n: int, value01: float) -> tuple[float, float]:
    """Point on axis ``i`` of ``n`` at ``value01`` (0 = centre, 1 = rim)."""
    a = angle_of(i, n)
    return g.cx + math.cos(a) * g.r * value01, g.cy + math.sin(a) * g.r * value01


def fmt(v: float) -> str:
    """JavaScript's ``String(Math.round(v * 10) / 10)``: half rounds up (towards +inf), ``-0`` is ``0``, no trailing ``.0``."""
    r = math.floor(v * 10 + 0.5) / 10
    if r == 0:
        return "0"
    if r == int(r):
        return str(int(r))
    return repr(r)


def polygon_path(g: RadarGeom, values01: list[float]) -> str:
    """Closed polygon through every axis: ``M x y L x y … Z``."""
    n = len(values01)
    if n == 0:
        return ""
    d = ""
    for i, v in enumerate(values01):
        x, y = point_at(g, i, n, v)
        d += f"{'M' if i == 0 else ' L'} {fmt(x)} {fmt(y)}"
    return f"{d} Z"


def ring_path(g: RadarGeom, n: int, level01: float) -> str:
    """The grid ring at ``level01``: a polygon through all ``n`` axes at that level."""
    return polygon_path(g, [level01] * n)


def label_anchor(g: RadarGeom, i: int, n: int) -> LabelAnchor:
    """Where axis ``i``'s label is anchored (at ``label_r``) and which compass side it faces, from 45 degree sectors."""
    a = angle_of(i, n)
    deg = (((a + math.pi / 2) * 180) / math.pi + 360) % 360
    side = _SIDES[_js_round(deg / 45) % 8]
    return LabelAnchor(x=g.cx + math.cos(a) * g.label_r, y=g.cy + math.sin(a) * g.label_r, side=side)


def radar_leaf(g: RadarGeom, n: int) -> RadarLeaf | None:
    """The palmate leaf the chart is drawn on: one lobe per axis, the stalk through the sinus nearest the bottom. Only for
    three axes or more."""
    if n < 3:
        return None
    half = math.pi / n

    def at(a: float, r: float) -> str:
        return f"{fmt(g.cx + math.cos(a) * r)} {fmt(g.cy + math.sin(a) * r)}"

    tip_r = g.r * 1.12
    sinus_r = g.r * 0.8
    shoulder_r = g.r * 1.04
    blade = ""
    for i in range(n):
        a = angle_of(i, n)
        if i == 0:
            blade = f"M {at(a - half, sinus_r)}"
        blade += f" Q {at(a - half * 0.55, shoulder_r)} {at(a, tip_r)} Q {at(a + half * 0.55, shoulder_r)} {at(a + half, sinus_r)}"
    stalk_angle = angle_of(0, n) + half
    best_gap = math.inf
    for i in range(n):
        m = angle_of(i, n) + half
        gap = abs(math.atan2(math.sin(m - math.pi / 2), math.cos(m - math.pi / 2)))
        if gap < best_gap:
            best_gap = gap
            stalk_angle = m
    stalk = f"M {at(stalk_angle, sinus_r)} Q {at(stalk_angle - 0.08, g.r * 1.1)} {at(stalk_angle, g.r * 1.3)}"
    return RadarLeaf(blade=f"{blade} Z", stalk=stalk)


def _js_round(v: float) -> int:
    """JavaScript's ``Math.round``: half rounds towards +inf (Python's ``round`` goes to even)."""
    return math.floor(v + 0.5)


__all__ = [
    "RADAR_VIEW",
    "LabelAnchor",
    "LabelSide",
    "RadarGeom",
    "RadarLeaf",
    "angle_of",
    "fmt",
    "label_anchor",
    "point_at",
    "polygon_path",
    "radar_geom",
    "radar_leaf",
    "ring_path",
]
