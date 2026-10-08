"""The public DTO never leaks: private contact facts, the grade, hidden blocks, hidden domains, weak skills, ids."""

from __future__ import annotations

import json

import pytest

from tayyari.contract.portfolio import (
    GRAPH_PUBLIC_MIN_SKILL,
    CopySlot,
    Portfolio,
    PracticeStats,
    PublicPortfolio,
)
from tayyari.portfolio.public import to_public
from tests.unit.portfolio._fixtures_b import ALL_FIXTURES, PERSONAS, fact, make_portfolio

EMAIL = "riya.kapoor.dev@example.com"
PHONE = "+91 98765 43210"
HEADER = f"{EMAIL} · {PHONE} · github.com/riyakapoor · riyakapoor.design"


def slot(slot_id: str, text: str, fact_ids: list[str], origin: str = "model") -> CopySlot:
    return CopySlot(id=slot_id, text=text, origin=origin, factIds=fact_ids)  # type: ignore[arg-type]


SLOTS = [
    slot("tagline", "TAGLINE-TEXT springs not durations", ["quote-1"]),
    slot("aboutLede", "ABOUT-LEDE-TEXT frontend engineer at Kitebird", ["exp-1"]),
    slot("aboutBody", "ABOUT-BODY-TEXT before that Saffron Ledger", ["exp-3"]),
    slot("exp:exp-1", "EXP1-TEXT rebuilt checkout\nowns Kite UI", ["exp-1"]),
    slot("exp:exp-3", "EXP3-TEXT table performance", ["exp-3"]),
    slot("proj:proj-1", "PROJ1-TEXT spring physics", ["proj-1"]),
    slot("proj:proj-2", "PROJ2-TEXT checkout experiments", ["proj-2"]),
    slot("pressHeadline", "PRESS-HEADLINE-TEXT Kitebird Engineer Rebuilds Checkout", ["exp-1"]),
    slot("pressDeck", "PRESS-DECK-TEXT deck", ["exp-1"]),
    slot("exp:gone-9", "ORPHAN-TEXT slot of a fact that does not exist", ["gone-9"]),
    slot("mystery", "MYSTERY-TEXT not a slot id", []),
]


def rich() -> Portfolio:
    """Riya's first look with private details planted everywhere a leak could hide."""
    p = make_portfolio("riya", slots=SLOTS)
    facts = list(p.content.facts)
    facts[[f.id for f in facts].index("edu-1")] = fact(
        "edu-1",
        "education",
        "B.Tech, Computer Science",
        org="Nandi Institute of Technology",
        start="2020",
        end="2024",
        grade="CGPA 8.6 / 10",
        detail="CGPA 8.6 / 10. Final-year project: a collaborative whiteboard.",
        highlights=["Graduated with CGPA 8.6 / 10"],
    )
    facts += [
        fact("contact-2", "contact", PHONE, quote=PHONE),
        fact("link-3", "link", "Write to me", url=f"mailto:{EMAIL}", quote=EMAIL),
        fact("link-4", "link", "Elsewhere", url="https://example.org/riya", quote=HEADER),
        fact("contact-3", "contact", "Based in Bengaluru", quote="Based in Bengaluru"),
    ]
    p.content = p.content.model_copy(update={"facts": facts})
    return p


def dump(p: Portfolio) -> str:
    return to_public(p).model_dump_json()


def set_block(p: Portfolio, kind: str, *, visible: bool = True, public: bool = True) -> Portfolio:
    out = p.model_copy(deep=True)
    for b in out.spec.blocks:
        if b.kind == kind:
            b.visible, b.public = visible, public
    return out


def with_privacy(p: Portfolio, **flags: bool) -> Portfolio:
    out = p.model_copy(deep=True)
    for name, value in flags.items():
        setattr(out.spec.privacy, name, value)
    return out


# ───────────────────────── nothing private by default ─────────────────────────


def test_default_privacy_leaks_nothing_private():
    p = rich()
    text = dump(p)
    for secret in (
        EMAIL,
        "kapoor.dev",
        "98765",
        PHONE,
        "CGPA",
        "8.6 / 10",
        "mailto:",
        p.profileId,
        "profileId",
    ):
        assert secret not in text, secret
    pub = to_public(p)
    assert pub.publicId == p.publicId
    assert not any(f.kind == "contact" for f in pub.content.facts)
    assert not any(f.url and f.url.startswith("mailto:") for f in pub.content.facts)
    edu = next(f for f in pub.content.facts if f.kind == "education")
    assert edu.grade is None and edu.detail == "Final-year project: a collaborative whiteboard."
    assert edu.highlights == ["Graduated with"]


def test_source_quotes_never_reach_the_page():
    pub = to_public(rich())
    assert pub.content.facts
    assert all(f.sourceRefs == [] for f in pub.content.facts)
    # the "Elsewhere" link is public, but the header line it was quoted from is not
    elsewhere = next(f for f in pub.content.facts if f.id == "link-4")
    assert elsewhere.url == "https://example.org/riya"
    assert HEADER not in json.dumps(pub.model_dump())


def test_the_public_dto_has_only_the_viewer_fields():
    data = to_public(rich()).model_dump()
    assert set(data) == {"publicId", "kitVersion", "spec", "content", "graph", "publishedAt", "defaultView"}
    assert data["spec"]["reasons"] == []
    assert data["spec"]["graph"]["hiddenDomainIds"] == []
    assert PublicPortfolio.model_validate(data) == to_public(rich())


def test_the_input_is_left_untouched():
    p = rich()
    before = p.model_dump()
    to_public(p)
    assert p.model_dump() == before


# ───────────────────────── privacy switches ─────────────────────────


def test_email_switch_shows_the_email_only():
    p = with_privacy(rich(), email=True)
    text = dump(p)
    assert EMAIL in text
    assert "98765" not in text
    pub = to_public(p)
    contacts = [f for f in pub.content.facts if f.kind == "contact"]
    assert [f.title for f in contacts] == ["Email"]
    assert any(f.url == f"mailto:{EMAIL}" for f in pub.content.facts)
    assert HEADER not in text


def test_phone_switch_shows_the_phone_only():
    p = with_privacy(rich(), phone=True)
    text = dump(p)
    assert PHONE in text
    assert EMAIL not in text and "mailto:" not in text
    assert [f.title for f in to_public(p).content.facts if f.kind == "contact"] == [PHONE]


def test_grades_switch_shows_the_grade():
    p = with_privacy(rich(), grades=True)
    edu = next(f for f in to_public(p).content.facts if f.kind == "education")
    assert edu.grade == "CGPA 8.6 / 10"
    assert "CGPA 8.6 / 10" in edu.detail  # type: ignore[operator]
    assert EMAIL not in dump(p)


def test_contact_facts_need_the_contact_block():
    p = set_block(with_privacy(rich(), email=True, phone=True), "contact", visible=False)
    text = dump(p)
    assert EMAIL not in text and PHONE not in text
    assert not any(f.kind in ("contact", "link") for f in to_public(p).content.facts)


def test_an_email_in_model_copy_is_scrubbed_but_the_owners_own_words_stay():
    slots = [
        slot("tagline", f"Reach me at {EMAIL} any time", ["quote-1"]),
        slot("aboutLede", f"Call {PHONE} or write to {EMAIL}.", ["exp-1"], origin="user"),
        slot("aboutBody", "Numbers like 10,000+ invoices and 2018-2023 stay.", ["exp-3"]),
    ]
    p = make_portfolio("riya", slots=slots)
    pub = to_public(p)
    texts = {s.id: s.text for s in pub.content.slots}
    assert EMAIL not in texts["tagline"] and texts["tagline"] == "Reach me at any time"
    assert texts["aboutLede"] == f"Call {PHONE} or write to {EMAIL}."
    assert texts["aboutBody"] == "Numbers like 10,000+ invoices and 2018-2023 stay."
    shown = to_public(with_privacy(p, email=True)).content.slots
    assert EMAIL in next(s for s in shown if s.id == "tagline").text


# ───────────────────────── hidden blocks ─────────────────────────


def test_a_hidden_block_takes_its_facts_and_slots_with_it():
    p = set_block(rich(), "experience", visible=False)
    pub = to_public(p)
    text = pub.model_dump_json()
    assert not any(f.kind == "experience" for f in pub.content.facts)
    assert not any(s.id.startswith("exp:") for s in pub.content.slots)
    for gone in ("EXP1-TEXT", "EXP3-TEXT", "Frontend Engineering Intern", "Freelance"):
        assert gone not in text, gone
    assert all(b.kind != "experience" for b in pub.spec.blocks)
    # the About lede (a shown block) is still there; it is the owner's choice to show it
    assert "ABOUT-LEDE-TEXT" in text


def test_a_block_that_is_not_public_is_hidden_from_viewers():
    p = set_block(rich(), "projects", visible=True, public=False)
    pub = to_public(p)
    assert not any(f.kind == "project" for f in pub.content.facts)
    assert not any(s.id.startswith("proj:") for s in pub.content.slots)
    assert "PROJ1-TEXT" not in pub.model_dump_json()


def test_about_slots_and_the_press_lead_follow_the_about_block():
    p = set_block(rich(), "about", visible=False)
    ids = {s.id for s in to_public(p).content.slots}
    assert ids.isdisjoint({"aboutLede", "aboutBody", "pressHeadline", "pressDeck"})
    assert "tagline" in ids
    shown = {s.id for s in to_public(rich()).content.slots}
    assert shown == {
        "tagline",
        "aboutLede",
        "aboutBody",
        "exp:exp-1",
        "exp:exp-3",
        "proj:proj-1",
        "proj:proj-2",
        "pressHeadline",
        "pressDeck",
    }


def test_slots_for_unknown_ids_or_missing_facts_never_ship():
    text = dump(rich())
    assert "ORPHAN-TEXT" not in text and "MYSTERY-TEXT" not in text


def test_slot_fact_ids_point_only_at_facts_that_ship():
    p = set_block(rich(), "experience", visible=False)
    pub = to_public(p)
    shipped = {f.id for f in pub.content.facts}
    for s in pub.content.slots:
        assert set(s.factIds) <= shipped, s.id


def test_hero_less_pages_drop_the_tagline_and_quotes():
    p = set_block(set_block(rich(), "hero", visible=False), "about", visible=False)
    pub = to_public(p)
    assert not any(f.kind == "quote" for f in pub.content.facts)
    assert not any(s.id == "tagline" for s in pub.content.slots)


def test_stats_follow_the_proof_block_and_the_graph_stats_switch():
    p = rich()
    assert to_public(p).content.stats
    no_proof = set_block(p, "proof", visible=False)
    assert to_public(no_proof).content.stats  # the graph still shows its stats strip
    no_strip = no_proof.model_copy(deep=True)
    no_strip.spec.graph.stats = False
    assert to_public(no_strip).content.stats == []
    assert to_public(no_strip).spec.graph.stats is False


def test_practice_stats_ship_only_beside_a_shown_graph_strip():
    p = rich()
    p.content = p.content.model_copy(
        update={
            "practice": PracticeStats(sessions=3, mockInterviews=1, domainsPractised=2, asOf="2026-10-08")
        }
    )
    assert to_public(p).content.practice is not None
    off = p.model_copy(deep=True)
    off.spec.graph.stats = False
    assert to_public(off).content.practice is None
    assert to_public(set_block(p, "graph", visible=False)).content.practice is None


# ───────────────────────── open to ─────────────────────────


def test_open_to_titles_ship_only_when_the_block_is_shown():
    p = rich()
    titles = p.content.openTo
    assert titles
    hidden = dump(p)  # the composer leaves the block off
    assert all(t not in hidden for t in titles)
    assert to_public(p).content.openTo == []
    on = to_public(set_block(p, "openTo"))
    assert on.content.openTo == titles
    assert any(b.kind == "openTo" for b in on.spec.blocks)
    off = set_block(p, "openTo", visible=True, public=False)
    assert to_public(off).content.openTo == []


# ───────────────────────── the graph ─────────────────────────


def test_hidden_domains_vanish_everywhere():
    p = rich()
    p.spec.graph.hiddenDomainIds = ["ai-llm", "backend-apis"]
    pub = to_public(p)
    text = pub.model_dump_json()
    hidden = [d for d in p.graph.domains if d.id in ("ai-llm", "backend-apis")]
    assert len(hidden) == 2
    for d in hidden:
        assert d.id not in text and d.name not in text, d.id
        for s in d.skills:
            assert s.name not in text, s.name
    assert [d.id for d in pub.graph.domains] == [
        d.id for d in p.graph.domains if d.id not in ("ai-llm", "backend-apis")
    ]
    assert pub.spec.graph.hiddenDomainIds == []
    assert p.spec.graph.hiddenDomainIds == ["ai-llm", "backend-apis"]  # the owner's copy is unchanged


def test_skills_under_the_public_minimum_are_never_listed():
    p = make_portfolio("sana", slots=[])
    low = [s for d in p.graph.domains for s in d.skills if s.score < GRAPH_PUBLIC_MIN_SKILL]
    assert low
    pub = to_public(p)
    graph_json = pub.graph.model_dump_json()
    for s in low:
        assert f'"name":"{s.name}"' not in graph_json
    assert all(s.score >= GRAPH_PUBLIC_MIN_SKILL for d in pub.graph.domains for s in d.skills)
    # domain scores are always shown as they are, even the low ones, and the mastery stays
    assert [(d.name, d.score) for d in pub.graph.domains] == [(d.name, d.score) for d in p.graph.domains]
    assert pub.graph.mastery == p.graph.mastery
    assert pub.graph.skillCount == p.graph.skillCount


def test_a_hidden_graph_block_sends_no_graph_data():
    pub = to_public(set_block(rich(), "graph", visible=False))
    assert pub.graph.domains == [] and pub.graph.mastery is None
    assert pub.graph.asOf and pub.graph.sources


# ───────────────────────── every fixture ─────────────────────────


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_fixture_ever_leaks_its_profile_id_or_hidden_things(name):
    p = make_portfolio(name)
    p.spec.graph.hiddenDomainIds = [p.graph.domains[0].id]
    hidden_name = p.graph.domains[0].name
    pub = to_public(p)
    text = pub.model_dump_json()
    assert p.profileId not in text
    graph_json = pub.graph.model_dump_json()
    assert f'"id":"{p.graph.domains[0].id}"' not in graph_json and f'"name":"{hidden_name}"' not in graph_json
    assert pub.spec.graph.hiddenDomainIds == []
    assert all(f.sourceRefs == [] for f in pub.content.facts)
    assert not any(f.kind == "contact" for f in pub.content.facts)
    assert not any(f.grade for f in pub.content.facts)
    assert all(s.score >= GRAPH_PUBLIC_MIN_SKILL for d in pub.graph.domains for s in d.skills)
    assert [b.kind for b in pub.spec.blocks] == [b.kind for b in p.spec.blocks if b.visible and b.public]


def test_every_persona_with_grades_private_hides_the_grade_text():
    for name in PERSONAS:
        p = make_portfolio(name)
        grades = [f.grade for f in p.content.facts if f.grade]
        text = dump(p)
        assert all(g not in text for g in grades), name
        shown = dump(with_privacy(p, grades=True))
        assert all(g in shown for g in grades), name
