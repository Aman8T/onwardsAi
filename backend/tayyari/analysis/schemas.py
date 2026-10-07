"""
Structured-output schemas for the two AI stages, as plain JSON Schema dicts.

    READER_SCHEMA – what one Haiku reader extracts from one source (server/schemas.ts ReaderOutput)
    SYNTH_SCHEMA  – what the Sonnet synthesizer returns (SynthOutput; normalised into a Profile by pipeline.py)

They are sent to the API as `output_config.format` (structured outputs), so they stay deliberately
"JSON-schema friendly": every object carries `additionalProperties: false`, there are no unions,
numeric bounds or string lengths, and the limits live in the descriptions (the model reads them).
The pipeline clamps and trims afterwards, so a slightly long answer never fails a whole run. Lists are
required (the Node version used `.default([])` for the same effect: a missing list is tolerated by the
pipeline, but the schema asks for it).
"""

from __future__ import annotations

from typing import Any

from tayyari.core import limits


def _s(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _n(description: str) -> dict[str, Any]:
    return {"type": "number", "description": description}


def _b(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _arr(items: dict[str, Any], description: str) -> dict[str, Any]:
    return {"type": "array", "items": items, "description": description}


def _obj(properties: dict[str, Any], required: list[str], description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    if description:
        schema["description"] = description
    return schema


# ───────────────────────── reader (Haiku, one per source) ─────────────────────────

READER_SIGNAL = _obj(
    {
        "label": _s(
            f'The signal itself, at most {limits.READER_SIGNAL_LABEL_MAX_CHARS} characters. E.g. "React", "Mentored 2 interns", "Supabase auth".'
        ),
        "detail": _s(
            f'Where or how much, at most {limits.READER_SIGNAL_DETAIL_MAX_CHARS} characters. E.g. "14 repos", "motion-kit", "resume", "3 case studies".'
        ),
    },
    ["label", "detail"],
)

READER_SKILL = _obj(
    {
        "name": _s(
            'Canonical skill name, short (e.g. "React", "PostgreSQL", "Accessibility", "Prompt design").'
        ),
        "evidence": _s(
            f"ONE concrete, specific sentence (at most {limits.READER_SKILL_EVIDENCE_MAX_CHARS} characters) naming the repo, project, role or page that proves it. "
            'E.g. "motion-kit: spring-physics animation library for React, 212 stars".'
        ),
        "strength": _n(
            f"Integer {limits.READER_STRENGTH_MIN}–{limits.READER_STRENGTH_MAX}. {limits.READER_STRENGTH_MIN} = only mentioned/listed; {limits.READER_STRENGTH_MIN + 1} = used once in a small or personal project; {limits.READER_STRENGTH_MIN + 2} = used repeatedly or in a real project; "
            f"{limits.READER_STRENGTH_MIN + 3} = professional depth with outcomes; {limits.READER_STRENGTH_MAX} = standout public artifact or leadership (e.g. a library with real adoption)."
        ),
    },
    ["name", "evidence", "strength"],
)

READER_PERSON = _obj(
    {
        "name": _s("Full name exactly as written in the source."),
        "currentRole": _s('Most recent job title, e.g. "Frontend Engineer". Students: e.g. "CS Student".'),
        "yearsExperience": _n(
            "Full-time professional experience in years up to today, one decimal, computed from dates (internships excluded)."
        ),
        "location": _s("City (and country if stated)."),
    },
    [],
    "Identity details, only if the source states them (mostly from a resume or profile page).",
)

READER_SCHEMA = _obj(
    {
        "sub": _s(
            f"Stat line for this source, at most {limits.READER_SUB_MAX_CHARS} characters, built from REAL counts in the source. "
            'E.g. "34 repos · 9 languages", "6 roles · 23 skills", "5 case studies", "1 shipped app · 3 flows".'
        ),
        "summary": _s("1–3 plain sentences: what this source shows about the person. No flattery."),
        "person": READER_PERSON,
        "signals": _arr(
            READER_SIGNAL,
            f"{limits.READER_SIGNALS_MAX}-12 short, scannable signals (technologies, achievements, habits) found in this source, most important first.",
        ),
        "skills": _arr(
            READER_SKILL,
            f"Up to {limits.READER_SKILLS_MAX} skills this source evidences, strongest evidence first. Only what the source actually shows.",
        ),
        "notes": _arr(
            {"type": "string"},
            f"{limits.READER_NOTES_MAX}–4 concrete observations about this source (each at most {limits.READER_NOTE_MAX_CHARS} characters), including at least one honest improvement "
            'opportunity. E.g. "Tests in only 4 of 34 repos", "Impact is described but rarely measured".',
        ),
    },
    ["sub", "summary", "signals", "skills", "notes"],
)

# ───────────────────────── synthesizer (Sonnet, once) ─────────────────────────

SYNTH_PERSON = _obj(
    {
        "name": _s('Real name if any source states it, otherwise "You".'),
        "headline": _s(
            '"<Current role> · <N.N> yrs · <City>", e.g. "Frontend Engineer · 2.4 yrs · Bengaluru". Omit unknown parts.'
        ),
        "currentRole": _s(
            f'The CURRENT role from the most recent title, without levels or grades ("Frontend Engineer II" → "Frontend Engineer"). Students: e.g. "CS Student". Max {limits.PERSON_ROLE_MAX_CHARS} characters.'
        ),
        "location": {"type": "string"},
        "yearsExperience": _n(
            "Full-time professional years up to today (internships excluded), one decimal."
        ),
        "summary": _s(
            "Two sentences: who they are professionally and what makes their profile distinctive. Grounded, no flattery."
        ),
    },
    ["name", "headline", "currentRole", "summary"],
)

SYNTH_EVIDENCE = _obj(
    {
        "sourceId": _s(
            'MUST be one of the source ids listed in the prompt (e.g. "resume", "github", "lovable-1").'
        ),
        "text": _s(
            f"Grounded proof, at most {limits.SKILL_EVIDENCE_TEXT_MAX_CHARS} characters, naming the repo / project / role. For gaps, state the observed absence "
            '(e.g. "No eval harness in any of 34 repos").'
        ),
    },
    ["sourceId", "text"],
)

SYNTH_DOMAIN = _obj(
    {
        "id": _s('kebab-case id, e.g. "frontend".'),
        "name": _s('Full domain name, e.g. "Frontend Engineering".'),
        "short": _s(f'Node label, at most {limits.DOMAIN_SHORT_MAX_CHARS} characters, e.g. "Frontend".'),
        "score": _n("Integer 0–100 (rubric in the system prompt)."),
        "summary": _s("One sentence on where they stand in this domain, grounded in evidence."),
    },
    ["id", "name", "short", "score", "summary"],
)

SYNTH_SKILL = _obj(
    {
        "id": _s('kebab-case id, unique across the graph, e.g. "react", "llm-evals".'),
        "name": _s(f"Skill name, at most {limits.SKILL_NAME_MAX_CHARS} characters."),
        "domainId": _s("id of one of the domains above."),
        "score": _n("Integer 0–100 (rubric in the system prompt). Gap skills score under 45."),
        "evidence": _arr(SYNTH_EVIDENCE, f"{limits.SKILL_EVIDENCE_MAX} evidence items, strongest first."),
        "unlocks": _arr(
            {"type": "string"},
            f'Exact titles of roles from the "roles" list that this skill unlocks or strengthens (0–{limits.SKILL_UNLOCKS_MAX}).',
        ),
    },
    ["id", "name", "domainId", "score", "evidence", "unlocks"],
)

SYNTH_REQUIREMENT = _obj(
    {
        "name": _s("Requirement name; reuse the exact skill name when it maps to a skill on the graph."),
        "skillId": _s("id of the matching skill on the graph, when there is one."),
        "have": _b("true when they already meet it well enough (current ≥ 60 and close to target)."),
        "current": _n("Integer 0–100: their level today (equal to the skill score when it maps to a skill)."),
        "target": _n("Integer 0–100: the level this role expects."),
    },
    ["name", "have", "current", "target"],
)

SYNTH_ROLE = _obj(
    {
        "id": _s('kebab-case id, e.g. "ai-product-engineer".'),
        "title": _s(f"Role title, at most {limits.ROLE_TITLE_MAX_CHARS} characters."),
        "alignment": _n("Integer 0–100: honest weighted coverage of this role's requirements today."),
        "eta": _s('Realistic time to be hireable at ~6 hrs/week, e.g. "~4 months".'),
        "why": _s(
            f"At most {limits.ROLE_WHY_MAX_CHARS} characters, grounded in their evidence: why this role, and what stands between them and it."
        ),
        "requirements": _arr(
            SYNTH_REQUIREMENT,
            f"{limits.SYNTH_REQUIREMENTS_RANGE[0]}–{limits.SYNTH_REQUIREMENTS_RANGE[1]} requirements mixing strengths they have and gaps.",
        ),
    },
    ["id", "title", "alignment", "eta", "why", "requirements"],
)

SYNTH_SOURCE = _obj(
    {
        "id": _s("Source id from the prompt."),
        "sub": _s(
            f'Refined stat line, at most {limits.PROFILE_SOURCE_SUB_MAX_CHARS} characters, real counts only, e.g. "34 repos · 9 languages".'
        ),
        "notes": _arr(
            {"type": "string"},
            f"{limits.PROFILE_SOURCE_NOTES_MAX}–3 concrete notes (each at most {limits.PROFILE_SOURCE_NOTE_MAX_CHARS} characters); at least one is an improvement opportunity for this source.",
        ),
    },
    ["id", "sub", "notes"],
)

SYNTH_SCHEMA = _obj(
    {
        "person": SYNTH_PERSON,
        "mastery": _obj(
            {
                "score": _n("Integer 0–100: how complete they are for their CURRENT role at their level."),
                "insight": _s(
                    f"At most {limits.MASTERY_INSIGHT_MAX_CHARS} characters: the one thing that defines their standing, e.g. strongest asset vs biggest drag."
                ),
            },
            ["score", "insight"],
        ),
        "domains": _arr(
            SYNTH_DOMAIN,
            f"{limits.SYNTH_DOMAINS_RANGE[0]}–{limits.SYNTH_DOMAINS_RANGE[1]} domains, ordered by relevance to the current role.",
        ),
        "skills": _arr(
            SYNTH_SKILL,
            f"{limits.SYNTH_SKILLS_RANGE[0]}–{limits.SYNTH_SKILLS_RANGE[1]} skills, 2–5 per domain, including {limits.SYNTH_GAP_SKILLS_RANGE[0]}–{limits.SYNTH_GAP_SKILLS_RANGE[1]} gap skills (score < 45).",
        ),
        "roles": _arr(
            SYNTH_ROLE,
            f"The {limits.SYNTH_ROLES_RANGE[0]}–{limits.SYNTH_ROLES_RANGE[1]} most probable NEXT roles.",
        ),
        "sources": _arr(SYNTH_SOURCE, "One entry per successfully read source."),
    },
    ["person", "mastery", "domains", "skills", "roles", "sources"],
)
