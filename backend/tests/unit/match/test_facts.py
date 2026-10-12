"""The fact book: quotes re-checked against source lines, entries split into bullets, skills with evidence tiers."""

from __future__ import annotations

from typing import Any

from tayyari.contract.models import Profile
from tayyari.contract.portfolio import Fact as PortfolioFact
from tayyari.contract.portfolio import SourceRef
from tayyari.contract.stored import StoredContext, StoredSource
from tayyari.match.facts import build_facts, format_period, locate_quote, user_fact
from tayyari.match.text import fold_text
from tayyari.portfolio.facts import usable_sources
from tests.unit.match.support import meera, riya


def _profile(skills: list[dict[str, Any]] | None = None) -> Profile:
    base = meera().profile.to_dict()
    base["skills"] = skills if skills is not None else []
    return Profile.from_dict(base)


def _ctx(resume: str, **others: tuple[str, str]) -> StoredContext:
    sources = [StoredSource(id="resume", label="Resume", kind="resume", content=resume)]
    sources += [
        StoredSource(id=sid, label=sid, kind=kind, content=body) for sid, (kind, body) in others.items()
    ]
    return StoredContext(sources=sources)


def _pf(**kw: Any) -> PortfolioFact:
    fields: dict[str, Any] = {"highlights": [], "tags": [], "sourceRefs": []}
    fields.update(kw)
    return PortfolioFact(**fields)


def test_every_quote_of_every_fact_is_a_passage_of_the_lines_it_names() -> None:
    for persona in (meera(), riya()):
        book = persona.book()
        sources = {s.id: s for s in usable_sources(persona.context)}
        assert book.facts
        for fact in book.facts:
            assert fact.refs, fact.id
            for ref in fact.refs:
                if ref.source == "user":
                    continue
                assert ref.lines is not None, fact.id
                first, last = ref.lines
                window = " ".join(fold_text(line) for line in sources[ref.docId].lines[first - 1 : last])
                assert fold_text(ref.quote) in window, (fact.id, ref.quote)


def test_a_fabricated_quote_is_dropped_and_recorded() -> None:
    ctx = _ctx("EXPERIENCE\n\nAcme — Marketing Manager    Pune · Jan 2021 – Present\n- Ran the newsletter.")
    real = _pf(
        id="exp-1",
        kind="experience",
        title="Marketing Manager",
        org="Acme",
        sourceRefs=[SourceRef(sourceId="resume", quote="Acme — Marketing Manager")],
        highlights=["Ran the newsletter.", "Tripled revenue single-handedly."],
    )
    invented = _pf(
        id="exp-2",
        kind="experience",
        title="CEO",
        org="Globex",
        sourceRefs=[SourceRef(sourceId="resume", quote="Globex — CEO of everything")],
    )
    book = build_facts(_profile(), ctx, [real, invented])
    ids = {f.id for f in book.facts}
    assert "exp-1" in ids
    assert "exp-1.1" in ids  # the real bullet stays
    assert "exp-1.2" not in ids  # a bullet that is no passage of any source is dropped
    assert "exp-2" not in ids  # an entry whose only quote is invented is dropped, with its bullets
    assert any("exp-2" in note and "no quote left" in note for note in book.dropped)
    assert any("exp-1.2" in note for note in book.dropped)


def test_quotes_are_compared_with_quote_marks_dashes_and_wrapping_folded() -> None:
    ctx = _ctx("SUMMARY\nI said “ship it” – and\n  we did, twice.")
    src = usable_sources(ctx)[0]
    assert locate_quote(src, 'I said "ship it" - and we did, twice.') == (2, 3)
    assert locate_quote(src, "we did, twice") == (3, 3)
    assert locate_quote(src, "nothing like this") is None


def test_a_wrapped_bullet_carries_both_of_its_lines() -> None:
    book = riya().book()
    wrapped = next(f for f in book.facts if f.id == "exp-1.1")
    assert wrapped.refs[0].lines == [14, 15]
    assert wrapped.kind == "accomplishment"
    assert wrapped.evidenceTier == "used"


def test_entries_become_an_entry_fact_plus_one_fact_per_bullet() -> None:
    book = meera().book()
    entry = book.entry("exp-1")
    assert entry is not None
    assert entry.bulletIds == ["exp-1.1", "exp-1.2", "exp-1.3", "exp-1.4", "exp-1.5"]
    assert entry.dates == "Mar 2023 - Present"
    assert entry.recency == 999_999
    job = book.get("exp-1")
    assert job is not None
    assert job.kind == "experience"
    degree = book.entry("edu-1")
    assert degree is not None
    assert degree.dates == "2015 - 2018"
    assert book.contact == ["meera.nair.growth@example.com", "+91 90000 45678"]
    assert book.name == "Meera Nair"


def test_dates_are_ascii_ranges_with_both_ends_or_one_date() -> None:
    assert format_period("2021-01", None, True) == "Jan 2021 - Present"
    assert format_period("2024-01", "2024-04", None) == "Jan 2024 - Apr 2024"
    assert format_period("2022", "2023", None) == "2022 - 2023"
    assert format_period("2022-05", "2022-05", None) == "May 2022"
    assert format_period("2020", None, None) == "2020"
    assert format_period(None, None, None) is None
    assert format_period(None, "2023-02", None) == "Feb 2023"


def test_skills_are_facts_only_when_a_source_line_shows_them() -> None:
    book = meera().book()
    names = {f.text for f in book.facts if f.kind == "skill"}
    assert {"Meta Ads", "CleverTap", "A/B testing", "Looker Studio", "WhatsApp marketing"} <= names
    # the graph's gap skills (score under 45) never become facts, even where a source mentions the word
    assert not names & {"Customer data platforms", "Marketing mix modelling", "Shopify Plus", "SQL"}


def test_a_gap_skill_is_not_a_fact_even_if_the_resume_mentions_it() -> None:
    profile = _profile(
        [
            {
                "id": "k8s",
                "name": "Kubernetes",
                "domainId": "paid",
                "score": 30,
                "evidence": [{"sourceId": "resume", "text": "Read about Kubernetes"}],
                "unlocks": [],
            }
        ]
    )
    ctx = _ctx(
        "EXPERIENCE\n\nAcme — Engineer    Pune · Jan 2021 – Present\n- Read about Kubernetes at lunch."
    )
    assert not [f for f in build_facts(profile, ctx, []).facts if f.kind == "skill"]


def test_evidence_tiers_artifact_built_used_listed() -> None:
    by = riya().book().by_id()
    assert by["proj-1"].evidenceTier == "artifact"  # a GitHub source backs the project
    assert by["skill-llm-api-integration"].evidenceTier == "artifact"  # a Lovable app backs the skill
    assert by["skill-ci-cd-tooling"].evidenceTier == "listed"  # only in a skills line of the resume
    assert by["skill-figma-to-code"].evidenceTier == "used"  # in a bullet
    assert by["exp-1.1"].evidenceTier == "used"

    resume = (
        "PROJECTS\n- Pricing model — A spreadsheet model for subscription pricing.\n"
        "  Cut churn by 12% in a pilot.\n- Poster — A hand-drawn poster."
    )
    ctx = _ctx(resume)
    outcome = _pf(
        id="proj-1",
        kind="project",
        title="Pricing model",
        sourceRefs=[
            SourceRef(
                sourceId="resume", quote="Pricing model — A spreadsheet model for subscription pricing."
            )
        ],
        highlights=["Cut churn by 12% in a pilot."],
    )
    plain = _pf(
        id="proj-2",
        kind="project",
        title="Poster",
        sourceRefs=[SourceRef(sourceId="resume", quote="Poster — A hand-drawn poster.")],
    )
    got = build_facts(_profile(), ctx, [outcome, plain]).by_id()
    assert got["proj-1"].evidenceTier == "built"  # a project with a stated outcome
    assert got["proj-1.1"].evidenceTier == "built"
    assert got["proj-2"].evidenceTier == "used"


def test_a_skill_that_only_a_public_page_shows_has_no_resume_source() -> None:
    looker = meera().book().by_id()["skill-looker-studio"]
    assert {r.source for r in looker.refs} == {"link"}
    assert looker.evidenceTier == "used"


def test_user_facts_carry_the_persons_own_words_and_a_user_source() -> None:
    fact = user_fact("I am fluent in English, Hindi and Marathi.", key="q-r6", kind="skill")
    assert fact.refs[0].source == "user"
    assert fact.refs[0].lines is None
    assert fact.id.startswith("user-")
    assert user_fact("I am fluent in English, Hindi and Marathi.", key="q-r6", kind="skill").id == fact.id
    book = meera().book(user_facts=(fact,))
    assert book.get(fact.id) == fact


def test_a_user_fact_that_cites_a_document_is_refused() -> None:
    forged = user_fact("I led a team of 40.", key="q-1")
    forged.refs[0].source = "resume"
    book = meera().book(user_facts=(forged,))
    assert book.get(forged.id) is None
    assert any("user fact" in note for note in book.dropped)


def test_notes_are_the_persons_own_words() -> None:
    ctx = _ctx(
        "EXPERIENCE\n\nAcme — Engineer    Pune · Jan 2021 – Present\n- Wrote tests.",
        notes=("notes", "I can relocate to Mumbai."),
    )
    profile = _profile(
        [
            {
                "id": "relocation",
                "name": "Relocate to Mumbai",
                "domainId": "paid",
                "score": 60,
                "evidence": [{"sourceId": "notes", "text": "I can relocate"}],
                "unlocks": [],
            }
        ]
    )
    book = build_facts(profile, ctx, [])
    relocation = next(f for f in book.facts if f.text == "Relocate to Mumbai")
    assert relocation.refs[0].source == "user"


def test_the_fact_book_is_plain_data_that_round_trips_through_json() -> None:
    from tayyari.match.models import FactBook

    for persona in (meera(), riya()):
        book = persona.book()
        again = FactBook.model_validate_json(book.model_dump_json())
        assert again == book
        assert again.entry("exp-1") is not None
