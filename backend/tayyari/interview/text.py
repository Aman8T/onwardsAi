"""Small text checks the interview modules share: is a quote a passage of what was said, is a name or number in the
sources, does a sentence read the person's character or claim something about them.

Everything here is code. A model's words reach a person only after passing these (or a template takes their place).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Final

from tayyari.core.text import clip_words, tidy
from tayyari.jobsearch.judge import fold, has_injection, quoted
from tayyari.jobsearch.prepare import clean_quote
from tayyari.portfolio.ground import Corpus, number_keys, unsupported_anchors, vocabulary

PERSONALITY: Final = re.compile(
    r"\bconfiden\w*|\bnervous\w*|\bnervousness\b|\btones?\b|\baccents?\b|\benerg(?:y|etic)\b|\benthusias\w*"
    r"|\bcharisma\w*|\bfillers?\b|\bum+\b|\bbody language\b|\beye contact\b|\bculture[- ]fit\b|\battitude\b"
    r"|\bpersonalit\w*|\bpassion\w*",
    re.IGNORECASE,
)
"""Words that read a person's character instead of their answer (project-soul.md: no personality reading)."""

_PLACEHOLDER: Final = re.compile(r"\[([^\[\]]*)\]")
_OPENING: Final = re.compile(r"[^\w]*([^\W\d_][^\W_]*)")
PLACEHOLDER_MIN: Final = 2
PLACEHOLDER_MAX: Final = 60

_WORD: Final = re.compile(r"[a-z0-9]+")
STOPWORDS: Final = vocabulary(
    "the and for with from that this these those your you our their its into onto over under about than then them they "
    "have has had was were are been being will would could should can may might must not but any all one two each every "
    "such also more most some very just only other another which what when where while who whom how why per via off out "
    "strong solid good great ability able skills skill understanding knowledge experience experienced proficiency working "
    "work works used using use build building team teams role roles including etc"
)
"""Words that say nothing about what a line is about (shared-word matching skips them)."""

_CLAIM: Final = re.compile(
    r"\byou(?:['’]ve|['’]re|\s+(?:have|had|are|were|already|did|built|used|led|worked|wrote|shipped|managed|made|ran"
    r"|own|owned|clearly|obviously|currently|really))\b"
    r"|\byour\s+(?:experience|resume|cv|work|profile|background|portfolio|github|projects?|skills?|record|history)"
    r"\s+(?:shows?|showed|proves?|proved|demonstrates?|has|have|includes?|mentions?|says?|lists?|confirms?|already)\b"
    r"|\bI(?:['’]ve|['’]m|\s+(?:have|had|am|built|led|worked|wrote|shipped|managed|made|ran|own|owned|know))\b",
    re.IGNORECASE,
)
"""Sentences that say what the person has done, knows or is. A Fix step is advice: it may not assert any of it."""

_NOT_VERBS: Final = vocabulary(
    "a an the this that these those it its you your yours i we they he she there here my our his her their and or but "
    "so if when while as at in on for from to of by with without about after before because since also then now more "
    "most some any no not never always often maybe once one two three each every other another what why how who which "
    "where all both either neither only just very really"
)

_OPENERS: Final = vocabulary(
    "i we my our the a an it its this that these those there here and but so if as at in on for to with from by of after "
    "before when while since because then first next last today once now also still just later earlier during over under "
    "through about between each every both either one two three four five six seven eight nine ten though although "
    "however instead whenever where what why how who which"
)
"""Words that open a sentence without being a name."""

_IRREGULAR_PAST: Final = vocabulary(
    "built led ran wrote made won taught grew cut put set sold got found drove spoke took brought kept held began rose "
    "drew chose split swept shut"
)
"""Verbs that open a sentence without ending in "ed" ("Built the page ...")."""

_INFLATION: Final[dict[str, frozenset[str]]] = {
    group: frozenset(forms.split())
    for group, forms in {
        "led": "led lead leads leading leader leaders",
        "headed": "headed head heads",
        "founded": "founded founder founders founding cofounded cofounder",
        "managed": "managed manage manages managing manager managers",
        "directed": "directed directs directing director directors",
        "spearheaded": "spearheaded spearhead spearheads spearheading",
        "owned": "owned own owns owning owner owners ownership",
        "architected": "architected architect architects architecting",
    }.items()
}
_INFLATION_GROUP: Final[dict[str, str]] = {form: g for g, forms in _INFLATION.items() for form in forms}
_POSSESSIVE: Final = vocabulary("her his their my our your its whose own")


def has_personality(text: str, around: str = "") -> bool:
    """Whether ``text`` names a trait of the person (confidence, tone, accent, energy, filler, culture fit ...). A word the
    person's own answers or sources use ("an energy company") is their content, not a reading of them: pass those as ``around``."""
    for found in PERSONALITY.finditer(text):
        stem = re.escape(found.group().lower()[:6])
        if not (around and re.search(rf"\b{stem}", around, re.IGNORECASE)):
            return True
    return False


def few_words(text: str, limit: int = 32) -> str:
    """The first words of ``text`` that fit ``limit`` characters, without trailing punctuation ("Strong proficiency in React")."""
    out = ""
    for word in tidy(text).split(" "):
        nxt = f"{out} {word}" if out else word
        if len(nxt) > limit:
            break
        out = nxt
    out = out.rstrip(" ,.;:()-–—?!\"'“”‘’")
    return out or tidy(text)[:limit].rstrip(" ,.;:()-–—?!")


def clip_quote(text: str, limit: int = 90) -> str:
    """A quote shortened at a word boundary for a sentence that carries it ("…" when cut)."""
    return clip_words(text, limit)


def unsupported(text: str, *parts: str) -> list[str]:
    """The numbers, names and identifiers of ``text`` that none of ``parts`` contains (see `portfolio.ground`)."""
    return unsupported_anchors(text, Corpus.of("\n".join(parts)))


def unseen_openers(text: str, *parts: str) -> list[str]:
    """Capitalised words that open a sentence of ``text`` and are neither common opening words, verbs ("Built", "Fixing") nor
    in ``parts``: a company or a name placed first. `unsupported` leaves them out because every sentence opens with a capital."""
    corpus = Corpus.of("\n".join(parts))
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        match = _OPENING.match(sentence)
        if match is None:
            continue
        word = match.group(1)
        low = word.lower()
        if (
            not word[0].isupper()
            or word.isupper()
            or low in _OPENERS
            or low.endswith(("ed", "ing"))
            or low in _IRREGULAR_PAST
            or corpus.has_word(low)
        ):
            continue
        found.append(word)
    return found


def inflated(text: str, corpus_text: str) -> list[str]:
    """Seniority verbs ("led", "owned", "managed" ...) of ``text`` that ``corpus_text`` does not use in some form."""
    tokens = _WORD.findall(fold(text))
    known = set(_WORD.findall(fold(corpus_text)))
    found: list[str] = []
    seen: set[str] = set()
    for i, word in enumerate(tokens):
        group = _INFLATION_GROUP.get(word)
        if group is None or group in seen:
            continue
        if word == "own" and i > 0 and tokens[i - 1] in _POSSESSIVE:
            continue
        seen.add(group)
        if not (_INFLATION[group] & known):
            found.append(word)
    return found


def claims_person(text: str) -> bool:
    """Whether ``text`` says what the person has done, knows or is ("you built", "your experience shows" ...)."""
    return _CLAIM.search(text) is not None


def starts_with_verb(text: str) -> bool:
    """Whether the first word of ``text`` reads as an imperative verb ("Find", "Write", "Pick"), not a noun or a pronoun."""
    first = tidy(text).split(" ")[0].strip("\"'“”‘’([") if tidy(text) else ""
    word = first.lower()
    if not word.isalpha() or len(word) < 2 or word in _NOT_VERBS:
        return False
    if word.endswith(("ing", "ed", "ly")):
        return False
    return not (word.endswith("s") and not word.endswith(("ss", "us", "is")))


def placeholders_of(text: str) -> list[str] | None:
    """The ``[placeholders]`` of ``text`` in order, or None when brackets are malformed (stray, nested, or not 2 to 60
    characters)."""
    found = _PLACEHOLDER.findall(text)
    rest = _PLACEHOLDER.sub("", text)
    if "[" in rest or "]" in rest:
        return None
    if any(not (PLACEHOLDER_MIN <= len(p.strip()) <= PLACEHOLDER_MAX) for p in found):
        return None
    return [p.strip() for p in found]


def outside_brackets(text: str) -> str:
    """``text`` with every ``[placeholder]`` taken out: what is left is what code must be able to source."""
    return " ".join(_PLACEHOLDER.sub(" ", text).split())


def numbers_of(text: str) -> frozenset[str]:
    """The numbers of ``text`` as comparable keys (``10,000`` equals ``10000``)."""
    return number_keys(text)


def tokens(text: str) -> set[str]:
    """The lower-cased, lightly stemmed words of ``text`` that say something (no stop words, at least 3 characters)."""
    out: set[str] = set()
    for word in _WORD.findall(fold(text)):
        if len(word) < 3 or word in STOPWORDS:
            continue
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word)
    return out


def shared_words(a: str, b: str) -> int:
    """How many meaningful words ``a`` and ``b`` share."""
    return len(tokens(a) & tokens(b))


def fold_all(texts: Iterable[str]) -> list[str]:
    return [fold(t) for t in texts]


def passage(quote: str, folded_texts: Sequence[str], *, min_words: int = 3, max_words: int = 40) -> bool:
    """Whether ``quote`` is a passage of one of the folded texts (case, spacing, quote marks and dashes ignored).

    With the defaults it is `jobsearch.judge.quoted` (3 to 40 words). A work sample's code is shorter and longer than
    speech, so its callers widen the limits; a passage is never shorter than 5 characters.
    """
    if min_words == 3 and max_words == 40:
        return any(quoted(quote, folded) for folded in folded_texts)
    words = quote.split()
    if not (min_words <= len(words) <= max_words) or len(quote.strip()) < 5:
        return False
    needle = fold(quote)
    return bool(needle) and any(needle in folded for folded in folded_texts)


_QUOTE_CLASSES: Final[dict[str, str]] = {
    "'": "['’‘]",
    "’": "['’‘]",
    "‘": "['’‘]",
    '"': '["“”]',
    "“": '["“”]',
    "”": '["“”]',
    "-": "[-‐‑‒–—―]",
    "–": "[-‐‑‒–—―]",
    "—": "[-‐‑‒–—―]",
}


def locate(answer: str, quote: str) -> str:
    """The wording of ``answer`` that ``quote`` stands for: the quote itself when it is there letter for letter, else the
    stretch of ``answer`` that matches it ignoring case, spacing, quote marks and dashes, else ``quote`` as given."""
    wanted = quote.strip()
    if wanted and wanted in answer:
        return wanted
    words = wanted.split()
    if not words:
        return wanted
    pattern = r"\s+".join("".join(_QUOTE_CLASSES.get(c) or re.escape(c) for c in word) for word in words)
    match = re.search(pattern, answer, re.IGNORECASE)
    return match.group(0) if match else wanted


__all__ = [
    "PERSONALITY",
    "PLACEHOLDER_MAX",
    "PLACEHOLDER_MIN",
    "STOPWORDS",
    "claims_person",
    "clean_quote",
    "clip_quote",
    "few_words",
    "fold",
    "fold_all",
    "has_injection",
    "has_personality",
    "inflated",
    "locate",
    "numbers_of",
    "outside_brackets",
    "passage",
    "placeholders_of",
    "quoted",
    "shared_words",
    "starts_with_verb",
    "tokens",
    "unseen_openers",
    "unsupported",
]
