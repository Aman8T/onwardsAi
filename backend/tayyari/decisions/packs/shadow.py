"""Shadow decisions: Jev's own numbers for skills, requirements and roles, logged next to Sonnet's, never used.

They run after synthesis and exist so a better Jev can be measured against the incumbent before it takes the work:
the log row of each question carries the incumbent value (``incumbent``) and Jev's interpreted answer
(``extra["value"]``: the level, its mapped score and its band), so agreement is a join away.

State ``whole``: the reader outputs of every source plus the (clipped) resume text, one state for every question,
batched into as few Jev calls as the question limit allows. (``windowed`` per-skill excerpts is the other strategy
in the design notes; retrieval for abstract skill names is weak, so ``whole`` is the default.)

Questions (all bound, so a question key carries what it is about):

    skill.level#<skillId>                 score, 6 levels: how strong is the evidence for the skill
    requirement.have#<roleId>#<n>         noul: does the person already meet requirement n of the role
    role.closest#all                      choice over the role ids ("title: why"), option order seeded by the profile id
    role.fit#<roleId>                     score, 6 levels: how ready for the role

Levels become 0-100 scores with `LEVEL_TO_SCORE` (0 to 5, 20, 38, 58, 78, 92) and 5 bands with `band_of` (under 40,
40 to 59, 60 to 74, 75 to 89, 90 and up: Sonnet's own rubric bands).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...contract import Profile, Role
from ...core.hashing import sha256_hex
from ...core.limits import (
    JEV_CHOICE_DESCRIPTION_MAX_CHARS,
    JEV_FIRST_READ_RESUME_CHARS,
    PROFILE_ROLES_MAX,
    PROFILE_SKILLS_MAX,
    ROLE_REQUIREMENTS_MAX,
)
from ...core.text import clip_words
from ...llm.jev import JevAnswer, JevQuestion
from ..service import ShadowMaterials
from ..spec import DecisionConfig, DecisionPack, DecisionSpec, question_key
from .common import choice_label, head, noul_yes

PACK_ID = "shadow"
VERSION = "1"

SKILL_LEVEL_ID = "skill.level"
REQUIREMENT_HAVE_ID = "requirement.have"
ROLE_CLOSEST_ID = "role.closest"
ROLE_FIT_ID = "role.fit"
CLOSEST_INSTANCE = "all"

LEVEL_TO_SCORE: tuple[int, ...] = (5, 20, 38, 58, 78, 92)
"""Jev level (0 to 5) to the 0-100 scale of Sonnet's skill scores and role alignments."""
BAND_EDGES: tuple[int, ...] = (40, 60, 75, 90)
"""A score is in band 0 below 40, 1 below 60, 2 below 75, 3 below 90 and 4 from 90 up."""
MAX_STATE_CHARS = 90_000
"""The whole state stays well under Jev's per-request limit (32k tokens for state and the longest question)."""
NAME_MAX_CHARS = 120

SKILL_LEVELS: tuple[str, ...] = (
    "None: nothing in the material shows this skill",
    "Named only: the skill is listed or mentioned, with nothing showing it was used",
    "Used once, or in a small, academic or personal setting",
    "Used repeatedly, or in real work",
    "Owned or led it, with concrete outcomes",
    "Standout: recognised by others, or adopted widely",
)
FIT_LEVELS: tuple[str, ...] = (
    "Not at all: nothing in the background matches what the role needs",
    "Far away: a little related background, most of the role would be new",
    "Some way off: several major gaps remain",
    "Getting there: one or two real gaps remain",
    "Close: ready after one small step",
    "Ready now: could be hired for the role today",
)
SKILL_QUESTION = (
    'How strong is the evidence that this person has used or mastered the skill named in "skill"?'
)
REQUIREMENT_QUESTION = 'Does the person already meet the requirement named in "requirement" at working level, for the role in "role"?'
FIT_QUESTION = 'How close is the person to being ready to be hired for the role named in "role" today?'
CLOSEST_QUESTION = "Which of these next roles is the closest fit for the person today?"


def band_of(score: float) -> int:
    """The band (0 to 4) of a 0-100 score, by Sonnet's rubric edges."""
    return sum(1 for edge in BAND_EDGES if score >= edge)


@dataclass(frozen=True, slots=True)
class LevelValue:
    """An interpreted 6-level answer: the level, the score it maps to, its band and the probability-weighted level."""

    level: int
    score: int
    band: int
    expected: float | None


def interpret_level(answer: JevAnswer) -> LevelValue | None:
    level = answer.score_level
    if level is None or not 0 <= level < len(LEVEL_TO_SCORE):
        return None
    score = LEVEL_TO_SCORE[level]
    expected = None if answer.score_expected is None else round(answer.score_expected, 3)
    return LevelValue(level=level, score=score, band=band_of(score), expected=expected)


def _config() -> DecisionConfig:
    """Shadow, with threshold 1.0: promoting one of these to primary by mistake sends every answer to its fallback."""
    return DecisionConfig(mode="shadow", threshold=1.0, fallback="skip", timeout_s=8.0)


def _name(args: Mapping[str, Any], key: str) -> str:
    return clip_words(args.get(key, ""), NAME_MAX_CHARS)


def _bind_skill(args: Mapping[str, Any]) -> JevQuestion:
    return JevQuestion.score({"question": SKILL_QUESTION, "skill": _name(args, "skill")}, list(SKILL_LEVELS))


def _bind_requirement(args: Mapping[str, Any]) -> JevQuestion:
    return JevQuestion.noul(
        {
            "question": REQUIREMENT_QUESTION,
            "requirement": _name(args, "requirement"),
            "role": _name(args, "role"),
        }
    )


def _bind_fit(args: Mapping[str, Any]) -> JevQuestion:
    return JevQuestion.score({"question": FIT_QUESTION, "role": _name(args, "role")}, list(FIT_LEVELS))


def _bind_closest(args: Mapping[str, Any]) -> JevQuestion:
    """``args["roles"]`` is a list of ``{"id", "title", "why"}`` already in the order to present them."""
    options: dict[str, str] = {}
    for role in args["roles"]:
        label = str(role["id"])
        if label and label not in options:
            description = f"{role['title']}: {role['why']}" if role.get("why") else str(role["title"])
            options[label] = clip_words(description, JEV_CHOICE_DESCRIPTION_MAX_CHARS)
    return JevQuestion.choice(CLOSEST_QUESTION, options)


SKILL_LEVEL_SPEC: DecisionSpec[LevelValue | None] = DecisionSpec(
    id=SKILL_LEVEL_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.score({"question": SKILL_QUESTION, "skill": "the named skill"}, list(SKILL_LEVELS)),
    bind=_bind_skill,
    interpret=interpret_level,
    neutral=None,
    default=_config(),
)
REQUIREMENT_HAVE_SPEC: DecisionSpec[bool | None] = DecisionSpec(
    id=REQUIREMENT_HAVE_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.noul(
        {"question": REQUIREMENT_QUESTION, "requirement": "the named requirement", "role": "the named role"}
    ),
    bind=_bind_requirement,
    interpret=noul_yes,
    neutral=None,
    default=_config(),
)
ROLE_CLOSEST_SPEC: DecisionSpec[str | None] = DecisionSpec(
    id=ROLE_CLOSEST_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.choice(CLOSEST_QUESTION, {"role-id": "title: why"}),
    bind=_bind_closest,
    interpret=choice_label,
    neutral=None,
    default=_config(),
)
ROLE_FIT_SPEC: DecisionSpec[LevelValue | None] = DecisionSpec(
    id=ROLE_FIT_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.score({"question": FIT_QUESTION, "role": "the named role"}, list(FIT_LEVELS)),
    bind=_bind_fit,
    interpret=interpret_level,
    neutral=None,
    default=_config(),
)


def build_state(materials: ShadowMaterials) -> dict[str, Any]:
    """``{"readers": {source id: reader output}, "resume": <clipped text>}``, kept under `MAX_STATE_CHARS`."""
    readers = {sid: out for sid, out in materials.reader_outputs.items() if out}
    resume = head(materials.resume_text, JEV_FIRST_READ_RESUME_CHARS)
    used = len(str(readers))
    if resume and used + len(resume) > MAX_STATE_CHARS:
        resume = resume[: max(0, MAX_STATE_CHARS - used)]
    return {"readers": readers, "resume": resume}


SHADOW_PACK: DecisionPack[ShadowMaterials] = DecisionPack(
    id=PACK_ID,
    specs=(SKILL_LEVEL_SPEC, REQUIREMENT_HAVE_SPEC, ROLE_CLOSEST_SPEC, ROLE_FIT_SPEC),
    build_state=build_state,
)


# ───────────────────────── the plan: bindings and incumbents ─────────────────────────


@dataclass(frozen=True, slots=True)
class ShadowPlan:
    """What to ask (``bindings``, by question key) and what to compare it with (``incumbent``, by question key)."""

    bindings: dict[str, dict[str, Any]]
    incumbent: dict[str, dict[str, Any]]


def _instance(value: str) -> str:
    """An id as a question-key instance (the key separator is not allowed inside it)."""
    return value.replace("#", "-")


def ordered_roles(roles: Sequence[Role], profile_id: str) -> list[Role]:
    """The roles in an order seeded by the profile id: stable for one profile (so a replay asks the same question),
    different between profiles (so option-order bias averages out). No hidden RNG: sorted by a sha256 of the pair."""
    return sorted(roles, key=lambda role: sha256_hex(f"{profile_id}|{role.id}"))


def build_plan(profile: Profile) -> ShadowPlan:
    """Every shadow question for ``profile``, with Sonnet's value as the incumbent."""
    bindings: dict[str, dict[str, Any]] = {}
    incumbent: dict[str, dict[str, Any]] = {}

    for skill in profile.skills[:PROFILE_SKILLS_MAX]:
        key = question_key(SKILL_LEVEL_ID, _instance(skill.id))
        if key in bindings:
            continue
        bindings[key] = {"skill": skill.name}
        incumbent[key] = {"score": skill.score, "band": band_of(skill.score), "skill_id": skill.id}

    roles = list(profile.roles[:PROFILE_ROLES_MAX])
    for role in roles:
        role_key = _instance(role.id)
        for n, req in enumerate(role.requirements[:ROLE_REQUIREMENTS_MAX]):
            key = question_key(REQUIREMENT_HAVE_ID, f"{role_key}#{n}")
            if key in bindings:
                continue
            bindings[key] = {"requirement": req.name, "role": role.title}
            incumbent[key] = {
                "have": req.have,
                "current": req.current,
                "target": req.target,
                "skill_id": req.skillId,
            }
        fit_key = question_key(ROLE_FIT_ID, role_key)
        if fit_key not in bindings:
            bindings[fit_key] = {"role": role.title}
            incumbent[fit_key] = {"score": role.alignment, "band": band_of(role.alignment)}

    if roles:
        presented = ordered_roles(roles, profile.id)
        closest = max(roles, key=lambda role: role.alignment)
        key = question_key(ROLE_CLOSEST_ID, CLOSEST_INSTANCE)
        bindings[key] = {"roles": [{"id": r.id, "title": r.title, "why": r.why} for r in presented]}
        incumbent[key] = {
            "role_id": closest.id,
            "ranking": [r.id for r in sorted(roles, key=lambda role: -role.alignment)],
            "option_order": [r.id for r in presented],
        }
    return ShadowPlan(bindings, incumbent)


__all__ = [
    "BAND_EDGES",
    "CLOSEST_INSTANCE",
    "FIT_LEVELS",
    "LEVEL_TO_SCORE",
    "MAX_STATE_CHARS",
    "PACK_ID",
    "REQUIREMENT_HAVE_ID",
    "REQUIREMENT_HAVE_SPEC",
    "ROLE_CLOSEST_ID",
    "ROLE_CLOSEST_SPEC",
    "ROLE_FIT_ID",
    "ROLE_FIT_SPEC",
    "SHADOW_PACK",
    "SKILL_LEVELS",
    "SKILL_LEVEL_ID",
    "SKILL_LEVEL_SPEC",
    "VERSION",
    "LevelValue",
    "ShadowPlan",
    "band_of",
    "build_plan",
    "build_state",
    "interpret_level",
    "ordered_roles",
]
