"""The card's drawings, as known-bad fixtures and properties (spec R1, "Checks and tests"): nothing of the evidence reaches
them, model text is escaped and the result is well-formed XML, no construct a proxy or a strict CSP would refuse, small,
no score against other people, and bars instead of a radar below three areas."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date
from itertools import product

import pytest

from tayyari.card.content import CardContent, DrawnArea, DrawnSkill, content_of
from tayyari.card.service import offered_skills
from tayyari.card.svg import card_svg, notice_svg, og_svg
from tayyari.card.text import ELLIPSIS, clean, clip, measure, wrap
from tayyari.contract.card import CardUpdate
from tayyari.contract.models import Profile
from tests.unit.card.support import profile_doc, world

SVG = "{http://www.w3.org/2000/svg}"
THEMES = ("light", "dark", "transparent")
SIZES = ("md", "sm")
FORBIDDEN_TAGS = {
    "script",
    "image",
    "foreignObject",
    "a",
    "style",
    "use",
    "iframe",
    "animate",
    "set",
    "animateTransform",
}
LEAF = re.compile(r'<path d="M [^"]* Q ')


def parse(svg: str) -> ET.Element:
    """Our own output, built in this test (not untrusted input), so the standard parser is fine here."""
    return ET.fromstring(svg)  # noqa: S314


def content(
    n: int = 6, *, skills: int = 3, role: str | None = "Senior Frontend Engineer", name: str = "Area"
) -> CardContent:
    areas = tuple(
        DrawnArea(
            name=f"{name} {i} of the graph", short=f"{name[:10]} {i}", score=20 + i * 9, role_target=60 + i
        )
        for i in range(n)
    )
    picked = tuple(DrawnSkill(name=f"Skill number {i}", score=90 - i) for i in range(skills))
    return CardContent(areas=areas, skills=picked, role_title=role, updated=date(2026, 10, 22))


def every_drawing(c: CardContent) -> list[str]:
    out = [
        card_svg(c, theme, size, compare=compare)
        for theme, size, compare in product(THEMES, SIZES, (False, True))
    ]
    return [*out, og_svg(c)]


def texts(svg: str) -> list[str]:
    root = parse(svg)
    return ["".join(el.itertext()) for el in root.iter() if el.tag in (f"{SVG}text", f"{SVG}title")]


# ───────────────────────── nothing of the evidence ─────────────────────────


async def test_no_evidence_line_reaches_any_drawing() -> None:
    """Built through the service from a profile whose evidence lines carry unique markers: none appears anywhere."""
    w = world()
    uid = await w.account()
    doc = profile_doc("1" * 32)
    markers = []
    for i, skill in enumerate(doc["skills"]):
        for j, ev in enumerate(skill["evidence"]):
            marker = f"ZQX{i:02d}{j:02d}MARK"
            ev["text"] = f"{ev['text']} {marker}"
            markers.append(marker)
    doc["person"]["name"] = "Riya NAMEMARK Kapoor"
    await w.profile("1" * 32, owner=uid, doc=doc)
    top = [s.id for s in offered_skills(Profile.model_validate(doc))[:3]]
    await w.service.update(
        uid,
        CardUpdate(
            profileId="1" * 32, shared=True, skills=top, roleId="senior-frontend-engineer", theme="light"
        ),
    )
    c = content_of(w.fakes.card.rows[uid])
    assert c.skills and c.role_title
    for svg in every_drawing(c):
        assert not any(m in svg for m in markers)
        assert "NAMEMARK" not in svg and "Riya" not in svg


# ───────────────────────── escaped, well-formed ─────────────────────────


def test_markup_in_a_name_is_escaped_and_the_svg_parses() -> None:
    evil = "<script>alert(1)</script>"
    c = CardContent(
        areas=tuple(DrawnArea(name=evil, short=evil[:14], score=50 + i, role_target=40) for i in range(4)),
        skills=(DrawnSkill(name='"><img src=x onerror=alert(1)>', score=80),),
        role_title="</text><script>alert(2)</script>",
        updated=date(2026, 10, 22),
    )
    for svg in every_drawing(c):
        root = parse(svg)
        assert root.tag == f"{SVG}svg"
        assert "<script" not in svg and "<img" not in svg
        assert "&lt;script&gt;" in svg or "&lt;script" in svg
        assert not [el for el in root.iter() if el.tag.removeprefix(SVG) in FORBIDDEN_TAGS]


def test_characters_xml_cannot_hold_are_removed() -> None:
    c = content(4, name="Bad\x00\x07\x1b￾\ud800name")
    for svg in every_drawing(c):
        parse(svg)
    assert clean("a\x00b\tc\n\nd\ud800") == "a b c d"


def test_every_theme_and_size_parses_with_and_without_a_role() -> None:
    for n, skills, role in product((0, 1, 2, 3, 5, 8), (0, 3), ("Data engineer", None)):
        for svg in every_drawing(content(n, skills=skills, role=role)):
            parse(svg)


# ───────────────────────── nothing a proxy or a CSP refuses ─────────────────────────


@pytest.mark.parametrize("n", [1, 3, 6, 8])
def test_no_forbidden_construct(n: int) -> None:
    for svg in [*every_drawing(content(n)), notice_svg("stopped"), notice_svg("error", "dark")]:
        root = parse(svg)
        for el in root.iter():
            assert el.tag.startswith(SVG) and el.tag.removeprefix(SVG) not in FORBIDDEN_TAGS
            for key, value in el.attrib.items():
                assert "href" not in key and not key.startswith("on")
                assert "url(" not in value and "javascript:" not in value
        for word in ("<script", "<image", "href", "url(", "@import", "@font-face", "<style", "<animate"):
            assert word not in svg


def test_a_known_bad_drawing_is_caught_by_the_same_checks() -> None:
    """The checks must fire: a drawing with a script or an external link fails them."""
    bad = card_svg(content()).replace("</svg>", '<script>x()</script><image href="https://x/y.png"/></svg>')
    root = parse(bad)
    assert [el for el in root.iter() if el.tag.removeprefix(SVG) in FORBIDDEN_TAGS]
    assert "href" in bad


def test_the_card_is_small() -> None:
    long = CardContent(
        areas=tuple(
            DrawnArea(
                name="An unusually long area name that goes on and on " * 3,
                short="Fourteen chars",
                score=s,
                role_target=t,
            )
            for s, t in zip((12, 99, 47, 63, 88, 5, 71, 34), (90, 10, 50, 50, 70, 30, 80, 40), strict=True)
        ),
        skills=tuple(
            DrawnSkill(name="A skill with a very long name indeed " * 3, score=90) for _ in range(3)
        ),
        role_title="Principal engineer of extremely long role titles " * 4,
        updated=date(2026, 10, 22),
    )
    for svg in every_drawing(long):
        assert len(svg.encode("utf-8")) < 20_000


# ───────────────────────── no score against other people ─────────────────────────


def test_no_percentage_rank_or_percentile_in_any_text() -> None:
    for n in (2, 6, 8):
        for svg in [*every_drawing(content(n)), notice_svg("stopped"), notice_svg("error")]:
            words = " ".join(texts(svg)).lower()
            assert "%" not in words
            assert not re.search(r"\b(rank|ranked|top|percentile)\b", words), words


# ───────────────────────── the shape ─────────────────────────


@pytest.mark.parametrize("n", [0, 1, 2])
def test_fewer_than_three_areas_are_bars_with_no_leaf(n: int) -> None:
    c = content(n)
    assert c.shape == "bars"
    for svg in every_drawing(c):
        assert not LEAF.search(svg)
        assert "<circle" not in svg
    md = card_svg(c, "light", "md")
    if n:
        assert md.count('height="6" rx="3"') == 2 * n, "a track and a fill per area"
    else:
        assert "No areas on this graph yet." in md


@pytest.mark.parametrize("n", [3, 5, 8])
def test_three_areas_or_more_are_a_radar_on_the_leaf(n: int) -> None:
    c = content(n)
    assert c.shape == "radar"
    md = card_svg(c, "light", "md")
    assert LEAF.search(md)
    assert md.count("<circle") == n


def test_the_role_outline_and_legend_only_on_a_medium_card_with_a_role() -> None:
    dashed = 'stroke-dasharray="10 8"'
    assert dashed in card_svg(content(), compare=True)
    assert "Senior Frontend Engineer expects" in " ".join(texts(card_svg(content(), compare=True)))
    assert dashed not in card_svg(content())
    assert dashed not in card_svg(content(), size="sm", compare=True)
    assert dashed not in card_svg(content(role=None), compare=True)
    assert dashed not in og_svg(content())


def test_the_small_card_lists_the_three_strongest_areas() -> None:
    c = content(6)
    shown = " ".join(texts(card_svg(c, size="sm")))
    strongest = sorted(c.areas, key=lambda a: -a.score)[:3]
    assert all(a.name in shown for a in strongest)
    assert sum(a.name in shown for a in c.areas) == 3


def test_the_linkedin_picture_names_the_strongest_area_and_a_tie_goes_first() -> None:
    c = CardContent(
        areas=(
            DrawnArea("Backend", "Backend", 70),
            DrawnArea("Frontend", "Frontend", 85),
            DrawnArea("Data", "Data", 85),
        ),
        skills=(),
        role_title=None,
        updated=date(2026, 10, 22),
    )
    assert "Strongest in Frontend" in " ".join(texts(og_svg(c)))
    root = parse(og_svg(c))
    assert (root.get("width"), root.get("height")) == ("1200", "627")


def test_the_card_sizes() -> None:
    for size, (w, h) in (("md", ("480", "330")), ("sm", ("340", "190"))):
        root = parse(card_svg(content(), size=size))  # type: ignore[arg-type]
        assert (root.get("width"), root.get("height"), root.get("viewBox")) == (w, h, f"0 0 {w} {h}")


def test_the_neutral_card_says_nothing_about_whose_it_was() -> None:
    stopped = " ".join(texts(notice_svg("stopped")))
    assert "This card isn't shared any more." in stopped
    assert "The card can't be drawn right now." in " ".join(texts(notice_svg("error")))


# ───────────────────────── clipping by width ─────────────────────────


def test_a_long_name_is_cut_with_an_ellipsis_within_its_room() -> None:
    name = "Design Systems & Motion for Large Organisations"
    cut = clip(name, 12, 120)
    assert cut.endswith(ELLIPSIS) and len(cut) < len(name)
    assert measure(cut, 12) <= 120
    assert clip("Short", 12, 120) == "Short"
    assert clip("Anything", 12, 2) == ""


def test_wide_characters_count_wider() -> None:
    assert measure("漢字", 10) > measure("ab", 10)
    assert measure("W", 10) > measure("i", 10)
    assert measure("abc", 10, bold=True) > measure("abc", 10)


def test_wrap_breaks_at_spaces_and_clips_the_last_line() -> None:
    lines = wrap("Strongest in Design Systems and Motion at a Very Large Company", 48, 516, 2)
    assert len(lines) == 2 and lines[1].endswith(ELLIPSIS)
    assert all(measure(line, 48) <= 516 for line in lines)
    assert wrap("Strongest in Data", 48, 516, 2) == ["Strongest in Data"]
