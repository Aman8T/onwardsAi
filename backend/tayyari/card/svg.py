"""The radar card as SVG text (canvas https://claude.ai/artifact/LG8rk27K2RfG68giTXcoX5, boards "Card", "Og" and "Edges").

Three drawings, all from a `CardContent` and nothing else:

- `card_svg`: the README card, ``md`` 480 x 330 (the radar with its rim labels, every area's score, up to three skills, an
  optional role outline and legend) or ``sm`` 340 x 190 (the radar alone and the three strongest areas), in the ``light``,
  ``dark`` or ``transparent`` theme;
- `og_svg`: the 1200 x 627 picture LinkedIn shows (always light; "Strongest in {area}"), which `card.png` rasterises;
- `notice_svg`: the neutral 340 x 120 card for a link that is not shared any more, or a card that cannot be drawn.

The rules the drawings keep (spec R1, "Rendering"): every text goes through `text.xml_text`; long names are cut with an
ellipsis by measured width, never left to overflow; the colours are the app's light and dark tokens written as hex (the
restraint check scans ``src/`` only); the fonts are the system stack; nothing moves; and the SVG holds no script, image, link,
``url(``, ``@import`` or ``@font-face``, so GitHub's image proxy and a strict CSP both take it as it is. The radar is the app's
own geometry (`card.geometry`), on the leaf from three areas up; fewer areas are bars.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from tayyari.card import geometry as geo
from tayyari.card.content import CardContent, DrawnArea
from tayyari.card.text import clip, measure, wrap, xml_text

Theme = Literal["light", "dark", "transparent"]
Size = Literal["md", "sm"]
Notice = Literal["stopped", "error"]

FONT: Final = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
OG_FONT: Final = "Geist, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
"""The LinkedIn picture is drawn by resvg with the bundled Geist (R1b); browsers fall back to the system stack."""
SITE: Final = "leafout.in"
KICKER: Final = "SKILL SHAPE"
NOTICES: Final[dict[Notice, str]] = {
    "stopped": "This card isn't shared any more.",
    "error": "The card can't be drawn right now.",
}
MD: Final = (480, 330)
SM: Final = (340, 190)
OG: Final = (1200, 627)
NOTICE: Final = (340, 120)
LABEL_R: Final = 262
"""Rim labels sit at 262 viewBox units, outside the leaf's tips (224)."""
VIEW_SPAN: Final = 680
"""The radar's drawing box in viewBox units (``-40 -40 680 680`` on the canvas)."""


@dataclass(frozen=True, slots=True)
class Paint:
    """A colour and its opacity (SVG 1.1 has no ``rgba()`` in presentation attributes, so opacity is its own attribute)."""

    hex: str
    opacity: float = 1.0

    def fill(self) -> str:
        return f'fill="{self.hex}"' + ("" if self.opacity == 1 else f' fill-opacity="{self.opacity:g}"')

    def stroke(self) -> str:
        return f'stroke="{self.hex}"' + ("" if self.opacity == 1 else f' stroke-opacity="{self.opacity:g}"')


@dataclass(frozen=True, slots=True)
class Palette:
    bg: Paint | None
    border: Paint
    text: Paint
    muted: Paint
    grid: Paint
    leaf: Paint
    leaf_line: Paint
    you_fill: Paint
    you_line: Paint
    role_fill: Paint
    role_line: Paint


# The light ("Dye on paper") and dark ("Dye by night") tokens of src/theme/tokens.css, as the canvas's Card board has them.
PALETTES: Final[dict[Theme, Palette]] = {
    "light": Palette(
        bg=Paint("#f0f7fe"),
        border=Paint("#c4d7eb"),
        text=Paint("#121e2b"),
        muted=Paint("#3c4f63"),
        grid=Paint("#d4e3f2"),
        leaf=Paint("#3d6690", 0.06),
        leaf_line=Paint("#3d6690", 0.22),
        you_fill=Paint("#3d6690", 0.18),
        you_line=Paint("#274562"),
        role_fill=Paint("#3d6690", 0.07),
        role_line=Paint("#5f7d9d"),
    ),
    "dark": Palette(
        bg=Paint("#0b131c"),
        border=Paint("#202f3f"),
        text=Paint("#dde6ef"),
        muted=Paint("#a0b0c1"),
        grid=Paint("#1c2a39"),
        leaf=Paint("#7bb9fa", 0.07),
        leaf_line=Paint("#7bb9fa", 0.24),
        you_fill=Paint("#7bb9fa", 0.22),
        you_line=Paint("#abd2fb"),
        role_fill=Paint("#7bb9fa", 0.08),
        role_line=Paint("#7e98b3"),
    ),
    "transparent": Palette(
        bg=None,
        border=Paint("#8b9cae"),
        text=Paint("#5b6f84"),
        muted=Paint("#6b7d90"),
        grid=Paint("#6b7d90", 0.35),
        leaf=Paint("#6b7d90", 0.08),
        leaf_line=Paint("#6b7d90", 0.35),
        you_fill=Paint("#5b7ca0", 0.22),
        you_line=Paint("#5b7ca0"),
        role_fill=Paint("#6b7d90", 0.08),
        role_line=Paint("#7d8fa2"),
    ),
}

f = geo.fmt


def _date(content: CardContent) -> str:
    return f"{content.updated.day} {content.updated:%b %Y}"


def _text(
    x: float,
    y: float,
    body: str,
    paint: Paint,
    size: float,
    *,
    anchor: str = "start",
    bold: bool = False,
    extra: str = "",
) -> str:
    """One line of text at baseline ``y``; ``body`` is plain text and is escaped here."""
    weight = ' font-weight="600"' if bold else ""
    end = "" if anchor == "start" else f' text-anchor="{anchor}"'
    return f'<text x="{f(x)}" y="{f(y)}" font-size="{size:g}"{weight}{end} {paint.fill()}{extra}>{xml_text(body)}</text>'


def _open(width: int, height: int, title: str, font: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" font-family="{font}">',
        f"<title>{xml_text(title)}</title>",
    ]


def _frame(width: int, height: int, pal: Palette) -> str:
    fill = pal.bg.fill() if pal.bg is not None else 'fill="none"'
    return f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" {fill} {pal.border.stroke()}/>'


def _header(width: int, pad: int, y: float, pal: Palette, size: float = 11) -> list[str]:
    spacing = f' letter-spacing="{size * 0.06:g}"'
    return [
        _text(pad, y, KICKER, pal.muted, size, bold=True, extra=spacing),
        _text(width - pad, y, SITE, pal.muted, size, anchor="end"),
    ]


def _title(content: CardContent) -> str:
    areas = content.areas
    if not areas:
        return "Skill shape: no areas on this graph yet."
    if content.shape == "bars":
        return "Skill shape: " + ", ".join(f"{a.name} {a.score}" for a in areas) + "."
    top = content.strongest() or areas[0]
    return f"Skill shape across {len(areas)} areas, strongest {top.name} at {top.score} of 100."


# ───────────────────────────── the radar ─────────────────────────────


def _radar(
    content: CardContent,
    pal: Palette,
    cx: float,
    cy: float,
    scale: float,
    *,
    compare: bool,
    label_size: float | None,
    label_budget: Callable[[float, str], float] | None = None,
) -> list[str]:
    """The leaf, grid, optional role outline, the person's shape and dots, scaled about (``cx``, ``cy``); rim labels in card
    pixels when ``label_size`` is given, each cut to ``label_budget(x, anchor)`` px."""
    g = geo.radar_geom()
    n = len(content.areas)
    out = [f'<g transform="translate({f(cx - g.cx * scale)} {f(cy - g.cy * scale)}) scale({scale:.4f})">']
    leaf = geo.radar_leaf(g, n)
    if leaf is not None:
        out.append(f'<path d="{leaf.blade}" {pal.leaf.fill()} {pal.leaf_line.stroke()} stroke-width="2"/>')
        out.append(
            f'<path d="{leaf.stalk}" fill="none" {pal.leaf_line.stroke()} stroke-width="3" stroke-linecap="round"/>'
        )
    rings = " ".join(geo.ring_path(g, n, level) for level in (0.25, 0.5, 0.75, 1))
    spokes = " ".join(
        f"M {f(g.cx)} {f(g.cy)} L {' '.join(f(v) for v in geo.point_at(g, i, n, 1))}" for i in range(n)
    )
    out.append(f'<path d="{rings}" fill="none" {pal.grid.stroke()} stroke-width="1.5"/>')
    out.append(f'<path d="{spokes}" fill="none" {pal.grid.stroke()} stroke-width="1.5"/>')
    if compare:
        role = geo.polygon_path(g, [_target(a) / 100 for a in content.areas])
        out.append(
            f'<path d="{role}" {pal.role_fill.fill()} {pal.role_line.stroke()} stroke-width="2.5" '
            f'stroke-dasharray="10 8"/>'
        )
    you = geo.polygon_path(g, [a.score / 100 for a in content.areas])
    out.append(
        f'<path d="{you}" {pal.you_fill.fill()} {pal.you_line.stroke()} stroke-width="4" stroke-linejoin="round"/>'
    )
    for i, area in enumerate(content.areas):
        x, y = geo.point_at(g, i, n, area.score / 100)
        out.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="8" {pal.you_line.fill()}/>')
    out.append("</g>")
    if label_size is not None:
        for i, area in enumerate(content.areas):
            a = geo.angle_of(i, n)
            lx, ly = cx + math.cos(a) * LABEL_R * scale, cy + math.sin(a) * LABEL_R * scale
            anchor = "middle" if abs(math.cos(a)) < 0.2 else "start" if math.cos(a) > 0 else "end"
            budget = label_budget(lx, anchor) if label_budget is not None else math.inf
            out.append(
                _text(
                    lx,
                    ly + label_size * 0.35,
                    clip(area.short, label_size, budget),
                    pal.muted,
                    label_size,
                    anchor=anchor,
                )
            )
    return out


def _target(area: DrawnArea) -> int:
    """The role's outline on an axis: what it expects, or the person's own score where the role names nothing there."""
    return area.score if area.role_target is None else area.role_target


def _rows(
    areas: tuple[DrawnArea, ...] | list[DrawnArea],
    pal: Palette,
    x0: float,
    x1: float,
    top: float,
    pitch: float,
    size: float,
) -> list[str]:
    """Area name and score per row, a 1 px rule under each; names cut to the room the score leaves."""
    out: list[str] = []
    for i, area in enumerate(areas):
        row = top + i * pitch
        base = row + pitch - 9
        score_w = measure(str(area.score), size, bold=True)
        out.append(_text(x0, base, clip(area.name, size, x1 - x0 - score_w - 8), pal.text, size))
        out.append(_text(x1, base, str(area.score), pal.text, size, anchor="end", bold=True))
        out.append(
            f'<rect x="{f(x0)}" y="{f(row + pitch - 2)}" width="{f(x1 - x0)}" height="1" {pal.grid.fill()}/>'
        )
    return out


def _legend(content: CardContent, pal: Palette, x: float, y: float, budget: float) -> list[str]:
    """ "Now" and "{role} expects", the line styles the radar uses, at baseline ``y``."""
    size = 11
    now_w = measure("Now", size)
    x2 = x + 21 + now_w + 14
    role = clip(f"{content.role_title} expects", size, max(0.0, budget - (x2 + 21 - x)))
    return [
        f'<line x1="{f(x)}" y1="{f(y - 4)}" x2="{f(x + 16)}" y2="{f(y - 4)}" {pal.you_line.stroke()} stroke-width="2"/>',
        _text(x + 21, y, "Now", pal.muted, size),
        f'<line x1="{f(x2)}" y1="{f(y - 4)}" x2="{f(x2 + 16)}" y2="{f(y - 4)}" {pal.role_line.stroke()} '
        f'stroke-width="1.5" stroke-dasharray="3 2"/>',
        _text(x2 + 21, y, role, pal.muted, size),
    ]


def _chips(content: CardContent, pal: Palette, x: float, top: float, width: float) -> list[str]:
    """Up to three skill pills, ``name score``, each cut to a third of the row."""
    size, gap, h = 11, 6, 20
    most = (width - 2 * gap) / 3
    out: list[str] = []
    for skill in content.skills[:3]:
        score = str(skill.score)
        name = clip(skill.name, size, most - 20 - measure(score, size))
        w = 20 + measure(name, size) + measure(score, size)
        out.append(
            f'<rect x="{f(x + 0.5)}" y="{f(top + 0.5)}" width="{f(w - 1)}" height="{h - 1}" rx="9.5" fill="none" {pal.border.stroke()}/>'
        )
        out.append(
            f'<text x="{f(x + 8)}" y="{f(top + 14)}" font-size="{size}" {pal.text.fill()}>{xml_text(name)}'
            f'<tspan dx="4" {pal.muted.fill()}>{xml_text(score)}</tspan></text>'
        )
        x += w + gap
    return out


def _bars(
    content: CardContent, pal: Palette, x0: float, x1: float, top: float, *, compare: bool, size: float = 12.5
) -> list[str]:
    """Fewer than three areas make no shape: a bar per area (name and score above), the role's level as a tick."""
    if not content.areas:
        return [_text(x0, top + 16, "No areas on this graph yet.", pal.muted, size)]
    out: list[str] = []
    score_w = measure("100", size, bold=True)
    width = x1 - x0
    for i, area in enumerate(content.areas):
        row = top + i * 40
        out.append(_text(x0, row + 13, clip(area.name, size, width - score_w - 10), pal.text, size))
        out.append(_text(x1, row + 13, str(area.score), pal.text, size, anchor="end", bold=True))
        out.append(
            f'<rect x="{f(x0)}" y="{f(row + 20)}" width="{f(width)}" height="6" rx="3" {pal.grid.fill()}/>'
        )
        if area.score > 0:
            out.append(
                f'<rect x="{f(x0)}" y="{f(row + 20)}" width="{f(width * area.score / 100)}" height="6" rx="3" '
                f"{pal.you_line.fill()}/>"
            )
        if compare:
            tx = x0 + width * _target(area) / 100
            out.append(
                f'<rect x="{f(tx - 1)}" y="{f(row + 18)}" width="2" height="10" {pal.role_line.fill()}/>'
            )
    return out


# ───────────────────────────── the README card ─────────────────────────────


def card_svg(
    content: CardContent, theme: Theme = "light", size: Size = "md", *, compare: bool = False
) -> str:
    """The README card. ``compare`` draws the role's outline and legend (``md`` only, and only when the card has a role)."""
    pal = PALETTES[theme]
    compare = compare and size == "md" and content.role_title is not None
    return _md(content, pal, compare) if size == "md" else _sm(content, pal)


def _md(content: CardContent, pal: Palette, compare: bool) -> str:
    w, h = MD
    pad = 18
    out = _open(w, h, _title(content), FONT)
    out.append(_frame(w, h, pal))
    out += _header(w, pad, pad + 9, pal)
    footer_y = h - pad
    has_skills = bool(content.skills)
    body_top, body_bottom = 40.0, (262.0 if has_skills else 296.0)

    if content.shape == "radar":
        scale = 0.3
        label_r = LABEL_R * scale
        budget = 60.0
        cx, cy = pad + budget + label_r, body_top + 88
        rows_x = cx + label_r + budget + 12

        def label_budget(x: float, anchor: str) -> float:
            right = rows_x - 12
            if anchor == "start":
                return right - x
            if anchor == "end":
                return x - pad
            return 2 * min(x - pad, right - x)

        out += _radar(
            content, pal, cx, cy, scale, compare=compare, label_size=30 * scale, label_budget=label_budget
        )
        n = len(content.areas)
        pitch = min(28.0, (body_bottom - body_top) / n)
        rows_top = max(body_top, cy - n * pitch / 2)
        out += _rows(content.areas, pal, rows_x, w - pad, rows_top, pitch, 12)
        if compare:
            out += _legend(content, pal, pad, cy + label_r + 22, rows_x - 12 - pad)
    else:
        out += _bars(content, pal, pad, w - pad, body_top + 4, compare=compare)
        if compare:
            out += _legend(content, pal, pad, body_top + 4 + 40 * len(content.areas) + 14, w - 2 * pad)

    if has_skills:
        out += _chips(content, pal, pad, 270, w - 2 * pad)
    out.append(
        _text(
            pad, footer_y, f"Every score rests on cited evidence · Updated {_date(content)}", pal.muted, 10.5
        )
    )
    out.append("</svg>")
    return "".join(out)


def _sm(content: CardContent, pal: Palette) -> str:
    w, h = SM
    pad = 14
    out = _open(w, h, _title(content), FONT)
    out.append(_frame(w, h, pal))
    out += _header(w, pad, pad + 9, pal)
    body_top = 32.0
    if content.shape == "radar":
        scale = 118 / VIEW_SPAN
        reach = 260 * scale  # the stalk's end, the farthest the drawing goes
        cx, cy = pad + reach + 4, body_top + 48
        rows_x = cx + reach + 14
        out += _radar(content, pal, cx, cy, scale, compare=False, label_size=None)
        strongest = sorted(content.areas, key=lambda a: -a.score)[:3]
        pitch = 26.0
        out += _rows(strongest, pal, rows_x, w - pad, cy - len(strongest) * pitch / 2, pitch, 12)
    else:
        out += _bars(content, pal, pad, w - pad, body_top + 2, compare=False, size=12)
    out.append(_text(pad, h - pad, f"Cited from evidence · {_date(content)}", pal.muted, 10.5))
    out.append("</svg>")
    return "".join(out)


# ───────────────────────────── the LinkedIn picture ─────────────────────────────


def og_svg(content: CardContent) -> str:
    """The 1200 x 627 picture for LinkedIn and other link previews: the radar on the left, "Strongest in {area}" and every
    area's score on the right. Always light (a feed is light or dark per reader, and the picture is one file)."""
    w, h = OG
    pal = PALETTES["light"]
    out = _open(w, h, _title(content) + " Every score rests on cited evidence. " + SITE, OG_FONT)
    out.append(f'<rect width="{w}" height="{h}" {(pal.bg or pal.grid).fill()}/>')
    col_x0, col_x1 = 620.0, 1136.0

    if content.shape == "radar":
        scale = 500 / VIEW_SPAN
        cx, cy = 64 + 340 * scale, h / 2

        def label_budget(x: float, anchor: str) -> float:
            right = col_x0 - 20
            if anchor == "start":
                return right - x
            if anchor == "end":
                return x - 24
            return 2 * min(x - 24, right - x)

        out += _radar(
            content, pal, cx, cy, scale, compare=False, label_size=26 * scale, label_budget=label_budget
        )
    else:
        out += _bars(content, pal, 64, 564, h / 2 - 40 * len(content.areas) / 2, compare=False, size=22)

    top = content.strongest()
    headline = wrap(f"Strongest in {top.name}" if top is not None else "Skill shape", 48, col_x1 - col_x0, 2)
    n = len(content.areas)
    pitch = min(36.0, 300 / max(n, 1))
    block = 22 + 26 + 56 * len(headline) + 20 + n * pitch + 24 + 20
    y = (h - block) / 2 + 18
    out.append(_text(col_x0, y, KICKER, pal.muted, 18, bold=True, extra=' letter-spacing="1.08"'))
    y += 26
    for line in headline:
        y += 52
        out.append(
            f'<text x="{f(col_x0)}" y="{f(y)}" font-size="48" font-weight="500" {pal.text.fill()}>{xml_text(line)}</text>'
        )
        y += 4
    y += 20
    out += _rows(content.areas, pal, col_x0, col_x1, y, pitch, 22 if pitch >= 34 else 19)
    y += n * pitch + 24 + 17
    out.append(_text(col_x0, y, "Every score rests on cited evidence", pal.muted, 17))
    out.append(_text(col_x1, y, SITE, pal.you_line, 17, anchor="end", bold=True))
    out.append("</svg>")
    return "".join(out)


# ───────────────────────────── the neutral card ─────────────────────────────


def notice_svg(kind: Notice, theme: Theme = "light") -> str:
    """The card an unknown, stopped or malformed link gets, and the one a failure gets: still a picture (a README never
    shows a broken image), saying nothing about whose card it was."""
    w, h = NOTICE
    pad = 16
    pal = PALETTES[theme]
    message = NOTICES[kind]
    out = _open(w, h, message, FONT)
    out.append(_frame(w, h, pal))
    out.append(_text(pad, pad + 10, KICKER, pal.muted, 11, bold=True, extra=' letter-spacing="0.66"'))
    out.append(_text(pad, h / 2 + 5, message, pal.text, 14))
    out.append(_text(pad, h - pad, SITE, pal.muted, 11))
    out.append("</svg>")
    return "".join(out)


def og_notice_svg(kind: Notice) -> str:
    """The 1200 x 627 picture an unknown or stopped link gets as its LinkedIn image (a failure gets the same, worded for it)."""
    w, h = OG
    pal = PALETTES["light"]
    message = NOTICES[kind]
    out = _open(w, h, message, OG_FONT)
    out.append(f'<rect width="{w}" height="{h}" {(pal.bg or pal.grid).fill()}/>')
    out.append(_text(96, 250, KICKER, pal.muted, 22, bold=True, extra=' letter-spacing="1.32"'))
    out.append(
        f'<text x="96" y="330" font-size="52" font-weight="500" {pal.text.fill()}>{xml_text(message)}</text>'
    )
    out.append(_text(96, 400, SITE, pal.you_line, 24, bold=True))
    out.append("</svg>")
    return "".join(out)


__all__ = [
    "FONT",
    "MD",
    "NOTICE",
    "NOTICES",
    "OG",
    "PALETTES",
    "SM",
    "Notice",
    "Palette",
    "Size",
    "Theme",
    "card_svg",
    "notice_svg",
    "og_notice_svg",
    "og_svg",
]
