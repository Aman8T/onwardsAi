"""The cover note: paragraphs built from (requirement, fact) pairs. Gaps stay on the match page, never in the note.

One call writes the paragraphs from the strongest covered requirements and the facts that answer them, and the facts
the person typed about logistics. The prompt says how many paragraphs the note has and asks for 280 to 360 words, so a
paragraph that is dropped rarely breaks the 250-word floor. Code then decides what stands:

  - a ``match`` paragraph must cite at least one fact and pass the sentence verifier (numbers, names, scope verbs,
    posting terms), speak as the person ("I"), never about them ("Smitha holds", "the resume records"), never say
    what they have not done, and finish every sentence (`voice_problem`);
  - no ``gap`` paragraph is written. A requirement the person cannot evidence is left out of the note (the soul allows
    "written as a gap or left out"): the person sees it on the match page and in "left out on purpose", and the
    employer reads what the person can show. A missing fact is not proof of absence, so the note never says "I have
    not". The ``gap`` machinery below (`gap_sentence`, the gap re-ask) stands for a note asked with gaps;
  - a ``logistics`` paragraph exists only when the person typed logistics facts (source ``user``) and cites only those;
  - a ``close`` paragraph makes no claim; a generic one is added when the model wrote none;
  - the generic openers ("I am writing to apply", "I am the perfect candidate", "To whom it may concern", "I saw your
    posting" ...) and puffery are never kept.

The call plan is at most two calls, never three:

  1. the whole note;
  2. when the checks refused a paragraph whose place is still empty: ONE re-ask (`ask_or_none`, so a failed call is
     only a refusal) for just the refused paragraphs, each with the reason and the exact facts it may cite. When
     nothing was refused but the checked note is under 250 words: the whole note once more, told how short it came out.

A match paragraph that is refused twice (or whose re-ask failed) is replaced by one built from the fact's own words,
"At <org>, I <the fact's text>" when the fact starts with a verb, or "I work as <title> at <org>." from the job
itself; a fact that cannot be said as a whole first-person sentence ("Handling patients on ...", a bullet cut off by
the page) gives nothing: never a fragment, and never a claim the facts do not carry. The body is at most 400 words (extra match paragraphs are cut from
the end). A note that is still under 250 words after all this is returned as it is, honestly short: a short true
note beats a padded one. When paragraphs were refused for good, the note's ``length`` lint item says which and why
(`CoverNote.lint`, read back with `refusal_suffix`).

The greeting and sign-off are added by the exporter, not written by a model.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

from tayyari.contract.match import (
    COVER_WORDS_MAX,
    COVER_WORDS_MIN,
    Coverage,
    CoverNote,
    CoverParagraph,
    Fact,
    LintItem,
    LintReport,
    Posting,
    Requirement,
    tier_weight,
)
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.calls import ask, ask_or_none
from tayyari.match.errors import CoverUnavailable
from tayyari.match.models import FactBook, Report
from tayyari.match.schemas import CoverOut, CoverParagraphOut, rows_of, schema_of
from tayyari.match.text import clip, fold_text, tidy, word_count
from tayyari.match.verify import check_text, exact_wording, puffery_in
from tayyari.prompts.match import (
    COVER_MAX_MATCH,
    COVER_SYSTEM,
    COVER_WORDS_TARGET,
    CoverPair,
    CoverRepair,
    build_cover_prompt,
    build_cover_repair_prompt,
    listing,
    match_ids,
    match_words,
)

COVER_MAX_TOKENS: Final = 2500
REASK_MAX_TOKENS: Final = 1500
"""The re-ask rewrites a few paragraphs, not the note: it never needs the whole budget of the first call."""
MAX_PAIRS: Final = 4
MAX_MATCH_PARAGRAPHS: Final = COVER_MAX_MATCH
PARAGRAPH_MAX_WORDS: Final = 150
REFUSED_MARK: Final = "Refused by the checks: "
"""Starts the part of the ``length`` lint item's detail that names the refused paragraphs (see `refusal_suffix`)."""
REFUSAL_NOTE_CHARS: Final = 170
"""The whole refusal note fits in what is left of the lint detail's 240 characters after "N words; the note should be ..."."""
THIN_WORDS: Final = 6
"""A paragraph built from a fact's own words with fewer words than this is too thin to stand for a requirement."""
LENGTH_LABEL: Final = f"Length is {COVER_WORDS_MIN} to {COVER_WORDS_MAX} words"
"""The label of the ``length`` lint item (the same text as `tayyari.match.lint.LABELS`, which imports this module)."""

BANNED_OPENERS: Final[tuple[str, ...]] = (
    "i am writing to apply",
    "i am writing to express",
    "i am writing to submit",
    "i am writing in response",
    "i am the perfect candidate",
    "to whom it may concern",
    "dear sir or madam",
    "i saw your posting",
    "i saw your job",
    "i saw your ad",
    "i came across your",
    "i am excited to apply",
    "i am thrilled to apply",
    "i would like to apply",
)
"""Openers that say nothing. A paragraph that starts with one is not kept."""
BANNED_PHRASES: Final[tuple[str, ...]] = (
    "perfect candidate",
    "perfect fit",
    "dream job",
    "dream role",
    "ideal candidate",
    "team player",
    "think outside the box",
)
GENERIC_CLOSE: Final = "Thank you for your time. I would welcome a conversation about the role."

_LOGISTICS = re.compile(
    r"notice period|relocat|available (?:from|to|for|immediately)|availability|work (?:permit|authori[sz]ation)|"
    r"authori[sz]ed to work|visa|sponsorship|remote|on-?site|hybrid|can join|able to join|start (?:date|on|in)|"
    r"based in|willing to|open to",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(?:not|no|never|yet|haven't|hasn't|don't|do not|have not|without|lack|lacking|limited|new to|beginning|"
    r"learning|first)\b|n't\b",
    re.IGNORECASE,
)
_PRESENT_BASE: Final = frozenset(
    [
        "own",
        "run",
        "lead",
        "maintain",
        "review",
        "mentor",
        "partner",
        "pair",
        "build",
        "design",
        "write",
        "manage",
        "organise",
        "organize",
        "ship",
        "create",
        "develop",
        "deliver",
        "set",
    ]
)
_IRREGULAR: Final = frozenset(
    [
        "built",
        "led",
        "ran",
        "wrote",
        "made",
        "drove",
        "grew",
        "won",
        "took",
        "taught",
        "set",
        "put",
        "cut",
        "began",
    ]
)


_ABOUT_DOCUMENT = re.compile(r"\b(?:resume|résumé|cv|curriculum vitae)\b", re.IGNORECASE)
_SAYS_NOT = re.compile(
    r"\bI (?:have not|haven't|had not|hadn't|do not|don't|did not|didn't|am not|lack|cannot claim)\b|\bnot yet\b|"
    r"\b(?:would not|wouldn't|won't|cannot|can't) (?:claim|call)\b|\b(?:is|are|remains?) (?:limited|basic)\b|"
    r"\bstill learning\b|\bnot (?:a|my) strength\b",
    re.IGNORECASE,
)
_DANGLING_END = re.compile(
    r"\b(?:and|or|of|to|the|a|an|in|for|with|by|from|at|on|as|via|into|than|while|including)$", re.IGNORECASE
)
MIN_SENTENCE_WORDS: Final = 3


def unfinished(text: str) -> str | None:
    """The first sentence of ``text`` that is not a whole sentence (too short, or ending on "and", "of", "in" ...)."""
    for sentence in re.split(r"(?<=[.!?])\s+", tidy(text)):
        body = sentence.strip().rstrip(".!?").strip()
        if not body:
            continue
        if (
            len(body.split()) < MIN_SENTENCE_WORDS
            or _DANGLING_END.search(body)
            or body.endswith((",", "(", "-"))
        ):
            return sentence
    return None


def _has_word(word: str, folded: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", folded) is not None


def voice_problem(text: str, person: str, known: str = "") -> str | None:
    """Why ``text`` cannot stand in the person's note, or None: it talks about the document instead of the work, names
    the person in the third person, says what they have not done, or leaves a sentence unfinished.

    A word of the person's name that ``known`` (the cited facts, the job) also holds is not a sign of the third person:
    Ravi Tata may well write about Tata Consultancy Services."""
    folded, context = fold_text(text), fold_text(known)
    if _ABOUT_DOCUMENT.search(text):
        return "talks about the resume, not the work"
    for word in {w for w in fold_text(person).replace(".", " ").split() if len(w) >= 3}:
        if _has_word(word, folded) and not _has_word(word, context):
            return "speaks of the person by name, not as I"
    if _SAYS_NOT.search(text):
        return "says what the person has not done"
    if unfinished(text) is not None:
        return "a sentence is not finished"
    return None


def is_logistics(fact: Fact) -> bool:
    """Is this a fact the person typed about logistics (notice period, relocation, work authorisation ...)?"""
    return (
        bool(fact.refs) and all(r.source == "user" for r in fact.refs) and bool(_LOGISTICS.search(fact.text))
    )


def banned_opener(text: str) -> str | None:
    """The generic opener ``text`` starts with, if any."""
    head = fold_text(text).lstrip("\"' ")
    return next((o for o in BANNED_OPENERS if head.startswith(o)), None)


def banned_phrase(text: str) -> str | None:
    """A banned phrase or puffery that ``text`` contains, if any."""
    folded = fold_text(text)
    found = next((p for p in BANNED_PHRASES if p in folded), None)
    return found or next(iter(puffery_in(text)), None)


def first_person(text: str) -> str | None:
    """A fact's resume-voice sentence as a first-person one ("Led the migration" -> "I led the migration"), or None
    when it does not start with a verb."""
    body = tidy(text).rstrip(".")
    first = body.split(" ", 1)[0]
    word = first.casefold()
    if not (word.endswith("ed") or word in _IRREGULAR or word in _PRESENT_BASE):
        return None
    return f"I {first[0].lower() + first[1:]}{body[len(first) :]}."


def _decap(text: str) -> str:
    """``text`` with its first letter lower-cased when its first word is an ordinary capitalised word: "Fluent in
    English" becomes "fluent in English", while "SQL", "iOS" and "A/B testing" keep theirs."""
    first = text.split(" ", 1)[0]
    if len(first) > 1 and first[0].isupper() and first[1:].islower():
        return text[0].lower() + text[1:]
    return text


def _gap_what(req: Requirement) -> str:
    """What a gap sentence calls the requirement: its first named term as the posting wrote it ("customer data
    platform", "CleverTap"), else its own text with the capital of the first word dropped. No trailing period and
    never an article of our own."""
    if req.terms:
        return tidy(req.terms[0]).rstrip(". ")
    text = tidy(req.text).rstrip(". ")
    return clip(_decap(text), 80)


def gap_sentence(missing: Sequence[Requirement]) -> str:
    """The plain sentences for requirements the person does not meet: "The role lists X. I haven't used it yet."
    for one, "The role lists X, Y and Z. I haven't used them yet." for several."""
    wants = list(dict.fromkeys(_gap_what(r) for r in missing))
    return f"The role lists {listing(wants)}. I haven't used {'it' if len(wants) == 1 else 'them'} yet."


@dataclass(frozen=True, slots=True)
class Refusal:
    """A paragraph the checks refused: its kind, why, and which requirements and facts it named."""

    kind: str
    reason: str
    requirement_ids: tuple[str, ...] = ()
    fact_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class CoverResult:
    note: CoverNote
    report: Report = field(default_factory=Report)
    calls: int = 1


def pick_pairs(posting: Posting, coverage: Sequence[Coverage], book: FactBook) -> list[CoverPair]:
    """The strongest (requirement, facts) pairs: covered or reworded rows of level 2 and up, best weighted level first."""
    reqs = {r.id: r for r in posting.requirements}
    facts = book.by_id()
    ranked: list[tuple[float, Requirement, tuple[Fact, ...]]] = []
    for row in coverage:
        req = reqs.get(row.requirementId)
        if (
            req is None
            or row.status not in ("covered", "partial", "synonym_only", "missing_have_it")
            or row.level < 2
        ):
            continue
        cited = [facts[f] for f in row.factIds if f in facts]
        cited.sort(key=lambda f: (f.kind not in ("accomplishment", "project"), f.kind == "skill"))
        if cited:
            chosen = cited[:2]
            # a bullet does not say where it happened: its job (the entry fact) travels with it
            parent = next((e for e in book.entries if chosen[0].id in e.bulletIds), None)
            if parent is not None and parent.factId in facts and facts[parent.factId] not in chosen:
                chosen.append(facts[parent.factId])
            ranked.append((tier_weight(req.tier) * row.level, req, tuple(chosen)))
    ranked.sort(key=lambda item: -item[0])
    return [CoverPair(req, cited) for _, req, cited in ranked[:MAX_PAIRS]]


def gap_requirements(posting: Posting, coverage: Sequence[Coverage]) -> list[Requirement]:
    """Must and required requirements the person does not meet (a ``missing_gap`` row), in posting order."""
    gaps = {c.requirementId for c in coverage if c.status == "missing_gap"}
    return [r for r in posting.requirements if r.id in gaps and r.tier in ("must", "required")]


def _accept(
    row: CoverParagraphOut,
    *,
    book: FactBook,
    posting: Posting,
    gaps: dict[str, Requirement],
    logistics_ids: set[str],
    report: Report,
) -> CoverParagraph | Refusal:
    facts = book.by_id()
    text = tidy(row.text)
    fact_ids = [f for f in dict.fromkeys(row.factIds) if f in facts]
    known = {x.id for x in posting.requirements}
    req_ids = [r for r in dict.fromkeys(row.requirementIds) if r in known]
    label = clip(text, 50)

    def refuse(reason: str) -> Refusal:
        report.add("paragraph", label, reason)
        return Refusal(row.kind, reason, tuple(req_ids), tuple(fact_ids))

    if not text or word_count(text) > PARAGRAPH_MAX_WORDS:
        return refuse("empty or too long")
    if banned_opener(text) or banned_phrase(text):
        return refuse("generic opener or puffery")
    terms = [t for r in posting.requirements for t in r.terms]
    job = " ".join(p for p in (posting.company, posting.title) if p)
    cited = [facts[f] for f in fact_ids]

    if row.kind == "match":
        if not cited:
            return refuse("a match paragraph cites no fact")
        check = check_text(
            text, cited, posting_terms=terms, extra=job, first_person_ok=True, max_words=PARAGRAPH_MAX_WORDS
        )
        if not check.ok or check.placeholders:
            return refuse("not supported by the facts it cites")
        problem = voice_problem(text, book.name, " ".join([job, *(f.text for f in cited)]))
        if problem is not None:
            return refuse(problem)
        return CoverParagraph(text=text, requirementIds=req_ids, factIds=fact_ids, kind="match")

    if row.kind == "gap":
        named = [gaps[r] for r in req_ids if r in gaps]
        if not named:
            return refuse("a gap paragraph must name a gap requirement")
        allowed = " ".join(f"{r.text} {' '.join(r.terms)}" for r in named)
        check = check_text(
            text,
            cited,
            posting_terms=(),
            extra=f"{allowed} {job}",
            first_person_ok=True,
            max_words=PARAGRAPH_MAX_WORDS,
            allow_no_facts=True,
        )
        if not check.ok or check.placeholders:
            return refuse("a gap paragraph makes a claim")
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            mentions = any(
                fold_text(t) in fold_text(sentence) for r in named for t in (*r.terms, _gap_what(r)) if t
            )
            if mentions and not _NEGATION.search(sentence):
                return refuse("a gap sentence must say the person has not done it")
        return CoverParagraph(text=text, requirementIds=[r.id for r in named], factIds=fact_ids, kind="gap")

    if row.kind == "logistics":
        if not logistics_ids or not cited or any(f.id not in logistics_ids for f in cited):
            return refuse("logistics only from facts the person typed")
        check = check_text(text, cited, extra=job, first_person_ok=True, max_words=PARAGRAPH_MAX_WORDS)
        if not check.ok:
            return refuse("not supported by the facts it cites")
        problem = voice_problem(text, book.name, " ".join([job, *(f.text for f in cited)]))
        if (
            problem is not None and problem != "says what the person has not done"
        ):  # "not before May" is logistics
            return refuse(problem)
        return CoverParagraph(text=text, requirementIds=[], factIds=[f.id for f in cited], kind="logistics")

    check = check_text(text, cited, extra=job, first_person_ok=True, max_words=60, allow_no_facts=True)
    if not check.ok or any(ch.isdigit() for ch in text):
        return refuse("a closing line makes no claims")
    return CoverParagraph(text=text, requirementIds=[], factIds=[f.id for f in cited], kind="close")


def total_words(paragraphs: Sequence[CoverParagraph]) -> int:
    return sum(word_count(p.text) for p in paragraphs)


def _org_of(fact: Fact, book: FactBook) -> tuple[str, str] | None:
    """The org of the job or project ``fact`` belongs to and the id of that entry's own fact (None without an org)."""
    entry = book.entry(fact.id) or next((e for e in book.entries if fact.id in e.bulletIds), None)
    return (entry.org, entry.factId) if entry is not None and entry.org else None


def _role_sentence(fact: Fact, book: FactBook) -> str | None:
    """ "I work as <title> at <org>." for a current job, "I worked as ..." for an earlier one, from its book entry."""
    entry = book.entry(fact.id)
    if entry is None or not entry.title:
        return None
    verb = "work" if entry.current else "worked"
    title = tidy(entry.title).rstrip(". ")
    return f"I {verb} as {title}" + (f" at {tidy(entry.org).rstrip('. ')}" if entry.org else "") + "."


def fact_paragraph(
    requirements: Sequence[Requirement], facts: Sequence[Fact], book: FactBook
) -> CoverParagraph | None:
    """A match paragraph made of a fact's own words: "At <org>, I led the migration ..." It is what stands when the
    model's paragraph was refused twice.

    The fact is the first accomplishment or project among ``facts`` that starts with a verb, so it can be said as a
    whole first-person sentence; when there is none, the job itself: "I work as <title> at <org>." (at most two jobs).
    A bullet that starts otherwise ("Handling patients on ...") or is cut off, and a bare skill name, give None:
    never a fragment. The org comes with the job's own fact, so the paragraph cites both, and the result is held to the
    same verifier and voice checks as a model's paragraph.
    """
    spoken_facts = [
        (f, s)
        for f in facts
        if f.kind in ("accomplishment", "project")
        for s in [first_person(exact_wording(f))]
        if s is not None and unfinished(s) is None
    ][:1]
    cited: dict[str, Fact] = {}
    sentences: list[str] = []
    for fact, spoken in spoken_facts:
        cited[fact.id] = fact
        sentence = spoken
        org = _org_of(fact, book)
        if org is not None and org[1] != fact.id and fold_text(org[0]) not in fold_text(fact.text):
            entry_fact = book.get(org[1])
            if entry_fact is not None:
                sentence = f"At {org[0]}, {sentence}"
                cited.setdefault(entry_fact.id, entry_fact)
        sentences.append(sentence)
    if not sentences:
        for fact in [f for f in facts if f.kind == "experience"][:2]:
            said = _role_sentence(fact, book)
            if said is not None:
                cited[fact.id] = fact
                sentences.append(said)
    text = " ".join(sentences)
    if word_count(text) < THIN_WORDS:
        return None
    check = check_text(text, list(cited.values()), first_person_ok=True, max_words=PARAGRAPH_MAX_WORDS)
    if not check.ok or check.placeholders:
        return None
    if voice_problem(text, book.name, " ".join(f.text for f in cited.values())) is not None:
        return None
    return CoverParagraph(
        text=text, requirementIds=[r.id for r in requirements], factIds=list(cited), kind="match"
    )


_AT_ORG = re.compile(r"^at [^,]{1,80}, ")


def _says_again(p: CoverParagraph, q: CoverParagraph) -> bool:
    """Does ``q`` already say everything ``p`` says: each sentence of ``p`` (an "At <org>," lead aside) is in ``q``, or
    ``p`` rests only on facts ``q`` cites and is shorter?"""
    other = fold_text(q.text)
    sentences = [
        _AT_ORG.sub("", fold_text(s).strip()) for s in re.split(r"(?<=[.!?])\s+", p.text) if s.strip()
    ]
    if sentences and all(s.rstrip(".!? ") in other for s in sentences):
        return True
    return bool(p.factIds) and set(p.factIds) <= set(q.factIds) and word_count(p.text) < word_count(q.text)


def _without_repeats(matches: Sequence[CoverParagraph], report: Report) -> list[CoverParagraph]:
    """The match paragraphs without one that another already says (a fact-text paragraph after a refusal often
    repeats the model's own); of two that say the same, the first stays."""
    kept: list[CoverParagraph] = []
    for i, p in enumerate(matches):
        others = [q for j, q in enumerate(matches) if j != i and (j < i or not _says_again(q, p))]
        if any(_says_again(p, q) for q in others):
            report.add("paragraph", clip(p.text, 50), "says what another paragraph already says")
            continue
        kept.append(p)
    return kept


def assemble(
    accepted: Sequence[CoverParagraph],
    *,
    pairs: Sequence[CoverPair],
    gaps: Sequence[Requirement],
    book: FactBook,
    report: Report,
) -> list[CoverParagraph]:
    """Order and complete the accepted paragraphs: matches (none that another one already says), one gap paragraph
    covering every must/required gap, logistics, a closing line; then cut extra matches until the body fits
    `COVER_WORDS_MAX`."""
    matches = _without_repeats([p for p in accepted if p.kind == "match"], report)[:MAX_MATCH_PARAGRAPHS]
    if not matches:
        for pair in pairs[:2]:
            fallback = fact_paragraph([pair.requirement], pair.facts, book)
            if fallback is not None:
                matches.append(fallback)
                report.add("paragraph", pair.requirement.id, "written from the fact itself")
    gap_paras = [p for p in accepted if p.kind == "gap"]
    covered = {r for p in gap_paras for r in p.requirementIds}
    missing = [r for r in gaps if r.id not in covered]
    if missing:
        gap_paras.append(
            CoverParagraph(
                text=gap_sentence(missing), requirementIds=[r.id for r in missing], factIds=[], kind="gap"
            )
        )
    logistics = [p for p in accepted if p.kind == "logistics"][:1]
    close = next((p for p in accepted if p.kind == "close"), None) or CoverParagraph(
        text=GENERIC_CLOSE, requirementIds=[], factIds=[], kind="close"
    )
    ordered = [*matches, *gap_paras, *logistics, close]
    while total_words(ordered) > COVER_WORDS_MAX:
        extra = [p for p in ordered if p.kind == "match"]
        if len(extra) <= 1:
            break
        ordered.remove(extra[-1])
        report.add("paragraph", clip(extra[-1].text, 50), "cut to keep the note under the word limit")
    return ordered


# ───────────────────────── the one re-ask ─────────────────────────

Row = CoverParagraph | Refusal
"""One row of the model's answer after the checks, in the order it was written."""


@dataclass(frozen=True, slots=True)
class _Repair:
    """A refused row worth asking for again: where it sat, what the model is told, and the refusal as it stood."""

    index: int
    ask: CoverRepair
    refusal: Refusal


def _plan_repairs(
    rows: Sequence[Row],
    *,
    pairs: Sequence[CoverPair],
    gap_by_id: dict[str, Requirement],
    logistics: Sequence[Fact],
) -> list[_Repair]:
    """The refused rows to ask for again, each with the exact facts it may cite.

    A refused match paragraph is asked for again only if its requirement still has no match paragraph (a good one may
    stand elsewhere in the answer); the facts are those of that requirement's pair, whatever the model cited. The gap
    paragraph is asked for again for every gap not covered yet, the logistics one from the facts the person typed. A
    refused closing line is not asked for: the generic one is as good. Each kind is asked for once.
    """
    pair_of = {p.requirement.id: p for p in pairs}
    kept = [r for r in rows if isinstance(r, CoverParagraph)]
    filled = {i for p in kept if p.kind == "match" for i in p.requirementIds}
    open_slots = [i for i in match_ids(pairs) if i not in filled]
    gapped = {i for p in kept if p.kind == "gap" for i in p.requirementIds}
    has_logistics = any(p.kind == "logistics" for p in kept)
    plan: list[_Repair] = []
    claimed: set[str] = set()
    asked: set[str] = set()
    for index, row in enumerate(rows):
        if isinstance(row, CoverParagraph):
            continue
        ask: CoverRepair | None = None
        if row.kind == "match":
            named = [i for i in row.requirement_ids if i in pair_of]
            if (
                not named
            ):  # it named no requirement we asked about: go by the facts it cited, else the first open place
                named = [p.requirement.id for p in pairs if any(f.id in row.fact_ids for f in p.facts)][:1]
            if not named:
                named = [i for i in open_slots if i not in claimed][:1]
            ids = [i for i in dict.fromkeys(named) if i in open_slots and i not in claimed]
            if ids:
                claimed.update(ids)
                facts = {f.id: f for i in ids for f in pair_of[i].facts}
                ask = CoverRepair(
                    "match", row.reason, tuple(pair_of[i].requirement for i in ids), tuple(facts.values())
                )
        elif row.kind == "gap" and "gap" not in asked:
            todo = [r for i, r in gap_by_id.items() if i not in gapped]
            if todo:
                asked.add("gap")
                ask = CoverRepair("gap", row.reason, tuple(todo), ())
        elif row.kind == "logistics" and "logistics" not in asked and logistics and not has_logistics:
            asked.add("logistics")
            ask = CoverRepair("logistics", row.reason, (), tuple(logistics))
        if ask is not None:
            plan.append(_Repair(index, ask, row))
    return plan


def _pick_row(rows: Sequence[CoverParagraphOut], used: set[int], ask: CoverRepair) -> int | None:
    """The row of the re-ask's answer for ``ask``: the first unused one of its kind that names one of its
    requirements, else the first unused one of its kind."""
    free = [i for i, row in enumerate(rows) if i not in used and row.kind == ask.kind]
    wanted = {r.id for r in ask.requirements}
    return next((i for i in free if wanted & set(rows[i].requirementIds)), free[0] if free else None)


async def _reask(
    claude: ClaudeClient,
    *,
    model: str,
    repairs: Sequence[_Repair],
    book: FactBook,
    posting: Posting,
    pairs: Sequence[CoverPair],
    gaps: Sequence[Requirement],
    logistics: Sequence[Fact],
    logistics_ids: set[str],
    report: Report,
    call_ctx: CallContext,
) -> dict[int, Row]:
    """ONE call for just the ``repairs``: what stands in place of each refused row (the model's new paragraph, or
    the refusal again when the call failed, the answer lacks the row or the checks refuse it once more)."""
    outcome: dict[int, Row] = {r.index: r.refusal for r in repairs}
    data = await ask_or_none(
        claude,
        model=model,
        purpose="match.cover",
        system=COVER_SYSTEM,
        user=build_cover_repair_prompt(
            job_title=posting.title,
            company=posting.company,
            person=book.name,
            repairs=[r.ask for r in repairs],
            words=match_words(pairs, gaps, logistics),
        ),
        schema=schema_of(
            CoverOut,
            factIds=[f.id for r in repairs for f in r.ask.facts],
            requirementIds=[r.id for r in posting.requirements],
        ),
        max_tokens=REASK_MAX_TOKENS,
        call_ctx=call_ctx,
    )
    if data is None:
        return outcome
    rows = rows_of(data, "paragraphs", CoverParagraphOut)
    used: set[int] = set()
    for repair in repairs:
        picked = _pick_row(rows, used, repair.ask)
        if picked is None:
            continue
        used.add(picked)
        allowed = {f.id for f in repair.ask.facts}
        row = rows[picked].model_copy(
            update={
                "requirementIds": [r.id for r in repair.ask.requirements],
                "factIds": [f for f in rows[picked].factIds if f in allowed],
            }
        )
        outcome[repair.index] = _accept(
            row,
            book=book,
            posting=posting,
            gaps={r.id: r for r in gaps},
            logistics_ids=logistics_ids,
            report=report,
        )
    return outcome


def _settle(
    rows: Sequence[Row],
    repairs: Sequence[_Repair],
    outcome: dict[int, Row],
    *,
    book: FactBook,
    pairs: Sequence[CoverPair],
    gaps: Sequence[Requirement],
    report: Report,
) -> tuple[list[CoverParagraph], list[Refusal]]:
    """The note's paragraphs once every refused row has its replacement, and the refusals that stand.

    The rows keep the order the model wrote them in. A refused row that was asked for again is replaced by the new
    paragraph, or (a match refused twice) by the fact-text paragraph; a gap or logistics paragraph refused twice is
    left to `assemble` (the plain gap sentences) or dropped. A refused row nobody asked for again is just dropped.
    """
    by_index = {r.index: r for r in repairs}
    kept: list[CoverParagraph] = []
    standing: list[Refusal] = []
    for index, row in enumerate(rows):
        if isinstance(row, CoverParagraph):
            kept.append(row)
            continue
        repair = by_index.get(index)
        if repair is None:
            continue
        new = outcome.get(index, repair.refusal)
        if isinstance(new, CoverParagraph):
            kept.append(new)
            continue
        standing.append(replace(new, requirement_ids=tuple(r.id for r in repair.ask.requirements)))
        if repair.ask.kind == "match":
            made = fact_paragraph(repair.ask.requirements, repair.ask.facts, book)
            if made is not None:
                kept.append(made)
                report.add("paragraph", clip(made.text, 50), "written from the fact itself")
    return assemble(kept, pairs=pairs, gaps=gaps, book=book, report=report), standing


# ───────────────────────── what the lint says about a short note ─────────────────────────


def refusal_note(refusals: Sequence[Refusal], posting: Posting) -> str:
    """Which paragraphs were refused and why, in one line that starts with `REFUSED_MARK`.

    Refusals of the same kind for the same reason are grouped, each paragraph named by the requirement it was about:
    "match (A / B): not supported by the facts it cites; gap: a gap paragraph makes a claim".
    """
    wording = {r.id: _gap_what(r) for r in posting.requirements}
    groups: dict[tuple[str, str], list[str]] = {}
    for refusal in refusals:
        about = next((wording[i] for i in refusal.requirement_ids if i in wording), "")
        names = groups.setdefault((refusal.kind, refusal.reason), [])
        if about:
            names.append(clip(about, 28))
    parts = [
        f"{kind} ({' / '.join(names)}): {reason}" if names else f"{kind}: {reason}"
        for (kind, reason), names in groups.items()
    ]
    return clip(REFUSED_MARK + "; ".join(parts), REFUSAL_NOTE_CHARS)


def short_note_lint(words: int, refusals: Sequence[Refusal], posting: Posting) -> LintReport:
    """The ``length`` lint item of a note that stayed under `COVER_WORDS_MIN`: failing, and saying which paragraphs
    were refused and why. The exporter checks the length again; `refusal_suffix` reads this part back."""
    detail = f"{words} words; the note should be {COVER_WORDS_MIN} to {COVER_WORDS_MAX}."
    if refusals:
        detail += f" {refusal_note(refusals, posting)}"
    return LintReport(items=[LintItem(id="length", ok=False, label=LENGTH_LABEL, detail=clip(detail, 240))])


def refusal_suffix(note: CoverNote) -> str:
    """The part of the note's ``length`` lint detail that names the refused paragraphs ("" when it has none)."""
    if note.lint is None:
        return ""
    item = next((i for i in note.lint.items if i.id == "length"), None)
    if item is None:
        return ""
    _, mark, tail = item.detail.partition(REFUSED_MARK)
    return f"{mark}{tail}"


# ───────────────────────── the call ─────────────────────────


async def write_cover(
    claude: ClaudeClient,
    *,
    model: str,
    book: FactBook,
    posting: Posting,
    coverage: Sequence[Coverage],
    call_ctx: CallContext,
) -> CoverResult:
    """Write the cover note for ``posting`` from ``book`` and its ``coverage`` (see the module docstring).

    Raises `CoverUnavailable` when no covered requirement has a fact to build a paragraph from.
    """
    pairs = pick_pairs(posting, coverage, book)
    if not pairs:
        raise CoverUnavailable
    # gaps are left out of the note (see the module docstring); the person sees them on the match page
    gaps: list[Requirement] = []
    gap_by_id = {r.id: r for r in gaps}
    logistics = [f for f in book.facts if is_logistics(f)]
    logistics_ids = {f.id for f in logistics}
    shown = {f.id: f for pair in pairs for f in pair.facts}
    for fact in logistics:
        shown[fact.id] = fact
    schema = schema_of(CoverOut, factIds=list(shown), requirementIds=[r.id for r in posting.requirements])
    report = Report()

    def prompt(note: str = "") -> str:
        return build_cover_prompt(
            job_title=posting.title,
            company=posting.company,
            person=book.name,
            pairs=pairs,
            gaps=gaps,
            logistics=logistics,
            note=note,
        )

    def screen(data: dict[str, Any]) -> list[Row]:
        return [
            _accept(
                row,
                book=book,
                posting=posting,
                gaps=gap_by_id,
                logistics_ids=logistics_ids,
                report=report,
            )
            for row in rows_of(data, "paragraphs", CoverParagraphOut)
        ]

    def plan(rows: Sequence[Row]) -> list[_Repair]:
        return _plan_repairs(rows, pairs=pairs, gap_by_id=gap_by_id, logistics=logistics)

    # call 1: the whole note
    data = await ask(
        claude,
        model=model,
        purpose="match.cover",
        system=COVER_SYSTEM,
        user=prompt(),
        schema=schema,
        max_tokens=COVER_MAX_TOKENS,
        call_ctx=call_ctx,
    )
    calls = 1
    rows = screen(data)
    repairs = plan(rows)
    outcome: dict[int, Row] = {}
    if repairs:  # call 2, when a refusal left a place empty: only the refused paragraphs
        calls = 2
        outcome = await _reask(
            claude,
            model=model,
            repairs=repairs,
            book=book,
            posting=posting,
            pairs=pairs,
            gaps=gaps,
            logistics=logistics,
            logistics_ids=logistics_ids,
            report=report,
            call_ctx=call_ctx,
        )
    local = Report()
    ordered, standing = _settle(rows, repairs, outcome, book=book, pairs=pairs, gaps=gaps, report=local)
    words = total_words(ordered)
    if not repairs and words < COVER_WORDS_MIN:  # call 2 instead: nothing to repair, the note is just short
        calls = 2
        again = await ask_or_none(
            claude,
            model=model,
            purpose="match.cover",
            system=COVER_SYSTEM,
            user=prompt(
                f"Your last answer had {words} words once checked; the body must be {COVER_WORDS_MIN} to "
                f"{COVER_WORDS_MAX} words (aim for {COVER_WORDS_TARGET[0]} to {COVER_WORDS_TARGET[1]}). "
                "Use more of the cited facts (what was done and why it matters), and add no new facts."
            ),
            schema=schema,
            max_tokens=COVER_MAX_TOKENS,
            call_ctx=call_ctx,
        )
        if again is not None:
            rows2 = screen(again)
            repairs2 = plan(rows2)
            local2 = Report()
            # never a third call: a match refused here gets the fact-text paragraph straight away
            ordered2, standing2 = _settle(
                rows2, repairs2, {}, book=book, pairs=pairs, gaps=gaps, report=local2
            )
            if total_words(ordered2) >= words:
                ordered, standing, local = ordered2, standing2, local2
    report.dropped.extend(local.dropped)
    words = total_words(ordered)
    lint = short_note_lint(words, standing, posting) if standing and words < COVER_WORDS_MIN else None
    return CoverResult(CoverNote(paragraphs=ordered, wordCount=words, lint=lint), report, calls)


__all__ = [
    "BANNED_OPENERS",
    "BANNED_PHRASES",
    "COVER_MAX_TOKENS",
    "GENERIC_CLOSE",
    "LENGTH_LABEL",
    "REASK_MAX_TOKENS",
    "REFUSED_MARK",
    "CoverResult",
    "Refusal",
    "assemble",
    "banned_opener",
    "banned_phrase",
    "fact_paragraph",
    "first_person",
    "gap_requirements",
    "gap_sentence",
    "is_logistics",
    "pick_pairs",
    "refusal_note",
    "refusal_suffix",
    "short_note_lint",
    "total_words",
    "unfinished",
    "voice_problem",
    "write_cover",
]
