"""The report's two drawings: the skill shape on its page (a light and a dark copy, as ``data:`` images), and the 1200 x 630
link preview (drawn to PNG by `report.png`).

Both reuse the radar card's geometry, palettes and text measuring (`tayyari.card`), so a shape looks the same on the card, on
the report and in the app. Every text node is escaped; nothing here holds a script, a link or an external reference.
"""

from __future__ import annotations

import base64
import math
from typing import Final

from tayyari.card import geometry as geo
from tayyari.card.svg import PALETTES, Palette
from tayyari.card.text import clip, wrap, xml_text
from tayyari.contract.report import ReportArea, ReportContent

f = geo.fmt

OG: Final = (1200, 630)
SHAPE: Final = (420, 340)
SITE: Final = "leafout.in"
KICKER: Final = "WHERE CAN I GO NEXT"
FONT: Final = "Geist, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
LABEL_R: Final = 262
VIEW_SPAN: Final = 680
FOOT: Final = "Every role cites a line of their own resume"
NOTICE_TEXT: Final = "This report isn't shared any more"


def _text(
    x: float, y: float, text: str, paint_fill: str, size: float, *, anchor: str = "start", bold: bool = False
) -> str:
    weight = ' font-weight="600"' if bold else ""
    return (
        f'<text x="{f(x)}" y="{f(y)}" font-size="{f(size)}"{weight} text-anchor="{anchor}" {paint_fill}>'
        f"{xml_text(text)}</text>"
    )


def _radar(
    areas: list[ReportArea], pal: Palette, cx: float, cy: float, scale: float, label_size: float
) -> list[str]:
    """Grid, spokes, the person's shape and dots, scaled about (``cx``, ``cy``), with rim labels in drawing pixels. Fewer
    than three areas make no shape: the rows beside it carry the scores instead."""
    n = len(areas)
    if n < 3:
        return []
    g = geo.radar_geom()
    out = [f'<g transform="translate({f(cx - g.cx * scale)} {f(cy - g.cy * scale)}) scale({scale:.4f})">']
    rings = " ".join(geo.ring_path(g, n, level) for level in (0.25, 0.5, 0.75, 1))
    spokes = " ".join(
        f"M {f(g.cx)} {f(g.cy)} L {' '.join(f(v) for v in geo.point_at(g, i, n, 1))}" for i in range(n)
    )
    out.append(f'<path d="{rings}" fill="none" {pal.grid.stroke()} stroke-width="1.5"/>')
    out.append(f'<path d="{spokes}" fill="none" {pal.grid.stroke()} stroke-width="1.5"/>')
    you = geo.polygon_path(g, [a.score / 100 for a in areas])
    out.append(
        f'<path d="{you}" {pal.you_fill.fill()} {pal.you_line.stroke()} stroke-width="4" stroke-linejoin="round"/>'
    )
    for i, area in enumerate(areas):
        x, y = geo.point_at(g, i, n, area.score / 100)
        out.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="8" {pal.you_line.fill()}/>')
    out.append("</g>")
    for i, area in enumerate(areas):
        a = geo.angle_of(i, n)
        lx, ly = cx + math.cos(a) * LABEL_R * scale, cy + math.sin(a) * LABEL_R * scale
        anchor = "middle" if abs(math.cos(a)) < 0.2 else "start" if math.cos(a) > 0 else "end"
        out.append(_text(lx, ly + label_size * 0.35, area.short, pal.muted.fill(), label_size, anchor=anchor))
    return out


def _open(width: int, height: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{xml_text(title)}" font-family="{FONT}">',
        f"<title>{xml_text(title)}</title>",
    ]


def shape_svg(areas: list[ReportArea], theme: str = "light") -> str:
    """The skill shape on the report's page, transparent behind, in the light or dark palette."""
    w, h = SHAPE
    pal = PALETTES["dark" if theme == "dark" else "light"]
    out = _open(w, h, f"Skill shape across {len(areas)} areas")
    out += _radar(areas, pal, w / 2, h / 2, 0.5, 14)
    out.append("</svg>")
    return "".join(out)


def data_uri(svg: str) -> str:
    """An SVG as a ``data:`` image (the page's CSP allows ``img-src data:`` and no inline style)."""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def headline(content: ReportContent) -> str:
    """The page's and the preview's one question, with the name only while the owner shows it."""
    who = content.person.name or "this person"
    return f"Where can {who} go next?"


def og_svg(content: ReportContent) -> str:
    """The 1200 x 630 link preview: the shape on the left; the question, the roles and the site on the right. Always light
    (a feed is light or dark per reader, and the picture is one file)."""
    w, h = OG
    pal = PALETTES["light"]
    out = _open(w, h, headline(content))
    out.append(f'<rect width="{w}" height="{h}" {(pal.bg or pal.grid).fill()}/>')
    x0, x1 = 620.0, 1136.0
    if len(content.areas) >= 3:
        out += _radar(content.areas, pal, 64 + 340 * (500 / VIEW_SPAN), h / 2, 500 / VIEW_SPAN, 19)
    titles = [r.title for r in content.roles]
    lines: list[str] = []
    for title in titles:
        lines += wrap(title, 40, x1 - x0, 2)
    if not lines:
        lines = wrap("Close to a move: the report shows what's missing", 34, x1 - x0, 3)
    lines = lines[:6]
    size = 40 if len(lines) <= 4 else 32
    pitch = size + 10
    block = 22 + 30 + 24 + pitch * len(lines) + 28 + 22
    y = (h - block) / 2 + 18
    out.append(_text(x0, y, KICKER, pal.muted.fill(), 18, bold=True))
    y += 38
    out.append(_text(x0, y, clip(headline(content), 24, x1 - x0), pal.muted.fill(), 24))
    y += 20
    for line in lines:
        y += pitch
        out.append(_text(x0, y, line, pal.text.fill(), size, bold=True))
    y += 46
    out.append(_text(x0, y, FOOT, pal.muted.fill(), 18))
    out.append(_text(x1, y, SITE, pal.you_line.fill(), 18, anchor="end", bold=True))
    out.append("</svg>")
    return "".join(out)


def og_notice_svg() -> str:
    """The preview an unknown or stopped link gets: says nothing about whose report it was."""
    w, h = OG
    pal = PALETTES["light"]
    out = _open(w, h, NOTICE_TEXT)
    out.append(f'<rect width="{w}" height="{h}" {(pal.bg or pal.grid).fill()}/>')
    out.append(_text(80, h / 2 - 20, KICKER, pal.muted.fill(), 20, bold=True))
    out.append(_text(80, h / 2 + 30, NOTICE_TEXT, pal.text.fill(), 40, bold=True))
    out.append(_text(80, h - 60, SITE, pal.you_line.fill(), 20, bold=True))
    out.append("</svg>")
    return "".join(out)


__all__ = ["OG", "data_uri", "headline", "og_notice_svg", "og_svg", "shape_svg"]
