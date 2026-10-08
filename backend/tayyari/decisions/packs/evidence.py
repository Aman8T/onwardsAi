"""Evidence support: does the source behind a skill's evidence line really say it?

Two stages, code first.

**Code stage** (pure functions, no model). The claim is normalised (NFKC, case-folded, curly quotes straightened,
ellipses and runs of whitespace collapsed). Then, in order:

    no_source       the source has no raw text (a scanned resume is read from its PDF): nothing to check against,
                    keep the claim
    verbatim        the claim, or every fragment of it around an ellipsis, appears in the source text: keep
    no_anchors      the claim has nothing checkable (no number, identifier or name): keep
    anchors_ok      every anchor (numbers, identifiers such as ``motion-kit``, capitalised names and acronyms) is found
                    in the source text, in the reader output JSON for that source, or in the facts JSON: keep
    anchors_missing at least one anchor is nowhere to be found: ask Jev

Numbers are never left to Jev. The check is lenient on purpose (plural forms, hyphen or space, accents,
substring matches of four characters or more): a missed anchor only costs one Jev question, never a drop.

**Jev stage**, only for ``anchors_missing`` claims, one question each over ``{"claim", "section"}`` where the section
is the best `JEV_EVIDENCE_WINDOW_CHARS` window of the source by token overlap. The question ``evidence.support``
is a three-way choice: supports / contradicts / says_nothing.

**Drop rule**: a claim is dropped only when the anchors are missing AND the relation is ``says_nothing`` or
``contradicts`` at confidence 0.8 or more. One signal alone keeps the claim. Anything that did not finish in time,
errored, or came back unsure keeps it too (fail open).

**Portfolio copy** reuses the same question (`copy_question`, `copy_verdict`): the generated sentence is the claim and
the verbatim quotes of the facts it cites are the source. There is no code stage (the portfolio grounder has already
checked the anchors, strictly) and no anchor short-cut: every sentence is asked. A sentence is dropped when the
relation is ``contradicts``, or ``says_nothing`` at confidence 0.8 or more; anything else keeps it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from ...core.limits import JEV_EVIDENCE_WINDOW_CHARS
from ...llm.jev import JevQuestion
from ..service import CopyClaim, CopyVerdict, EvidenceClaim, EvidenceVerdict
from ..spec import DecisionConfig, DecisionPack, DecisionResult, DecisionSpec
from .common import choice_label

PACK_ID = "evidence"
SPEC_ID = "evidence.support"
VERSION = "1"

SUPPORTS = "supports"
CONTRADICTS = "contradicts"
SAYS_NOTHING = "says_nothing"
RELATIONS = (SUPPORTS, CONTRADICTS, SAYS_NOTHING)
UNSUPPORTED = frozenset({CONTRADICTS, SAYS_NOTHING})

DROP_CONFIDENCE = 0.8
"""A claim is dropped only on a relation this sure (and with the anchors missing)."""
MAX_ANCHORS = 12
MIN_VERBATIM_CHARS = 8
CONCURRENCY = 8
"""Claims asked at once (the size of one wave of `DecisionRunner.run_many`)."""

CodeVerdict = Literal["no_source", "verbatim", "no_anchors", "anchors_ok", "anchors_missing"]


# ───────────────────────── the spec ─────────────────────────

SUPPORT_SPEC: DecisionSpec[str | None] = DecisionSpec(
    id=SPEC_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.choice(
        "How does the section of the source document relate to the claim?",
        {
            SUPPORTS: "The section states the claim, or directly implies that it is true",
            CONTRADICTS: "The section states the opposite of the claim, or implies that it is false",
            SAYS_NOTHING: "The section does not address what the claim asserts, either way",
        },
    ),
    interpret=choice_label,
    neutral=None,
    default=DecisionConfig(mode="primary", threshold=0.8, audit_rate=0.05, fallback="claude", timeout_s=4.0),
)


@dataclass(frozen=True, slots=True)
class EvidenceQuestion:
    """The subject of one evidence question: the claim and the best window of its source."""

    claim: str
    section: str


def build_state(subject: EvidenceQuestion) -> dict[str, Any]:
    return {"claim": subject.claim, "section": subject.section}


EVIDENCE_PACK: DecisionPack[EvidenceQuestion] = DecisionPack(
    id=PACK_ID, specs=(SUPPORT_SPEC,), build_state=build_state
)


# ───────────────────────── normalising ─────────────────────────

_QUOTES = str.maketrans(
    {
        0x2018: "'",
        0x2019: "'",
        0x201A: "'",
        0x201B: "'",
        0x2032: "'",
        0x201C: '"',
        0x201D: '"',
        0x201E: '"',
        0x201F: '"',
        0x2033: '"',
        0x00AB: '"',
        0x00BB: '"',
    }
)
_DOTS = re.compile(r"\.{2,}")
_SPACE = re.compile(r"\s+")
_ALNUM = re.compile(r"[^\W_]+")
_COMBINING = re.compile(f"[{chr(0x300)}-{chr(0x36F)}]")


def _tidy(text: str) -> str:
    """NFKC, straight quotes, case-folded (NFKC already turns the ellipsis character into three dots)."""
    return unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()


def normalise(text: str) -> str:
    """The comparison form of ``text``: NFKC, straight quotes, case-folded, ellipses and whitespace collapsed."""
    return _SPACE.sub(" ", _DOTS.sub(" ", _tidy(text))).strip()


def fragments(text: str) -> list[str]:
    """The normalised pieces of ``text`` between its ellipses (one piece when there is none)."""
    parts = (_SPACE.sub(" ", part).strip() for part in _DOTS.split(_tidy(text)))
    return [part for part in parts if part]


def fold(text: str) -> str:
    """Accent-stripped, case-folded text, for token comparison."""
    return _COMBINING.sub("", unicodedata.normalize("NFKD", text)).casefold()


# ───────────────────────── anchors ─────────────────────────


@dataclass(frozen=True, slots=True)
class Anchor:
    """A checkable token of a claim: ``text`` as written, ``key`` as compared (folded, alphanumerics only for names)."""

    kind: Literal["number", "identifier", "name"]
    text: str
    key: str


_IDENT = re.compile(r"(?<![\w.@/-])[^\W\d_][^\W_]*(?:[-_./][^\W_]+)+(?![\w-])")
_WORD = re.compile(r"[^\W\d_][^\W_]*(?:\+\+|#)?")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,_]*(?:\.\d+)?")
_CLAUSE_END = ".!?:;-*" + chr(0x2022) + chr(0x2013) + chr(0x2014)
"""Characters that end a clause: a capitalised word right after one is just the start of a sentence."""


def _vocabulary(words: str) -> frozenset[str]:
    return frozenset(words.split())


_STOP_CAPS = _vocabulary(
    "a an and or of in at on for to with by from via as my our the this that these those it its "
    "i we he she they you your his her their "
    "january february march april may june july august september october november december "
    "monday tuesday wednesday thursday friday saturday sunday"
)
_MIN_PART_CHARS = 2
"""An identifier whose parts are all one letter long ("e.g", "a/b") is not worth checking, nor is a one-letter word."""


def _digits_key(raw: str) -> str:
    return raw.replace(",", "").replace("_", "")


def _compact(text: str) -> str:
    return "".join(_ALNUM.findall(fold(text)))


def _clause_start(text: str, pos: int) -> bool:
    before = text[:pos].rstrip()
    return not before or before[-1] in _CLAUSE_END


def extract_anchors(text: str, *, limit: int = MAX_ANCHORS) -> list[Anchor]:
    """The checkable tokens of ``text``, in reading order and without duplicates, at most ``limit``.

    - identifiers: ``motion-kit``, ``user/repo``, ``next.js``, ``v2.1`` (a letter, then separator-joined parts)
    - names: capitalised words that do not open a clause, acronyms and mixed-case words (SQL, TypeScript, iOS),
      and words with a digit inside (k8s, web3)
    - numbers: ``42``, ``1,200``, ``2.4`` (not the digits inside an identifier)
    """
    found: list[tuple[int, Anchor]] = []
    claimed: list[tuple[int, int]] = []

    for m in _IDENT.finditer(text):
        parts = re.split(r"[-_./]", m.group())
        if all(len(p) < _MIN_PART_CHARS for p in parts):
            claimed.append((m.start(), m.end()))
            continue
        key = _compact(m.group())
        if key:
            found.append((m.start(), Anchor("identifier", m.group(), key)))
        claimed.append((m.start(), m.end()))

    for m in _WORD.finditer(text):
        if any(s <= m.start() < e for s, e in claimed):
            continue
        word = m.group()
        core = word.rstrip("+#")
        if len(core) < _MIN_PART_CHARS:
            continue
        has_digit = any(ch.isdigit() for ch in core)
        mixed = any(ch.isupper() for ch in core[1:])
        capitalised = (
            core[0].isupper() and not _clause_start(text, m.start()) and core.casefold() not in _STOP_CAPS
        )
        if has_digit or mixed or capitalised:
            key = _compact(word)
            if key:
                found.append((m.start(), Anchor("name", word, key)))
            claimed.append((m.start(), m.end()))

    for m in _NUMBER.finditer(text):
        if any(s <= m.start() < e for s, e in claimed):
            continue
        found.append((m.start(), Anchor("number", m.group(), _digits_key(m.group()))))

    found.sort(key=lambda item: item[0])
    out: list[Anchor] = []
    seen: set[tuple[bool, str]] = set()
    for _, anchor in found:
        marker = (anchor.kind == "number", anchor.key)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(anchor)
        if len(out) >= limit:
            break
    return out


# ───────────────────────── the source corpus ─────────────────────────


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


@dataclass(slots=True)
class SourceCorpus:
    """Everything a claim about one source may be checked against, prepared once per source."""

    raw: str
    normalised: str
    compact: str
    words: frozenset[str]
    numbers: frozenset[str]
    _positions: list[tuple[int, str]] | None = None

    def positions(self) -> list[tuple[int, str]]:
        """``(offset, case-folded word)`` for every word of the stripped source text, built once on first use
        (a window search then only walks this list instead of re-scanning a source of hundreds of kilobytes)."""
        if self._positions is None:
            self._positions = word_positions(self.raw.strip())
        return self._positions


def build_corpus(
    source_text: str, reader_output: dict[str, Any] | None = None, facts: dict[str, Any] | None = None
) -> SourceCorpus:
    """The source text, the reader output JSON and the facts JSON as one searchable corpus.

    ``normalised`` is the source text alone (for the verbatim check); ``compact``, ``words`` and ``numbers`` cover all three.
    """
    pieces = [source_text]
    pieces.extend(_dump(extra) for extra in (reader_output, facts) if extra)
    folded = fold("\n".join(pieces))
    tokens = _ALNUM.findall(folded)
    numbers = frozenset(_digits_key(n) for n in re.findall(r"\d[\d,_]*(?:\.\d+)?", folded))
    return SourceCorpus(
        raw=source_text,
        normalised=normalise(source_text),
        compact="".join(tokens),
        words=frozenset(tokens),
        numbers=numbers,
    )


def anchor_found(anchor: Anchor, corpus: SourceCorpus) -> bool:
    """Is ``anchor`` somewhere in ``corpus``? Lenient: plurals, hyphen or space, accents, short names as whole words."""
    key = anchor.key
    if anchor.kind == "number":
        return key in corpus.numbers
    candidates = [key]
    if len(key) >= 5 and key.endswith("s"):
        candidates.append(key[:-1])
    for candidate in candidates:
        if len(candidate) >= 4:
            if candidate in corpus.compact:
                return True
        elif candidate in corpus.words:
            return True
    if anchor.kind == "identifier":
        parts = [p for p in _ALNUM.findall(fold(anchor.text)) if p]
        return bool(parts) and all(p in corpus.words for p in parts)
    return False


@dataclass(frozen=True, slots=True)
class CodeCheck:
    """The outcome of the code stage for one claim."""

    verdict: CodeVerdict
    anchors: tuple[Anchor, ...] = ()
    missing: tuple[Anchor, ...] = ()

    @property
    def keep(self) -> bool:
        """True when the code alone settles it: keep the claim. Only ``anchors_missing`` needs Jev."""
        return self.verdict != "anchors_missing"


def code_check(claim_text: str, corpus: SourceCorpus) -> CodeCheck:
    """Run the code stage for ``claim_text`` against ``corpus`` (see the module docstring)."""
    if not corpus.raw.strip():
        return CodeCheck("no_source")
    pieces = fragments(claim_text)
    if sum(len(p) for p in pieces) >= MIN_VERBATIM_CHARS and all(p in corpus.normalised for p in pieces):
        return CodeCheck("verbatim")
    anchors = extract_anchors(claim_text)
    if not anchors:
        return CodeCheck("no_anchors")
    missing = tuple(a for a in anchors if not anchor_found(a, corpus))
    if missing:
        return CodeCheck("anchors_missing", tuple(anchors), missing)
    return CodeCheck("anchors_ok", tuple(anchors))


# ───────────────────────── the window ─────────────────────────

_STOP_WORDS = _vocabulary(
    "the and for with that this from into onto over under about across using used use built build "
    "made make led lead was were are has have had not but its their our your you they also more most "
    "than then when where which while who whom what how"
)
_ANCHOR_WEIGHT = 3.0


def word_positions(body: str) -> list[tuple[int, str]]:
    """``(offset, case-folded word)`` of every word in ``body``."""
    return [(m.start(), m.group().casefold()) for m in _ALNUM.finditer(body)]


def best_window(
    source: str,
    claim_text: str,
    size: int = JEV_EVIDENCE_WINDOW_CHARS,
    *,
    positions: list[tuple[int, str]] | None = None,
) -> str:
    """The ``size``-character window of ``source`` that shares the most (weighted) words with the claim.

    Anchor words weigh three times a plain word. The window starts a little before the first matching word
    so the sentence it opens has some context. A source that fits is returned whole, and a claim sharing no word
    with the source gets the opening of the source. ``positions`` is `word_positions` of the stripped source,
    when the caller has it (`SourceCorpus.positions`), so many claims about one source scan it only once.
    """
    body = source.strip()
    if len(body) <= size:
        return body

    weights: dict[str, float] = {}
    for word in _ALNUM.findall(claim_text.casefold()):
        if len(word) >= 3 and word not in _STOP_WORDS:
            weights[word] = max(weights.get(word, 0.0), 1.0)
    for anchor in extract_anchors(claim_text):
        for word in _ALNUM.findall(anchor.text.casefold()):
            if len(word) >= 2 or anchor.kind == "number":
                weights[word] = _ANCHOR_WEIGHT
    if not weights:
        return body[:size]

    if positions is None:
        positions = word_positions(body)
    hits = [(pos, tok) for pos, tok in positions if tok in weights]
    if not hits:
        return body[:size]

    margin = size // 10
    span = size - margin
    counts: dict[str, int] = {}
    score = 0.0
    right = 0
    best_score, best_pos = -1.0, hits[0][0]
    for pos, token in hits:
        limit = pos + span
        while right < len(hits) and hits[right][0] < limit:
            tok = hits[right][1]
            counts[tok] = counts.get(tok, 0) + 1
            if counts[tok] == 1:
                score += weights[tok]
            right += 1
        if score > best_score:
            best_score, best_pos = score, pos
        counts[token] -= 1
        if counts[token] == 0:
            score -= weights[token]
    start = max(0, best_pos - margin)
    return body[start : start + size].strip()


# ───────────────────────── the verdicts ─────────────────────────


def kept(claim: EvidenceClaim, reason: str, relation: str | None = None) -> EvidenceVerdict:
    return EvidenceVerdict(
        claim.skill_id, claim.source_id, claim.text, keep=True, relation=relation, reason=reason
    )


def code_verdict(claim: EvidenceClaim, check: CodeCheck) -> EvidenceVerdict:
    """The verdict for a claim the code settled (every `CodeCheck` except ``anchors_missing``)."""
    reasons = {
        "no_source": "no source text to check against, kept",
        "verbatim": "found word for word in the source",
        "no_anchors": "nothing specific to check, kept",
        "anchors_ok": "names and numbers all found in the source",
    }
    return kept(claim, reasons.get(check.verdict, "kept"))


def jev_verdict(
    claim: EvidenceClaim, check: CodeCheck, result: DecisionResult[Any] | None
) -> EvidenceVerdict:
    """The verdict for an ``anchors_missing`` claim given Jev's (or the Claude fallback's) answer.

    Dropped only when the relation is ``says_nothing`` or ``contradicts`` with confidence >= `DROP_CONFIDENCE`.
    """
    absent = f"{len(check.missing)} of {len(check.anchors)} names or numbers not found in the source"
    if result is None or not result.used or not isinstance(result.value, str):
        return kept(claim, f"{absent}, but the check gave no answer, kept")
    relation = result.value
    confidence = result.confidence or 0.0
    if relation in UNSUPPORTED and confidence >= DROP_CONFIDENCE:
        return EvidenceVerdict(
            claim.skill_id,
            claim.source_id,
            claim.text,
            keep=False,
            relation=relation,
            reason=f"{absent}, and the source {relation.replace('_', ' ')} ({confidence:.2f})",
        )
    return kept(claim, f"{absent}, but the check says {relation} ({confidence:.2f}), kept", relation)


# ───────────────────────── portfolio copy ─────────────────────────


_COPY_DROP_WORDS = {CONTRADICTS: "contradict it", SAYS_NOTHING: "say nothing about it"}


def copy_question(claim: CopyClaim) -> EvidenceQuestion:
    """The evidence question for one portfolio sentence: the sentence and the best window of its corpus."""
    return EvidenceQuestion(claim.sentence, best_window(claim.corpus, claim.sentence))


def copy_kept(
    claim: CopyClaim, reason: str, relation: str | None = None, confidence: float | None = None
) -> CopyVerdict:
    return CopyVerdict(
        claim.slot_id, claim.sentence, keep=True, relation=relation, confidence=confidence, reason=reason
    )


def copy_verdict(claim: CopyClaim, result: DecisionResult[Any] | None) -> CopyVerdict:
    """The verdict for a portfolio sentence given Jev's (or the Claude fallback's) answer.

    Dropped when the relation is ``contradicts``, or ``says_nothing`` with confidence >= `DROP_CONFIDENCE`.
    No answer, an unsure answer (the runner already escalated it to Claude) or ``supports`` keeps the sentence.
    """
    if result is None or not result.used or not isinstance(result.value, str):
        return copy_kept(claim, "the check gave no answer, kept")
    relation = result.value
    confidence = result.confidence or 0.0
    if relation == CONTRADICTS or (relation == SAYS_NOTHING and confidence >= DROP_CONFIDENCE):
        return CopyVerdict(
            claim.slot_id,
            claim.sentence,
            keep=False,
            relation=relation,
            confidence=confidence,
            reason=f"the facts it cites {_COPY_DROP_WORDS[relation]} ({confidence:.2f})",
        )
    return copy_kept(claim, f"the check answered {relation} ({confidence:.2f}), kept", relation, confidence)


__all__ = [
    "CONCURRENCY",
    "CONTRADICTS",
    "DROP_CONFIDENCE",
    "EVIDENCE_PACK",
    "MAX_ANCHORS",
    "PACK_ID",
    "RELATIONS",
    "SAYS_NOTHING",
    "SPEC_ID",
    "SUPPORTS",
    "SUPPORT_SPEC",
    "UNSUPPORTED",
    "VERSION",
    "Anchor",
    "CodeCheck",
    "CodeVerdict",
    "EvidenceQuestion",
    "SourceCorpus",
    "anchor_found",
    "best_window",
    "build_corpus",
    "build_state",
    "code_check",
    "code_verdict",
    "copy_kept",
    "copy_question",
    "copy_verdict",
    "extract_anchors",
    "fold",
    "fragments",
    "jev_verdict",
    "kept",
    "normalise",
    "word_positions",
]
