"""The first read: what Jev makes of a resume within a second or two, shown on the analysis screen while the Haiku
readers are still working.

State: ``{"resume": <text, at most JEV_FIRST_READ_RESUME_CHARS>, "notes": <optional>}``. One Jev call carries every
question below. The decision is display only: the answers become three to five short log lines and a few chips, and
are stored (``FirstReadResult.facts``) for comparison with the Sonnet profile. Nothing here reaches Sonnet.

Questions (field neutral, so a nurse or a welder reads as well as a developer):

    career_family   choice over 18 professional families, each with example roles
    career_stage    choice over 7 stages, described by situation (no year counts)
    depth           6-level score: how deep the demonstrated skill is, described by situations, not degrees
    nouls           leads_or_mentors, public_proof, measured_outcomes, ai_experience, progression_promoted,
                    progression_scope, progression_switch, and resume_like (a sanity gate)

Jev is never asked to count years or work with dates: the Haiku reader and the code own those.

Interpretation (all in code): a line or chip needs the answer to be used by the runner (above its threshold) and,
for a yes or no, ``|2p-1| >= 0.6``. ``resume_like`` answered "no" with that confidence silences the whole read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...core.limits import JEV_FIRST_READ_RESUME_CHARS, SYNTH_NOTES_CLIP_CHARS
from ...core.text import clip_words
from ...llm.jev import JevQuestion
from ..service import FirstReadInput, FirstReadResult
from ..spec import DecisionConfig, DecisionPack, DecisionSpec, PackResult
from .common import (
    SIGNAL_MAX_CHARS,
    choice_label,
    confidence_of,
    head,
    noul_criteria,
    noul_yes,
    score_level,
    used_value,
)

PACK_ID = "first_read"
VERSION = "2"
"""Bumped from the design notes' wording: the questions were tightened (criteria added, depth rules spelled out)."""

CAREER_FAMILY_ID = "first_read.career_family"
CAREER_STAGE_ID = "first_read.career_stage"
DEPTH_ID = "first_read.depth"
RESUME_LIKE_ID = "first_read.resume_like"

SIGNAL_MIN_CONFIDENCE = 0.6
"""A yes or no becomes a line or chip only at ``|2p-1| >= 0.6`` (p <= 0.2 or p >= 0.8)."""
MAX_LINES = 5
UNKNOWN_FAMILY = "other_or_unclear"
UNKNOWN_STAGE = "unclear"


# ───────────────────────── the options ─────────────────────────


@dataclass(frozen=True, slots=True)
class _Option:
    label: str
    display: str
    description: str


FAMILIES: tuple[_Option, ...] = (
    _Option(
        "software_engineering",
        "Software engineering",
        "Software or web developer, mobile engineer, DevOps or platform engineer, QA engineer, embedded software developer",
    ),
    _Option(
        "data_ai",
        "Data and AI",
        "Data analyst, data scientist, machine learning engineer, analytics engineer, statistician, BI developer",
    ),
    _Option(
        "product_management",
        "Product management",
        "Product manager, product owner, program manager, technical project manager, business analyst",
    ),
    _Option(
        "design_ux",
        "Design and UX",
        "UX or UI designer, product designer, graphic or visual designer, interaction designer, user researcher",
    ),
    _Option(
        "marketing_growth",
        "Marketing and growth",
        "Marketer, growth manager, SEO or content marketer, brand manager, social media manager, PR specialist",
    ),
    _Option(
        "sales_bd",
        "Sales and business development",
        "Sales executive, account executive, account manager, business developer, sales engineer, partnerships",
    ),
    _Option(
        "operations_supply",
        "Operations and supply chain",
        "Operations manager, supply chain or logistics planner, procurement, project coordinator, facilities manager",
    ),
    _Option(
        "finance_accounting",
        "Finance and accounting",
        "Accountant, financial analyst, auditor, banker, investment or risk analyst, tax specialist",
    ),
    _Option(
        "healthcare_clinical",
        "Healthcare",
        "Doctor, nurse, pharmacist, therapist, dentist, lab technician, public health worker, caregiver",
    ),
    _Option(
        "education_training",
        "Education and training",
        "Teacher, lecturer, tutor, curriculum designer, corporate trainer, academic advisor",
    ),
    _Option(
        "legal_compliance",
        "Legal and compliance",
        "Lawyer, paralegal, compliance officer, contracts manager, policy analyst",
    ),
    _Option(
        "engineering_physical",
        "Engineering and architecture",
        "Mechanical, civil, electrical or chemical engineer, architect, manufacturing or process engineer",
    ),
    _Option(
        "research_science",
        "Research and science",
        "Researcher, laboratory scientist, PhD student or postdoc, R&D specialist, field scientist",
    ),
    _Option(
        "creative_media",
        "Creative and media",
        "Writer, editor, journalist, photographer, video producer, animator, musician, artist",
    ),
    _Option(
        "people_hr",
        "People and HR",
        "Recruiter, HR generalist, talent or people partner, learning and development, career coach",
    ),
    _Option(
        "customer_support_success",
        "Customer support and success",
        "Customer support agent, customer success manager, help desk, client services, call centre",
    ),
    _Option(
        "trades_skilled_services",
        "Skilled trades and services",
        "Electrician, plumber, technician, chef, mechanic, driver, hospitality, retail or other hands-on service work",
    ),
    _Option(
        UNKNOWN_FAMILY,
        "Mixed or unclear",
        "None of the above fits, the document is too thin to tell, or the person splits their time evenly between fields",
    ),
)

STAGES: tuple[_Option, ...] = (
    _Option(
        "student_or_new_grad",
        "student or new grad",
        "Studying, or finished studying recently, with little or no full-time work yet",
    ),
    _Option(
        "early_career",
        "early career",
        "First real roles, still learning the job and building foundations",
    ),
    _Option(
        "established",
        "established",
        "Solid, independent practitioner with a visible track record in the field",
    ),
    _Option(
        "senior_or_lead",
        "senior or lead",
        "Senior specialist, team lead, or manager of a team",
    ),
    _Option(
        "executive_or_founder",
        "executive or founder",
        "Runs a whole function or company: director level and above, or a founder",
    ),
    _Option(
        "career_changer",
        "career changer",
        "Moving into a new field, with earlier experience in a different one",
    ),
    _Option(UNKNOWN_STAGE, "unclear", "The document does not show enough to tell"),
)

FAMILY_NAMES: dict[str, str] = {o.label: o.display for o in FAMILIES}
STAGE_NAMES: dict[str, str] = {o.label: o.display for o in STAGES}

DEPTH_LEVELS: tuple[str, ...] = (
    "Nothing in the document shows professional skill, or it is impossible to tell",
    "Learning: coursework, tutorials, self-study or a first small exercise, but no real work yet",
    "Junior: does small pieces of real work with guidance from other people",
    "Competent: delivers real work from start to finish, independently",
    "Advanced: owns significant scope or a major result, with outcomes that can be measured",
    "Expert: recognised as an authority, or responsible for outcomes at the level of a whole organisation",
)
DEPTH_PHRASES: dict[int, str] = {
    1: "still learning the basics",
    2: "does real work with guidance",
    3: "owns real work end to end",
    4: "owns significant scope with results",
    5: "recognised authority or organisation-wide scope",
}
DEPTH_TOP = len(DEPTH_LEVELS) - 1


@dataclass(frozen=True, slots=True)
class _Flag:
    """One yes/no question: its wording, its criteria, and how a confident yes is shown."""

    id: str
    question: str
    true_text: str
    false_text: str
    line: str
    signal: str


FLAGS: tuple[_Flag, ...] = (
    _Flag(
        "first_read.leads_or_mentors",
        "Does the person describe leading, managing, supervising or mentoring other people?",
        "Names people or teams they led, managed, supervised or mentored",
        "Works as an individual contributor only, or leading is not mentioned",
        "Leads or mentors others",
        "Leads or mentors others",
    ),
    _Flag(
        "first_read.measured_outcomes",
        "Does the person state concrete measured results (numbers, percentages, revenue, scale, time saved)?",
        "States at least one concrete measured result",
        "Describes duties and projects without any measured result",
        "States measured results",
        "Measured results",
    ),
    _Flag(
        "first_read.progression_promoted",
        "Does the resume show a promotion or a more senior title over time?",
        "Shows a promotion, or a more senior title than an earlier one",
        "One role, the same title throughout, or no sign of promotion",
        "Promoted over time",
        "Promoted over time",
    ),
    _Flag(
        "first_read.public_proof",
        "Does the person link to or describe publicly viewable work they made (portfolio, publication, repository, product, exhibit, talk)?",
        "Links to, or clearly describes, work the public can see",
        "No public work is mentioned, or only private or employer-internal work",
        "Shares public work",
        "Public work",
    ),
    _Flag(
        "first_read.ai_experience",
        "Does the person describe hands-on work with AI or machine learning tools or systems?",
        "Describes building, training, evaluating or working with AI or machine learning tools or systems",
        "No hands-on AI or machine learning work is described",
        "Works hands-on with AI",
        "Hands-on with AI",
    ),
    _Flag(
        "first_read.progression_scope",
        "Does the resume show growing scope or responsibility over time?",
        "Later roles cover a larger team, budget, product or area than earlier ones",
        "Scope looks flat, or is not described",
        "Responsibility grows over time",
        "Growing scope",
    ),
    _Flag(
        "first_read.progression_switch",
        "Does the work history show a switch between different fields?",
        "The work history moves from one professional field to another",
        "The work history stays in one field, or is too short to show a move",
        "Has switched fields",
        "Switched fields",
    ),
)
"""In the order their lines are shown when there is room (five lines at most, the first read and depth first)."""

FLAG_IDS: tuple[str, ...] = tuple(f.id for f in FLAGS)
_FLAG_BY_ID: dict[str, _Flag] = {f.id: f for f in FLAGS}


# ───────────────────────── specs ─────────────────────────


def _config(threshold: float) -> DecisionConfig:
    """The spec's own default; ``defaults.toml`` (same values) and the env overrides sit on top of it."""
    return DecisionConfig(mode="primary", threshold=threshold, fallback="skip", timeout_s=2.5)


def _options(options: tuple[_Option, ...]) -> dict[str, str]:
    return {o.label: o.description for o in options}


CAREER_FAMILY_SPEC: DecisionSpec[str | None] = DecisionSpec(
    id=CAREER_FAMILY_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.choice(
        "Which professional field does this person mainly work in, or train for?", _options(FAMILIES)
    ),
    interpret=choice_label,
    neutral=None,
    default=_config(0.45),
)

CAREER_STAGE_SPEC: DecisionSpec[str | None] = DecisionSpec(
    id=CAREER_STAGE_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.choice("Which career stage does the document show?", _options(STAGES)),
    interpret=choice_label,
    neutral=None,
    default=_config(0.45),
)

DEPTH_SPEC: DecisionSpec[int | None] = DecisionSpec(
    id=DEPTH_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.score(
        "How deep is the professional skill this person demonstrates in their main field? "
        "Judge from the situations described (what they did, alone or with help, and what came of it), "
        "not from degrees, titles or the names of schools and employers. "
        "When torn between two levels, pick the lower one.",
        list(DEPTH_LEVELS),
    ),
    interpret=score_level,
    neutral=None,
    default=_config(0.5),
)


def _flag_spec(flag: _Flag) -> DecisionSpec[bool | None]:
    return DecisionSpec(
        id=flag.id,
        version=VERSION,
        pack=PACK_ID,
        question=JevQuestion.noul(flag.question, noul_criteria(flag.true_text, flag.false_text)),
        interpret=noul_yes,
        neutral=None,
        default=_config(SIGNAL_MIN_CONFIDENCE),
    )


RESUME_LIKE_SPEC: DecisionSpec[bool | None] = DecisionSpec(
    id=RESUME_LIKE_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.noul(
        "Does this text look like a resume or professional profile?",
        noul_criteria(
            "Reads like the resume, CV or professional profile of a person",
            "Something else: an article, code, a form, a login page or random text",
        ),
    ),
    interpret=noul_yes,
    neutral=None,
    default=_config(SIGNAL_MIN_CONFIDENCE),
)


def build_state(inp: FirstReadInput) -> dict[str, Any]:
    """``{"resume": <clipped text>, "notes": <clipped notes, only when there are some>}``."""
    state: dict[str, Any] = {"resume": head(inp.resume_text, JEV_FIRST_READ_RESUME_CHARS)}
    notes = head(inp.notes_text, SYNTH_NOTES_CLIP_CHARS)
    if notes:
        state["notes"] = notes
    return state


FIRST_READ_PACK: DecisionPack[FirstReadInput] = DecisionPack(
    id=PACK_ID,
    specs=(
        CAREER_FAMILY_SPEC,
        CAREER_STAGE_SPEC,
        DEPTH_SPEC,
        *(_flag_spec(f) for f in FLAGS),
        RESUME_LIKE_SPEC,
    ),
    build_state=build_state,
)


def spec_ids_to_ask(inp: FirstReadInput) -> list[str]:
    """Every spec id, except ``resume_like`` when there is no resume text (a notes-only input is not a resume,
    so the sanity gate would silence a read that is perfectly fine)."""
    has_resume = bool(head(inp.resume_text, 1))
    return [s.id for s in FIRST_READ_PACK.specs if has_resume or s.id != RESUME_LIKE_ID]


# ───────────────────────── interpretation ─────────────────────────


def _known(label: Any, names: dict[str, str], unknown: str) -> str | None:
    """The display name of ``label`` when it is a real, informative option."""
    if not isinstance(label, str) or label == unknown:
        return None
    return names.get(label)


def _flag_state(results: PackResult, flag_id: str) -> bool | None:
    """True or False when Jev is sure enough (``|2p-1| >= 0.6``), else None (unknown)."""
    value = used_value(results, flag_id, min_confidence=SIGNAL_MIN_CONFIDENCE)
    return value if isinstance(value, bool) else None


def interpret_first_read(results: PackResult) -> FirstReadResult | None:
    """The display lines, chips and facts of one first read; None when there is nothing worth showing.

    Lines, in order: ``First read: <family>, <stage>``; ``Depth n of 5: <situation>``; then confident yes answers
    (leads, measured results, promotion, public work, AI, scope, field switch) until five lines are used.
    """
    like = _flag_state(results, RESUME_LIKE_ID)
    if like is False:
        return None

    family_label = used_value(results, CAREER_FAMILY_ID)
    stage_label = used_value(results, CAREER_STAGE_ID)
    depth = used_value(results, DEPTH_ID)
    family = _known(family_label, FAMILY_NAMES, UNKNOWN_FAMILY)
    stage = _known(stage_label, STAGE_NAMES, UNKNOWN_STAGE)
    flags = {flag_id: _flag_state(results, flag_id) for flag_id in FLAG_IDS}

    lines: list[str] = []
    named = [part for part in (family, stage) if part]
    if named:
        lines.append(f"First read: {', '.join(named)}")
    if isinstance(depth, int) and not isinstance(depth, bool) and 1 <= depth <= DEPTH_TOP:
        lines.append(f"Depth {depth} of {DEPTH_TOP}: {DEPTH_PHRASES[depth]}")
    for flag in FLAGS:
        if len(lines) >= MAX_LINES:
            break
        if flags[flag.id] is True:
            lines.append(flag.line)
    signals = [clip_words(f.signal, SIGNAL_MAX_CHARS) for f in FLAGS if flags[f.id] is True]
    if not lines:
        return None

    confidence = {
        key: round(c, 3)
        for key in (CAREER_FAMILY_ID, CAREER_STAGE_ID, DEPTH_ID, RESUME_LIKE_ID, *FLAG_IDS)
        if (c := confidence_of(results, key)) is not None
    }
    facts: dict[str, Any] = {
        "career_family": family_label if isinstance(family_label, str) else None,
        "career_stage": stage_label if isinstance(stage_label, str) else None,
        "depth": depth if isinstance(depth, int) and not isinstance(depth, bool) else None,
        "resume_like": like,
        **{flag_id.removeprefix(f"{PACK_ID}."): state for flag_id, state in flags.items()},
        "confidence": {key.removeprefix(f"{PACK_ID}."): value for key, value in confidence.items()},
    }
    return FirstReadResult(lines=lines, signals=signals, facts=facts)


__all__ = [
    "CAREER_FAMILY_ID",
    "CAREER_FAMILY_SPEC",
    "CAREER_STAGE_ID",
    "CAREER_STAGE_SPEC",
    "DEPTH_ID",
    "DEPTH_LEVELS",
    "DEPTH_PHRASES",
    "DEPTH_SPEC",
    "FAMILIES",
    "FAMILY_NAMES",
    "FIRST_READ_PACK",
    "FLAGS",
    "FLAG_IDS",
    "MAX_LINES",
    "PACK_ID",
    "RESUME_LIKE_ID",
    "RESUME_LIKE_SPEC",
    "SIGNAL_MIN_CONFIDENCE",
    "STAGES",
    "STAGE_NAMES",
    "VERSION",
    "build_state",
    "interpret_first_read",
    "spec_ids_to_ask",
]
