"""Reading a job posting: one Haiku call that sees the posting and nothing else, then code that keeps only what the
posting really says.

The reader is never given a person: `read_posting` has no profile, facts or resume parameter, so a posting cannot
be bent towards (or away from) anyone, and the result is cacheable by the posting's hash alone.

What code enforces on the model's answer (each refusal is recorded in the returned `Report`):

  - a requirement's or gate's ``quote`` must be a passage of the posting after folding whitespace, quote marks
    and dashes (`text.fold_text`); a row whose quote is not is dropped;
  - ``terms`` must be passages of the quote; others are dropped;
  - ``provenance == inferred`` can never be ``must`` or ``required`` (downgraded to ``preferred``);
  - at most 30 requirements and 6 gates (the stressed ones are kept);
  - text that instructs a model ("ignore previous instructions", "rate this candidate 10/10") is found by code as
    well as by the model, listed in ``anomalies``, never followed, and any requirement or gate built from it is
    dropped;
  - web addresses are listed in ``links`` and never opened.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from tayyari.contract.match import (
    GATE_BLOCK_SEVERITY,
    MAX_GATES,
    MAX_REQUIREMENTS,
    Gate,
    Posting,
    Requirement,
    WorkMode,
    tier_weight,
)
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.calls import ask
from tayyari.match.errors import NoRequirementsFound, PostingTextError
from tayyari.match.models import Report
from tayyari.match.schemas import GateOut, PostingOut, RequirementOut, rows_of, schema_of
from tayyari.match.text import clip, fold_text, posting_hash, quote_in, tidy, urls_in
from tayyari.portfolio.ground import split_sentences, tokens_of
from tayyari.prompts.match import POSTING_SYSTEM, build_posting_prompt

MAX_POSTING_CHARS: Final = 20_000
MIN_POSTING_CHARS: Final = 60
POSTING_MAX_TOKENS: Final = 4096
POSTING_SCHEMA: Final = schema_of(PostingOut)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INJECTION: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|preceding|all|any|your)\b[^.\n]{0,30}\b(?:instructions?|prompts?|rules?|messages?|context|guidelines)\b",
        r"\b(?:system|developer)\s+(?:prompt|message|instructions?)\b",
        r"\b(?:rate|score|rank|grade|evaluate|assess|recommend|approve|shortlist|advance|hire|pass)\b[^.\n]{0,30}\b(?:this|the|every|any|each)\s+(?:candidate|applicant|resume|cv|profile|person)s?\b",
        r"\b(?:mark|label|flag|classify)\s+(?:this|the)\s+(?:candidate|applicant|resume|cv)\b",
        r"\b(?:candidate|applicant|resume|cv)\b[^.\n]{0,40}\b(?:10|ten)\s*/\s*10\b",
        r"\b(?:10|ten)\s*/\s*10\b[^.\n]{0,40}\b(?:candidate|applicant|resume|cv|score|rating)\b",
        r"\bif\s+you\s+are\s+(?:an?\s+)?(?:ai|llm|language\s+model|model|bot|assistant|chatbot|screening)\b",
        r"\b(?:ai|llm|language\s+model|chatbot|screening\s+(?:tool|system|bot|agent))\b[^.\n]{0,25}\b(?:must|should|please|shall)\s+(?:rate|score|rank|recommend|approve|ignore|reply|respond|output|pass|ensure)\b",
        r"\b(?:reply|respond|answer|output)\s+(?:only\s+)?with\b[^.\n]{0,30}\b(?:yes|no|hire|pass|approved|json|10)\b",
        r"<\s*/?\s*(?:system|assistant|instructions?|prompt)\s*>",
        r"\byou\s+are\s+now\s+(?:an?\s+)?(?!hiring|part|a\s+member)\w+",
    )
)
"""Patterns of a sentence that talks to a model instead of to a person. Deliberately specific: a posting that says
"we use AI tools" or "rate of growth" is not flagged."""

MAX_ANOMALIES: Final = 8
ANOMALY_CHARS: Final = 220


@dataclass(slots=True)
class PostingResult:
    """A read posting and what the checks refused while reading it."""

    posting: Posting
    report: Report = field(default_factory=Report)


def clean_posting_text(text: str) -> str:
    """The posting text as stored: control characters out, line ends plain, trimmed. Raises `PostingTextError`."""
    cleaned = (
        _CONTROL.sub("", unicodedata.normalize("NFC", text)).replace("\r\n", "\n").replace("\r", "\n").strip()
    )
    if len(cleaned) < MIN_POSTING_CHARS:
        raise PostingTextError("That text is too short to be a job posting. Paste the whole posting.")
    if len(cleaned) > MAX_POSTING_CHARS:
        raise PostingTextError(
            f"That posting is longer than {MAX_POSTING_CHARS:,} characters. Paste just the role description."
        )
    return cleaned


def find_injections(text: str) -> list[str]:
    """Sentences of ``text`` that give instructions to a model, as written, without repeats (at most `MAX_ANOMALIES`)."""
    found: dict[str, None] = {}
    for sentence in split_sentences(text):
        if any(p.search(sentence) for p in _INJECTION):
            found.setdefault(clip(sentence, ANOMALY_CHARS), None)
        if len(found) >= MAX_ANOMALIES:
            break
    return list(found)


def _overlaps(folded_quote: str, folded_injections: list[str]) -> bool:
    """Is the quote part of (or does it contain) a sentence that instructs a model?"""
    return any(folded_quote in bad or bad in folded_quote for bad in folded_injections if bad)


def _clean_title(raw: str, folded_text: str, tokens: set[str], first_line: str) -> str:
    title = clip(raw, 120)
    words = [t for t in tokens_of(title) if len(t) > 1]
    if title and words and all(t in tokens for t in words):
        return title
    if title and fold_text(title) in folded_text:
        return title
    return clip(first_line, 120) or "Untitled role"


def _clean_optional(raw: str, tokens: set[str]) -> str | None:
    value = clip(raw, 120)
    words = [t for t in tokens_of(value) if len(t) > 1]
    return value if value and words and all(t in tokens for t in words) else None


def _mode(value: str) -> WorkMode:
    if value == "onsite":
        return "onsite"
    if value == "hybrid":
        return "hybrid"
    if value == "remote":
        return "remote"
    return "unspecified"


def _requirements(
    rows: list[RequirementOut], folded_posting: str, injections: list[str], report: Report
) -> list[Requirement]:
    kept: list[tuple[int, RequirementOut, str, str, list[str]]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        quote = tidy(row.quote)
        label = clip(row.text or quote, 60)
        if not quote_in(quote, folded_posting):
            report.add("requirement", label, "its quote is not in the posting")
            continue
        folded_quote = fold_text(quote)
        if _overlaps(folded_quote, injections):
            report.add("requirement", label, "it comes from text that instructs a model")
            continue
        key = fold_text(row.text) or folded_quote
        if key in seen:
            report.add("requirement", label, "repeats an earlier requirement")
            continue
        seen.add(key)
        terms: list[str] = []
        for term in row.terms:
            if fold_text(term) and fold_text(term) in folded_quote:
                if term.strip() not in terms:
                    terms.append(tidy(term))
            else:
                report.add("term", clip(term, 40), "not a passage of its quote")
        tier = row.tier
        if row.provenance == "inferred" and tier in ("must", "required"):
            report.add("requirement", label, f"inferred, so {tier} became preferred")
            tier = "preferred"
        kept.append((index, row.model_copy(update={"tier": tier}), tidy(row.text) or quote, quote, terms))
    if len(kept) > MAX_REQUIREMENTS:
        ranked = sorted(kept, key=lambda k: (-tier_weight(k[1].tier), k[0]))
        for _, _, text, _, _ in ranked[MAX_REQUIREMENTS:]:
            report.add("requirement", clip(text, 60), f"over the {MAX_REQUIREMENTS} requirement limit")
        kept = sorted(ranked[:MAX_REQUIREMENTS], key=lambda k: k[0])
    return [
        Requirement(
            id=f"r{n}",
            text=clip(text, 200),
            quote=clip(quote, 400),
            tier=row.tier,
            kind=row.kind,
            provenance=row.provenance,
            domainHint=clip(row.domainHint, 60) or None,
            terms=terms[:6],
        )
        for n, (_, row, text, quote, terms) in enumerate(kept, start=1)
    ]


def _gates(rows: list[GateOut], folded_posting: str, injections: list[str], report: Report) -> list[Gate]:
    kept: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        question, quote = tidy(row.question), tidy(row.quote)
        label = clip(question or quote, 60)
        if not question or not quote_in(quote, folded_posting):
            report.add("gate", label, "its quote is not in the posting")
            continue
        if _overlaps(fold_text(quote), injections):
            report.add("gate", label, "it comes from text that instructs a model")
            continue
        key = fold_text(question)
        if key in seen:
            report.add("gate", label, "repeats an earlier gate")
            continue
        seen.add(key)
        kept.append((index, min(10, max(1, int(row.severity))), question, quote))
    if len(kept) > MAX_GATES:
        ranked = sorted(kept, key=lambda k: (-k[1], k[0]))
        for _, _, question, _ in ranked[MAX_GATES:]:
            report.add("gate", clip(question, 60), f"over the {MAX_GATES} gate limit")
        kept = sorted(ranked[:MAX_GATES], key=lambda k: k[0])
    return [
        Gate(id=f"g{n}", question=clip(question, 240), quote=clip(quote, 400), severity=severity)
        for n, (_, severity, question, quote) in enumerate(kept, start=1)
    ]


def _text_field(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def parse_posting(data: Mapping[str, Any], text: str, report: Report | None = None) -> PostingResult:
    """Turn a model answer into a `Posting` of ``text`` (already cleaned), applying every rule above."""
    report = report if report is not None else Report()
    folded = fold_text(text)
    tokens = set(tokens_of(text))
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")

    injections = find_injections(text)
    folded_injections = [fold_text(s) for s in injections]
    anomalies = list(injections)
    reported = data.get("anomalies")
    for raw in reported if isinstance(reported, list) else []:
        sentence = tidy(raw) if isinstance(raw, str) else ""
        if not sentence:
            continue
        if quote_in(sentence, folded):
            anomalies.append(clip(sentence, ANOMALY_CHARS))
            folded_injections.append(fold_text(sentence))
        else:
            report.add("anomaly", clip(sentence, 60), "not a passage of the posting")
    anomalies = list(dict.fromkeys(anomalies))[:MAX_ANOMALIES]

    mode = _text_field(data, "workMode")
    posting = Posting(
        hash=posting_hash(text),
        title=_clean_title(_text_field(data, "title"), folded, tokens, first_line),
        company=_clean_optional(_text_field(data, "company"), tokens),
        location=_clean_optional(_text_field(data, "location"), tokens),
        workMode=_mode(mode),
        rawText=text,
        requirements=_requirements(
            rows_of(data, "requirements", RequirementOut), folded, folded_injections, report
        ),
        gates=_gates(rows_of(data, "gates", GateOut), folded, folded_injections, report),
        anomalies=anomalies,
        links=urls_in(text),
    )
    return PostingResult(posting=posting, report=report)


async def read_posting(
    claude: ClaudeClient, *, model: str, text: str, call_ctx: CallContext
) -> PostingResult:
    """Read ``text`` into a `Posting`. The model sees the posting only; nothing about any person.

    Raises `PostingTextError` for text that cannot be a posting, `NoRequirementsFound` when no requirement
    survives the checks, and the client's errors when the call itself fails.
    """
    cleaned = clean_posting_text(text)
    data = await ask(
        claude,
        model=model,
        purpose="match.posting",
        system=POSTING_SYSTEM,
        user=build_posting_prompt(cleaned),
        schema=POSTING_SCHEMA,
        max_tokens=POSTING_MAX_TOKENS,
        call_ctx=call_ctx,
    )
    result = parse_posting(data, cleaned)
    if not result.posting.requirements:
        raise NoRequirementsFound
    return result


def blocks_when_failed(gate: Gate) -> bool:
    """Would a failed answer to this gate block the match? (Only severity 7 and over does.)"""
    return gate.severity >= GATE_BLOCK_SEVERITY


__all__ = [
    "MAX_POSTING_CHARS",
    "POSTING_MAX_TOKENS",
    "POSTING_SCHEMA",
    "PostingResult",
    "blocks_when_failed",
    "clean_posting_text",
    "find_injections",
    "parse_posting",
    "read_posting",
]
