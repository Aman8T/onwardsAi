"""Source triage: one look at the head and tail of an ingested source before a Haiku reader spends money on it.

State: ``{"source_kind", "host", "title", "text_head", "text_tail"}``. Questions:

    text_quality       choice: readable / garbled_or_code_dominated / login_or_block_wall / empty_or_placeholder
    about_person_work  noul: does the text show the work of one specific person or team
    mainly_english     noul
    page_type          choice (links and portfolios only): what kind of page this is

What the answers may do. Web pages and resumes are untrusted text and Jev is not injection hardened, so triage can
only skip a source or label it, and a skip needs a second signal:

    skip    the verdict is ``login_or_block_wall`` or ``empty_or_placeholder`` (and the runner used it), AND either
            Jev is at least 0.9 sure, or the code agrees (the text is under 400 characters, or a login/bot-wall
            pattern shows in it). A resume or the person's own notes are never skipped, and neither is a source
            with no text at all (a scanned resume has no text and is read from its PDF).
    label   ``page_type`` refines the label of a generic link ("Blog", "Publication", ...).
    notes   ``about_person_work`` false adds a note to the source and a log line; ``mainly_english`` false adds a
            log line. Neither skips anything.
"""

from __future__ import annotations

import re
from typing import Any

from ...core.limits import JEV_TRIAGE_HEAD_CHARS, JEV_TRIAGE_TAIL_CHARS
from ...llm.jev import JevQuestion
from ..service import TriageInput, TriageResult
from ..spec import DecisionConfig, DecisionPack, DecisionSpec, Fallback, PackResult
from .common import choice_label, confidence_of, head, noul_criteria, noul_yes, used_value

PACK_ID = "triage"
VERSION = "1"

TEXT_QUALITY_ID = "triage.text_quality"
ABOUT_PERSON_ID = "triage.about_person_work"
MAINLY_ENGLISH_ID = "triage.mainly_english"
PAGE_TYPE_ID = "triage.page_type"

READABLE = "readable"
GARBLED = "garbled_or_code_dominated"
WALL = "login_or_block_wall"
EMPTY = "empty_or_placeholder"
SKIP_VERDICTS = frozenset({WALL, EMPTY})

SKIP_CONFIDENCE = 0.9
"""Jev alone may skip a source only when it is at least this sure; below it the code has to agree."""
SHORT_TEXT_CHARS = 400
"""Under this many characters the code agrees with "wall" or "empty": too little to read anyway."""
WALL_SCAN_MAX_CHARS = 8000
"""A wall pattern counts only on a page this short: a long portfolio with a "sign in" link is not a wall."""
LABEL_MIN_CONFIDENCE = 0.5
ABOUT_PERSON_MIN_CONFIDENCE = 0.7
ENGLISH_MIN_CONFIDENCE = 0.8

NEVER_SKIP_KINDS = frozenset({"resume", "notes"})
"""The person's own text: never skipped, and only the language is checked."""
PAGE_TYPE_KINDS = frozenset({"link", "portfolio"})
TITLE_MAX_CHARS = 300

PAGE_TYPE_OPTIONS: dict[str, str] = {
    "cv_or_resume": "A CV or resume page",
    "portfolio_or_case_studies": "A portfolio or a set of case studies",
    "blog_or_articles": "A blog or articles the person wrote",
    "publication_or_talk": "A publication, paper, talk or presentation",
    "code_or_package_page": "A code repository or package page",
    "product_or_app_page": "A page for a product or app the person made",
    "profile_page": "A profile page on some platform",
    "company_or_team_page": "A company or team page",
    "other": "None of the above",
}
PAGE_LABELS: dict[str, str] = {
    "cv_or_resume": "Resume",
    "portfolio_or_case_studies": "Portfolio",
    "blog_or_articles": "Blog",
    "publication_or_talk": "Publication",
    "code_or_package_page": "Code",
    "product_or_app_page": "Product",
    "profile_page": "Profile",
    "company_or_team_page": "Company",
}
"""``other`` has no entry: it never relabels a source."""

SKIP_REASONS: dict[str, str] = {
    WALL: "This page is behind a login or a bot check, so it could not be read. Try a public link, or paste what matters as a note.",
    EMPTY: "This page has no real content yet (it looks empty or like a placeholder), so there was nothing to read.",
}
NOT_PERSON_NOTE = "This page does not seem to show one person's or team's own work."

_WALL = re.compile(
    r"(?:sign|log)[ -]?in (?:to|with) (?:continue|view|see|access|read)"
    r"|please (?:sign|log)[ -]?in"
    r"|you (?:must|need to) be (?:signed|logged)[ -]?in"
    r"|(?:sign|log)[ -]?in (?:or|and) (?:sign|log)[ -]?up"
    r"|join (?:now )?to (?:view|see|continue)"
    r"|create (?:a )?(?:free )?account to (?:view|see|continue)"
    r"|enable javascript|javascript is (?:disabled|required)"
    r"|verify (?:that )?you are (?:a )?human|are you a robot|checking your browser"
    r"|just a moment\.\.\.|attention required"
    r"|access denied|403 forbidden|captcha|cloudflare ray id|authwall"
    r"|this (?:content|page|profile) is (?:not available|private|unavailable)"
    r"|page not found|error 404|404 not found",
    re.IGNORECASE,
)


# ───────────────────────── specs ─────────────────────────


def _config(threshold: float, fallback: Fallback = "claude") -> DecisionConfig:
    return DecisionConfig(mode="primary", threshold=threshold, fallback=fallback, timeout_s=2.5)


TEXT_QUALITY_SPEC: DecisionSpec[str | None] = DecisionSpec(
    id=TEXT_QUALITY_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.choice(
        "What is the condition of this extracted text?",
        {
            READABLE: "Normal readable text about something",
            GARBLED: "Garbled characters, or mostly code, markup or boilerplate with little prose",
            WALL: "A login prompt, a bot check, a cookie or paywall screen or an access-denied message instead of the real content",
            EMPTY: "Nothing meaningful: blank, a placeholder, an error page or 'coming soon'",
        },
    ),
    interpret=choice_label,
    neutral=None,
    default=_config(0.6),
)

ABOUT_PERSON_SPEC: DecisionSpec[bool | None] = DecisionSpec(
    id=ABOUT_PERSON_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.noul(
        "Does this text show the work, experience, skills or achievements of one specific person or team?",
        noul_criteria(
            "It shows what one particular person or team did or can do",
            "It is generic, about something else, or about no one in particular",
        ),
    ),
    interpret=noul_yes,
    neutral=None,
    default=_config(ABOUT_PERSON_MIN_CONFIDENCE),
)

MAINLY_ENGLISH_SPEC: DecisionSpec[bool | None] = DecisionSpec(
    id=MAINLY_ENGLISH_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.noul("Is this text mainly written in English?"),
    interpret=noul_yes,
    neutral=None,
    default=_config(ENGLISH_MIN_CONFIDENCE, "skip"),
)

PAGE_TYPE_SPEC: DecisionSpec[str | None] = DecisionSpec(
    id=PAGE_TYPE_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.choice("What type of page is this?", PAGE_TYPE_OPTIONS),
    interpret=choice_label,
    neutral=None,
    default=_config(LABEL_MIN_CONFIDENCE),
)


def split_text(text: str) -> tuple[str, str]:
    """The head (first `JEV_TRIAGE_HEAD_CHARS`) and the tail (last `JEV_TRIAGE_TAIL_CHARS`) of ``text``, never overlapping."""
    body = text.strip()
    first = body[:JEV_TRIAGE_HEAD_CHARS]
    rest = body[JEV_TRIAGE_HEAD_CHARS:]
    return first, rest[-JEV_TRIAGE_TAIL_CHARS:] if rest else ""


def build_state(inp: TriageInput) -> dict[str, Any]:
    first, last = split_text(inp.text)
    return {
        "source_kind": inp.kind,
        "host": inp.host or "",
        "title": head(inp.title, TITLE_MAX_CHARS),
        "text_head": first,
        "text_tail": last,
    }


TRIAGE_PACK: DecisionPack[TriageInput] = DecisionPack(
    id=PACK_ID,
    specs=(TEXT_QUALITY_SPEC, ABOUT_PERSON_SPEC, MAINLY_ENGLISH_SPEC, PAGE_TYPE_SPEC),
    build_state=build_state,
)


def spec_ids_to_ask(kind: str) -> list[str]:
    """What to ask about a source of ``kind``: a resume or notes only get the language check, a link or portfolio
    also gets the page type, the rest the quality, the person and the language."""
    if kind in NEVER_SKIP_KINDS:
        return [MAINLY_ENGLISH_ID]
    ids = [TEXT_QUALITY_ID, ABOUT_PERSON_ID, MAINLY_ENGLISH_ID]
    if kind in PAGE_TYPE_KINDS:
        ids.append(PAGE_TYPE_ID)
    return ids


# ───────────────────────── code checks ─────────────────────────


def code_agrees_unreadable(text: str) -> bool:
    """The code's own opinion: too short to read (under 400 characters), or a login/bot-wall pattern on a short page."""
    body = text.strip()
    if len(body) < SHORT_TEXT_CHARS:
        return True
    if len(body) > WALL_SCAN_MAX_CHARS:
        return False
    first, last = split_text(body)
    return _WALL.search(first) is not None or _WALL.search(last) is not None


# ───────────────────────── interpretation ─────────────────────────


def _where(inp: TriageInput) -> str:
    return inp.host or {"link": "This link", "portfolio": "The portfolio"}.get(inp.kind, "This source")


def interpret_triage(inp: TriageInput, results: PackResult) -> TriageResult:
    """The `TriageResult` for ``inp`` from the pack's answers (see the module docstring for the rules)."""
    quality = used_value(results, TEXT_QUALITY_ID)
    quality_confidence = confidence_of(results, TEXT_QUALITY_ID)
    english = used_value(results, MAINLY_ENGLISH_ID, min_confidence=ENGLISH_MIN_CONFIDENCE)
    about_person = used_value(results, ABOUT_PERSON_ID, min_confidence=ABOUT_PERSON_MIN_CONFIDENCE)
    page_type = used_value(results, PAGE_TYPE_ID, min_confidence=LABEL_MIN_CONFIDENCE)
    where = _where(inp)

    skip = False
    if (
        quality in SKIP_VERDICTS
        and inp.kind not in NEVER_SKIP_KINDS
        and inp.text.strip()
        and ((quality_confidence or 0.0) >= SKIP_CONFIDENCE or code_agrees_unreadable(inp.text))
    ):
        skip = True

    notes: list[str] = []
    log_lines: list[str] = []
    if skip:
        log_lines.append(
            f"{where} sits behind a login or bot check, skipping it"
            if quality == WALL
            else f"{where} has no real content, skipping it"
        )
    else:
        if about_person is False:
            notes.append(NOT_PERSON_NOTE)
            log_lines.append(f"{where} does not look like personal work, reading it anyway")
        if english is False:
            log_lines.append(f"{where} is mostly not in English, reading it anyway")

    label = PAGE_LABELS.get(page_type) if isinstance(page_type, str) and not skip else None
    return TriageResult(
        skip=skip,
        skip_reason=SKIP_REASONS[quality] if skip and isinstance(quality, str) else None,
        label=label,
        notes=notes,
        log_lines=log_lines,
        text_quality=quality if isinstance(quality, str) else None,
        english=english if isinstance(english, bool) else None,
    )


__all__ = [
    "ABOUT_PERSON_ID",
    "ABOUT_PERSON_MIN_CONFIDENCE",
    "ABOUT_PERSON_SPEC",
    "EMPTY",
    "ENGLISH_MIN_CONFIDENCE",
    "GARBLED",
    "LABEL_MIN_CONFIDENCE",
    "MAINLY_ENGLISH_ID",
    "MAINLY_ENGLISH_SPEC",
    "NEVER_SKIP_KINDS",
    "NOT_PERSON_NOTE",
    "PACK_ID",
    "PAGE_LABELS",
    "PAGE_TYPE_ID",
    "PAGE_TYPE_KINDS",
    "PAGE_TYPE_OPTIONS",
    "PAGE_TYPE_SPEC",
    "READABLE",
    "SHORT_TEXT_CHARS",
    "SKIP_CONFIDENCE",
    "SKIP_REASONS",
    "SKIP_VERDICTS",
    "TEXT_QUALITY_ID",
    "TEXT_QUALITY_SPEC",
    "TRIAGE_PACK",
    "VERSION",
    "WALL",
    "WALL_SCAN_MAX_CHARS",
    "build_state",
    "code_agrees_unreadable",
    "interpret_triage",
    "spec_ids_to_ask",
    "split_text",
]
