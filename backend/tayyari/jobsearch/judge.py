"""The read: Claude Haiku checks a batch of postings against the person's evidence lines; code checks what it says.

The schema puts the citations before the status in every item (a requirement's ``quote`` and ``evidence`` before its
``status``; ``levelQuote`` before ``level``). Then code, for each posting:

    quotes      a requirement's quote must be in the posting, word for word after folding case, spacing, quote marks
                and dashes (3 to 40 words). One that is not is dropped and counted.
    evidence    a requirement read as met or partly met must cite at least one evidence id that exists; otherwise it is
                dropped (it is neither shown as met nor as a gap: we cannot tell).
    level       from the years the posting asks for, read in code, when it states them: more than a year over the
                person's is a step up, a top of range below theirs less one year is below, else at. A "Senior" title with
                no years is a step up for someone under four years. Otherwise the reader's level counts only with a
                quote that checks out; else "not stated".
    years       a requirement that states years of experience is skipped: the level reads it, not the evidence.
    dropped     a posting read as another kind of role, as far above the person's level, with nothing of the person's
                evidence met, or with text aimed at an AI is not shown. Text aimed at an AI counts when the reader quotes
                it and the quote is in the posting, or when code finds a phrase it knows; a reader's word alone does not.
    the word    strong: the person's kind or next role, at (or no stated) level, three or more requirements checked, at
                least three in four met (a partly met counts half), at most one gap. good: at least half met and not a
                step up. Otherwise a stretch. Never a number on screen.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from tayyari.contract.jobsearch import AiFitWord, AskMet, LevelWord
from tayyari.discover.textutil import YearsAsked

from .brief import Evidence
from .narrow import SENIOR_STEP_YEARS, senior_title

MAX_REQUIREMENTS: Final = 6
MAX_MET_SHOWN: Final = 3
MAX_GAPS_SHOWN: Final = 3
QUOTE_MIN_WORDS: Final = 3
QUOTE_MAX_WORDS: Final = 40
LEVEL_QUOTE_MAX_WORDS: Final = 25
STRONG_SHARE: Final = 0.75
GOOD_SHARE: Final = 0.5
STRONG_MIN_CHECKED: Final = 3

Status = Literal["met", "partly", "not_shown"]
Kind = Literal["same", "next", "adjacent", "different"]
ReaderLevel = Literal["below", "at", "step_up", "far_above", "unclear"]
DropReason = Literal["missing", "different", "far_above", "nothing_met", "instructions"]

JUDGE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["postings"],
    "properties": {
        "postings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "requirements", "levelQuote", "level", "kind", "instructionQuote"],
                "properties": {
                    "id": {"type": "string"},
                    "requirements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["quote", "evidence", "status"],
                            "properties": {
                                "quote": {"type": "string"},
                                "evidence": {"type": "array", "items": {"type": "string"}},
                                "status": {"type": "string", "enum": ["met", "partly", "not_shown"]},
                            },
                        },
                    },
                    "levelQuote": {"type": "string"},
                    "level": {"type": "string", "enum": ["below", "at", "step_up", "far_above", "unclear"]},
                    "kind": {"type": "string", "enum": ["same", "next", "adjacent", "different"]},
                    "instructionQuote": {"type": "string"},
                },
            },
        }
    },
}

_FOLD = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "‟": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "•": " ", "·": " ", " ": " ",
    }
)  # fmt: skip
_SPACE = re.compile(r"\s+")
_EDGE = re.compile(r"^[\s\-*•:;,.]+|[\s\-*•:;,.]+$")
_INJECTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts?)"
    r"|disregard\s+(?:all\s+|the\s+)?(?:previous|prior|above)\s+(?:instructions|prompts?|text)"
    r"|if\s+you\s+are\s+an?\s+(?:ai|llm|language\s+model|large\s+language\s+model|chatbot|ai\s+assistant)\b"
    r"|(?:ai|llm|language\s+models?)\s+(?:reading|screening|reviewing|parsing)\s+this"
    r"|rate\s+(?:this|the)\s+(?:candidate|applicant|resume)\s+(?:as\s+)?(?:highly|high|a\s+(?:strong|perfect))",
    re.IGNORECASE,
)
"""Phrases that only make sense aimed at an AI. Narrow on purpose: "as an AI engineer" or "OpenAI, Anthropic" in a list of
tools are ordinary posting text."""
_YEARS_REQUIREMENT = re.compile(
    r"\b\d{1,2}\s*(?:\+|(?:-|–|to)\s*\d{1,2}\s*\+?)?\s*(?:years?|yrs?)\b", re.IGNORECASE
)


def fold(text: str) -> str:
    """Text compared for quotes: NFKC, lower case, one kind of quote mark and dash, single spaces."""
    text = unicodedata.normalize("NFKC", text).translate(_FOLD).lower()
    return _SPACE.sub(" ", text).strip()


def _clean_quote(quote: object) -> str:
    return _EDGE.sub("", quote.strip()) if isinstance(quote, str) else ""


def quoted(quote: str, folded_text: str, *, max_words: int = QUOTE_MAX_WORDS) -> bool:
    """Whether ``quote`` is a passage of the posting (``folded_text`` is `fold` of it) of an acceptable length."""
    words = quote.split()
    if not (QUOTE_MIN_WORDS <= len(words) <= max_words):
        return False
    return fold(quote) in folded_text


def has_injection(text: str) -> bool:
    """Whether a posting carries a phrase that only makes sense aimed at an AI (see `_INJECTION`)."""
    return _INJECTION.search(text) is not None


def code_level(asked: YearsAsked | None, years: float | None, title: str = "") -> LevelWord | None:
    """The level from the years a posting asks for, when both sides are known (see the module docstring). A "Senior" title
    that states no years is a step up for someone with under `SENIOR_STEP_YEARS`."""
    if years is None:
        return None
    if asked is None:
        return "step_up" if senior_title(title) and years < SENIOR_STEP_YEARS else None
    if asked.minimum > years + 1:
        return "step_up"
    if asked.upper is not None and asked.upper < years - 1:
        return "below"
    return "at"


@dataclass(slots=True)
class Verdict:
    """One posting after the checks: what is shown, or why it is not."""

    met: list[AskMet] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    level: LevelWord = "unclear"
    level_quote: str | None = None
    fit: AiFitWord | None = None
    dropped: DropReason | None = None
    quotes_dropped: int = 0
    credit: float = 0.0
    checked: int = 0


def verify(
    item: Mapping[str, Any] | None,
    *,
    text: str,
    evidence: Mapping[str, Evidence],
    asked: YearsAsked | None,
    years: float | None,
    title: str = "",
) -> Verdict:
    """Check one posting's read against the posting's text and the evidence lines, and work out its fit word."""
    verdict = Verdict()
    if item is None:
        verdict.dropped = "missing"
        return verdict
    folded = fold(text)
    instruction = _clean_quote(item.get("instructionQuote"))
    flagged = (
        bool(instruction) and len(instruction.split()) >= QUOTE_MIN_WORDS and fold(instruction) in folded
    )
    if flagged or has_injection(text):
        verdict.dropped = "instructions"
        return verdict
    met = partly = 0
    shown: list[AskMet] = []
    partial: list[AskMet] = []
    raw = item.get("requirements")
    for req in (raw if isinstance(raw, list) else [])[:MAX_REQUIREMENTS]:
        if not isinstance(req, Mapping):
            continue
        quote = _clean_quote(req.get("quote"))
        if not quoted(quote, folded):
            verdict.quotes_dropped += 1
            continue
        if _YEARS_REQUIREMENT.search(quote):
            continue
        status = req.get("status")
        if status == "not_shown":
            if quote not in verdict.gaps:
                verdict.gaps.append(quote)
            continue
        if status not in ("met", "partly"):
            continue
        ids = req.get("evidence")
        cited = [
            evidence[i]
            for i in (ids if isinstance(ids, list) else [])
            if isinstance(i, str) and i in evidence
        ]
        if not cited:
            verdict.quotes_dropped += 1
            continue
        ask = AskMet(ask=quote, cite=cited[0].cite, partly=status == "partly")
        if status == "met":
            met += 1
            shown.append(ask)
        else:
            partly += 1
            partial.append(ask)
    verdict.met = [*shown, *partial][:MAX_MET_SHOWN]
    checked = met + partly + len(verdict.gaps)
    verdict.checked = checked
    verdict.credit = met + 0.5 * partly
    verdict.gaps = verdict.gaps[:MAX_GAPS_SHOWN]

    level_quote = _clean_quote(item.get("levelQuote"))
    level_ok = (
        bool(level_quote)
        and fold(level_quote) in folded
        and len(level_quote.split()) <= LEVEL_QUOTE_MAX_WORDS
    )
    verdict.level_quote = level_quote if level_ok else None
    reader_level = item.get("level")
    from_code = code_level(asked, years, title)
    if from_code is not None:
        verdict.level = from_code
    elif level_ok and reader_level in ("below", "at", "step_up"):
        verdict.level = reader_level
    elif level_ok and reader_level == "far_above":
        verdict.dropped = "far_above"
        return verdict

    kind = item.get("kind")
    if kind == "different":
        verdict.dropped = "different"
        return verdict
    if verdict.credit == 0:
        verdict.dropped = "nothing_met"
        return verdict
    share = verdict.credit / checked
    if (
        kind in ("same", "next")
        and verdict.level in ("at", "unclear")
        and checked >= STRONG_MIN_CHECKED
        and share >= STRONG_SHARE
        and len(verdict.gaps) <= 1
    ):
        verdict.fit = "strong"
    elif share >= GOOD_SHARE and verdict.level != "step_up":
        verdict.fit = "good"
    else:
        verdict.fit = "stretch"
    return verdict


FIT_RANK: Final[dict[str, int]] = {"strong": 0, "good": 1, "stretch": 2}
LEVEL_RANK: Final[dict[str, int]] = {"at": 0, "unclear": 1, "below": 2, "step_up": 3}


def order_key(verdict: Verdict) -> tuple[int, int, float, int]:
    """Best first: the word, then the level, then the share met, then how many were met."""
    share = verdict.credit / verdict.checked if verdict.checked else 0.0
    return (
        FIT_RANK.get(verdict.fit or "", 9),
        LEVEL_RANK.get(verdict.level, 9),
        -share,
        -len(verdict.met),
    )


__all__ = [
    "JUDGE_SCHEMA",
    "Verdict",
    "code_level",
    "fold",
    "has_injection",
    "order_key",
    "quoted",
    "verify",
]
