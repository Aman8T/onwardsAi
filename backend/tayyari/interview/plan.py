"""The plan of an interview, made in code from what the person has shown: no model call, the same inputs give the same plan.

    role      a role from a search with AI: the asks the evidence shows, a gap, a claim and a story. Quotas by kind (and by
              what the person wants more of), asks the posting marks as met first; a kind with no material is filled
              from the next kind that has some.
    resume    claims on the resume: numbers first, a "Led ..." line, a line from a source that is not the resume (a repo,
              a page), then any other line. Skills the analysis could not evidence are never asked as if the resume
              listed them: the profile's low-scoring skills are Tayyari's reading, not the person's words.
    stories   "tell me about a time ...", one per evidence line with people or action in it, distinct skills first.
    work      a work-sample template for the family of the person's work, and its three follow-ups.

Every slot's sources are the person's own evidence lines or the posting's own words. Nothing is
invented, and a plan shorter than asked says so ("Your profile is short, so ...").
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from tayyari.contract.interview import (
    InterviewFocus,
    InterviewKind,
    InterviewLength,
    InterviewPlan,
    PlanRequest,
    PlanSlot,
    RoleSource,
    SlotKind,
    SlotSource,
)
from tayyari.contract.jobsearch import AiJob, AskMet
from tayyari.contract.models import Profile
from tayyari.core.text import clip_words, tidy
from tayyari.jobsearch.brief import Evidence, evidence_lines

from .errors import BadPlan, NoTemplate, RoleGone
from .roles import RoleLookup
from .tasks import WorkTemplate, template_for_title
from .text import few_words, fold, has_injection, shared_words

KINDS: Final[tuple[SlotKind, ...]] = ("ask", "gap", "claim", "story")
"""The order of a role interview, and of the next kind to fill a missing one from."""
ROLE_QUOTAS: Final[dict[InterviewFocus | None, dict[SlotKind, int]]] = {
    None: {"ask": 2, "gap": 1, "claim": 1, "story": 1},
    "gaps": {"ask": 1, "gap": 2, "claim": 1, "story": 1},
    "claims": {"ask": 2, "gap": 1, "claim": 2, "story": 0},
    "stories": {"ask": 2, "gap": 1, "claim": 0, "story": 2},
}
SHORT_SLOTS: Final = 3
SHORT_STORIES: Final = 2
FULL_SLOTS: Final[dict[str, int]] = {"resume": 5, "stories": 4}
MINUTES_PER_SLOT: Final[dict[str, int]] = {"role": 4, "resume": 3, "stories": 5}
WORK_EXTRA_MINUTES: Final = 9
"""A work sample is its template's minutes plus about three minutes for each of its three follow-ups."""
SNIPPET_CHARS: Final = 140

_DIGIT: Final = re.compile(r"\d")
_LEADER: Final = re.compile(
    r"\b(?:led|lead|leads|owned|owns|ran|runs|built|builds|launched|shipped|founded|managed|drove|rebuilt|migrated"
    r"|architected|designed|created|spearheaded|headed)\b",
    re.IGNORECASE,
)
_PEOPLE: Final = re.compile(
    r"\b(?:led|lead|leads|leading|mentor\w*|ran|run|runs|pair|pairs|paired|pairing|partner\w*|organi[sz]\w*|taught"
    r"|teach\w*|present\w*|coordinat\w*|worked with|work with|handl\w*|resolv\w*|negotiat\w*|review\w*|collaborat\w*"
    r"|managed|facilitat\w*|trained|coach\w*|supervis\w*|onboard\w*|hosted|spoke)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One slot before it has an id: its kind, topic, sources, and the folded evidence texts it relies on."""

    kind: SlotKind
    topic: str
    sources: tuple[SlotSource, ...]
    uses: tuple[str, ...] = ()
    skill: str | None = None


@dataclass(frozen=True, slots=True)
class BuiltPlan:
    """The plan and what building it read: the job and the posting text (a role interview), the template (a work sample)."""

    plan: InterviewPlan
    job: AiJob | None = None
    posting_text: str | None = None
    template: WorkTemplate | None = None
    injected: bool = False


@dataclass(slots=True)
class _Chooser:
    """Picks evidence lines nobody has used yet, a line of a skill not yet used first."""

    texts: set[str] = field(default_factory=set)
    skills: set[str] = field(default_factory=set)

    def pick(self, lines: Sequence[Evidence]) -> Evidence | None:
        free = [line for line in lines if fold(line.cite.text) not in self.texts]
        fresh = [line for line in free if line.skill not in self.skills]
        pool = fresh or free
        return pool[0] if pool else None

    def use(self, cand: Candidate) -> None:
        self.texts.update(cand.uses)
        if cand.skill:
            self.skills.add(cand.skill)


# ───────────────────────── helpers ─────────────────────────


def resume_labels(profile: Profile) -> frozenset[str]:
    """The source labels that are the resume (a line from any other source is a page, a repo or a project)."""
    return frozenset({"Resume", *(s.label for s in profile.sources if s.kind == "resume")})


def _resume_first(lines: Sequence[Evidence], resume: frozenset[str]) -> list[Evidence]:
    return [
        *(ln for ln in lines if ln.cite.source in resume),
        *(ln for ln in lines if ln.cite.source not in resume),
    ]


def line_candidate(kind: SlotKind, line: Evidence) -> Candidate:
    return Candidate(
        kind=kind,
        topic=line.skill,
        sources=(SlotSource(origin="evidence", text=line.cite.text, source=line.cite.source),),
        uses=(fold(line.cite.text),),
        skill=line.skill,
    )


def _ask_candidate(met: AskMet, by_text: dict[str, Evidence]) -> Candidate:
    line = by_text.get(fold(met.cite.text))
    return Candidate(
        kind="ask",
        topic=line.skill if line is not None else few_words(met.ask),
        sources=(
            SlotSource(origin="posting", text=met.ask),
            SlotSource(origin="evidence", text=met.cite.text, source=met.cite.source),
        ),
        uses=(fold(met.cite.text),),
        skill=line.skill if line is not None else None,
    )


def _gap_candidate(gap: str, lines: Sequence[Evidence]) -> Candidate:
    """A gap and the evidence line nearest to it by the words they share, if any share one."""
    best: Evidence | None = None
    best_score = 0
    for line in lines:
        score = shared_words(gap, f"{line.skill} {line.cite.text}")
        if score > best_score:
            best, best_score = line, score
    sources = [SlotSource(origin="posting", text=gap)]
    uses: tuple[str, ...] = ()
    if best is not None:
        sources.append(SlotSource(origin="evidence", text=best.cite.text, source=best.cite.source))
        uses = (fold(best.cite.text),)
    return Candidate(kind="gap", topic=few_words(gap), sources=tuple(sources), uses=uses)


def _slots(cands: Sequence[Candidate]) -> list[PlanSlot]:
    return [
        PlanSlot(id=f"s{i + 1}", kind=c.kind, topic=c.topic, sources=list(c.sources))
        for i, c in enumerate(cands)
    ]


def plan_minutes(kind: InterviewKind, count: int, template: WorkTemplate | None = None) -> int:
    """About how long it takes: 4 minutes a question for a role, 3 for the resume, 5 for a story; a work sample is its
    template's minutes and about 3 for each follow-up."""
    if template is not None:
        return template.minutes + 3 * count
    return MINUTES_PER_SLOT.get(kind, 4) * count


def short_note(count: int, wanted: int) -> str | None:
    if count >= wanted:
        return None
    return (
        f"Your profile is short, so this interview has {count} question{'' if count == 1 else 's'}, not {wanted}. "
        "Add a project or a link to get more."
    )


def injection_snippet(text: str) -> str | None:
    """The first sentence or line of ``text`` that is aimed at an AI, shortened; None when no single piece is."""
    for piece in re.split(r"(?<=[.!?])\s+|\n+", text):
        if has_injection(piece):
            return clip_words(piece, SNIPPET_CHARS).rstrip(" .!?")
    return None


def _injection_note(text: str, dropped: bool) -> str:
    snippet = injection_snippet(text)
    head = "This posting has text aimed at an AI"
    head = f"{head}: “{snippet}”." if snippet else f"{head}."
    return f"{head} We left it out{' and skipped the question it touched' if dropped else ''}."


# ───────────────────────── one kind each ─────────────────────────


def plan_role(
    profile: Profile,
    job: AiJob,
    *,
    length: InterviewLength | None,
    focus: InterviewFocus | None,
) -> tuple[list[Candidate], int, bool]:
    """The candidates of a role interview in order, how many were asked for, and whether text aimed at an AI cost a slot."""
    lines = evidence_lines(profile)
    resume = resume_labels(profile)
    by_text = {fold(ln.cite.text): ln for ln in lines}
    quotas = ROLE_QUOTAS[focus]
    asks = sorted(job.met, key=lambda m: m.partly)  # stable: asks shown in full first
    ask_pool = [_ask_candidate(m, by_text) for m in asks if not has_injection(m.ask)]
    gap_pool = [_gap_candidate(g, lines) for g in job.gaps if not has_injection(g)]
    dropped = len(ask_pool) < len(asks) or len(gap_pool) < len(job.gaps)
    digit_lines = _resume_first([ln for ln in lines if _DIGIT.search(ln.cite.text)], resume)
    people_lines = _resume_first([ln for ln in lines if _PEOPLE.search(ln.cite.text)], resume)
    any_lines = _resume_first(list(lines), resume)
    chooser = _Chooser()
    picked: dict[SlotKind, list[Candidate]] = {kind: [] for kind in KINDS}

    def take(kind: SlotKind) -> bool:
        cand: Candidate | None = None
        if kind == "ask":
            cand = ask_pool.pop(0) if ask_pool else None
        elif kind == "gap":
            cand = gap_pool.pop(0) if gap_pool else None
        elif kind == "claim":
            line = chooser.pick(digit_lines)
            cand = line_candidate("claim", line) if line is not None else None
        else:
            line = chooser.pick(people_lines) or chooser.pick(any_lines)
            cand = line_candidate("story", line) if line is not None else None
        if cand is None:
            return False
        chooser.use(cand)
        picked[kind].append(cand)
        return True

    for kind in KINDS:
        for _ in range(quotas[kind]):
            take(kind)
    for index, kind in enumerate(KINDS):
        for _ in range(quotas[kind] - len(picked[kind])):
            for step in range(1, len(KINDS)):
                if take(KINDS[(index + step) % len(KINDS)]):
                    break
    ordered = [cand for kind in KINDS for cand in picked[kind]]
    wanted = SHORT_SLOTS if length == "short" else sum(quotas.values())
    return ordered[:wanted], wanted, dropped


def plan_resume(profile: Profile, *, length: InterviewLength | None) -> tuple[list[Candidate], int]:
    """Claims to defend: two lines with a number, a "Led ..." line, a line from a page or repo, and one more line. A step
    with no material is filled from any other evidence line."""
    lines = evidence_lines(profile)
    resume = resume_labels(profile)
    digit_lines = _resume_first([ln for ln in lines if _DIGIT.search(ln.cite.text)], resume)
    leader_lines = sorted(
        _resume_first([ln for ln in lines if _LEADER.search(ln.cite.text)], resume),
        key=lambda ln: 0 if (m := _LEADER.search(ln.cite.text)) is not None and m.start() == 0 else 1,
    )
    other_lines = [ln for ln in lines if ln.cite.source not in resume]
    any_lines = _resume_first(list(lines), resume)
    chooser = _Chooser()
    cands: list[Candidate] = []
    for step in ("digit", "digit", "leader", "other", "any"):
        pool = {"digit": digit_lines, "leader": leader_lines, "other": other_lines, "any": any_lines}[step]
        line = chooser.pick(pool)
        cand: Candidate | None = line_candidate("claim", line) if line is not None else None
        if cand is None:
            line = chooser.pick(any_lines)
            cand = line_candidate("claim", line) if line is not None else None
        if cand is None:
            continue
        chooser.use(cand)
        cands.append(cand)
    wanted = SHORT_SLOTS if length == "short" else FULL_SLOTS["resume"]
    return cands[:wanted], wanted


def plan_stories(profile: Profile, *, length: InterviewLength | None) -> tuple[list[Candidate], int]:
    """Stories to tell: evidence lines with people or action in them, distinct skills first, then any other line."""
    lines = evidence_lines(profile)
    resume = resume_labels(profile)
    people_lines = _resume_first([ln for ln in lines if _PEOPLE.search(ln.cite.text)], resume)
    any_lines = _resume_first(list(lines), resume)
    wanted = SHORT_STORIES if length == "short" else FULL_SLOTS["stories"]
    chooser = _Chooser()
    cands: list[Candidate] = []
    while len(cands) < wanted:
        line = chooser.pick(people_lines) or chooser.pick(any_lines)
        if line is None:
            break
        cand = line_candidate("story", line)
        chooser.use(cand)
        cands.append(cand)
    return cands, wanted


def plan_work(template: WorkTemplate, role: RoleSource | None = None) -> BuiltPlan:
    """A work sample: the template's follow-ups as `task` slots. They have no source: the work itself is what they ask about."""
    slots = [
        PlanSlot(id=f"s{i + 1}", kind="task", topic=f.topic, sources=[])
        for i, f in enumerate(template.follow_ups)
    ]
    return BuiltPlan(
        plan=InterviewPlan(
            kind="work",
            title=f"Work sample · {template.label}",
            slots=slots,
            minutes=plan_minutes("work", len(slots), template),
            role=role,
            family=template.label,
        ),
        template=template,
    )


# ───────────────────────── the plan ─────────────────────────


def _work_title(profile: Profile) -> str:
    current = tidy(profile.person.currentRole)
    if current:
        return current
    ranked = sorted(profile.roles, key=lambda r: -r.alignment)
    return tidy(ranked[0].title) if ranked else ""


async def build_plan(profile: Profile, req: PlanRequest, roles: RoleLookup) -> BuiltPlan:
    """The plan for ``req`` and what it was made from. Raises `BadPlan` (no role chosen), `RoleGone` (the role is not in the
    person's searches any more) or `NoTemplate` (no work sample for this kind of work)."""
    kind = req.kind
    if kind == "role":
        if req.role is None:
            raise BadPlan("Choose a role to practise for.")
        found = await roles.role(profile.id, req.role.runId, req.role.jobId)
        if found is None:
            raise RoleGone()
        job, text = found
        cands, wanted, dropped = plan_role(profile, job, length=req.length, focus=req.focus)
        slots = _slots(cands)
        injected = has_injection(text)
        notes = [n for n in (short_note(len(slots), wanted),) if n]
        if injected:
            notes.append(_injection_note(text, dropped))
        return BuiltPlan(
            plan=InterviewPlan(
                kind="role",
                title=f"{job.title} at {job.employer}",
                slots=slots,
                minutes=plan_minutes("role", len(slots)),
                role=req.role,
                note=" ".join(notes) or None,
            ),
            job=job,
            posting_text=text,
            injected=injected,
        )
    if kind == "work":
        title: str
        if req.role is not None:
            found = await roles.role(profile.id, req.role.runId, req.role.jobId)
            if found is None:
                raise RoleGone()
            title = found[0].title
        else:
            title = _work_title(profile)
        template = template_for_title(title) if title else None
        if template is None:
            raise NoTemplate()
        return plan_work(template, req.role)
    if kind == "resume":
        cands, wanted = plan_resume(profile, length=req.length)
        title = "Defend your resume"
    else:
        cands, wanted = plan_stories(profile, length=req.length)
        title = "Behavioural stories"
    slots = _slots(cands)
    return BuiltPlan(
        plan=InterviewPlan(
            kind=kind,
            title=title,
            slots=slots,
            minutes=plan_minutes(kind, len(slots)),
            note=short_note(len(slots), wanted),
        )
    )


__all__ = [
    "FULL_SLOTS",
    "KINDS",
    "ROLE_QUOTAS",
    "SHORT_SLOTS",
    "BuiltPlan",
    "Candidate",
    "build_plan",
    "injection_snippet",
    "line_candidate",
    "plan_minutes",
    "plan_resume",
    "plan_role",
    "plan_stories",
    "plan_work",
    "resume_labels",
    "short_note",
]
