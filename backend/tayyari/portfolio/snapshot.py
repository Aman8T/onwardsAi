"""Frozen views of a profile: the graph snapshot, the proof stats, the person and the "open to" lines.

No model call and no randomness: the same profile and date always give the same snapshot, and every number is
copied from the profile untouched (the parity script compares the rendered graph with this snapshot exactly).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Final

from tayyari.contract.models import Profile, SourceSummary
from tayyari.contract.portfolio import (
    GraphDomain,
    GraphMastery,
    GraphSkill,
    GraphSnapshot,
    PortfolioPerson,
    ProofStat,
)
from tayyari.contract.stored import StoredContext
from tayyari.core.text import clip_words, tidy

EVIDENCE_MAX_CHARS: Final = 220
STATS_MAX: Final = 4
OPEN_TO_MAX: Final = 2

# ───────────────────────── graph snapshot ─────────────────────────


def _score(value: int) -> int:
    return max(0, min(100, int(value)))


def graph_snapshot(profile: Profile, as_of: date) -> GraphSnapshot:
    """The Skill Graph as it is today: domains in the profile's fixed order, each with its skills strongest first
    (and the first evidence line), the skill count, the role mastery and the labels of the sources that were read."""
    by_domain: dict[str, list[GraphSkill]] = {}
    for skill in sorted(profile.skills, key=lambda s: -s.score):  # stable: ties keep the profile order
        evidence = clip_words(skill.evidence[0].text, EVIDENCE_MAX_CHARS) if skill.evidence else ""
        by_domain.setdefault(skill.domainId, []).append(
            GraphSkill(id=skill.id, name=skill.name, score=_score(skill.score), evidence=evidence or None)
        )
    domains = [
        GraphDomain(
            id=d.id,
            name=d.name,
            short=d.short,
            score=_score(d.score),
            skills=by_domain.get(d.id, []),
        )
        for d in profile.domains
    ]
    return GraphSnapshot(
        asOf=as_of.isoformat(),
        domains=domains,
        skillCount=len(profile.skills),
        mastery=GraphMastery(score=_score(profile.mastery.score), role=profile.person.currentRole),
        sources=[s.label for s in profile.sources if s.ok],
    )


# ───────────────────────── proof stats ─────────────────────────

_COUNT_UNIT = re.compile(r"^(\d[\d,]*(?:\.\d+)?\+?)\s+(.+)$")
_COUNT_ONLY = re.compile(r"^(\d[\d,]*(?:\.\d+)?\+?)$")
_GENERIC_SOURCE = frozenset(
    {"resume", "portfolio", "personal site", "website", "site", "link", "notes", "cv"}
)
_RESUME_UNITS: Final = {
    "yrs": "years of experience",
    "yr": "years of experience",
    "years": "years of experience",
    "year": "years of experience",
    "projects": "projects",
    "project": "project",
    "roles": "roles",
    "role": "role",
}
_DROP_UNITS: Final = frozenset({"page", "pages", "flows", "services"})
_PRIORITY: Final = {"stars": 0, "followers": 1, "subscribers": 1}
_LOW_PRIORITY: Final = frozenset({"languages", "language"})


def _is_zero(value: str) -> bool:
    try:
        return float(value.rstrip("+").replace(",", "")) == 0
    except ValueError:
        return False


def _source_stats(src: SourceSummary, sub: str) -> list[tuple[int, ProofStat]]:
    """(priority, stat) for every counted segment of one source's ``sub`` line ("34 repos · 9 languages")."""
    out: list[tuple[int, ProofStat]] = []
    previous = ""
    for raw in sub.split("·"):
        segment = tidy(raw)
        if not segment:
            continue
        label = ""
        value = ""
        if (m := _COUNT_UNIT.match(segment)) is not None:
            value, unit = m.group(1), m.group(2).strip()
            if src.kind == "resume":
                mapped = _RESUME_UNITS.get(unit.lower())
                label = mapped or ""
            elif unit.lower() in _DROP_UNITS:
                label = ""
            else:
                prefix = "" if src.label.strip().lower() in _GENERIC_SOURCE else f"{src.label.strip()} "
                label = f"{prefix}{unit}"
        elif _COUNT_ONLY.match(segment) and previous and src.kind != "resume":
            value, label = segment, previous  # "Conference talks · 2"
        previous = segment
        if not value or not label or _is_zero(value):
            continue
        unit_word = label.split()[-1].lower()
        priority = _PRIORITY.get(unit_word, 4 if unit_word in _LOW_PRIORITY else 2)
        out.append((priority, ProofStat(value=value, label=label, sourceId=src.id)))
    return out


def proof_stats(profile: Profile, ctx: StoredContext) -> list[ProofStat]:
    """Up to four real counts taken from the sources' own summaries (no model, nothing invented).

    Each ok source's ``sub`` is split on "·"; a segment that starts with a number becomes a stat ("34 repos"
    from the GitHub source becomes value "34", label "GitHub repos"). Stats from outside the resume come first
    (stars, followers and subscribers before repos, case studies and posts, languages last); resume counts
    (years, projects, roles) only fill the remaining places.
    """
    ranked: list[tuple[int, int, int, ProofStat]] = []
    for index, src in enumerate(profile.sources):
        if not src.ok:
            continue
        sub = src.sub or str(ctx.readers.get(src.id, {}).get("sub", ""))
        for position, (priority, stat) in enumerate(_source_stats(src, sub)):
            ranked.append((1 if src.kind == "resume" else 0, priority, index * 100 + position, stat))
    ranked.sort(key=lambda r: (r[0], r[1], r[2]))
    seen: set[tuple[str, str]] = set()
    stats: list[ProofStat] = []
    for _, _, _, stat in ranked:
        key = (stat.value, stat.label.lower())
        if key in seen:
            continue
        seen.add(key)
        stats.append(stat)
        if len(stats) == STATS_MAX:
            break
    return stats


# ───────────────────────── person ─────────────────────────


def _is_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(unicodedata.name(c, "").startswith("LATIN") for c in letters)


def _first_cluster(word: str) -> str:
    """The first letter of a word with the combining marks that follow it (a Devanagari syllable stays whole)."""
    for i, ch in enumerate(word):
        if ch.isalpha():
            j = i + 1
            while j < len(word) and unicodedata.category(word[j]).startswith("M"):
                j += 1
            cluster = word[i:j]
            while cluster and "VIRAMA" in unicodedata.name(cluster[-1], ""):
                cluster = cluster[:-1]  # a dangling half-letter is not a monogram
            return cluster
    return ""


def monogram(name: str) -> str:
    """Initials of the first two name words, upper-case; a name in another script gets its first character."""
    words = [w for w in re.split(r"[\s\-–—]+", tidy(name)) if any(c.isalpha() for c in w)]
    if not words:
        return (tidy(name)[:1] or "?").upper()
    if all(_is_latin(w) for w in words[:2]):
        return "".join(_first_cluster(w) for w in words[:2]).upper()
    return _first_cluster(words[0])


def build_person(profile: Profile) -> PortfolioPerson:
    p = profile.person
    return PortfolioPerson(
        name=p.name,
        headline=p.headline,
        role=p.currentRole,
        location=p.location,
        yearsExperience=p.yearsExperience,
        monogram=monogram(p.name),
    )


def open_to(profile: Profile) -> list[str]:
    """The two roles the person lines up with best (titles only), strongest first."""
    out: list[str] = []
    for role in sorted(profile.roles, key=lambda r: -r.alignment):
        title = tidy(role.title)
        if title and title.lower() not in {t.lower() for t in out}:
            out.append(title)
        if len(out) == OPEN_TO_MAX:
            break
    return out


__all__ = ["build_person", "graph_snapshot", "monogram", "open_to", "proof_stats"]
