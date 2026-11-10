"""The two arrangements: the uploaded resume from the golden fact books (source order, nothing invented), and a resume
for a next role with the scripted model and with the lexical fallback."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.llm.errors import ClaudeOverloaded
from tayyari.match.bullets import export_blockers
from tayyari.match.lint import failing
from tayyari.match.service import MatchService
from tayyari.match.variant import BULLETS_OLDER, BULLETS_RECENT, MAX_SKILLS, lexical_relevance
from tayyari.match.verify import exact_wording
from tayyari.resume.arrange import (
    block_ids,
    bullet_id,
    on_resume,
    path_variant,
    role_requirements,
    role_terms,
    score_role,
    upload_variant,
)
from tayyari.resume.marks import marks
from tests.fakes.claude import FakeClaudeClient
from tests.unit.match.support import Persona
from tests.unit.resume.support import (
    CTX,
    HAIKU,
    entries,
    line_ids,
    lines_of,
    meera,
    riya,
    role_of,
    skill_names,
)

# ───────────────────────── the uploaded resume ─────────────────────────


@pytest.mark.parametrize("persona", [meera(), riya()], ids=lambda p: p.name)
def test_every_line_of_the_uploaded_resume_is_a_resume_fact_in_its_own_words(persona: Persona) -> None:
    book = persona.book()
    facts = book.by_id()
    variant = upload_variant(book)

    assert lines_of(variant), "the fixture has resume bullets"
    for line in lines_of(variant):
        [fact_id] = line.factIds
        fact = facts[fact_id]
        assert line.id == bullet_id(fact_id)
        assert line.text == exact_wording(fact)  # the fact's words, not a rewrite
        assert (line.verdict, line.status, line.placeholders) == ("exact", "verified", [])
        assert on_resume(fact)
    assert export_blockers(variant) == []
    assert (variant.decisions, variant.changes, variant.lint) == ([], [], None)


@pytest.mark.parametrize("persona", [meera(), riya()], ids=lambda p: p.name)
def test_the_uploaded_resume_keeps_the_order_of_the_file(persona: Persona) -> None:
    book = persona.book()
    facts = book.by_id()
    variant = upload_variant(book)

    def first_line(fact_id: str) -> int:
        return min(ref.lines[0] for ref in facts[fact_id].refs if ref.source == "resume" and ref.lines)

    for section in (variant.experience, variant.projects):
        heads = [first_line(e.entryFactId) for e in section]
        assert heads == sorted(heads)
        for entry in section:
            rows = [first_line(line.factIds[0]) for line in entry.bullets]
            assert rows == sorted(rows)
    degrees = [first_line(e.factId) for e in variant.education]
    assert degrees == sorted(degrees)


def test_meeras_uploaded_resume_is_her_three_jobs_with_every_bullet() -> None:
    book = meera().book()
    variant = upload_variant(book)

    assert [e.entryFactId for e in variant.experience] == ["exp-1", "exp-2", "exp-3"]
    assert line_ids(variant, "exp-1") == [f"b-exp-1.{n}" for n in range(1, 6)]
    assert line_ids(variant, "exp-2") == ["b-exp-2.1", "b-exp-2.2", "b-exp-2.3"]
    assert line_ids(variant, "exp-3") == ["b-exp-3.1", "b-exp-3.2"]
    assert variant.projects == []
    assert [(e.factId, e.title, e.org, e.dates) for e in variant.education] == [
        ("edu-1", "B.Com", "Savitribai Phule Pune University", "2015 - 2018")
    ]
    # the heading of a job is the book's, never edited
    job = variant.experience[0]
    entry = book.entry("exp-1")
    assert entry is not None
    assert (job.title, job.org, job.dates) == (entry.title, entry.org, entry.dates)
    assert (variant.header.name, variant.header.headline, variant.header.contact) == (
        book.name,
        book.headline,
        book.contact,
    )


def test_no_summary_is_written() -> None:
    summary = upload_variant(meera().book()).summary
    assert (summary.id, summary.text, summary.factIds, summary.status) == ("summary", "", [], "dropped")


def test_a_project_only_github_shows_is_not_on_the_uploaded_resume() -> None:
    book = riya().book()
    variant = upload_variant(book)
    facts = book.by_id()

    assert not on_resume(facts["proj-5"])  # the fixture: notes-ai is a repository the resume does not list
    assert [e.entryFactId for e in variant.projects] == ["proj-1", "proj-2", "proj-3", "proj-4"]
    # a project the file lists in one line is written from that block itself
    assert line_ids(variant, "proj-4") == ["b-proj-4"]
    assert block_ids(book.entry("proj-4")) == ["proj-4"]  # type: ignore[arg-type]


def test_the_skills_are_evidenced_skill_facts_the_resume_shows_and_each_cites_its_fact() -> None:
    book = meera().book()
    facts = book.by_id()
    variant = upload_variant(book)

    [group] = variant.skills
    assert group.group == "Skills" and 0 < len(group.items) <= MAX_SKILLS
    for item in group.items:
        skill = facts[item.factIds[0]]
        assert skill.kind == "skill" and item.name == skill.text
        assert all(on_resume(facts[f]) or f == skill.id for f in item.factIds)
        assert (
            on_resume(skill) or len(item.factIds) == 2
        )  # shown by the file itself, or by a resume line it cites
    # the fixture: Looker Studio is on her public page only, so the uploaded resume does not list it
    assert not on_resume(facts["skill-looker-studio"])
    assert "Looker Studio" not in skill_names(variant)


def test_a_skill_another_source_evidences_is_listed_when_a_resume_line_names_it() -> None:
    book = riya().book()
    facts = book.by_id()
    variant = upload_variant(book)

    react = next(item for group in variant.skills for item in group.items if item.name == "React")
    assert not on_resume(facts["skill-react"])  # the fixture: its two references are GitHub lines
    skill_id, line_id = react.factIds
    assert skill_id == "skill-react" and on_resume(facts[line_id]) and "React" in facts[line_id].text


@pytest.mark.parametrize("persona", [meera(), riya()], ids=lambda p: p.name)
async def test_the_uploaded_resume_exports_and_reads_back_clean(persona: Persona) -> None:
    book = persona.book()
    exported = await MatchService(claude=FakeClaudeClient(), model=HAIKU).export_resume(
        upload_variant(book), book
    )
    assert exported.variant.lint is not None
    assert failing(exported.variant.lint) == []
    assert exported.files.pdf.startswith(b"%PDF") and exported.files.docx[:2] == b"PK"


# ───────────────────────── a resume for a next role ─────────────────────────


def test_the_requirement_table_is_built_from_the_roles_own_words() -> None:
    role = role_of(riya(), "design-engineer")
    table = role_requirements(role)

    assert [r.id for r in table] == ["r1", "r2", "r3", "r4", "r5"]
    assert [r.text for r in table] == [need.name for need in role.requirements] == role_terms(role)
    assert all(r.quote == r.text and r.terms == [r.text] and r.tier == "required" for r in table)


def _scores(rows: dict[str, tuple[int, list[str]]]) -> dict[str, Any]:
    return {"scores": [{"factId": f, "score": s, "requirementIds": r} for f, (s, r) in rows.items()]}


async def test_a_path_resume_is_ordered_by_the_models_relevance_in_one_call() -> None:
    persona = riya()
    book, role = persona.book(), role_of(persona, "design-engineer")
    claude = FakeClaudeClient()
    claude.script_structured(
        "match.relevance",
        _scores({"exp-1.6": (97, ["r1"]), "exp-1.4": (91, ["r2"]), "exp-2.6": (88, ["r1"])}),
    )

    relevance = await score_role(claude, model=HAIKU, book=book, role=role, call_ctx=CTX)
    variant = path_variant(book, role, relevance, skills=persona.profile.skills)

    assert relevance.from_model and len(claude.calls_for("match.relevance")) == 1
    [call] = claude.calls_for("match.relevance")
    assert "Motion & animation" in call.user_text and "AI UX patterns" in call.user_text  # the role's table
    assert line_ids(variant, "exp-1")[:2] == ["b-exp-1.6", "b-exp-1.4"]  # most relevant first
    assert line_ids(variant, "exp-2")[0] == "b-exp-2.6"
    assert len(line_ids(variant, "exp-1")) == BULLETS_RECENT
    assert all(len(e.bullets) <= BULLETS_OLDER for e in variant.experience[1:])


async def test_every_line_of_a_path_resume_is_the_facts_own_wording() -> None:
    persona = riya()
    book, role = persona.book(), role_of(persona, "senior-frontend-engineer")
    claude = FakeClaudeClient()
    claude.script_structured("match.relevance", _scores({"exp-1.1": (90, ["r2"])}))
    variant = path_variant(
        book, role, await score_role(claude, model=HAIKU, book=book, role=role, call_ctx=CTX)
    )
    facts = book.by_id()

    assert lines_of(variant)
    for line in lines_of(variant):
        [fact_id] = line.factIds
        assert line.text == exact_wording(facts[fact_id])
        assert (line.verdict, line.status) == ("exact", "verified")
    assert variant.summary.status == "dropped" and variant.summary.text == ""
    assert [m.kind for m in marks(variant, upload_variant(book))].count("edited") == 0
    # the headings are the book's: employer, title and dates never vary
    for entry in entries(variant):
        known = book.entry(entry.entryFactId)
        assert known is not None
        assert (entry.title, entry.org, entry.dates) == (known.title, known.org, known.dates)


async def test_when_the_model_cannot_be_asked_the_lexical_score_stands_in() -> None:
    persona = riya()
    book, role = persona.book(), role_of(persona, "design-systems-engineer")
    claude = FakeClaudeClient()
    claude.script_structured("match.relevance", ClaudeOverloaded())

    relevance = await score_role(claude, model=HAIKU, book=book, role=role, call_ctx=CTX)
    variant = path_variant(book, role, relevance, skills=persona.profile.skills)

    assert not relevance.from_model
    assert relevance.scores == lexical_relevance(role_requirements(role), book.facts).scores
    assert variant == path_variant(
        book, role, skills=persona.profile.skills
    )  # no relevance: the same stand-in
    assert lines_of(variant) and export_blockers(variant) == []


async def test_a_block_only_github_shows_is_surfaced_when_it_scores_for_the_role() -> None:
    persona = riya()
    book, role = persona.book(), role_of(persona, "ai-product-engineer")
    claude = FakeClaudeClient()
    claude.script_structured("match.relevance", _scores({"proj-5": (95, ["r3"])}))
    upload = upload_variant(book)

    variant = path_variant(
        book, role, await score_role(claude, model=HAIKU, book=book, role=role, call_ctx=CTX)
    )

    assert variant.projects[0].entryFactId == "proj-5"  # surfaced first, as the match does
    assert ("b-proj-5", "added") in [(m.bulletId, m.kind) for m in marks(variant, upload)]
    assert "b-proj-5" not in [line.id for line in lines_of(upload)]
    # without a score for the role it is not surfaced
    quiet = path_variant(book, role, lexical_relevance([], book.facts))
    assert "proj-5" not in [e.entryFactId for e in quiet.projects[:1]]


def test_the_skills_of_a_path_resume_lead_with_the_roles_and_never_list_a_gap() -> None:
    persona = riya()
    book, role = persona.book(), role_of(persona, "ai-product-engineer")
    variant = path_variant(book, role, skills=persona.profile.skills)
    names = skill_names(variant)
    evidenced = {f.text for f in book.facts if f.kind == "skill"}

    # React and TypeScript are requirements she meets; LLM API integration is below the role's level but evidenced
    assert names[:3] == ["React", "TypeScript", "LLM API integration"]
    assert variant.skills[0].group == "Skills"
    # the graph marks these as gaps and no source line shows them: they can never be listed
    gaps = [need.name for need in role.requirements if not need.have and need.name not in evidenced]
    assert gaps and not set(gaps) & set(names)
    assert set(names) <= evidenced and len(names) <= MAX_SKILLS
    assert len(names) == len(set(names))


def test_a_requirement_finds_the_skill_the_graph_ties_to_it_under_another_name() -> None:
    persona = meera()
    book, role = persona.book(), role_of(persona, "senior-growth")

    tied = path_variant(book, role, skills=persona.profile.skills)
    untied = path_variant(book, role)

    # "Lifecycle marketing" is her skill "Email marketing" in the graph, "Paid media" her "Meta Ads"
    assert skill_names(tied)[:2] == ["Email marketing", "Meta Ads"]
    assert [g.group for g in tied.skills] == ["Skills", "Other skills"]
    assert set(skill_names(untied)) == set(skill_names(tied)) == set(skill_names(upload_variant(book)))
