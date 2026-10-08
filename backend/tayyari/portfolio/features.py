"""The numbers and flags the composer reads: graph shape, content counts and the shape of a career.

`compute_features` reads the `Profile` (graph, sources, person), the extracted facts and, when there is one,
Jev's first read (`FirstReadResult.facts`: ``career_stage``, ``career_family``, ``public_proof``,
``measured_outcomes``, ``leads_or_mentors``, ``progression_switch``). Every first-read key is read defensively:
a missing, null or oddly typed answer simply falls back to a profile heuristic, so the composer never depends
on Jev being present (and a wrong Jev answer only picks a different, still valid, layout).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from tayyari.contract.models import Profile
from tayyari.contract.portfolio import Fact, FactKind

from .kit import GraphShape, Lean

Stage = Literal["student", "early", "mid", "senior", "switcher"]
STAGES: Final[tuple[Stage, ...]] = ("student", "early", "mid", "senior", "switcher")

# Jev's `career_stage` options (decisions/packs/first_read.py) -> our stage.
_JEV_STAGE: Final[dict[str, Stage]] = {
    "student_or_new_grad": "student",
    "early_career": "early",
    "established": "mid",
    "senior_or_lead": "senior",
    "executive_or_founder": "senior",
    "career_changer": "switcher",
}

# Jev's `career_family` options -> what the work looks like (software engineering is refined by keywords).
_JEV_LEAN: Final[dict[str, Lean]] = {
    "data_ai": "data",
    "product_management": "people",
    "design_ux": "design",
    "marketing_growth": "words",
    "sales_bd": "people",
    "operations_supply": "systems",
    "finance_accounting": "data",
    "healthcare_clinical": "people",
    "education_training": "people",
    "legal_compliance": "words",
    "engineering_physical": "hardware",
    "research_science": "data",
    "creative_media": "words",
    "people_hr": "people",
    "customer_support_success": "people",
    "trades_skilled_services": "hardware",
}
_UNKNOWN_FAMILY: Final = "other_or_unclear"

# ───────────────────────── the feature record ─────────────────────────

SENIOR_YEARS: Final = 8.0
MID_YEARS: Final = 3.0
SENIOR_TITLE_YEARS: Final = 5.0
_SWITCH_THRESHOLD: Final = 0.7


@dataclass(frozen=True)
class Features:
    """Everything the composer scores. The defaults describe a typical mid-career profile."""

    # graph shape
    n_domains: int = 6
    n_skills: int = 18
    n_rich: int = 4
    """Domains with at least 3 skills."""
    top: int = 80
    second: int = 70
    median: float = 60.0
    has_mastery: bool = True
    n_sources: int = 2
    source_kinds: tuple[str, ...] = ("github", "resume")
    # content
    n_experience: int = 2
    n_projects: int = 2
    n_education: int = 1
    n_achievements: int = 0
    n_clips: int = 0
    n_links: int = 1
    n_quotes: int = 0
    # the career
    years_experience: float = 3.0
    stage: Stage = "mid"
    lean: Lean = "other"
    career_family: str | None = None
    """Jev's family label when a first read supplied one."""
    has_public_proof: bool = False
    writing_led: bool = False
    project_led: bool = False
    measured: bool = False
    leads: bool = False
    from_first_read: bool = False
    """True when at least one of the career answers came from Jev."""

    @property
    def shape(self) -> GraphShape:
        return GraphShape(
            n_domains=self.n_domains,
            n_skills=self.n_skills,
            n_rich=self.n_rich,
            top=self.top,
            second=self.second,
            median=self.median,
            has_mastery=self.has_mastery,
            single_source=self.n_sources <= 1,
        )

    def count(self, kind: FactKind) -> int:
        return {
            "experience": self.n_experience,
            "project": self.n_projects,
            "education": self.n_education,
            "achievement": self.n_achievements,
            "clip": self.n_clips,
            "link": self.n_links,
            "quote": self.n_quotes,
        }.get(kind, 0)


# ───────────────────────── first-read access ─────────────────────────


def _flag(first_read: Mapping[str, Any] | None, key: str) -> bool | None:
    value = first_read.get(key) if isinstance(first_read, Mapping) else None
    return value if isinstance(value, bool) else None


def _label(first_read: Mapping[str, Any] | None, key: str) -> str | None:
    value = first_read.get(key) if isinstance(first_read, Mapping) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


# ───────────────────────── dates ─────────────────────────

_YM = re.compile(r"^\s*(\d{4})(?:[-/](\d{1,2}))?")


def _ym(value: str | None) -> tuple[int, int | None] | None:
    m = _YM.match(value) if value else None
    if not m:
        return None
    month = int(m.group(2)) if m.group(2) else None
    return int(m.group(1)), month if month is not None and 1 <= month <= 12 else None


def _reference(profile: Profile) -> float:
    """The profile's date (year + month fraction) as the "now" of every duration, so results are repeatable."""
    ym = _ym(profile.createdAt)
    if ym is None:
        return 2026.0 + 9 / 12
    return ym[0] + ((ym[1] or 6) - 1) / 12


def _span(fact: Fact, now: float) -> tuple[float, float] | None:
    """(start, end) of an experience fact as fractional years, or None when it has no usable start."""
    start = _ym(fact.start)
    if start is None:
        return None
    begin = start[0] + ((start[1] - 1) / 12 if start[1] else 0.0)
    if fact.current:
        return begin, max(begin, now)
    end = _ym(fact.end)
    if end is None:
        return None
    stop = end[0] + (end[1] / 12 if end[1] else 1.0)
    return begin, max(begin, stop)


def _years_from_facts(facts: list[Fact], now: float) -> float:
    spans = [s for f in facts if f.kind == "experience" and (s := _span(f, now))]
    if not spans:
        return 0.0
    return max(0.0, round(max(e for _, e in spans) - min(b for b, _ in spans), 1))


# ───────────────────────── text heuristics ─────────────────────────

_LEAN_WORDS: Final[dict[Lean, re.Pattern[str]]] = {
    "design": re.compile(
        r"\b(design\w*|front-?end|ux|ui|motion|animation|visual\w*|graphic\w*|figma|interaction|illustrat\w*|typograph\w*)\b"
    ),
    "systems": re.compile(
        r"\b(back-?end|distributed|infra\w*|cloud|devops|platform|reliability|sre|security|database\w*|storage"
        r"|kubernetes|systems?|networking|payments?|apis?|servers?|compiler\w*)\b"
    ),
    "data": re.compile(
        r"\b(data|analytic\w*|analysis|analysts?|statistic\w*|machine learning|ml|ai|bi|sql"
        r"|visuali[sz]ation|research\w*|scientists?|forecast\w*|quantitative)\b"
    ),
    "hardware": re.compile(
        r"\b(hardware|iot|embedded|electronic\w*|ece|arduino|firmware|mechanical|civil|circuit\w*|robot\w*"
        r"|manufactur\w*|electrical|sensors?)\b"
    ),
    "words": re.compile(
        r"\b(writ\w*|content|edit\w*|copy\w*|newsletter\w*|journalis\w*|blog\w*|seo|marketing|social media"
        r"|campaign\w*|storytell\w*)\b"
    ),
    "people": re.compile(
        r"\b(teach\w*|educat\w*|tutor\w*|curriculum|training|recruit\w*|hr|people|customers?|support|sales"
        r"|coach\w*|nurs\w*|health\w*|clinic\w*|counsel\w*|hospitality)\b"
    ),
}
_MIN_LEAN_SCORE: Final = 0.6

_SWITCH_PHRASES: Final = re.compile(
    r"\b(former(?:ly)?|career[- ](?:change|switch|transition|pivot|changer)|changed careers|"
    r"switch(?:ed|ing)? (?:careers|fields|industries)|transition(?:ed|ing)? (?:into|to|from)|"
    r"pivot(?:ed|ing)? (?:into|to|from)|re-?skill(?:ed|ing)?|retrained)\b",
    re.IGNORECASE,
)
_STUDENT_WORDS: Final = re.compile(
    r"\b(student|undergrad\w*|fresher|new grad(?:uate)?|graduate student)\b", re.IGNORECASE
)
_SENIOR_TITLE: Final = re.compile(
    r"\b(staff|principal|lead|head|director|manager|architect|vp|chief|founder|cto|ceo)\b", re.IGNORECASE
)
_LEAD_WORDS: Final = re.compile(
    r"\b(led|lead|leads|leading|managed|manage|manages|mentor\w*|supervis\w*|team of|head of|coordinat\w*)\b",
    re.IGNORECASE,
)
_NUMBER: Final = re.compile(r"\d")
_LEVEL_WORDS: Final = frozenset(
    [
        "junior",
        "senior",
        "sr",
        "jr",
        "staff",
        "lead",
        "principal",
        "associate",
        "intern",
        "trainee",
        "head",
        "chief",
        "assistant",
        "graduate",
        "the",
        "and",
        "for",
        "with",
        "grade",
        "grades",
    ]
)


def _text_score(text: str, weight: float) -> dict[Lean, float]:
    low = text.lower()
    return {lean: weight for lean, pattern in _LEAN_WORDS.items() if pattern.search(low)}


def _lean_scores(profile: Profile) -> dict[Lean, float]:
    scores: dict[Lean, float] = dict.fromkeys(_LEAN_WORDS, 0.0)

    def add(text: str, weight: float) -> None:
        for lean, w in _text_score(text, weight).items():
            scores[lean] += w

    for d in profile.domains:
        add(d.name, max(0.0, min(d.score, 100)) / 100)
    for s in sorted(profile.skills, key=lambda s: -s.score)[:6]:
        add(s.name, 0.5 * max(0.0, min(s.score, 100)) / 100)
    add(profile.person.currentRole, 1.5)
    add(profile.person.headline, 0.5)
    for r in sorted(profile.roles, key=lambda r: -r.alignment)[:2]:
        add(r.title, 0.5 * max(0.0, min(r.alignment, 100)) / 100)
    return scores


def _lean(profile: Profile, career_family: str | None) -> Lean:
    scores = _lean_scores(profile)
    if career_family and career_family != _UNKNOWN_FAMILY:
        if career_family == "software_engineering":
            return "design" if scores["design"] > scores["systems"] else "systems"
        mapped = _JEV_LEAN.get(career_family)
        if mapped:
            return mapped
    best = max(_LEAN_WORDS, key=lambda lean: (scores[lean], -list(_LEAN_WORDS).index(lean)))
    return best if scores[best] >= _MIN_LEAN_SCORE else "other"


def _title_tokens(title: str | None) -> set[str]:
    words = re.findall(r"[a-z]{3,}", (title or "").lower())
    return {w[:5] for w in words if w not in _LEVEL_WORDS}


def _switch_score(profile: Profile, facts: list[Fact], now: float) -> float:
    """How strongly the history reads as a change of field (0..1.2): wording, a role that shares no words with
    any earlier one, and a real stretch of earlier work."""
    jobs = [f for f in facts if f.kind == "experience"]
    if len(jobs) < 2:
        return 0.0
    score = 0.0
    if _SWITCH_PHRASES.search(f"{profile.person.summary} {profile.person.headline} {profile.goal or ''}"):
        score += 0.5
    dated = sorted(
        ((s[0], f) for f in jobs if (s := _span(f, now))),
        key=lambda pair: -pair[0],
    )
    if len(dated) >= 2:
        latest = _title_tokens(dated[0][1].title)
        earlier = [_title_tokens(f.title) for _, f in dated[1:]]
        if latest and all(t and not (t & latest) for t in earlier):
            score += 0.4
            earlier_years = sum(max(0.0, s[1] - s[0]) for _, f in dated[1:] if (s := _span(f, now)))
            if earlier_years >= 3.0:
                score += 0.3
    return score


def _stage(
    profile: Profile,
    facts: list[Fact],
    years: float,
    now: float,
    leads: bool,
    first_read: Mapping[str, Any] | None,
) -> tuple[Stage, bool]:
    """(stage, came from Jev)."""
    label = _label(first_read, "career_stage")
    if label and label in _JEV_STAGE:
        return _JEV_STAGE[label], True
    if _flag(first_read, "progression_switch") is True and any(f.kind == "experience" for f in facts):
        return "switcher", True

    n_exp = sum(1 for f in facts if f.kind == "experience")
    education = [f for f in facts if f.kind == "education"]
    in_school = any(
        "expected" in " ".join(filter(None, (f.start, f.end, f.detail, f.title))).lower()
        or ((end := _ym(f.end)) is not None and end[0] + (end[1] / 12 if end[1] else 1.0) > now)
        for f in education
    )
    studentish = bool(_STUDENT_WORDS.search(f"{profile.person.currentRole} {profile.person.headline}"))
    if (studentish and years < 2.0) or (in_school and years < 1.5) or (n_exp == 0 and years < 0.5):
        return "student", False
    if _switch_score(profile, facts, now) >= _SWITCH_THRESHOLD:
        return "switcher", False
    senior_title = bool(_SENIOR_TITLE.search(profile.person.currentRole))
    if years >= SENIOR_YEARS or (years >= SENIOR_TITLE_YEARS and (leads or senior_title)):
        return "senior", False
    if years >= MID_YEARS:
        return "mid", False
    return "early", False


# ───────────────────────── compute ─────────────────────────


def compute_features(
    profile: Profile, facts: list[Fact], first_read: Mapping[str, Any] | None = None
) -> Features:
    """Measure a profile for the composer.

    ``first_read`` is `FirstReadResult.facts` (or None). Its answers win over the heuristics when they are
    present and well formed: ``career_stage``, ``career_family``, ``public_proof``, ``measured_outcomes``,
    ``leads_or_mentors``, ``progression_switch``.
    """
    now = _reference(profile)
    scores = sorted((d.score for d in profile.domains), reverse=True)
    top = scores[0] if scores else 0
    second = scores[1] if len(scores) > 1 else 0
    median = float(statistics.median(scores)) if scores else 0.0
    per_domain: dict[str, int] = {}
    for s in profile.skills:
        per_domain[s.domainId] = per_domain.get(s.domainId, 0) + 1
    n_rich = sum(1 for n in per_domain.values() if n >= 3)
    ok_sources = [s for s in profile.sources if s.ok]

    counts: dict[str, int] = {}
    for f in facts:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    n_exp, n_proj = counts.get("experience", 0), counts.get("project", 0)
    n_clips = counts.get("clip", 0)

    highlights = [h for f in facts if f.kind == "experience" for h in f.highlights]
    texts = highlights + [f.detail or "" for f in facts if f.kind in ("experience", "project")]
    measured = _flag(first_read, "measured_outcomes")
    if measured is None:
        measured = any(_NUMBER.search(t) for t in texts)
    leads = _flag(first_read, "leads_or_mentors")
    if leads is None:
        leads = any(_LEAD_WORDS.search(h) for h in highlights)
    public = _flag(first_read, "public_proof")
    if public is None:
        public = any(s.kind in ("github", "portfolio", "lovable", "link") for s in ok_sources)

    years = profile.person.yearsExperience
    if years is None:
        years = _years_from_facts(facts, now)
    stage, jev_stage = _stage(profile, facts, float(years), now, leads, first_read)

    family = _label(first_read, "career_family")
    if family == _UNKNOWN_FAMILY:
        family = None
    lean = _lean(profile, family)
    writing_led = (n_clips >= 2 and n_clips >= n_proj) or (lean == "words" and n_clips >= 1)
    project_led = n_proj >= 3 and n_proj >= 2 * max(1, n_exp)

    return Features(
        n_domains=len(profile.domains),
        n_skills=len(profile.skills),
        n_rich=n_rich,
        top=top,
        second=second,
        median=median,
        has_mastery=profile.mastery.score > 0,
        n_sources=len(ok_sources),
        source_kinds=tuple(sorted({s.kind for s in ok_sources})),
        n_experience=n_exp,
        n_projects=n_proj,
        n_education=counts.get("education", 0),
        n_achievements=counts.get("achievement", 0),
        n_clips=n_clips,
        n_links=counts.get("link", 0),
        n_quotes=counts.get("quote", 0),
        years_experience=float(years),
        stage=stage,
        lean=lean,
        career_family=family,
        has_public_proof=public,
        writing_led=writing_led,
        project_led=project_led,
        measured=measured,
        leads=leads,
        from_first_read=jev_stage or family is not None,
    )


__all__ = ["STAGES", "Features", "Stage", "compute_features"]
