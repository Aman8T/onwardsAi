"""The resume for one posting, composed by code from the person's cited facts.

    score      Haiku scores every fact 0-100 against the requirement table. It never rewrites. When the call fails,
               a lexical score (word overlap with the requirements, weighted by tier) stands in
    select     code chooses: the summary's facts, up to 5 bullets for the most recent job and 3 for each older one
               (most relevant first), up to 3 projects, up to 2 degrees; facts that only a non-resume source shows
               and that a coverage row ties to a requirement are surfaced
    skills     code groups the person's skill facts by the posting's requirement kinds; the posting's own spelling is
               used when a fact supports the term; a skill the person has not evidenced (a ``missing_gap`` term) can
               never enter, whatever a model suggests
    phrase     Haiku words each chosen fact as one resume sentence (at most three calls, all at once); every sentence
               goes through `bullets.assess_draft`; what fails the checks falls back to the fact's own wording
    decisions  a figure the facts lack, a stretched scope verb or an unconfirmed posting term makes the bullet
               ``needs_user`` with a `Decision`; the resume cannot be exported until they are settled

The result is a `ResumeVariant`; `export` turns it into files and `lint` reads those files back.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from tayyari.contract.match import (
    Bullet,
    Change,
    Coverage,
    Decision,
    EducationEntry,
    ExperienceEntry,
    Fact,
    Posting,
    Requirement,
    ResumeHeader,
    ResumeVariant,
    SkillGroup,
    SkillItem,
    tier_weight,
)
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.aliases import same_term
from tayyari.match.bullets import (
    SUMMARY_MAX_WORDS,
    DraftContext,
    all_bullets,
    assess_draft,
    fallback_bullet,
)
from tayyari.match.calls import ask_or_none, gather_all
from tayyari.match.models import BookEntry, FactBook, Report
from tayyari.match.schemas import (
    BulletOut,
    PhraseOut,
    RelevanceOut,
    RelevanceRowOut,
    rows_of,
    schema_of,
)
from tayyari.match.text import clip, term_in_corpus
from tayyari.match.verify import corpus_of
from tayyari.portfolio.ground import tokens_of
from tayyari.prompts.match import (
    PHRASE_SYSTEM,
    RELEVANCE_SYSTEM,
    PhraseItem,
    build_phrase_prompt,
    build_relevance_prompt,
)

RELEVANCE_MAX_TOKENS: Final = 3500
PHRASE_MAX_TOKENS: Final = 3000
MAX_PHRASE_CALLS: Final = 3
PHRASE_PER_CALL: Final = 8
BULLETS_RECENT: Final = 5
BULLETS_OLDER: Final = 3
MAX_ENTRIES: Final = 5
MAX_PROJECTS: Final = 3
MAX_EDUCATION: Final = 2
MAX_SKILLS: Final = 14
MAX_CHANGES: Final = 10
SUMMARY_FACTS: Final = 4
EXTRA_SKILL_RELEVANCE: Final = 50
"""A skill no requirement names is shown only when it scores this much against the table."""

_STOP: Final = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "over",
        "under",
        "about",
        "using",
        "used",
        "use",
        "built",
        "build",
        "led",
        "own",
        "owned",
        "owns",
        "across",
        "their",
        "them",
        "they",
        "have",
        "has",
        "had",
        "were",
        "was",
        "are",
        "also",
        "than",
        "then",
        "such",
        "each",
        "per",
        "via",
        "not",
        "but",
        "you",
        "your",
        "our",
        "out",
        "its",
        "all",
        "any",
        "can",
        "more",
        "most",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "work",
        "team",
        "role",
        "experience",
        "years",
        "year",
        "strong",
        "ability",
    ]
)
_SCORED_KINDS: Final = frozenset({"accomplishment", "project", "skill", "cert", "other"})
_GROUP_OF_KIND: Final[dict[str, str]] = {
    "tool": "Tools",
    "language": "Languages",
    "domain": "Domain",
    "cert": "Certifications",
    "skill": "Skills",
}
_GROUP_ORDER: Final = ("Tools", "Skills", "Domain", "Languages", "Certifications", "Other skills")
_SOURCE_NAME: Final = {
    "github": "GitHub",
    "lovable": "Lovable app",
    "link": "public page",
    "user": "own words",
}


@dataclass(slots=True)
class Relevance:
    """How much each fact helps the requirement table (0-100), and which requirements it speaks to."""

    scores: dict[str, int] = field(default_factory=dict)
    links: dict[str, list[str]] = field(default_factory=dict)
    from_model: bool = False


@dataclass(slots=True)
class VariantResult:
    variant: ResumeVariant
    report: Report = field(default_factory=Report)
    relevance: Relevance = field(default_factory=Relevance)


# ───────────────────────── relevance ─────────────────────────


def _tokens(text: str) -> set[str]:
    return {t for t in tokens_of(text) if len(t) > 2 and t not in _STOP and not t.isdigit()}


def lexical_relevance(requirements: Sequence[Requirement], facts: Sequence[Fact]) -> Relevance:
    """A stand-in score when the model cannot be asked: word overlap with each requirement, weighted by its tier."""
    wanted = [(r, _tokens(" ".join([r.text, *r.terms]))) for r in requirements]
    out = Relevance()
    for fact in facts:
        if fact.kind not in _SCORED_KINDS:
            continue
        have = _tokens(fact.text)
        total = 0.0
        hits: list[tuple[float, str]] = []
        for req, tokens in wanted:
            if not tokens:
                continue
            overlap = len(tokens & have)
            if not overlap:
                continue
            share = min(1.0, overlap / min(3, len(tokens)))
            total += tier_weight(req.tier) * share
            hits.append((tier_weight(req.tier) * share, req.id))
        out.scores[fact.id] = round(100 * min(1.0, total / 1.2))
        out.links[fact.id] = [rid for _, rid in sorted(hits, reverse=True)[:3]]
    return out


async def score_relevance(
    claude: ClaudeClient,
    *,
    model: str,
    requirements: Sequence[Requirement],
    facts: Sequence[Fact],
    call_ctx: CallContext,
) -> Relevance:
    """Ask Haiku to score the facts against the requirement table. Facts it skips, or all of them when the call
    fails, get the lexical score. Scores are clamped to 0-100 and only real fact and requirement ids survive."""
    fallback = lexical_relevance(requirements, facts)
    scored = [f for f in facts if f.kind in _SCORED_KINDS]
    if not scored or not requirements:
        return fallback
    data = await ask_or_none(
        claude,
        model=model,
        purpose="match.relevance",
        system=RELEVANCE_SYSTEM,
        user=build_relevance_prompt(requirements, scored),
        schema=schema_of(
            RelevanceOut, factId=[f.id for f in scored], requirementIds=[r.id for r in requirements]
        ),
        max_tokens=RELEVANCE_MAX_TOKENS,
        call_ctx=call_ctx,
    )
    if data is None:
        return fallback
    known = {f.id for f in scored}
    req_ids = {r.id for r in requirements}
    result = Relevance(scores=dict(fallback.scores), links=dict(fallback.links), from_model=True)
    seen: set[str] = set()
    for row in rows_of(data, "scores", RelevanceRowOut):
        if row.factId not in known or row.factId in seen:
            continue
        seen.add(row.factId)
        result.scores[row.factId] = min(100, max(0, int(row.score)))
        result.links[row.factId] = [r for r in dict.fromkeys(row.requirementIds) if r in req_ids][:3]
    return result


# ───────────────────────── selection ─────────────────────────


@dataclass(frozen=True, slots=True)
class Picked:
    """An entry of the resume and the fact ids chosen for its bullets, in the order they will be written."""

    entry: BookEntry
    fact_ids: tuple[str, ...]
    reordered: bool = False


def _bullet_pool(book: FactBook, entry: BookEntry) -> list[str]:
    """The facts an entry can be written from: its bullets, or the entry itself when a project has none."""
    if entry.bulletIds:
        return list(entry.bulletIds)
    return [entry.factId] if entry.kind == "project" else []


def _pick(
    book: FactBook,
    entry: BookEntry,
    relevance: Relevance,
    limit: int,
    surfaced: set[str],
) -> Picked:
    pool = _bullet_pool(book, entry)
    order = {fid: i for i, fid in enumerate(pool)}
    ranked = sorted(pool, key=lambda f: (f not in surfaced, -relevance.scores.get(f, 0), order[f]))
    chosen = ranked[:limit]
    return Picked(entry, tuple(chosen), reordered=chosen != sorted(chosen, key=lambda f: order[f]))


def select(
    book: FactBook, relevance: Relevance, surfaced: set[str]
) -> tuple[list[Picked], list[Picked], list[BookEntry]]:
    """``(jobs, projects, degrees)`` chosen for the resume (see the module docstring)."""

    def entry_score(entry: BookEntry) -> int:
        return max((relevance.scores.get(f, 0) for f in _bullet_pool(book, entry)), default=0)

    jobs = sorted((e for e in book.entries if e.kind == "experience"), key=lambda e: -e.recency)
    keep = jobs[:2]
    rest = sorted(jobs[2:], key=lambda e: (-entry_score(e), -e.recency))[: max(0, MAX_ENTRIES - len(keep))]
    chosen_jobs = sorted([*keep, *rest], key=lambda e: -e.recency)
    picked_jobs = [
        _pick(book, e, relevance, BULLETS_RECENT if i == 0 else BULLETS_OLDER, surfaced)
        for i, e in enumerate(chosen_jobs)
    ]

    projects = sorted(
        (e for e in book.entries if e.kind == "project"),
        key=lambda e: (
            e.factId not in surfaced and not any(b in surfaced for b in e.bulletIds),
            -entry_score(e),
        ),
    )[:MAX_PROJECTS]
    picked_projects = [_pick(book, e, relevance, 2, surfaced) for e in projects]
    degrees = [e for e in book.entries if e.kind == "education"][:MAX_EDUCATION]
    return picked_jobs, picked_projects, degrees


# ───────────────────────── skills ─────────────────────────


def _coverage_by_requirement(coverage: Sequence[Coverage]) -> dict[str, Coverage]:
    return {c.requirementId: c for c in coverage}


def gap_terms(requirements: Sequence[Requirement], coverage: Sequence[Coverage]) -> set[str]:
    """Terms of requirements the person has no evidence for (a ``missing_gap`` row). They never reach the skills list."""
    cov = _coverage_by_requirement(coverage)
    return {t for r in requirements if r.id in cov and cov[r.id].status == "missing_gap" for t in r.terms}


def skill_violations(
    groups: Sequence[SkillGroup],
    book: FactBook,
    requirements: Sequence[Requirement],
    coverage: Sequence[Coverage],
) -> list[str]:
    """Skill items that must not be on the resume: one that names a gap term, or one no skill fact backs."""
    facts = book.by_id()
    gaps = gap_terms(requirements, coverage)
    bad: list[str] = []
    for group in groups:
        for item in group.items:
            backed = any(f in facts and facts[f].kind == "skill" for f in item.factIds)
            if not backed or any(same_term(item.name, g) for g in gaps):
                bad.append(item.name)
    return bad


def build_skills(
    book: FactBook,
    requirements: Sequence[Requirement],
    coverage: Sequence[Coverage],
    relevance: Relevance,
    report: Report,
) -> list[SkillGroup]:
    """The skills section: the person's skill facts grouped by the posting's requirement kinds (see the module docstring)."""
    facts = book.by_id()
    cov = _coverage_by_requirement(coverage)
    gaps = gap_terms(requirements, coverage)
    chosen: dict[str, tuple[str, SkillItem]] = {}

    def add(group: str, name: str, fact: Fact) -> None:
        key = name.casefold()
        if key in chosen or len(chosen) >= MAX_SKILLS:
            return
        if any(same_term(name, g) for g in gaps):
            report.add("skill", name, "the posting's requirement for it is a gap")
            return
        chosen[key] = (group, SkillItem(name=name, factIds=[fact.id], evidenceTier=fact.evidenceTier))

    for req in sorted(requirements, key=lambda r: -tier_weight(r.tier)):
        row = cov.get(req.id)
        if row is None or row.status not in ("covered", "partial", "synonym_only", "missing_have_it"):
            continue
        group = _GROUP_OF_KIND.get(req.kind)
        if group is None:
            continue
        for fid in row.factIds:
            fact = facts.get(fid)
            if fact is None or fact.kind != "skill":
                continue
            posting_word = next((t for t in req.terms if same_term(t, fact.text)), None)
            add(group, posting_word or fact.text, fact)
    for fact in sorted(
        (f for f in book.facts if f.kind == "skill"), key=lambda f: -relevance.scores.get(f.id, 0)
    ):
        if relevance.scores.get(fact.id, 0) >= EXTRA_SKILL_RELEVANCE and fact.evidenceTier != "listed":
            add("Other skills", fact.text, fact)
    by_group: dict[str, list[SkillItem]] = {}
    for group, item in chosen.values():
        by_group.setdefault(group, []).append(item)
    return [SkillGroup(group=g, items=by_group[g]) for g in _GROUP_ORDER if g in by_group]


# ───────────────────────── the phrase calls ─────────────────────────


def draft_context(
    posting: Posting, coverage: Sequence[Coverage], *, max_words: int | None = None
) -> DraftContext:
    """What a sentence is judged against besides its facts: the posting's terms and the facts coverage ties to each."""
    cov = _coverage_by_requirement(coverage)
    support: dict[str, set[str]] = {}
    terms: dict[str, None] = {}
    for req in posting.requirements:
        row = cov.get(req.id)
        for term in req.terms:
            terms.setdefault(term, None)
            if row is not None and row.status in ("covered", "partial", "synonym_only", "missing_have_it"):
                support.setdefault(term, set()).update(row.factIds)
    kwargs = {"max_words": max_words} if max_words else {}
    return DraftContext(
        posting_terms=tuple(terms),
        term_support={t: frozenset(ids) for t, ids in support.items()},
        **kwargs,
    )


@dataclass(slots=True)
class _Item:
    item_id: str
    kind: str
    primary: Fact
    pool: list[Fact]
    wants: list[str]


def _allowed_terms(pool: Sequence[Fact], terms: Sequence[str]) -> tuple[str, ...]:
    corpus, raw = corpus_of(pool)
    return tuple(t for t in terms if term_in_corpus(t, corpus, raw))


def _chunks(items: Sequence[_Item]) -> list[list[_Item]]:
    if not items:
        return []
    calls = min(MAX_PHRASE_CALLS, max(1, math.ceil(len(items) / PHRASE_PER_CALL)))
    size = math.ceil(len(items) / calls)
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


async def _phrase(
    claude: ClaudeClient,
    *,
    model: str,
    chunk: Sequence[_Item],
    terms: Sequence[str],
    job_title: str,
    call_ctx: CallContext,
) -> dict[str, BulletOut]:
    facts: dict[str, Fact] = {}
    for item in chunk:
        for fact in item.pool:
            facts.setdefault(fact.id, fact)
    prompt_items = [
        PhraseItem(
            item_id=i.item_id,
            kind=i.kind,
            fact_ids=tuple(f.id for f in i.pool),
            allowed_terms=_allowed_terms(i.pool, terms),
            wants=tuple(i.wants),
        )
        for i in chunk
    ]
    data = await ask_or_none(
        claude,
        model=model,
        purpose="match.phrase",
        system=PHRASE_SYSTEM,
        user=build_phrase_prompt(prompt_items, facts, job_title),
        schema=schema_of(PhraseOut, itemId=[i.item_id for i in chunk], factIds=list(facts)),
        max_tokens=PHRASE_MAX_TOKENS,
        call_ctx=call_ctx,
    )
    if data is None:
        return {}
    allowed = {i.item_id for i in chunk}
    out: dict[str, BulletOut] = {}
    for row in rows_of(data, "bullets", BulletOut):
        if row.itemId in allowed:
            out.setdefault(row.itemId, row)
    return out


def _settle(
    item: _Item,
    row: BulletOut | None,
    ctx: DraftContext,
    decisions: list[Decision],
    report: Report,
) -> Bullet:
    """The bullet for one item: the model's sentence if it passes every check, else the fact's own words."""
    summary = item.item_id == "summary"
    if row is None:
        report.add("bullet", item.item_id, "the model gave no sentence")
        return _empty_summary() if summary else fallback_bullet(item.item_id, item.primary)
    by_id = {f.id: f for f in item.pool}
    cited_ids = [
        item.primary.id,
        *(f for f in dict.fromkeys(row.factIds) if f in by_id and f != item.primary.id),
    ]
    cited = [by_id[f] for f in cited_ids]
    result = assess_draft(item.item_id, row.text, cited, ctx, primary=item.primary, model_verdict=row.verdict)
    if result.bullet is None:
        report.add("bullet", item.item_id, result.rejected or "rejected")
        return _empty_summary() if summary else fallback_bullet(item.item_id, item.primary)
    if result.decision is not None:
        decisions.append(result.decision)
    return result.bullet


def _empty_summary() -> Bullet:
    return Bullet(id="summary", text="", factIds=[], verdict="exact", placeholders=[], status="dropped")


# ───────────────────────── compose ─────────────────────────


def _surfaced_ids(book: FactBook, coverage: Sequence[Coverage]) -> set[str]:
    """Facts a coverage row says the person HAS but the resume lacks (``missing_have_it``), and whose sources are not the resume."""
    facts = book.by_id()
    return {
        fid
        for c in coverage
        if c.status == "missing_have_it"
        for fid in c.factIds
        if fid in facts and all(r.source != "resume" for r in facts[fid].refs)
    }


async def compose_variant(
    claude: ClaudeClient,
    *,
    model: str,
    book: FactBook,
    posting: Posting,
    coverage: Sequence[Coverage],
    call_ctx: CallContext,
    relevance: Relevance | None = None,
    writer_model: str | None = None,
) -> VariantResult:
    """Compose the resume of ``book`` for ``posting`` (see the module docstring). ``relevance`` skips the scoring
    call; ``writer_model`` (default ``model``) phrases the sentences."""
    report = Report()
    facts = book.by_id()
    reqs = posting.requirements
    score = relevance or await score_relevance(
        claude, model=model, requirements=reqs, facts=book.facts, call_ctx=call_ctx
    )
    surfaced = _surfaced_ids(book, coverage)
    jobs, projects, degrees = select(book, score, surfaced)
    skills = build_skills(book, reqs, coverage, score, report)

    # the sentences to write
    items: list[_Item] = []
    req_text = {r.id: r.text for r in reqs}

    def wants(fid: str) -> list[str]:
        return [req_text[r] for r in score.links.get(fid, []) if r in req_text][:2]

    top = sorted(
        (f for f in book.facts if f.kind in ("accomplishment", "project")),
        key=lambda f: -score.scores.get(f.id, 0),
    )[:SUMMARY_FACTS]
    if top:
        pool: dict[str, Fact] = {}
        for fact in top:
            pool.setdefault(fact.id, fact)
            parent = next((e for e in book.entries if fact.id in e.bulletIds), None)
            if parent is not None and parent.factId in facts:
                pool.setdefault(parent.factId, facts[parent.factId])
        items.append(
            _Item(
                "summary",
                "summary",
                top[0],
                list(pool.values()),
                [req_text[r] for r in score.links.get(top[0].id, []) if r in req_text][:3],
            )
        )
    picked_all = [*jobs, *projects]
    for picked in picked_all:
        entry_fact = facts.get(picked.entry.factId)
        for fid in picked.fact_ids:
            candidate = facts.get(fid)
            if candidate is None:
                continue
            pool_facts = (
                [candidate]
                if entry_fact is None or entry_fact.id == candidate.id
                else [candidate, entry_fact]
            )
            items.append(_Item(f"b-{fid}", "bullet", candidate, pool_facts, wants(fid)))

    terms = list(dict.fromkeys(t for r in reqs for t in r.terms))
    chunks = _chunks(items)
    answers = await gather_all(
        *(
            _phrase(
                claude,
                model=writer_model or model,
                chunk=c,
                terms=terms,
                job_title=posting.title,
                call_ctx=call_ctx,
            )
            for c in chunks
        )
    )
    rows: dict[str, BulletOut] = {}
    for part in answers:
        rows.update(part)

    ctx = draft_context(posting, coverage)
    summary_ctx = draft_context(posting, coverage, max_words=SUMMARY_MAX_WORDS)
    decisions: list[Decision] = []
    settled: dict[str, Bullet] = {}
    for item in items:
        settled[item.item_id] = _settle(
            item, rows.get(item.item_id), summary_ctx if item.item_id == "summary" else ctx, decisions, report
        )

    def entry_of(picked: Picked) -> ExperienceEntry:
        return ExperienceEntry(
            entryFactId=picked.entry.factId,
            title=picked.entry.title,
            org=picked.entry.org,
            dates=picked.entry.dates,
            bullets=[settled[f"b-{f}"] for f in picked.fact_ids if f"b-{f}" in settled],
        )

    experience = [entry_of(p) for p in jobs]
    project_entries = [entry_of(p) for p in projects]
    education = [EducationEntry(factId=e.factId, title=e.title, org=e.org, dates=e.dates) for e in degrees]
    variant = ResumeVariant(
        header=ResumeHeader(name=book.name, headline=book.headline, contact=list(book.contact)),
        summary=settled.get("summary", _empty_summary()),
        skills=skills,
        experience=experience,
        projects=project_entries,
        education=education,
        decisions=decisions,
        changes=[],
        lint=None,
    )
    variant = variant.model_copy(update={"changes": _changes(book, variant, picked_all, surfaced, skills)})
    return VariantResult(variant, report, score)


def _changes(
    book: FactBook,
    variant: ResumeVariant,
    picked: Sequence[Picked],
    surfaced: set[str],
    skills: Sequence[SkillGroup],
) -> list[Change]:
    facts = book.by_id()
    out: list[Change] = []
    shown = {fid for b in all_bullets(variant) for fid in b.factIds} | {
        fid for g in skills for item in g.items for fid in item.factIds
    }
    for fid in sorted(surfaced & shown):
        fact = facts[fid]
        where = _SOURCE_NAME.get(fact.refs[0].source, "other sources")
        out.append(
            Change(
                kind="surfaced",
                text=f"Added {clip(fact.text, 80)} from your {where}; your resume did not say it.",
                factIds=[fid],
            )
        )
    for p in picked:
        if p.reordered:
            ids = [f for f in p.fact_ids]
            out.append(
                Change(
                    kind="reordered",
                    text=f"Moved the lines of {p.entry.title} that match the posting to the top.",
                    factIds=ids,
                )
            )
    for b in all_bullets(variant):
        if b.verdict == "reframed" and b.status != "dropped":
            out.append(
                Change(kind="reworded", text=f"Reworded: {clip(b.text, 100)}", factIds=list(b.factIds))
            )
    chosen = {p.entry.factId: set(p.fact_ids) for p in picked}
    cut = [
        f
        for e in book.entries
        if e.factId in chosen
        for f in _bullet_pool(book, e)
        if f not in chosen[e.factId]
    ]
    if cut:
        out.append(
            Change(
                kind="left_out",
                text=f"Left out {len(cut)} lower-relevance line{'s' if len(cut) != 1 else ''} to keep it short.",
                factIds=cut[:8],
            )
        )
    dropped_entries = [
        e for e in book.entries if e.kind == "experience" and e.factId not in {p.entry.factId for p in picked}
    ]
    if dropped_entries:
        out.append(
            Change(
                kind="left_out",
                text="Left out older or less relevant roles: "
                + ", ".join(clip(e.title, 40) for e in dropped_entries[:4])
                + ".",
                factIds=[e.factId for e in dropped_entries[:4]],
            )
        )
    return out[:MAX_CHANGES]


__all__ = [
    "BULLETS_OLDER",
    "BULLETS_RECENT",
    "MAX_SKILLS",
    "Picked",
    "Relevance",
    "VariantResult",
    "build_skills",
    "compose_variant",
    "draft_context",
    "gap_terms",
    "lexical_relevance",
    "score_relevance",
    "select",
    "skill_violations",
]
