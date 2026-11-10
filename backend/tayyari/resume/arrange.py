"""Two arrangements of a person's blocks, both by code and both in the facts' own words.

    upload_variant   the resume as the person wrote it: every job, project and degree the resume file has, each with its
                     resume bullets in the order of the file, and the evidenced skills the file names. No model call
    path_variant     the blocks arranged for one next role of the graph: a requirement table built from
                     `Role.requirements`, the facts scored against it (`match.variant.score_relevance`, or its lexical
                     stand-in), `match.variant.select` choosing entries and bullets, `match.variant.build_skills`
                     ordering the skills

Nothing here writes a sentence: a bullet is `match.bullets.fallback_bullet`, the fact's own wording, ``exact`` and
``verified``. The summary stays what the match gives for an empty one (a ``dropped`` bullet with no text), so no
arrangement carries a line that no fact wrote.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from tayyari.contract.match import (
    Bullet,
    Coverage,
    CoverageStatus,
    EducationEntry,
    ExperienceEntry,
    Fact,
    Requirement,
    ResumeHeader,
    ResumeVariant,
    SkillGroup,
    SkillItem,
)
from tayyari.contract.models import Role, RoleRequirement, Skill
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.aliases import same_term
from tayyari.match.bullets import fallback_bullet
from tayyari.match.models import BookEntry, FactBook, Report
from tayyari.match.text import tidy
from tayyari.match.variant import (
    EXTRA_SKILL_RELEVANCE,
    MAX_SKILLS,
    Picked,
    Relevance,
    build_skills,
    gap_terms,
    lexical_relevance,
    names_term,
    score_relevance,
    select,
)

SUMMARY_ID: Final = "summary"
BULLET_PREFIX: Final = "b-"
UPLOAD_SKILLS_GROUP: Final = "Skills"
OTHER_SKILLS_GROUP: Final = "Other skills"
"""The group `match.variant.build_skills` puts skills in that no requirement names."""
SURFACE_MIN_RELEVANCE: Final = EXTRA_SKILL_RELEVANCE
"""A block that only GitHub or Lovable shows is surfaced on a path resume when it scores this much for the role."""
_NO_LINE: Final = 10**9


def bullet_id(fact_id: str) -> str:
    """The id of the line written from the block ``fact_id`` (the same on every resume, as the match names them)."""
    return f"{BULLET_PREFIX}{fact_id}"


def empty_summary() -> Bullet:
    """What the match gives for a summary nobody wrote: no text, ``dropped``, so the files carry no SUMMARY section."""
    return Bullet(id=SUMMARY_ID, text="", factIds=[], verdict="exact", placeholders=[], status="dropped")


def on_resume(fact: Fact) -> bool:
    """Is ``fact`` in the resume file (one of its references quotes the resume)?"""
    return any(ref.source == "resume" for ref in fact.refs)


def block_ids(entry: BookEntry) -> list[str]:
    """The blocks the lines of ``entry`` are written from: its bullet facts, or the entry itself for a project that has
    none (the rule of `match.variant.select`)."""
    if entry.bulletIds:
        return list(entry.bulletIds)
    return [entry.factId] if entry.kind == "project" else []


def entry_of_block(book: FactBook, fact_id: str) -> BookEntry | None:
    """The job or project whose line the block ``fact_id`` is, or None when it is not a line of one."""
    return next(
        (e for e in book.entries if e.kind in ("experience", "project") and fact_id in block_ids(e)), None
    )


def header_of(book: FactBook) -> ResumeHeader:
    return ResumeHeader(name=book.name, headline=book.headline, contact=list(book.contact))


def entry_heading(entry: BookEntry, bullets: list[Bullet]) -> ExperienceEntry:
    """A job or project of the resume: title, employer and dates exactly as the book has them."""
    return ExperienceEntry(
        entryFactId=entry.factId, title=entry.title, org=entry.org, dates=entry.dates, bullets=bullets
    )


def _resume_line(fact: Fact) -> int:
    """The first line of the resume file that ``fact`` quotes (a large number when it names none)."""
    lines = [ref.lines[0] for ref in fact.refs if ref.source == "resume" and ref.lines]
    return min(lines) if lines else _NO_LINE


# ───────────────────────── the uploaded resume ─────────────────────────


def _file_entries(book: FactBook, kind: str) -> list[tuple[BookEntry, Fact]]:
    """The entries of ``kind`` that the resume file has, in the order of the file."""
    facts = book.by_id()
    found = [(e, facts[e.factId]) for e in book.entries if e.kind == kind and e.factId in facts]
    return sorted((pair for pair in found if on_resume(pair[1])), key=lambda pair: _resume_line(pair[1]))


def _upload_entries(book: FactBook, kind: str) -> list[ExperienceEntry]:
    facts = book.by_id()
    out: list[ExperienceEntry] = []
    for entry, _ in _file_entries(book, kind):
        pool = sorted(
            (facts[f] for f in block_ids(entry) if f in facts and on_resume(facts[f])), key=_resume_line
        )
        out.append(entry_heading(entry, [fallback_bullet(bullet_id(f.id), f) for f in pool]))
    return out


def _from_person(fact: Fact) -> bool:
    return bool(fact.refs) and all(ref.source == "user" for ref in fact.refs)


def _upload_skill_items(book: FactBook) -> list[SkillItem]:
    """The evidenced skills the resume file shows, in the book's order: a skill fact that quotes the resume, or one a
    resume line names word for word (that line is then cited too; a bullet or a plain line before a job's heading)."""
    lines = sorted(
        (f for f in book.facts if f.kind != "skill" and on_resume(f)),
        key=lambda f: f.kind in ("experience", "project", "education"),
    )
    items: list[SkillItem] = []
    for fact in book.facts:
        if len(items) >= MAX_SKILLS:
            break
        if fact.kind != "skill" or _from_person(fact):
            continue
        cites = [fact.id]
        if not on_resume(fact):
            named = next((line for line in lines if names_term(line, fact.text)), None)
            if named is None:
                continue
            cites.append(named.id)
        items.append(SkillItem(name=fact.text, factIds=cites, evidenceTier=fact.evidenceTier))
    return items


def _upload_skills(book: FactBook) -> list[SkillGroup]:
    items = _upload_skill_items(book)
    return [SkillGroup(group=UPLOAD_SKILLS_GROUP, items=items)] if items else []


def upload_variant(book: FactBook) -> ResumeVariant:
    """The uploaded resume as a resume (see the module docstring). Blocks that only another source shows (a GitHub
    project the file does not list) are not on it; they stay in the person's blocks and can be put back."""
    return ResumeVariant(
        header=header_of(book),
        summary=empty_summary(),
        skills=_upload_skills(book),
        experience=_upload_entries(book, "experience"),
        projects=_upload_entries(book, "project"),
        education=[
            EducationEntry(factId=e.factId, title=e.title, org=e.org, dates=e.dates)
            for e, _ in _file_entries(book, "education")
        ],
        decisions=[],
        changes=[],
        lint=None,
    )


# ───────────────────────── a resume for a next role ─────────────────────────


def _needs(role: Role) -> list[tuple[RoleRequirement, str]]:
    """The requirements of ``role`` that have a name, each with that name tidied."""
    return [(need, name) for need in role.requirements if (name := tidy(need.name))]


def role_requirements(role: Role) -> list[Requirement]:
    """The requirement table of a role of the graph, built in code: one row per requirement the graph names, in its own
    words. The graph does not say how strongly a role asks for each, so every row weighs the same."""
    return [
        Requirement(
            id=f"r{n}",
            text=name,
            quote=name,
            tier="required",
            kind="skill",
            provenance="stated",
            terms=[name],
        )
        for n, (_, name) in enumerate(_needs(role), start=1)
    ]


def role_terms(role: Role) -> list[str]:
    """The words of the role's requirements: a typed line that uses one needs a fact that supports it."""
    return [name for _, name in _needs(role)]


def _role_coverage(
    book: FactBook, role: Role, requirements: Sequence[Requirement], skills: Sequence[Skill]
) -> list[Coverage]:
    """Which of the person's skill facts stand behind each requirement, from the graph and the book alone (no model):
    the evidenced skill the graph ties to it (``skillId``), or one of the same name. A requirement the graph marks as
    not met and that no skill fact shows is a gap, so it can never enter the skills list. The rows only steer
    `build_skills`: they are never stored or shown, and carry no level."""
    evidenced = [f for f in book.facts if f.kind == "skill" and not _from_person(f)]
    graph_name = {s.id: s.name for s in skills}
    rows: list[Coverage] = []
    for req, (need, _) in zip(requirements, _needs(role), strict=True):
        tied = graph_name.get(need.skillId or "")
        backing = [f.id for f in evidenced if f.text == tied or same_term(f.text, req.text)]
        if backing:
            status: CoverageStatus = "covered" if need.have else "partial"
        else:
            status = "unknown" if need.have else "missing_gap"
        rows.append(
            Coverage(
                requirementId=req.id, factIds=backing, status=status, level=0, note="From the skill graph."
            )
        )
    return rows


def _path_skills(
    book: FactBook,
    role: Role,
    requirements: Sequence[Requirement],
    score: Relevance,
    skills: Sequence[Skill],
) -> list[SkillGroup]:
    """The skills of a path resume: the evidenced skills behind the role's requirements first, then those that score
    for the role (`match.variant.build_skills`), then the rest of the uploaded resume's skills. The order varies per
    resume, never what the person has: a gap can not enter, and nothing is listed that no fact shows."""
    coverage = _role_coverage(book, role, requirements, skills)
    groups = build_skills(book, requirements, coverage, score, Report())
    gaps = gap_terms(requirements, coverage)
    listed = {item.name.casefold() for group in groups for item in group.items}
    shown = {fid for group in groups for item in group.items for fid in item.factIds}
    room = MAX_SKILLS - sum(len(group.items) for group in groups)
    rest: list[SkillItem] = []
    for item in _upload_skill_items(book):
        if len(rest) >= room:
            break
        if item.name.casefold() in listed or item.factIds[0] in shown:
            continue
        if any(same_term(item.name, gap) for gap in gaps):
            continue
        rest.append(item)
    if not rest:
        return groups
    if groups and groups[-1].group == OTHER_SKILLS_GROUP:
        last = groups[-1]
        return [*groups[:-1], SkillGroup(group=last.group, items=[*last.items, *rest])]
    return [*groups, SkillGroup(group=OTHER_SKILLS_GROUP, items=rest)]


def _surfaced(book: FactBook, relevance: Relevance) -> set[str]:
    """Blocks only GitHub or Lovable shows that score for the role: they are put first, as the match surfaces them."""
    return {
        f.id
        for f in book.facts
        if f.refs
        and all(ref.source in ("github", "lovable") for ref in f.refs)
        and relevance.scores.get(f.id, 0) >= SURFACE_MIN_RELEVANCE
    }


def path_variant(
    book: FactBook,
    role: Role,
    relevance: Relevance | None = None,
    *,
    skills: Sequence[Skill] = (),
) -> ResumeVariant:
    """The blocks of ``book`` arranged for ``role`` (see the module docstring). ``relevance`` is the model's scoring of
    the facts against the role's requirements; without it the lexical score stands in. ``skills`` are the graph's
    skills, so a requirement finds the evidenced skill the graph ties to it even under another name."""
    requirements = role_requirements(role)
    score = relevance if relevance is not None else lexical_relevance(requirements, book.facts)
    facts = book.by_id()
    jobs, projects, degrees = select(book, score, _surfaced(book, score))

    def entry_of(picked: Picked) -> ExperienceEntry:
        return entry_heading(
            picked.entry,
            [fallback_bullet(bullet_id(f), facts[f]) for f in picked.fact_ids if f in facts],
        )

    return ResumeVariant(
        header=header_of(book),
        summary=empty_summary(),
        skills=_path_skills(book, role, requirements, score, skills),
        experience=[entry_of(p) for p in jobs],
        projects=[entry_of(p) for p in projects],
        education=[EducationEntry(factId=e.factId, title=e.title, org=e.org, dates=e.dates) for e in degrees],
        decisions=[],
        changes=[],
        lint=None,
    )


async def score_role(
    claude: ClaudeClient, *, model: str, book: FactBook, role: Role, call_ctx: CallContext
) -> Relevance:
    """One relevance call for a path resume: the facts scored against the role's requirement table. A failed call (or
    a role with no requirements) gives the lexical score; only the daily spend ceiling propagates."""
    return await score_relevance(
        claude, model=model, requirements=role_requirements(role), facts=book.facts, call_ctx=call_ctx
    )


__all__ = [
    "BULLET_PREFIX",
    "SUMMARY_ID",
    "SURFACE_MIN_RELEVANCE",
    "UPLOAD_SKILLS_GROUP",
    "block_ids",
    "bullet_id",
    "empty_summary",
    "entry_heading",
    "entry_of_block",
    "header_of",
    "on_resume",
    "path_variant",
    "role_requirements",
    "role_terms",
    "score_role",
    "upload_variant",
]
