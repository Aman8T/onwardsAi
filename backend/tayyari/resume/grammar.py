"""The grammar check of a draft: one Haiku call proposes fixes, code keeps only the ones that change no fact.

A proposal is the same line with its grammar, spelling or punctuation fixed. The model is asked for nothing else, and code
does not take its word for it: `vet` compares every proposal with the line it answers and keeps it only when

* it differs from the line, is one line, and fits `MAX_LINE_CHARS` (and holds no ``[x]`` placeholder);
* every figure, name, tool and posting term of the line is still in it, and nothing of those kinds appears that the line
  did not have (`match.verify.check_text` read both ways: the new line against the old as its only fact, and the old line
  against the new one);
* its scope verbs are the same (the leadership verbs exactly, the strongest of the doing and supporting verbs the same);
* it holds no word the line did not have except a function word or a spelling or inflection of a word it loses (so a fix
  that slips in a claim, or drops a word of substance, is refused).

A refused proposal is dropped and logged with its reason, never shown. Nothing is stored: a proposal the person accepts is
sent back as the ordinary ``edit`` op, so Finalise checks the line again like any typed line. With no model key, or when
the call fails, the check answers no proposals and says it could not run.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys the model is asked for)

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from tayyari.contract.match import Bullet, ResumeVariant
from tayyari.contract.resume import MAX_LINE_CHARS, GrammarProposal
from tayyari.core import limits
from tayyari.core.logging import get_logger
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.calls import ask_or_none
from tayyari.match.schemas import rows_of, schema_of
from tayyari.match.text import tidy
from tayyari.match.verify import PLACEHOLDER, check_text, scope_verbs
from tayyari.prompts.resume import GRAMMAR_SYSTEM, build_grammar_prompt
from tayyari.resume.ops import entries_of

log = get_logger(__name__)

GRAMMAR_MAX_TOKENS: Final = 3500
_WORD: Final = re.compile(r"[^\W_]+")
_SIMILAR: Final = 0.8
"""How alike two words must be (difflib ratio) for one to count as a spelling or inflection of the other."""
FUNCTION_WORDS: Final = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "and",
        "or",
        "but",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "from",
        "into",
        "over",
        "under",
        "than",
        "then",
        "also",
        "so",
        "not",
        "no",
        "per",
        "via",
    ]
)
"""Words a grammar fix may add or drop (articles, prepositions, auxiliaries, conjunctions): they carry no claim."""
_IRREGULAR: Final = {
    form: base
    for base, forms in {
        "run": "ran",
        "write": "wrote written",
        "build": "built",
        "lead": "led",
        "drive": "drove driven",
        "make": "made",
        "grow": "grew grown",
        "win": "won",
        "sell": "sold",
        "teach": "taught",
        "hold": "held",
        "bring": "brought",
        "begin": "began begun",
        "give": "gave given",
        "take": "took taken",
        "see": "saw seen",
        "know": "knew known",
        "lose": "lost",
        "send": "sent",
        "spend": "spent",
        "meet": "met",
        "speak": "spoke spoken",
        "choose": "chose chosen",
        "find": "found",
        "get": "got gotten",
        "keep": "kept",
        "think": "thought",
        "understand": "understood",
        "undertake": "undertook undertaken",
        "oversee": "oversaw overseen",
        "do": "did done",
        "go": "went gone",
        "set": "set",
    }.items()
    for form in forms.split()
}
"""Past forms of common irregular verbs, each with its base: a tense fix ("run" to "ran") is not a new word."""


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GrammarRowOut(_Out):
    bulletId: str
    after: str = Field(
        description="The whole line with its grammar, spelling or punctuation fixed, and nothing else changed."
    )
    why: str = Field(description="A few plain words naming the mistake fixed.")


class GrammarOut(_Out):
    fixes: list[GrammarRowOut] = Field(
        description="One entry per line that has a mistake; empty when every line is correct."
    )


# ───────────────────────── the guard ─────────────────────────


def _scope_profile(text: str) -> tuple[frozenset[str], int]:
    """The leadership verbs of ``text`` (as their groups) and the strongest level of the other scope verbs."""
    found = scope_verbs(text)
    lead = frozenset(verb for verb, level in found.items() if level == 3)
    return lead, max((level for level in found.values() if level < 3), default=0)


def _words(text: str) -> Counter[str]:
    """The words of ``text`` (folded; a one-letter word other than a digit is an apostrophe's tail and is ignored)."""
    return Counter(
        w for w in (m.group().casefold() for m in _WORD.finditer(text)) if len(w) > 1 or w.isdigit()
    )


def _similar(one: str, other: str) -> bool:
    """Is ``other`` a spelling or inflection of ``one`` (letters only: a figure must match exactly)?"""
    if not (one.isalpha() and other.isalpha()):
        return False
    one, other = _IRREGULAR.get(one, one), _IRREGULAR.get(other, other)
    if one == other:
        return True
    if sorted(one) == sorted(other):  # two letters swapped ("teh", "form" and "from") when an end is kept
        return one[0] == other[0] or one[-1] == other[-1]
    return SequenceMatcher(None, one, other).ratio() >= _SIMILAR


def _words_agree(before: str, after: str) -> bool:
    """Do the two lines hold the same words, give or take function words and spellings? A word of ``after`` that
    ``before`` lacks must be a spelling of a word ``before`` has that ``after`` lacks, and the other way round."""
    gone = list((_words(before) - _words(after)).elements())
    new = list((_words(after) - _words(before)).elements())
    for word in sorted(new, key=lambda w: w in FUNCTION_WORDS):  # the words of substance pair up first
        partner = next((g for g in gone if _similar(g, word)), None)
        if partner is not None:
            gone.remove(partner)
        elif word not in FUNCTION_WORDS:
            return False
    return all(w in FUNCTION_WORDS for w in gone)


def same_facts(before: str, after: str, terms: Iterable[str] = ()) -> str | None:
    """None when ``after`` says what ``before`` says (see the module docstring), else why not, as a short slug:
    ``number``, ``name``, ``term``, ``scope`` or ``word`` for something it adds or changes, and ``lost_number``,
    ``lost_name``, ``lost_term``, ``lost_scope``, ``lost_word`` for something it drops."""
    words = list(terms)
    for text, other, prefix in ((after, before, ""), (before, after, "lost_")):
        check = check_text(
            text,
            [],
            posting_terms=words,
            extra=other,
            first_person_ok=True,
            max_words=None,
            allow_no_facts=True,
        )
        for kind, found in (
            ("number", check.numbers),
            ("name", check.names),
            ("term", check.terms),
            ("scope", check.escalated),
        ):
            if found:
                return f"{prefix}{kind}"
        if check.problems:
            return f"{prefix}word"
    if _scope_profile(before) != _scope_profile(after):
        return "scope"
    if not _words_agree(before, after):
        return "word"
    return None


def vet(before: str, after: str, terms: Iterable[str] = ()) -> str | None:
    """None when ``after`` may be offered for the line ``before``, else why not (a short slug, never the text)."""
    if "\n" in after.strip() or "\r" in after.strip():
        return "multiline"
    fixed = tidy(after)
    if not fixed:
        return "empty"
    if len(fixed) > MAX_LINE_CHARS:
        return "too_long"
    if PLACEHOLDER.search(fixed):
        return "placeholder"
    if fixed == tidy(before):
        return "unchanged"
    return same_facts(tidy(before), fixed, terms)


# ───────────────────────── the call ─────────────────────────


@dataclass(slots=True)
class GrammarResult:
    proposals: list[GrammarProposal] = field(default_factory=list)
    ran: bool = True
    """False when the model could not be asked (no key, a failure, a timeout)."""
    dropped: list[str] = field(default_factory=list)
    """The reason of each proposal the guard refused, in order (logged by the caller's log line, never shown)."""


def lines_of(variant: ResumeVariant) -> list[Bullet]:
    """The lines of a draft the check reads: the ones that have text, in reading order, at most `GRAMMAR_MAX_LINES`."""
    lines = [
        b for entry in entries_of(variant) for b in entry.bullets if b.status != "dropped" and b.text.strip()
    ]
    return lines[: limits.GRAMMAR_MAX_LINES]


async def propose(
    claude: ClaudeClient,
    *,
    model: str,
    variant: ResumeVariant,
    terms: Sequence[str] = (),
    call_ctx: CallContext,
) -> GrammarResult:
    """The grammar fixes for ``variant`` that pass the guard. One call for the whole draft; a draft with no lines costs no
    call. A failed call (or no key) gives ``ran=False``; only the daily spend ceiling propagates."""
    lines = lines_of(variant)
    if not lines:
        return GrammarResult()
    by_id = {b.id: b for b in lines}
    data = await ask_or_none(
        claude,
        model=model,
        purpose="resume.grammar",
        system=GRAMMAR_SYSTEM,
        user=build_grammar_prompt([(b.id, b.text) for b in lines]),
        schema=schema_of(GrammarOut, bulletId=list(by_id)),
        max_tokens=GRAMMAR_MAX_TOKENS,
        call_ctx=call_ctx,
    )
    if data is None:
        return GrammarResult(ran=False)
    result = GrammarResult()
    seen: set[str] = set()
    rows = rows_of(data, "fixes", GrammarRowOut)
    for row in rows:
        line = by_id.get(row.bulletId)
        if line is None:
            reason: str | None = "unknown_line"
        elif row.bulletId in seen:
            reason = "duplicate"
        else:
            reason = vet(line.text, row.after, terms)
        if line is None or reason is not None:
            result.dropped.append(reason or "unknown_line")
            log.info("resume_grammar_dropped", reason=reason)
            continue
        seen.add(row.bulletId)
        why = tidy(row.why)[: limits.GRAMMAR_WHY_MAX_CHARS]
        result.proposals.append(
            GrammarProposal(bulletId=line.id, before=line.text, after=tidy(row.after), why=why)
        )
    log.info(
        "resume_grammar",
        lines=len(lines),
        proposed=len(rows),
        kept=len(result.proposals),
        dropped=dict(Counter(result.dropped)),
    )
    return result


__all__ = [
    "FUNCTION_WORDS",
    "GRAMMAR_MAX_TOKENS",
    "GrammarOut",
    "GrammarResult",
    "GrammarRowOut",
    "lines_of",
    "propose",
    "same_facts",
    "vet",
]
