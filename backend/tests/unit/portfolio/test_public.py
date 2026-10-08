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
    # the count is the number of skills actually sent, not the size of the owner's graph
    assert pub.graph.skillCount == sum(len(d.skills) for d in pub.graph.domains) < p.graph.skillCount


def test_a_hidden_graph_block_sends_no_graph_data():
    for hidden in (set_block(rich(), "graph", visible=False), set_block(rich(), "graph", public=False)):
        pub = to_public(hidden)
        assert pub.graph.domains == [] and pub.graph.mastery is None
        assert pub.graph.skillCount == 0
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


# ───────────────────────── skill evidence never ships ─────────────────────────


def test_skill_evidence_lines_never_reach_a_viewer():
    p = rich()
    evidence = [s.evidence for d in p.graph.domains for s in d.skills if s.evidence]
    assert evidence, "the fixture's graph has evidence lines to leak"
    pub = to_public(p)
    text = pub.model_dump_json()
    assert all(s.evidence is None for d in pub.graph.domains for s in d.skills)
    assert all(line not in text for line in evidence)
    assert [(s.id, s.name, s.score) for d in pub.graph.domains for s in d.skills] == [
        (s.id, s.name, s.score)
        for d in p.graph.domains
        for s in d.skills
        if s.score >= GRAPH_PUBLIC_MIN_SKILL
    ]
    assert any(s.evidence for d in p.graph.domains for s in d.skills)  # the owner's copy keeps them


def test_the_skill_count_is_the_number_of_skills_sent():
    p = rich()
    p.spec.graph.hiddenDomainIds = ["ai-llm"]
    pub = to_public(p)
    sent = sum(len(d.skills) for d in pub.graph.domains)
    assert pub.graph.skillCount == sent
    assert sent < p.graph.skillCount


# ───────────────────────── grades, however they are written ─────────────────────────


def put_slot(p: Portfolio, new: CopySlot) -> None:
    """Add ``new`` to the portfolio's copy, replacing the slot of the same id."""
    p.content.slots = [s for s in p.content.slots if s.id != new.id] + [new]


def with_edu(**changes) -> Portfolio:
    p = rich()
    facts = list(p.content.facts)
    i = [f.id for f in facts].index("edu-1")
    facts[i] = facts[i].model_copy(update=changes)
    p.content = p.content.model_copy(update={"facts": facts})
    return p


@pytest.mark.parametrize(
    "written",
    [
        "Graduated with 8.6 / 10",
        "Graduated with 8.6/10",
        "Graduated with 8.60 / 10",
        "Graduated with CGPA: 8.6/10",
        "Graduated with a cgpa of 8.6 out of 10",
        "Graduated with GPA 8.6",
        "Graduated with 8.6 CGPA",
        "Graduated with (8.6/10)",
        "Graduated with 8.6",
    ],
)
def test_a_hidden_grade_is_stripped_however_it_is_written(written: str):
    p = with_edu(
        grade="CGPA 8.6 / 10",
        title="B.Tech, Computer Science",
        detail=written,
        highlights=[written, "Scored 8.6 / 10 overall"],
        tags=["GPA 8.6"],
        org="Nandi Institute",
    )
    put_slot(p, slot("aboutBody", f"{written}. Then Saffron Ledger.", ["edu-1"]))
    put_slot(p, slot("aboutLede", f"{written}.", ["edu-1"], origin="user"))
    text = dump(p)
    for needle in ("8.6", "8.60", "CGPA", "cgpa", "GPA", "/ 10", "/10", "out of 10"):
        assert needle not in text, (written, needle)
    edu = next(f for f in to_public(p).content.facts if f.kind == "education")
    assert edu.grade is None
    assert edu.detail.startswith("Graduated with") and edu.tags == []
    body = next(s for s in to_public(p).content.slots if s.id == "aboutBody")
    assert body.text.endswith("Then Saffron Ledger.")


def test_the_grade_words_go_from_every_fact_and_slot_even_where_the_score_is_not_the_grade():
    other = fact(
        "proj-9",
        "project",
        "Grading tool",
        detail="Computes CGPA and GPA for a class.",
        highlights=["GPA calculator for 120 students"],
    )
    p = rich()
    p.content = p.content.model_copy(update={"facts": [*p.content.facts, other]})
    put_slot(p, slot("proj:proj-9", "A CGPA calculator for 120 students", ["proj-9"]))
    pub = to_public(p)
    shown = next(f for f in pub.content.facts if f.id == "proj-9")
    assert "GPA" not in shown.model_dump_json().upper()
    assert "120 students" in " ".join(shown.highlights)
    assert not any("GPA" in s.text.upper() for s in pub.content.slots)


def test_a_score_followed_by_a_unit_is_another_quantity_and_stays():
    p = with_edu(grade="8.6", detail="Latency fell from 8.6s to 2s; the 8.6M rows and 18.6 users stay")
    edu = next(f for f in to_public(p).content.facts if f.kind == "education")
    assert edu.detail == "Latency fell from 8.6s to 2s; the 8.6M rows and 18.6 users stay"
    p2 = with_edu(grade="8.6", detail="Finished with 8.6 overall")
    assert (
        next(f for f in to_public(p2).content.facts if f.kind == "education").detail
        == "Finished with overall"
    )


def test_a_short_grade_is_removed_only_from_its_own_fact():
    p = with_edu(grade="A", detail="Grade: A", highlights=["Top of the class: A"])
    p.content = p.content.model_copy(
        update={"facts": [*p.content.facts, fact("proj-9", "project", "A/B test harness", detail="A tool")]}
    )
    pub = to_public(p)
    edu = next(f for f in pub.content.facts if f.kind == "education")
    assert edu.detail == "Grade" and edu.highlights == ["Top of the class"]
    proj = next(f for f in pub.content.facts if f.id == "proj-9")
    assert (proj.title, proj.detail) == ("A/B test harness", "A tool")


def test_the_grade_shows_everywhere_once_the_switch_is_on():
    p = with_privacy(
        with_edu(grade="CGPA 8.6 / 10", detail="CGPA 8.6 / 10. Final-year project."), grades=True
    )
    put_slot(p, slot("aboutBody", "Graduated with an 8.6 average", ["edu-1"]))
    pub = to_public(p)
    edu = next(f for f in pub.content.facts if f.kind == "education")
    assert edu.grade == "CGPA 8.6 / 10" and edu.detail.startswith("CGPA 8.6 / 10")
    assert next(s for s in pub.content.slots if s.id == "aboutBody").text == "Graduated with an 8.6 average"


# ───────────────────────── contact details inside links ─────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://wa.me/919876543210",
        "wa.me/919876543210",
        "https://api.whatsapp.com/send?phone=919876543210",
        "https://api.whatsapp.com/send/?text=hi",
        "https://www.whatsapp.com/send?phone=1",
        "https://t.me/riya_k",
        "https://telegram.me/riya_k",
        "https://signal.me/#p/+919876543210",
        "tel:+919876543210",
        "TEL:+919876543210",
        "sms:+919876543210?body=hi",
        "viber://chat?number=%2B919876543210",
        "https://example.org/call/+91-98765-43210",
        "https://example.org/contact?phone=9876543210",
        "https://example.org/contact?phone=%2B91%2098765%2043210",
    ],
)
def test_phone_and_messenger_links_follow_the_phone_switch(url: str):
    p = rich()
    p.content = p.content.model_copy(
        update={
            "facts": [*p.content.facts, fact("link-9", "link", "Message me", url=url, quote="Message me")]
        }
    )
    assert not any(f.id == "link-9" for f in to_public(p).content.facts)
    assert "Message me" not in dump(p)
    shown = to_public(with_privacy(p, phone=True)).content.facts
    assert any(f.id == "link-9" and f.url == url for f in shown)
    # and the e-mail switch does not unlock it
    assert not any(f.id == "link-9" for f in to_public(with_privacy(p, email=True)).content.facts)


@pytest.mark.parametrize(
    "url",
    [
        "mailto:riya.kapoor.dev@example.com",
        "https://calendly.com/riya/intro?email=riya.kapoor.dev@example.com",
        "https://calendly.com/riya/intro?email=riya.kapoor.dev%40example.com&name=Riya",
        "https://forms.example.org/hire?contact=riya.kapoor.dev%40example.com",
    ],
)
def test_links_that_carry_an_email_follow_the_email_switch(url: str):
    p = rich()
    p.content = p.content.model_copy(
        update={
            "facts": [*p.content.facts, fact("link-9", "link", "Book a call", url=url, quote="Book a call")]
        }
    )
    text = dump(p)
    assert "Book a call" not in text and "kapoor.dev" not in text
    assert any(f.id == "link-9" for f in to_public(with_privacy(p, email=True)).content.facts)
    assert not any(f.id == "link-9" for f in to_public(with_privacy(p, phone=True)).content.facts)


def test_ordinary_links_and_long_ids_in_links_stay():
    p = rich()
    links = [
        fact("link-5", "link", "GitHub", url="https://github.com/riyakapoor", quote="GitHub"),
        fact(
            "link-6",
            "link",
            "LinkedIn",
            url="https://www.linkedin.com/in/riya-kapoor-1a2b3c4d5",
            quote="LinkedIn",
        ),
        fact("link-7", "link", "A post", url="https://x.com/riya/status/1234567890123456789", quote="A post"),
        fact("link-8", "link", "Telegram docs", url="https://core.telegram.org/api", quote="Telegram docs"),
    ]
    p.content = p.content.model_copy(update={"facts": [*p.content.facts, *links]})
    shown = {f.id for f in to_public(p).content.facts}
    assert {"link-5", "link-6", "link-7", "link-8"} <= shown


def test_a_project_link_that_hides_a_phone_number_loses_its_link_but_not_the_project():
    p = rich()
    facts = list(p.content.facts)
    i = [f.id for f in facts].index("proj-1")
    facts[i] = facts[i].model_copy(update={"url": "https://example.org/demo?call=%2B919876543210"})
    p.content = p.content.model_copy(update={"facts": facts})
    proj = next(f for f in to_public(p).content.facts if f.id == "proj-1")
    assert proj.url is None
    assert next(f for f in to_public(with_privacy(p, phone=True)).content.facts if f.id == "proj-1").url


# ───────────────────────── the PII scrubber over every string ─────────────────────────

ADDRESS = "Address: 12 Baker Street, Flat 4, Bengaluru 560001"


def test_no_email_phone_or_address_line_reaches_a_viewer_in_any_string():
    p = rich()
    facts = list(p.content.facts)
    i = [f.id for f in facts].index("exp-1")
    facts[i] = facts[i].model_copy(
        update={
            "org": f"Kitebird ({EMAIL})",
            "location": "Bengaluru",
            "detail": f"D2C commerce. Reach me at {PHONE}.\n{ADDRESS}",
            "highlights": [ADDRESS, f"Call {PHONE}", "Rebuilt checkout", "Phone 98765 4321"],
            "tags": ["React", EMAIL],
        }
    )
    p.content = p.content.model_copy(update={"facts": facts})
    put_slot(
        p, slot("aboutBody", f"Based at 12 Baker Street, Bengaluru.\nMail {EMAIL} or {PHONE}.", ["exp-1"])
    )
    p.content.person.headline = f"Frontend Engineer · {PHONE}"
    text = dump(p)
    leaks = (
        EMAIL,
        "kapoor.dev",
        "98765",
        "Baker Street",
        "560001",
        "Address:",
        "[email]",
        "[phone]",
        "[address]",
    )
    for leak in leaks:
        assert leak not in text, leak
    pub = to_public(p)
    exp = next(f for f in pub.content.facts if f.id == "exp-1")
    assert exp.highlights == ["Rebuilt checkout"]
    assert exp.detail == "D2C commerce. Reach me at."
    assert exp.tags == ["React"]
    assert exp.org == "Kitebird"
    assert next(s for s in pub.content.slots if s.id == "aboutBody").text == "Mail or."
    assert pub.content.person.headline == "Frontend Engineer"


def test_a_shown_email_and_phone_survive_the_scrubber_but_an_address_never_does():
    p = with_privacy(rich(), email=True, phone=True)
    facts = list(p.content.facts)
    i = [f.id for f in facts].index("exp-1")
    facts[i] = facts[i].model_copy(update={"highlights": [f"Write to {EMAIL} or {PHONE}", ADDRESS]})
    p.content = p.content.model_copy(update={"facts": facts})
    exp = next(f for f in to_public(p).content.facts if f.id == "exp-1")
    assert exp.highlights == [f"Write to {EMAIL} or {PHONE}"]
    assert dump(p).count(EMAIL) >= 1 and "Baker Street" not in dump(p)


def test_a_contact_fact_that_is_really_an_address_is_dropped_even_with_the_switch_on():
    p = with_privacy(rich(), phone=True)
    p.content = p.content.model_copy(
        update={"facts": [*p.content.facts, fact("contact-9", "contact", ADDRESS, quote=ADDRESS)]}
    )
    assert not any(f.id == "contact-9" for f in to_public(p).content.facts)
    assert "Baker Street" not in dump(p)


def test_ordinary_numbers_dates_and_scores_survive_the_scrubber():
    p = rich()
    facts = list(p.content.facts)
    i = [f.id for f in facts].index("exp-1")
    kept = [
        "Raised conversion 9.4% across 10,000 orders",
        "Worked 2018 - 2020 - 2022",
        "Shipped on 2025-07-14",
        "Cut LCP from 4.1s to 1.9s",
    ]
    facts[i] = facts[i].model_copy(update={"highlights": kept})
    p.content = p.content.model_copy(update={"facts": facts})
    exp = next(f for f in to_public(p).content.facts if f.id == "exp-1")
    assert exp.highlights == kept
