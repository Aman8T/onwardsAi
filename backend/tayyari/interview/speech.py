"""What speech may do for one practice interview: which lines the voice may read, and which words bias the listening.

`speech_context(state)` is code over the stored session; no model, no I/O. The speech routes ask the interview for it and hand
it to the speech service, which knows nothing of interviews:

    lines      the folded text of everything the interviewer said or is saying: every interviewer turn (a lead and its
               question as one line, as they are spoken), the current line's text and its lead on their own, and a work
               sample's task sentence. `/api/speech/speak` reads a line only if it is one of these, so the voice can never be
               made to read anything but the interview's own words.
    keyterms   up to 50 terms (each at most 64 characters, no repeats, most useful first) that Sarvam's speech-to-text is told
               to expect: the employer and the role title, the work sample's material name, the slots' topics, then the names
               and technical words of the slots' sources (the person's own evidence and the posting's asks) found by their
               shape alone: "Kitebird", "LCP", "Storybook", "TypeScript", "safe-area", "Next.js". A word that only starts a
               sentence is not taken for a name ("Rebuilt", "Led").

The terms bias recognition; they are never shown, never sent to a model and never an instruction (a posting is untrusted
text, and its words only ever end up as hints to a speech recogniser).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from tayyari.core.text import tidy

from .state import SessionState
from .text import STOPWORDS, fold

KEYTERM_LIMIT: Final = 50
KEYTERM_CHARS: Final = 64
TOPIC_MAX_WORDS: Final = 3
TOPIC_MAX_CHARS: Final = 40
RUN_MAX_WORDS: Final = 4
"""Names that sit side by side are one term ("Kite UI") up to this many words."""

_SENTENCE: Final = re.compile(r"(?<=[.!?])\s+|[\n\r;•·]+")
_TOKEN: Final = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-._][A-Za-z0-9]+)*")
_PART_SPLIT: Final = re.compile(r"[-._]")
_INNER_CAPS: Final = re.compile(r"[a-z][A-Z]")

_TIER_SHAPE: Final = 1
"""CamelCase, ALL CAPS, letters with digits, dotted names: a technical word whatever the sentence."""
_TIER_NAME: Final = 2
"""A capitalised word in the middle of a sentence."""
_TIER_COMPOUND: Final = 3
"""A lower-case compound with a hyphen or underscore ("safe-area", "cross-browser")."""


@dataclass(frozen=True, slots=True)
class SpeechContext:
    """What the speech routes need to know about one session."""

    active: bool
    """The interview can still be answered (transcribing a piece is for an active one)."""
    lines: frozenset[str]
    """Folded texts of the interviewer's lines (`tayyari.interview.text.fold`)."""
    keyterms: tuple[str, ...]

    def allows(self, text: str) -> bool:
        """Whether ``text`` is one of the interview's own lines (case, spacing, quote marks and dashes ignored)."""
        folded = fold(text)
        return bool(folded) and folded in self.lines


def _tier(token: str, *, initial: bool) -> int | None:
    """How good a recognition hint ``token`` is, 1 (best) to 3, or None when it is an ordinary word."""
    parts = [p for p in _PART_SPLIT.split(token) if p]
    if len(token) < 2 or all(len(p) < 2 for p in parts):
        return None
    if _INNER_CAPS.search(token) or (token.isupper() and token.isalnum() and len(token) <= 8):
        return _TIER_SHAPE
    if any(ch.isdigit() for ch in token) or (len(parts) > 1 and "." in token):
        return _TIER_SHAPE
    if token.isalpha() and token[0].isupper():
        return None if initial or token.lower() in STOPWORDS else _TIER_NAME
    if (
        len(parts) > 1
        and all(p.isalpha() and p.islower() for p in parts)
        and not all(p in STOPWORDS for p in parts)
    ):
        return _TIER_COMPOUND
    return None


def _terms_of(text: str) -> Iterable[tuple[str, int]]:
    """``(term, tier)`` for the names and technical words of ``text``. Neighbouring names read as one ("Kite UI")."""
    for sentence in _SENTENCE.split(text):
        run: list[str] = []
        run_tier = 0
        previous_end = -1
        first = True
        for match in _TOKEN.finditer(sentence):
            token = match.group()
            tier = _tier(token, initial=first)
            first = False
            is_name = tier is not None and tier <= _TIER_NAME
            joins = run and len(run) < RUN_MAX_WORDS and sentence[previous_end : match.start()] == " "
            if is_name and tier is not None and joins:
                run.append(token)
                run_tier = min(run_tier, tier)
            else:
                if run:
                    yield " ".join(run), run_tier
                run, run_tier = ([token], tier or 0) if is_name else ([], 0)
                if tier == _TIER_COMPOUND:
                    yield token, tier
            previous_end = match.end()
        if run:
            yield " ".join(run), run_tier


def _shown(term: str) -> str | None:
    """``term`` as a key term: one space between words, within the length limit, else None."""
    cleaned = tidy(term).strip(" ,.;:()[]{}\"'")
    return cleaned if 2 <= len(cleaned) <= KEYTERM_CHARS else None


def keyterms_of(state: SessionState) -> tuple[str, ...]:
    """The key terms of a session, best first (see the module docstring)."""
    chosen: dict[str, str] = {}

    def add(term: str | None) -> None:
        cleaned = _shown(term) if term else None
        if cleaned and len(chosen) < KEYTERM_LIMIT:
            chosen.setdefault(cleaned.casefold(), cleaned)

    employer = tidy(state.employer)
    add(employer)
    if state.kind == "role" and state.title:
        add(state.title.removesuffix(f" at {employer}") if employer else state.title)
    task = state.task
    if task is not None:
        add(task.materialName)
    for slot in state.slots:
        topic = tidy(slot.topic)
        if topic and len(topic.split()) <= TOPIC_MAX_WORDS and len(topic) <= TOPIC_MAX_CHARS:
            add(topic)

    found: Counter[str] = Counter()
    display: dict[str, str] = {}
    best: dict[str, int] = {}
    order: dict[str, int] = {}
    texts = [source.text for slot in state.slots for source in slot.sources]
    if task is not None and task.materialKind == "code":
        texts.append(task.material)
    for text in texts:
        for term, tier in _terms_of(text):
            key = term.casefold()
            found[key] += 1
            display.setdefault(key, term)
            best[key] = min(best.get(key, tier), tier)
            order.setdefault(key, len(order))
    for key in sorted(found, key=lambda k: (best[k] > _TIER_NAME, -found[k], order[k])):
        add(display[key])
    return tuple(chosen.values())


def lines_of(state: SessionState) -> frozenset[str]:
    """The folded texts of every line the interviewer said or is saying."""
    out: set[str] = set()

    def add(text: str | None) -> None:
        folded = fold(text) if text else ""
        if folded:
            out.add(folded)

    for turn in state.turns:
        if turn.speaker == "interviewer":
            add(turn.text)
    line = state.current
    if line is not None:
        add(line.text)
        add(line.lead)
        if line.lead:
            add(f"{line.lead} {line.text}")
    if state.task is not None:
        add(state.task.task)
    return frozenset(out)


def speech_context(state: SessionState, *, active: bool) -> SpeechContext:
    """The speech context of a session in this state."""
    return SpeechContext(active=active, lines=lines_of(state), keyterms=keyterms_of(state))


__all__ = [
    "KEYTERM_CHARS",
    "KEYTERM_LIMIT",
    "SpeechContext",
    "keyterms_of",
    "lines_of",
    "speech_context",
]
