"""What "Where can I go next" shows (N1), worked out in code from a profile, the owner's choices and the person's employer
names. Pure functions: no storage, no model, no clock. The service feeds them; the tests prove them.

The rules (docs/roadmap.md N1, "What the report shows"):

- A role is **credible** when at least half its requirements are evidenced (``have``, rounded up) and at least one evidenced
  requirement maps (by ``skillId``, then by the skill's name) to a skill with at least one evidence line. At most three are
  listed: most evidenced asks first, then ``alignment``, then the graph's order.
- **Ready now** when at most one requirement is not evidenced; otherwise the role carries ``Role.eta`` as written.
- **Quotes**: up to two per role, from the evidence of its evidenced requirements' skills, strongest skill first, never the
  same line twice on a page, never a line that speaks of a layoff. The text is the evidence verbatim, then redacted, then
  clipped at a word boundary. Its id is stable while the line stays in the graph, so hiding it survives a refresh.
- **Near misses**: up to two roles below the bar, each with the asks it is missing.
- **Redaction**: while ``showOrgs`` is off, every employer name the person's own records hold (and its form without a
  company suffix) becomes "their employer". A client named inside a sentence is not in those records, which is why the owner
  sees every quote before sharing.
- **The person line** is current role · years · place, never an employer; the name is first name and initial, and only
  while the owner shows it.

Nothing here scores the person against anyone; the counts are counts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from tayyari.contract.models import Profile, Role, RoleRequirement, Skill
from tayyari.contract.report import (
    REPORT_AREAS_MAX,
    REPORT_MISSING_MAX,
    REPORT_NEAR_MISSES_MAX,
    REPORT_QUOTE_MAX,
    REPORT_QUOTES_PER_ROLE,
    REPORT_ROLES_MAX,
    ReportArea,
    ReportChoices,
    ReportContent,
    ReportNearMiss,
    ReportPerson,
    ReportQuote,
    ReportRole,
)
from tayyari.core.text import clip_words

EMPLOYER: Final = "their employer"
"""What an employer's name becomes while ``showOrgs`` is off."""
SHORT_MAX: Final = 14
ORG_MIN_CHARS: Final = 2
QUOTE_ID_LEN: Final = 12
UNKNOWN_SOURCE: Final = "Source"

_SUFFIXES: Final = frozenset(
    {
        "ltd",
        "ltd.",
        "limited",
        "pvt",
        "pvt.",
        "private",
        "inc",
        "inc.",
        "llc",
        "llp",
        "corp",
        "corp.",
        "corporation",
        "co",
        "co.",
        "company",
        "group",
        "technologies",
        "technology",
        "tech",
        "solutions",
        "systems",
        "services",
        "software",
        "consulting",
        "consultancy",
        "labs",
        "india",
        "global",
        "(india)",
    }
)
"""Words a company's name is often written with and without ("Zentrix Systems Pvt Ltd" is also "Zentrix")."""

_LAYOFF: Final = re.compile(
    r"\b(laid[\s-]?off|lay[\s-]?offs?|let go|retrench\w*|terminat\w*|redundan\w*|fired|sacked)\b",
    re.IGNORECASE,
)
"""A line that speaks of losing a job is never quoted: the report never says why someone is looking."""


# ───────────────────────── small pieces ─────────────────────────


def _tidy(text: str) -> str:
    return " ".join(text.split())


def quote_id(source_id: str, text: str) -> str:
    """A quote's id: stable while the same line from the same source stays in the graph."""
    digest = hashlib.sha256(f"{source_id}\n{_tidy(text)}".encode()).hexdigest()
    return digest[:QUOTE_ID_LEN]


def short_name(full: str) -> str | None:
    """ "Ravi Kumar Sharma" -> "Ravi S."; one word stays as it is; nothing for an empty name."""
    words = _tidy(full).split(" ")
    words = [w for w in words if w]
    if not words:
        return None
    if len(words) == 1:
        return words[0]
    return f"{words[0]} {words[-1][0].upper()}."


def person_line(profile: Profile) -> str:
    """Current role · years · place, each part only when the graph has it; never an employer."""
    person = profile.person
    parts: list[str] = []
    role = _tidy(person.currentRole)
    if role:
        parts.append(role)
    years = person.yearsExperience
    if years is not None and years > 0:
        whole = math.floor(years + 0.5)
        parts.append("under a year" if whole < 1 else f"{whole} year{'s' if whole != 1 else ''}")
    place = _tidy(person.location or "")
    if place:
        parts.append(place)
    return " · ".join(parts)


def report_areas(profile: Profile) -> list[ReportArea]:
    """The graph's areas in their own order; past eight, the eight strongest stay, still in the graph's order."""
    domains = list(enumerate(profile.domains))
    if len(domains) > REPORT_AREAS_MAX:
        kept = sorted(domains, key=lambda pair: (-pair[1].score, pair[0]))[:REPORT_AREAS_MAX]
        domains = sorted(kept, key=lambda pair: pair[0])
    out: list[ReportArea] = []
    for _, d in domains:
        short = _tidy(d.short or d.name)
        if len(short) > SHORT_MAX:
            short = short[: SHORT_MAX - 1].rstrip() + "…"
        out.append(ReportArea(name=_tidy(d.name), short=short, score=max(0, min(100, int(d.score)))))
    return out


# ───────────────────────── employer names ─────────────────────────


def org_names(blobs: Iterable[Any]) -> list[str]:
    """Every employer name held under an ``org`` key anywhere in the given JSON (resume drafts, fact books, the person's
    own-words facts), each once, in the order first seen."""
    seen: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "org" and isinstance(value, str):
                    name = _tidy(value)
                    if len(name) >= ORG_MIN_CHARS and name.casefold() not in seen:
                        seen[name.casefold()] = name
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for blob in blobs:
        walk(blob)
    return list(seen.values())


def org_variants(name: str) -> list[str]:
    """The name, and the name with trailing company words removed one at a time ("Zentrix Systems Pvt Ltd", "Zentrix
    Systems Pvt", "Zentrix Systems", "Zentrix"). A variant shorter than three characters is dropped: too likely a word."""
    words = _tidy(name).split(" ")
    out = [" ".join(words)]
    while len(words) > 1 and words[-1].casefold() in _SUFFIXES:
        words = words[:-1]
        variant = " ".join(words)
        if len(variant) >= 3:
            out.append(variant)
    return [v for v in dict.fromkeys(out) if len(v) >= ORG_MIN_CHARS]


def redact(text: str, orgs: Sequence[str]) -> str:
    """Every employer name in ``text`` (any case, whole words, longest form first) becomes "their employer"."""
    variants = sorted({v for name in orgs for v in org_variants(name)}, key=len, reverse=True)
    out = text
    for variant in variants:
        out = re.sub(rf"(?<!\w){re.escape(variant)}(?!\w)", EMPLOYER, out, flags=re.IGNORECASE)
    return out


# ───────────────────────── roles ─────────────────────────


@dataclass(frozen=True, slots=True)
class _Graded:
    index: int
    role: Role
    have: int
    total: int
    skills: tuple[Skill, ...]
    """The skills behind the evidenced requirements that hold evidence, strongest first."""


def _skill_for(req: RoleRequirement, by_id: dict[str, Skill], by_name: dict[str, Skill]) -> Skill | None:
    if req.skillId and req.skillId in by_id:
        return by_id[req.skillId]
    return by_name.get(_tidy(req.name).casefold())


def _grade(profile: Profile) -> list[_Graded]:
    by_id = {s.id: s for s in profile.skills}
    by_name = {_tidy(s.name).casefold(): s for s in profile.skills}
    order = {s.id: i for i, s in enumerate(profile.skills)}
    graded: list[_Graded] = []
    for index, role in enumerate(profile.roles):
        total = len(role.requirements)
        have = sum(1 for r in role.requirements if r.have)
        backing: dict[str, Skill] = {}
        for req in role.requirements:
            if not req.have:
                continue
            skill = _skill_for(req, by_id, by_name)
            if skill is not None and skill.evidence and skill.id not in backing:
                backing[skill.id] = skill
        skills = tuple(sorted(backing.values(), key=lambda s: (-s.score, order.get(s.id, 0))))
        graded.append(_Graded(index=index, role=role, have=have, total=total, skills=skills))
    return graded


def is_credible(have: int, total: int, backed: bool) -> bool:
    """At least half the asks evidenced (rounded up) and at least one cited line behind them."""
    return total > 0 and backed and have >= math.ceil(total / 2)


def _missing(role: Role) -> list[str]:
    names = [_tidy(r.name) for r in role.requirements if not r.have]
    return [n for n in dict.fromkeys(names) if n][:REPORT_MISSING_MAX]


def _rank(g: _Graded) -> tuple[int, int, int]:
    return (-g.have, -g.role.alignment, g.index)


# ───────────────────────── the report ─────────────────────────


def compose(
    profile: Profile, choices: ReportChoices, orgs: Sequence[str]
) -> tuple[ReportContent, ReportContent]:
    """``(offered, public)``: everything the owner chooses from (hidden quotes flagged, near misses always present), and
    exactly what a visitor sees. Both are redacted by ``showOrgs``.

    While ``showOrgs`` is off and no employer name is known (``orgs`` is empty: the person has no resume blocks yet),
    nothing can be redacted, so the public page carries no quotes at all; the owner can open /resume once (which records
    their employers), or turn employer names on after reading every line. "Off" is never a promise the code cannot keep."""
    labels = {s.id: _tidy(s.label) or UNKNOWN_SOURCE for s in profile.sources}
    hidden = set(choices.hiddenQuotes)
    hide_orgs = not choices.showOrgs
    quotes_public = choices.showQuotes and (choices.showOrgs or bool(orgs))
    graded = _grade(profile)
    passing = [g for g in graded if is_credible(g.have, g.total, bool(g.skills))]
    credible = sorted(passing, key=_rank)[:REPORT_ROLES_MAX]
    # a near miss is below the bar: a role that passes it but is cut by the limit of three is not "close, not yet"
    above = {g.index for g in passing}
    near = sorted((g for g in graded if g.index not in above and g.total > 0), key=_rank)[
        :REPORT_NEAR_MISSES_MAX
    ]

    used: set[str] = set()
    roles: list[ReportRole] = []
    for g in credible:
        quotes: list[ReportQuote] = []
        for skill in g.skills:
            for ev in skill.evidence:
                if len(quotes) >= REPORT_QUOTES_PER_ROLE:
                    break
                raw = _tidy(ev.text)
                key = raw.casefold()
                if not raw or key in used or _LAYOFF.search(raw):
                    continue
                used.add(key)
                text = redact(raw, orgs) if hide_orgs else raw
                qid = quote_id(ev.sourceId, raw)
                quotes.append(
                    ReportQuote(
                        id=qid,
                        text=clip_words(text, REPORT_QUOTE_MAX),
                        source=labels.get(ev.sourceId, UNKNOWN_SOURCE),
                        hidden=qid in hidden,
                    )
                )
            if len(quotes) >= REPORT_QUOTES_PER_ROLE:
                break
        roles.append(
            ReportRole(
                id=g.role.id,
                title=_tidy(g.role.title),
                have=g.have,
                total=g.total,
                ready=g.total - g.have <= 1,
                eta=_tidy(g.role.eta),
                quotes=quotes,
                missing=_missing(g.role),
            )
        )
    near_misses = [
        ReportNearMiss(id=g.role.id, title=_tidy(g.role.title), missing=_missing(g.role)) for g in near
    ]
    areas = report_areas(profile)
    line = person_line(profile)
    name = short_name(profile.person.name)

    offered = ReportContent(
        person=ReportPerson(name=name, line=line),
        areas=areas,
        roles=roles,
        nearMisses=near_misses,
        openToWork=choices.openToWork,
    )
    public = ReportContent(
        person=ReportPerson(name=name if choices.showName else None, line=line),
        areas=areas,
        roles=[
            r.model_copy(update={"quotes": [q for q in r.quotes if not q.hidden] if quotes_public else []})
            for r in roles
        ],
        nearMisses=near_misses if choices.showNearMisses else [],
        openToWork=choices.openToWork,
    )
    return offered, public


def version_of(content: ReportContent) -> str:
    """The first 8 hex characters of the sha256 of the public content: it changes only when the page would."""
    blob = json.dumps(
        content.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


__all__ = [
    "EMPLOYER",
    "compose",
    "is_credible",
    "org_names",
    "org_variants",
    "person_line",
    "quote_id",
    "redact",
    "report_areas",
    "short_name",
    "version_of",
]
