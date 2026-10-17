"""The router: what a person's turn is, decided in code from the button they pressed or the words they said. No model.

    repeat      "say that again", "repeat", "pardon", "come again"           the same line comes back
    skip        "skip", "next question", "let's skip"                        the slot keeps what it has; next slot
    dont_know   "I don't know", "no idea", "not sure", "I can't remember",   a short answer that gives up; the slot is
                "pass"                                                       Couldn't defend and the interview moves on
    dispute     "that's not what my resume says", "I didn't lead the whole   checked against the line it is about
                thing", "not quite right"
    unclear     fewer than two words
    answer      anything else: the judge reads it

A button (`action` other than `answer`) always wins. Words are matched whole, never inside a longer answer: "I would skip
the cache" is an answer. "Don't know" only counts when it opens a short answer: "I think the biggest one was the images? I'm
not sure how we measured it" is an answer, and the judge reads it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Literal

from tayyari.contract.interview import AnswerAction

Route = Literal["repeat", "skip", "dont_know", "dispute", "unclear", "answer"]

DONT_KNOW_MAX_WORDS: Final = 25
"""A longer answer is an answer, whatever it opens with."""
DONT_KNOW_TAIL_WORDS: Final = 8
"""Words that may follow the don't-know phrase before it stops being the whole of the answer."""
MIN_WORDS: Final = 2

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'"})
_FILLER = re.compile(
    r"^(?:(?:um+|uh+|er+|hmm+|hm+|well|so|honestly|sorry|oh|okay|ok|yeah|actually|frankly|to be honest"
    r"|to be fair|i mean|please)\s+)+"
)
_POLITE = r"(?:(?:can|could|would|will) you |please |sorry |um |uh |hey |oh )*"

_REPEAT = re.compile(
    rf"{_POLITE}(?:"
    r"(?:say|repeat|read|ask) (?:that|it|the question|this|the last bit|the last part|that question)"
    r"(?: again| one more time| once more)?"
    r"|(?:say|repeat|ask) (?:that |it )?(?:again|one more time|once more)"
    r"|repeat(?: that| the question| it| please)?"
    r"|pardon(?: me)?"
    r"|come again"
    r"|once more"
    r"|what was (?:that|the question)"
    r"|(?:i )?(?:didn'?t|did not|couldn'?t|could not) (?:hear|catch) (?:that|you|the question|it)"
    r"|what did you say"
    r"|sorry what"
    r")(?: please)?"
)
_SKIP = re.compile(
    r"(?:(?:let'?s|lets|can we|could we|i'?d like to|i want to|i will|i'll|please|just|i would like to) )*"
    r"(?:skip(?: this| that| it)?(?: one| question| for now)?|pass on (?:this|that|it)|move on"
    r"|next(?: question| one)?)(?: please| now)?"
)
_DONT_KNOW = re.compile(
    r"(?:i do not know|i don'?t know|dunno|i have no idea|i've no idea|no idea|i'?m not sure|i am not sure|not sure"
    r"|i can'?t (?:remember|recall|think of)|i cannot (?:remember|recall|think of)|pass)\b"
)
_DISPUTES: Final = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:that'?s|that is|this is|it'?s) not (?:what|how) (?:my |the )?(?:resume|cv|profile|linkedin|portfolio|github)"
        r" (?:says|said|say|shows|showed)\b",
        r"\b(?:that'?s|that is) not what (?:i|we) (?:wrote|put|said|did)\b",
        r"\b(?:my |the )?(?:resume|cv|profile) (?:doesn'?t|does not|didn'?t|did not) say (?:that|this)\b",
        r"\bi (?:didn'?t|did not) (?:lead|do|build|own|run|write|create|make|manage|design|ship|launch|deliver)"
        r"(?: all of| all)? (?:the )?(?:whole|entire|all|everything)\b",
        r"\bi never (?:led|did|built|owned|ran|wrote|created|made|managed|designed|shipped|launched|delivered)"
        r"(?: all of| all)? (?:the )?(?:whole|entire|all|everything)\b",
        r"\bnot quite right\b",
        r"\bthat'?s (?:not (?:quite )?(?:right|true|accurate|correct)|wrong|incorrect|inaccurate)\b",
    )
)


def normalise(text: str) -> str:
    """Lower case, one kind of apostrophe, no punctuation but apostrophes, single spaces. Letters, digits and the marks that
    belong to a letter (other scripts write their vowels with them) stay."""
    lowered = text.translate(_APOSTROPHES).lower()
    kept = "".join(c if c == "'" or unicodedata.category(c)[0] in "LNM" else " " for c in lowered)
    return " ".join(kept.split())


def _is_dont_know(norm: str) -> bool:
    words = norm.split()
    if not words or len(words) >= DONT_KNOW_MAX_WORDS:
        return False
    lead = _FILLER.sub("", norm + " ") if norm else ""
    lead = lead.strip() or norm
    found = _DONT_KNOW.match(lead)
    if found is None:
        return False
    tail = lead[found.end() :].split()
    return len(tail) <= DONT_KNOW_TAIL_WORDS


def classify(text: str, action: AnswerAction | None = None) -> Route:
    """What this turn is. ``action`` (a button) wins unless it is the plain ``answer``; then the words decide."""
    if action in ("repeat", "skip", "dont_know"):
        return action
    norm = normalise(text)
    if _REPEAT.fullmatch(norm):
        return "repeat"
    if _SKIP.fullmatch(norm):
        return "skip"
    if any(pattern.search(norm) for pattern in _DISPUTES):
        return "dispute"
    if _is_dont_know(norm):
        return "dont_know"
    if len(norm.split()) < MIN_WORDS:
        return "unclear"
    return "answer"


__all__ = ["DONT_KNOW_MAX_WORDS", "Route", "classify", "normalise"]
