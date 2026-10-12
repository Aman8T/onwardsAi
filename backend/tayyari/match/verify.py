"""The sentence verifier of the match: is every claim of a drafted sentence in the facts it cites?

It reuses the portfolio verifier's anchors (`portfolio.ground`: numbers with their units, names, identifiers) and adds
the four checks a resume or cover note needs:

  numbers        a number (with its unit) that no cited fact has is `unsupported`. The caller replaces it with a
                 `[x]` placeholder and asks the person; a number is never kept on a model's word
  scope verbs    led / owned / managed ... need the same verb in a cited fact; built / created ... need a fact
                 that does at least as much (a fact that only says "contributed" or "supported" does not);
                 otherwise the sentence is an escalation, marked `stretch`
  posting terms  a term the posting uses (React, A/B testing ...) may appear only when a cited fact supports it
                 (alias-aware); otherwise it is unsupported
  names          employers, tools, identifiers and capitalised names that no cited fact has, puffery that no fact
                 uses, and (for a resume) the first person

The checks are pure code: no model, no network.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from tayyari.contract.match import Fact
from tayyari.match.aliases import same_term
from tayyari.match.text import clip, term_in_corpus, terms_in_text, tidy
from tayyari.portfolio.ground import (
    Corpus,
    Measure,
    measures_of,
    split_sentences,
    unsupported_anchors,
    vocabulary,
)

PLACEHOLDER: Final = re.compile(r"\[x\](?:%|\+|[kKmMxX])?")
"""A slot for a figure the person has not given yet: ``[x]``, ``[x]%``."""
_PLACEHOLDER_STRIP: Final = re.compile(r"\[x\]")

# ───────────────────────── scope verbs ─────────────────────────

_LEADERSHIP: Final[dict[str, frozenset[str]]] = {
    "led": vocabulary("led lead leads leading"),
    "owned": vocabulary("owned own owns owning"),
    "managed": vocabulary("managed manage manages managing"),
    "headed": vocabulary("headed head heads"),
    "directed": vocabulary("directed directs directing"),
    "spearheaded": vocabulary("spearheaded spearhead spearheads spearheading"),
    "architected": vocabulary("architected architects architecting"),
    "oversaw": vocabulary("oversaw oversee oversees overseeing"),
    "founded": vocabulary("founded found founds cofounded"),
}
"""Level 3: claims of leadership or ownership. A drafted sentence may use one only if a cited fact uses the same verb."""
_BUILDING: Final = vocabulary(
    "built build builds building created create creates creating designed design designs designing developed "
    "develop develops developing shipped ship ships shipping delivered deliver delivers delivering implemented "
    "implement implements implementing launched launch launches launching established establish establishes "
    "wrote write writes writing authored author authors authoring ran run runs running drove drive drives driving "
    "introduced introduce introduces introducing rebuilt rebuild rebuilds rebuilding redesigned redesign "
    "redesigns migrated migrate migrates maintained maintain maintains maintaining automated automate automates "
    "set established engineered engineer engineers"
)
"""Level 2: doing the work."""
_SUPPORTING: Final = vocabulary(
    "contributed contribute contributes contributing supported support supports supporting assisted assist "
    "assists assisting helped help helps helping participated participate participates participating "
    "collaborated collaborate collaborates collaborating partnered partner partners partnering joined join "
    "joins joining reviewed review reviews reviewing aided aid aids paired pair pairs pairing mentored mentor "
    "mentors mentoring"
)
"""Level 1: taking part, supporting, helping."""
_FORM_GROUP: Final[dict[str, str]] = {form: g for g, forms in _LEADERSHIP.items() for form in forms}
_BARE: Final = vocabulary(
    "build create design develop deliver implement launch establish write author run drive introduce rebuild "
    "redesign migrate maintain automate engineer ship set contribute support assist help participate collaborate "
    "partner join review aid pair mentor lead own manage head direct spearhead architect oversee found"
)
"""Base forms are also nouns ("the checkout rebuild", "team lead", "their own"): they count as a verb only where a clause
begins (the start of the text, after punctuation, "and", "then", or a subject pronoun)."""
_CLAUSE_PRE: Final = vocabulary("i we they she he and then also who while")
_WORD: Final = re.compile(r"[^\W_]+")
_CLAUSE_PUNCT: Final = ".;:!?,(\n-–—•"
BASELINE_LEVEL: Final = 2
"""A fact with no scope verb of its own ("Responsive landing pages for 3 clients") is read as work that was done."""


def _clause_start(folded: str, pos: int) -> bool:
    before = folded[:pos].rstrip()
    if not before or before[-1] in _CLAUSE_PUNCT:
        return True
    last = _WORD.findall(before)[-1:] or [""]
    return last[0] in _CLAUSE_PRE


def scope_verbs(text: str) -> dict[str, int]:
    """The scope verbs of ``text`` as ``{verb group or word: level}`` (3 lead/own, 2 build, 1 support)."""
    folded = text.casefold()
    found: dict[str, int] = {}
    for m in _WORD.finditer(folded):
        word = m.group()
        if word in _BARE and not _clause_start(folded, m.start()):
            continue  # "the checkout rebuild", "team lead", "their own roadmap"
        group = _FORM_GROUP.get(word)
        if group is not None:
            found[group] = 3
        elif word in _BUILDING:
            found.setdefault(word, 2)
        elif word in _SUPPORTING:
            found.setdefault(word, 1)
    return found


def escalations(text: str, cited_text: str) -> list[str]:
    """The verbs of ``text`` that claim more scope than the cited facts' text does (empty when it does not).

    A leadership verb must be (a form of) one the facts use; any other verb must not outrank the strongest scope the
    facts show (`BASELINE_LEVEL` when they show none)."""
    mine = scope_verbs(text)
    if not mine:
        return []
    theirs = scope_verbs(cited_text)
    their_level = max(theirs.values(), default=BASELINE_LEVEL)
    out: list[str] = []
    for verb, level in mine.items():
        if level == 3:
            if verb not in theirs:
                out.append(verb)
        elif level > their_level:
            out.append(verb)
    return out


# ───────────────────────── numbers ─────────────────────────

_NUMBER_WORDS: Final = vocabulary(
    "two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion "
    "dozen dozens hundreds thousands millions"
)
_WORD_VALUE: Final[dict[str, int]] = {
    w: n
    for n, w in enumerate(
        [
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ],
        start=2,
    )
} | {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
"""A spelled-out number is supported by the same number written in digits ("three" by "3")."""
_NUMBER_WORD_TOKEN: Final = re.compile(
    r"\b(?:" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\b", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class NumberSlot:
    """A number of a sentence that the cited facts do not have: where it sits and what to put there."""

    text: str
    start: int
    end: int
    replacement: str
    """The sentence's own wording of the slot with the figure taken out: ``[x]%`` for ``40%``."""


def unsupported_numbers(text: str, corpus: Corpus) -> list[NumberSlot]:
    """Numbers (with their unit) and spelled-out numbers of ``text`` that ``corpus`` does not state."""
    clean = PLACEHOLDER.sub(lambda m: " " * len(m.group()), text)
    out: list[NumberSlot] = []
    for m in measures_of(clean, strict=True):
        if corpus.has_measure(m):
            continue
        out.append(_slot(clean, m))
    for found in _NUMBER_WORD_TOKEN.finditer(clean):
        word = found.group().casefold()
        if word in corpus.token_set or str(_WORD_VALUE.get(word, "")) in corpus.numbers:
            continue
        out.append(NumberSlot(found.group(), found.start(), found.end(), "[x]"))
    out.sort(key=lambda s: s.start)
    return out


def _slot(text: str, measure: Measure) -> NumberSlot:
    span = text[measure.start : measure.end]
    digits = re.search(r"\d[\d,_]*(?:\.\d+)?", span)
    if digits is None:
        return NumberSlot(span, measure.start, measure.end, "[x]")
    return NumberSlot(
        span.strip(), measure.start, measure.end, span[: digits.start()] + "[x]" + span[digits.end() :]
    )


def with_placeholders(text: str, slots: Sequence[NumberSlot]) -> str:
    """``text`` with each slot's figure replaced by ``[x]`` (the unit stays: ``by [x]%``)."""
    out = text
    for slot in sorted(slots, key=lambda s: -s.start):
        out = out[: slot.start] + slot.replacement + out[slot.end :]
    return out


def placeholders_in(text: str) -> list[str]:
    """The ``[x]`` slots of ``text`` as written, without repeats (``[x]%``)."""
    return list(dict.fromkeys(PLACEHOLDER.findall(text)))


# ───────────────────────── puffery, pronouns ─────────────────────────

_PUFFERY: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("passionate", r"\bpassionate\b"),
        ("world-class", r"\bworld[-\s]class\b"),
        ("rockstar", r"\brock[-\s]?stars?\b"),
        ("ninja", r"\bninjas?\b"),
        ("guru", r"\bgurus?\b"),
        ("visionary", r"\bvisionar(?:y|ies)\b"),
        ("cutting-edge", r"\bcutting[-\s]edge\b"),
        ("synergy", r"\bsynerg(?:y|ies|istic)\b"),
        ("results-driven", r"\bresults?[-\s]driven\b"),
        ("dynamic", r"\bdynamic\b"),
        ("seasoned", r"\bseasoned\b"),
        ("expert in", r"\bexpert\s+in\b"),
        ("proven track record", r"\bproven\s+track\s+record\b"),
        ("self-starter", r"\bself[-\s]starter\b"),
        ("go-getter", r"\bgo[-\s]getter\b"),
        ("team player", r"\bteam\s+player\b"),
        ("perfect fit", r"\bperfect\s+(?:fit|candidate)\b"),
    )
)
_FIRST_PERSON: Final = re.compile(r"\b(?:I|me|my|myself|I'm|I've|I'd)\b")


def puffery_in(text: str) -> list[str]:
    return [label for label, pattern in _PUFFERY if pattern.search(text)]


# ───────────────────────── the check ─────────────────────────


@dataclass(slots=True)
class SentenceCheck:
    """The verdict on one drafted sentence against the facts it cites."""

    numbers: list[NumberSlot] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    """Names, identifiers and puffery the cited facts do not have."""
    terms: list[str] = field(default_factory=list)
    """Posting terms the sentence uses that no cited fact supports."""
    escalated: list[str] = field(default_factory=list)
    """Scope verbs stronger than the cited facts allow."""
    placeholders: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """Everything else (empty text, first person, no cited fact ...)."""

    @property
    def ok(self) -> bool:
        return not (self.numbers or self.names or self.terms or self.escalated or self.problems)


def fact_text(fact: Fact) -> str:
    """Everything one fact vouches for: its wording and the passages it quotes."""
    return "\n".join([fact.text, *(ref.quote for ref in fact.refs)])


def corpus_of(facts: Iterable[Fact], extra: str = "") -> tuple[Corpus, str]:
    """The words the cited ``facts`` allow (plus ``extra`` allowed text) as a corpus, and the same text raw."""
    raw = "\n".join(p for p in ["\n".join(fact_text(f) for f in facts), extra] if p)
    return Corpus.of(raw), raw


def check_text(
    text: str,
    cited: Sequence[Fact],
    *,
    posting_terms: Iterable[str] = (),
    extra: str = "",
    first_person_ok: bool = False,
    max_words: int | None = 45,
    allow_no_facts: bool = False,
) -> SentenceCheck:
    """Check ``text`` (one or a few sentences) against the facts it cites. ``extra`` is further allowed wording (for
    a gap sentence, the requirement it names). A ``[x]`` placeholder is allowed: it is a question, not a claim.
    ``allow_no_facts`` lets a sentence that makes no claim about the person (a gap sentence, a closing line) cite
    nothing: it is then judged against ``extra`` alone."""
    result = SentenceCheck()
    body = tidy(text)
    if not body:
        result.problems.append("the sentence is empty")
        return result
    if not cited and not allow_no_facts:
        result.problems.append("it cites no fact")
        return result
    corpus, raw = corpus_of(cited, extra)
    result.placeholders = placeholders_in(body)
    scan = _PLACEHOLDER_STRIP.sub(" ", body)

    result.numbers = unsupported_numbers(body, corpus)
    spelled = {s.text.casefold() for s in result.numbers}
    all_numbers = {m.text.casefold() for m in measures_of(scan)}
    posting_terms = list(posting_terms)
    for anchor in unsupported_anchors(scan, corpus):
        if anchor.casefold() in all_numbers or anchor.casefold() in spelled:
            continue
        if any(same_term(anchor, t) for t in posting_terms):
            continue  # a posting's own word is judged below, as a term
        result.names.append(anchor)
    result.names += [
        f"{label} (puffery)" for label in puffery_in(body) if label not in {p for p in puffery_in(raw)}
    ]
    result.terms = [t for t in terms_in_text(body, posting_terms) if not term_in_corpus(t, corpus, raw)]
    result.escalated = escalations(body, raw)
    if not first_person_ok and _FIRST_PERSON.search(body):
        result.problems.append("it speaks in the first person")
    if max_words is not None and len(body.split()) > max_words:
        result.problems.append(f"it is longer than {max_words} words")
    return result


def sentences_of(text: str) -> list[str]:
    """The sentences of ``text`` (the portfolio's splitter)."""
    return split_sentences(text)


def exact_wording(fact: Fact, limit: int = 300) -> str:
    """The fact's own words as a sentence: whole when it fits, else its first sentences up to ``limit``."""
    text = tidy(fact.text)
    if len(text) <= limit:
        return text
    cut = 0
    for m in re.finditer(r"[.!?](?:\s|$)", text[: limit + 1]):
        cut = m.end()
    return text[:cut].strip() if cut >= limit * 0.4 else clip(text, limit)


__all__ = [
    "BASELINE_LEVEL",
    "PLACEHOLDER",
    "NumberSlot",
    "SentenceCheck",
    "check_text",
    "corpus_of",
    "escalations",
    "exact_wording",
    "fact_text",
    "placeholders_in",
    "puffery_in",
    "scope_verbs",
    "sentences_of",
    "unsupported_numbers",
    "with_placeholders",
]
