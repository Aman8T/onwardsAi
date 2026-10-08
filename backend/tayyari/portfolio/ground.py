"""Grounding: is every sentence of a portfolio copy slot backed by the facts it cites?

`check_slot` is the strict, code-only check (no model). A slot passes when, for the facts it cites and no others:

  1. it fits its character limit (`SLOT_LIMITS`), is plain text (no HTML or markdown), and has no private detail
     (email, grade);
  2. the corpus is not empty and every cited fact id exists (an empty corpus is a failure: it would let anything
     through, as `code_check`'s ``no_source`` does for a scanned resume);
  3. every checkable token of every sentence is found in the corpus: numbers (``10,000`` equals ``10000``),
     months, identifiers (``motion-kit``, ``Next.js``) and capitalised names. The match is strict and
     word-bounded (``Java`` does not match ``JavaScript``) and case-insensitive; spelled-out numbers (``two`` ...
     ``twenty``) count as numbers; a capitalised word that opens a sentence (or any word of a headline) must be in
     the corpus, a function word or an ordinary verb;
  4. seniority verbs (led, headed, founded, managed, directed, spearheaded, owned, architected) appear in the
     corpus in some form, and puffery (passionate, world-class, rockstar ...) does not appear unless the corpus
     itself says it.

The corpus of a slot is the verbatim quotes of its cited facts plus their structured fields and highlights
(`fact_text`), never every fact: a number that belongs to fact B is not allowed in a slot that cites only fact A.
The person's own name, role, headline and location (``person``) are allowed words as well.

`verify_slots` runs `check_slot`, then asks the decision service (`check_copy`) about every sentence of the slots
that passed: a sentence the cited facts contradict, or say nothing about, fails its slot. With decisions off
(`NullDecisionService`) the code check stands alone.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from tayyari.contract.portfolio import FACT_SLOT_PREFIXES, CopySlot, Fact, PortfolioPerson, slot_limit
from tayyari.core.logging import get_logger
from tayyari.decisions.packs.evidence import Anchor, extract_anchors, fold
from tayyari.decisions.service import CopyClaim, CopyVerdict, DecisionContext, DecisionService

log = get_logger(__name__)

ANCHOR_LIMIT: Final = 40
"""Anchors taken from one sentence (the evidence default of 12 is for single claims; a slot has several sentences)."""
MAX_EXP_LINES: Final = 3
"""An ``exp:<id>`` slot is up to this many lines."""

# ───────────────────────── text helpers (shared with facts.py) ─────────────────────────

_TOKEN = re.compile(r"[^\W_]+")
_NUMBER = re.compile(r"\d[\d,_]*(?:\.\d+)?")
_WORD = re.compile(r"[^\W\d_][^\W_]*")


def vocabulary(words: str) -> frozenset[str]:
    """The set of whitespace-separated words of ``words`` (a readable way to write a word list)."""
    return frozenset(words.split())


_MONTH_NAMES: Final = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
MONTH_INDEX: Final[dict[str, int]] = {
    **{name: i + 1 for i, name in enumerate(_MONTH_NAMES)},
    **{name[:3]: i + 1 for i, name in enumerate(_MONTH_NAMES)},
    "sept": 9,
}
MONTH_ABBR: Final = tuple(name[:3].capitalize() for name in _MONTH_NAMES)
MONTH_LONG: Final = tuple(name.capitalize() for name in _MONTH_NAMES)


def tokens_of(text: str) -> list[str]:
    """Case- and accent-folded alphanumeric tokens of ``text`` in reading order."""
    return _TOKEN.findall(fold(text))


def number_keys(text: str) -> frozenset[str]:
    """The numbers in ``text`` as comparable keys (separators removed: ``10,000`` and ``10000`` are the same)."""
    return frozenset(m.group().replace(",", "").replace("_", "") for m in _NUMBER.finditer(fold(text)))


def tidy_text(value: str) -> str:
    """Whitespace collapsed to single spaces."""
    return " ".join(value.split())


def short(text: str, limit: int = 60) -> str:
    """``text`` cut to ``limit`` characters for a problem message."""
    one = tidy_text(text)
    return one if len(one) <= limit else f"{one[: limit - 1].rstrip()}…"


@dataclass(frozen=True, slots=True)
class Corpus:
    """The words a slot may use, prepared once: folded tokens in order, a token set, stems, numbers and months."""

    text: str
    tokens: tuple[str, ...]
    token_set: frozenset[str]
    stems: frozenset[str]
    joined: str
    numbers: frozenset[str]
    months: frozenset[int]

    @classmethod
    def of(cls, text: str) -> Corpus:
        toks = tuple(tokens_of(text))
        return cls(
            text=text,
            tokens=toks,
            token_set=frozenset(toks),
            stems=frozenset(_stem(t) for t in toks),
            joined=f" {' '.join(toks)} ",
            numbers=number_keys(text),
            months=frozenset(MONTH_INDEX[t] for t in toks if t in MONTH_INDEX),
        )

    def has_word(self, token: str) -> bool:
        """The whole token, allowing a plural on either side (``component`` and ``components``)."""
        if token in self.token_set:
            return True
        if len(token) >= 5 and token.endswith("s") and token[:-1] in self.token_set:
            return True
        return len(token) >= 4 and f"{token}s" in self.token_set

    def has_sequence(self, parts: Sequence[str]) -> bool:
        return f" {' '.join(parts)} " in self.joined


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


# ───────────────────────── the corpus of a slot ─────────────────────────


def _period_words(value: str | None) -> str:
    """``2025-07`` as words a sentence may repeat: ``2025-07 Jul 2025 July 2025``."""
    if not value:
        return ""
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?", value.strip())
    if not m:
        return value
    year, month = m.group(1), m.group(2)
    if month and 1 <= int(month) <= 12:
        i = int(month) - 1
        return f"{value} {MONTH_ABBR[i]} {year} {MONTH_LONG[i]} {year}"
    return value


def fact_text(fact: Fact) -> str:
    """Everything one fact vouches for: its quotes, title, organisation, place, period, detail, highlights and tags.

    The grade is left out on purpose (it is private by default and never belongs in copy).
    """
    parts: list[str | None] = [ref.quote for ref in fact.sourceRefs]
    parts += [fact.title, fact.org, fact.location, _period_words(fact.start), _period_words(fact.end)]
    if fact.current:
        parts.append("Present now")
    parts.append(fact.detail)
    parts += fact.highlights
    parts += fact.tags
    return "\n".join(p for p in parts if p)


def person_text(person: PortfolioPerson | None) -> str:
    """The person's name, headline, role, place and years: words their copy may use besides the facts."""
    if person is None:
        return ""
    years = f"{person.yearsExperience:g} years" if person.yearsExperience is not None else ""
    return "\n".join(p for p in (person.name, person.headline, person.role, person.location, years) if p)


def slot_corpus(
    slot: CopySlot, facts_by_id: Mapping[str, Fact], person: PortfolioPerson | None = None
) -> str:
    """The text a slot's sentences may rest on: the cited facts (those that exist), plus the person's words."""
    seen: dict[str, None] = dict.fromkeys(slot.factIds)
    pieces = [fact_text(facts_by_id[i]) for i in seen if i in facts_by_id]
    extra = person_text(person)
    if extra and (pieces or slot.origin == "resume"):
        pieces.append(extra)
    return "\n".join(pieces)


# ───────────────────────── sentences ─────────────────────────

_ABBREVIATIONS: Final = vocabulary(
    "mr mrs ms dr st inc ltd co corp vs etc jr sr prof no fig approx e.g i.e u.s a.m p.m"
)
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\"'“‘(\[]?[A-Z0-9])")
_LIST_MARK = re.compile(r"^\s*(?:[-*+•–—]|\d+[.)])\s+")


def split_sentences(text: str) -> list[str]:
    """The sentences of ``text``: every line is split at ``. ! ?`` followed by a capital (not after ``St.``, ``Dr.``)."""
    out: list[str] = []
    for raw in text.split("\n"):
        line = _LIST_MARK.sub("", raw).strip()
        if not line:
            continue
        start = 0
        for m in _BOUNDARY.finditer(line):
            last = line[start : m.start()].split()[-1:] or [""]
            word = last[0].rstrip(".!?").lower()
            if word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                continue
            out.append(line[start : m.start()].strip())
            start = m.end()
        tail = line[start:].strip()
        if tail:
            out.append(tail)
    return [s for s in out if s]


# ───────────────────────── lints ─────────────────────────

_HTML = re.compile(r"</?[A-Za-z][^>]*>|<!--|&(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);")
_MARKDOWN = re.compile(
    r"\*\*|__|~~|`|\]\(|^\s{0,3}#{1,6}\s|^\s*>\s?|^\s*(?:[-*+•]|\d+[.)])\s"
    r"|(?<![\w*])\*[^\s*][^*\n]*\*(?![\w*])|(?<![\w_])_[^\s_][^_\n]*_(?![\w_])",
    re.MULTILINE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_GRADE = re.compile(r"\b(?:cgpa|gpa)\b", re.IGNORECASE)

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
    )
)
"""Banned puffery, as `(label, pattern)`. Allowed only where the cited facts themselves use the word."""

_INFLATION: Final[dict[str, frozenset[str]]] = {
    "led": vocabulary("led lead leads leading leader leaders"),
    "headed": vocabulary("headed head heads"),
    "founded": vocabulary("founded founder founders founding cofounded cofounder"),
    "managed": vocabulary("managed manage manages managing manager managers"),
    "directed": vocabulary("directed directs directing director directors"),
    "spearheaded": vocabulary("spearheaded spearhead spearheads spearheading"),
    "owned": vocabulary("owned own owns owning owner owners ownership"),
    "architected": vocabulary("architected architect architects architecting"),
}
_INFLATION_GROUP: Final[dict[str, str]] = {
    form: group for group, forms in _INFLATION.items() for form in forms
}
_POSSESSIVE: Final = vocabulary("her his their my our your its whose own")

_GENDERED: Final = vocabulary("she he her hers him his herself himself")

_UNITS: Final = (
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
)
_NUMBER_WORDS: Final[dict[str, int]] = {word: n for n, word in enumerate(_UNITS, start=2)} | {
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_MULTIPLIERS: Final = vocabulary("hundred thousand million billion lakh crore")


def _lint(text: str, *, multiline: bool) -> list[str]:
    problems: list[str] = []
    if _HTML.search(text):
        problems.append("contains HTML; copy is plain text")
    if _MARKDOWN.search(text):
        problems.append(
            "contains markdown (bullets, asterisks, backticks, headings or links); copy is plain text"
        )
    if not multiline and "\n" in text:
        problems.append("contains a line break; this slot is a single paragraph")
    if any(ord(ch) < 32 and ch != "\n" for ch in text):
        problems.append("contains control characters")
    if any(
        unicodedata.category(ch) == "So" and (ord(ch) >= 0x1F000 or 0x2600 <= ord(ch) <= 0x27BF)
        for ch in text
    ):
        problems.append("contains an emoji or symbol; copy is plain text")
    if _EMAIL.search(text):
        problems.append("contains an email address; contact details are private")
    if _URL.search(text):
        problems.append("contains a web address; links are shown separately")
    if _GRADE.search(text):
        problems.append("mentions a grade; grades are private")
    return problems


# ───────────────────────── anchors ─────────────────────────

_CLAUSE_END: Final = ".!?:;-*" + chr(0x2022) + chr(0x2013) + chr(0x2014)
_OPENERS: Final = "\"'“‘([ "


def _clause_start(text: str, pos: int) -> bool:
    before = text[:pos].rstrip(_OPENERS)
    return not before or before[-1] in _CLAUSE_END


_START_WORDS: Final = vocabulary(
    "a an the this that these those it its i we he she they you your his her their our my me us them there here "
    "and or but so if then than as at by for from in into of on onto over to up down with within without about "
    "after before during since until while who whom whose which what when where why how also only both each "
    "every many most more some such other another any all no not very just still already now currently "
    "recently previously formerly today lately first second third last next new early late once twice across "
    "around along among behind below beside between beyond despite except inside near off out outside past per "
    "plus through throughout toward towards under unlike upon via versus whether either neither because "
    "although though however instead meanwhile otherwise thus therefore based one two three four five six "
    "seven eight nine ten full part former senior junior strong small large single end open "
    "earlier later afterwards afterward overall alongside together often always usually sometimes never ever soon yet "
    "even much few several various own same different early lately initially originally eventually finally "
    "additionally furthermore likewise similarly notably especially mainly mostly largely today tonight "
    "yesterday tomorrow here there everywhere nowhere whenever wherever whatever whoever"
)

_COMMON_VERBS: Final = vocabulary(
    "build rebuild ship write rewrite design redesign mentor run launch cut reduce improve increase grow create "
    "develop deliver maintain work help teach publish speak study join review test fix add make move turn take "
    "bring drive win lift raise drop trim save speed migrate replace automate analyse analyze plan "
    "organise organize begin start finish complete open close keep set put get give find see use pair partner "
    "coordinate support wrote built ran took made won taught grew began shipped sit sat stand span bridge "
    "deploy document measure monitor optimise optimize refactor release prototype craft shape serve "
    "power handle track connect integrate collect clean visualise visualize explain translate train ask answer "
    "present share contribute volunteer judge tutor edit report host record cover sell buy hire "
    "sharpen simplify streamline unify upgrade modernise modernize stabilise stabilize secure harden "
    "reach hit earn land top clear cross claim gain grab post log pick push pull hold kick play ramp roll send "
    "show sign skip slash solve spin sort stack steer step stop strike swap switch tackle tell tie tune unlock "
    "untangle update wrap break call carry catch change check chase choose climb code cook draft draw ease enter "
    "expand extend fill flip focus follow form fuel gather guide hand heal jump kill lay learn leave link list "
    "load lock look map mark match mend merge mine mix model name nail order pack paint parse pass patch pay "
    "pitch place plant polish pour prep press price print probe process produce promote prove rank read refine "
    "remove rename repair rescue reshape resolve restore return revive ride rise route sail scan score search "
    "seed select settle shift shrink size sketch spark split spot spread stage stitch store stream strengthen "
    "stretch submit supply sweep talk tame target team think tighten type unblock value verify view visit walk "
    "watch wire halve double triple quadruple slim sharpen anchor back bake boost cap champion convert crack "
    "debug decode defend dig drill drop fold grade guard handle iron lead lend lighten loosen mold net "
    "overhaul pilot plug prune pursue quantify reboot recover refresh renew reorganise reorganize reuse "
    "reveal rework rotate rush safeguard shorten smooth speed stand steady surface sustain tackle taste "
    "thread trace trade transform transition trigger turnaround uncover unpack uplift validate weave widen"
)


def _verbish(word: str) -> bool:
    """Is ``word`` an ordinary verb form (``rebuilds``, ``shipped``, ``running``) of the short list above?"""
    if word in _COMMON_VERBS:
        return True
    bases: set[str] = set()
    if word.endswith(("ies", "ied")):
        bases.add(word[:-3] + "y")
    if word.endswith("es"):
        bases.update((word[:-2], word[:-1]))
    if word.endswith("s"):
        bases.add(word[:-1])
    if word.endswith("ed"):
        bases.update((word[:-2], word[:-1]))
        if len(word) > 4 and word[-3] == word[-4]:
            bases.add(word[:-3])  # shipped -> ship
    if word.endswith("ing"):
        bases.update((word[:-3], word[:-3] + "e"))
        if len(word) > 5 and word[-4] == word[-5]:
            bases.add(word[:-4])  # running -> run
    return any(base in _COMMON_VERBS for base in bases)


def _loose_known(word: str, corpus: Corpus) -> bool:
    if corpus.has_word(word) or _stem(word) in corpus.stems:
        return True
    if word in _START_WORDS:
        return True
    if len(word) > 4 and word.endswith("ly"):
        return True
    return _verbish(word)


def _is_title_word(text: str) -> bool:
    return text.isalpha() and text[:1].isupper() and text[1:].islower()


def _month_mentions(sentence: str) -> list[tuple[str, int]]:
    """Capitalised month names of a sentence (``May`` only next to a year or a day) and their month numbers."""
    out: list[tuple[str, int]] = []
    for m in re.finditer(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
        r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b",
        sentence,
    ):
        name = m.group(1)
        if name == "May":
            after = sentence[m.end() :]
            before = sentence[: m.start()]
            if not (
                re.match(r"\s+(?:\d{1,2}[,\s]+)?\d{4}", after)
                or re.search(r"\d{1,2}(?:st|nd|rd|th)?\s+$", before)
            ):
                continue
        out.append((name, MONTH_INDEX[name.lower()]))
    return out


_WORD_COMPOUND = re.compile(r"[^\W\d_]+(?:[-_][^\W\d_]+)+")


def _anchor_found(anchor: Anchor, corpus: Corpus) -> bool:
    """Strict, word-bounded: ``Java`` is not ``JavaScript``; ``front-end`` may be ``front end`` or ``frontend``."""
    if anchor.kind == "number":
        return anchor.key in corpus.numbers
    parts = tokens_of(anchor.text)
    if not parts:
        return True
    if len(parts) == 1:
        return corpus.has_word(parts[0])
    if "".join(parts) in corpus.token_set or corpus.has_sequence(parts):
        return True
    if anchor.kind == "identifier" and _WORD_COMPOUND.fullmatch(anchor.text):
        # "spring-physics": both words must be in the facts; "React-based": only the named part has to be
        raw = re.split(r"[-_]", anchor.text)
        needed = [p for p in raw if not p.islower()] or raw
        return all(corpus.has_word(w) for part in needed for w in tokens_of(part))
    return False


def unsupported_anchors(text: str, corpus: Corpus) -> list[str]:
    """The numbers, identifiers and names of ``text`` that ``corpus`` does not contain (same strict matcher as slots)."""
    return [a.text for a in extract_anchors(text, limit=ANCHOR_LIMIT) if not _anchor_found(a, corpus)]


def _anchor_problems(sentence: str, corpus: Corpus, *, headline: bool) -> list[str]:
    problems: list[str] = []
    loose: list[tuple[str, int]] = []
    for anchor in extract_anchors(sentence, limit=ANCHOR_LIMIT):
        if anchor.kind == "name" and headline and _is_title_word(anchor.text):
            continue  # every word of a headline is capitalised: handled with the loose words below
        if not _anchor_found(anchor, corpus):
            what = f"the number {anchor.text}" if anchor.kind == "number" else f'"{anchor.text}"'
            problems.append(f"{what} is not in the facts this slot cites")
    for name, month in _month_mentions(sentence):
        if month not in corpus.months:
            problems.append(f"the month {name} is not in the facts this slot cites")
    tokens = tokens_of(sentence)
    for token in tokens:
        value = _NUMBER_WORDS.get(token)
        if value is not None and not (token in corpus.token_set or str(value) in corpus.numbers):
            problems.append(f'the number "{token}" is not in the facts this slot cites')
        elif token in _MULTIPLIERS and token not in corpus.token_set:
            problems.append(f'the number word "{token}" is not in the facts this slot cites')
    for m in _WORD.finditer(sentence):
        word = m.group()
        if len(word) < 2 or not word[0].isupper() or word.isupper() or any(c.isupper() for c in word[1:]):
            continue
        if headline or _clause_start(sentence, m.start()):
            loose.append((word, m.start()))
    for word, _ in loose:
        folded = fold(word)
        if not _loose_known(folded, corpus):
            problems.append(f'"{word}" is not in the facts this slot cites')
    return problems


def _seniority_problems(sentence: str, corpus: Corpus) -> list[str]:
    problems: list[str] = []
    tokens = tokens_of(sentence)
    seen: set[str] = set()
    for i, word in enumerate(tokens):
        group = _INFLATION_GROUP.get(word)
        if group is None or group in seen:
            continue
        if word == "own" and i > 0 and tokens[i - 1] in _POSSESSIVE:
            continue
        seen.add(group)
        if not any(form in corpus.token_set for form in _INFLATION[group]):
            problems.append(f'"{word}" claims seniority the cited facts do not state')
    return problems


def _puffery_problems(sentence: str, corpus_text: str) -> list[str]:
    problems: list[str] = []
    for label, pattern in _PUFFERY:
        if pattern.search(sentence) and not pattern.search(corpus_text):
            problems.append(f'"{label}" is puffery; describe what they did instead')
    return problems


def _pronoun_problems(sentence: str, corpus: Corpus) -> list[str]:
    found = sorted({t for t in tokens_of(sentence) if t in _GENDERED and t not in corpus.token_set})
    if not found:
        return []
    return [
        f'the pronoun "{found[0]}" assumes a gender the facts do not state; use the first name or no pronoun'
    ]


# ───────────────────────── check_slot ─────────────────────────


@dataclass(frozen=True, slots=True)
class SlotCheck:
    """The code verdict on one slot: ``problems`` read as notes for the model's re-ask."""

    slot_id: str
    ok: bool
    problems: list[str] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)


def check_slot(
    slot: CopySlot, facts_by_id: Mapping[str, Fact], *, person: PortfolioPerson | None = None
) -> SlotCheck:
    """Validate ``slot`` against the facts it cites (see the module docstring).

    ``person`` adds the person's name, headline, role and place to the allowed words. A slot of origin ``resume``
    that cites no facts (the deterministic fallback for a headline made of the person's own name and role) is
    accepted only when ``person`` is given and its words cover the text.
    """
    problems: list[str] = []
    text = slot.text
    limit = slot_limit(slot.id)
    if limit is None:
        problems.append(f"{slot.id!r} is not a known slot")
    elif len(text) > limit:
        problems.append(f"the text is {len(text)} characters; the limit is {limit}")

    multiline = slot.id.startswith("exp:")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if multiline and len(lines) > MAX_EXP_LINES:
        problems.append(f"{len(lines)} lines; an experience slot has at most {MAX_EXP_LINES}")
    problems += _lint(text, multiline=multiline)

    fact_prefix, _, fact_ref = slot.id.partition(":")
    if fact_ref and fact_prefix in ("exp", "proj") and text.strip():
        if fact_ref not in facts_by_id:
            problems.append(f"{slot.id!r} belongs to a fact that does not exist")
        elif facts_by_id[fact_ref].kind != FACT_SLOT_PREFIXES[fact_prefix]:
            problems.append(f"{slot.id!r} must belong to a {FACT_SLOT_PREFIXES[fact_prefix]} fact")
        elif fact_ref not in slot.factIds:
            problems.append(f"{slot.id!r} must cite fact {fact_ref}")

    sentences = split_sentences(text)
    if not text.strip():
        return SlotCheck(slot.id, not problems, problems, [])

    cited_ids = list(dict.fromkeys(slot.factIds))
    unknown = [i for i in cited_ids if i not in facts_by_id]
    problems += [f'unknown factId "{fact_id}"' for fact_id in unknown]
    own_words = slot.origin == "resume" and person is not None and not cited_ids
    if not cited_ids and not own_words:
        problems.append("cites no facts; every slot must name the facts it rests on")
    if len(unknown) == len(cited_ids) and not own_words:
        return SlotCheck(slot.id, False, _dedupe(problems), sentences)
    corpus_text = slot_corpus(slot, facts_by_id, person)
    corpus = Corpus.of(corpus_text)
    headline = slot.id == "pressHeadline"
    for sentence in sentences:
        problems += _anchor_problems(sentence, corpus, headline=headline)
        problems += _seniority_problems(sentence, corpus)
        problems += _puffery_problems(sentence, corpus_text)
        problems += _pronoun_problems(sentence, corpus)
    problems = _dedupe(problems)
    return SlotCheck(slot.id, not problems, problems, sentences)


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


# ───────────────────────── verify_slots ─────────────────────────


async def verify_slots(
    slots: Sequence[CopySlot],
    facts: Sequence[Fact],
    decisions: DecisionService,
    ctx: DecisionContext,
    *,
    person: PortfolioPerson | None = None,
) -> tuple[list[CopySlot], dict[str, list[str]]]:
    """Code check, then the decision service's claim check, for each slot.

    Returns ``(kept slots, problems by slot id)``. A slot with no text makes no claim: it is neither kept nor
    reported (the caller fills it from the fallback). With decisions off, or when the service fails or answers
    late, only the code check applies (the service fails open by contract).
    """
    facts_by_id = {f.id: f for f in facts}
    problems: dict[str, list[str]] = {}
    passed: list[CopySlot] = []
    claims: list[CopyClaim] = []
    for slot in slots:
        verdict = check_slot(slot, facts_by_id, person=person)
        if not verdict.ok:
            problems[slot.id] = verdict.problems
            continue
        if not verdict.sentences:
            continue
        passed.append(slot)
        corpus = slot_corpus(slot, facts_by_id, person)
        claims.extend(CopyClaim(slot.id, sentence, corpus) for sentence in verdict.sentences)

    verdicts = await _ask(decisions, claims, ctx)
    dropped: dict[str, list[str]] = {}
    for claim, answer in zip(claims, verdicts, strict=True):
        if answer is not None and not answer.keep:
            why = answer.reason or answer.relation or "not supported"
            dropped.setdefault(claim.slot_id, []).append(f'"{short(claim.sentence)}" is not supported: {why}')

    kept: list[CopySlot] = []
    for slot in passed:
        if slot.id in dropped:
            problems[slot.id] = dropped[slot.id]
        else:
            kept.append(slot)
    return kept, problems


async def _ask(
    decisions: DecisionService, claims: Sequence[CopyClaim], ctx: DecisionContext
) -> list[CopyVerdict | None]:
    """One verdict per claim (None when there is none): a missing or failing check keeps the sentence."""
    check = getattr(decisions, "check_copy", None)
    if check is None or not claims:
        return [None] * len(claims)
    try:
        answers: list[CopyVerdict] = await check(list(claims), ctx)
    except Exception as exc:
        log.warning("portfolio_copy_check_failed", error_type=type(exc).__name__)
        return [None] * len(claims)
    if len(answers) == len(claims):
        return list(answers)
    by_key = {(a.slot_id, a.sentence): a for a in answers}
    return [by_key.get((c.slot_id, c.sentence)) for c in claims]


__all__ = [
    "ANCHOR_LIMIT",
    "MAX_EXP_LINES",
    "MONTH_ABBR",
    "MONTH_INDEX",
    "MONTH_LONG",
    "Corpus",
    "SlotCheck",
    "check_slot",
    "fact_text",
    "number_keys",
    "person_text",
    "short",
    "slot_corpus",
    "split_sentences",
    "tidy_text",
    "tokens_of",
    "unsupported_anchors",
    "verify_slots",
    "vocabulary",
]
