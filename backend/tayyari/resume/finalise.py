"""Finalise: every line the person typed is checked in code against the facts it cites.

A line the person edited waits as ``needs_user``. `finalise` runs `match.verify.check_text` on each such line, against
the block it was written from and anything the person has said about it before. What no cited fact supports becomes a
`Finding`: a figure (``number``), a scope verb stronger than the facts show (``scope``), a word of the role's
requirements that no fact supports (``term``), or a name, tool or praise word no fact has (``name``). Each finding has
two answers, given per line:

    mine   it is true: the typed line becomes a fact in the person's own words (`match.facts.user_fact`), joins the book
           and the line's ``factIds``, and the line is checked again
    undo   back to the fact's own wording

A line with nothing to find, or settled by an answer, is ``verified``. While any finding waits, the resume is not
final and cannot be exported. Leafout writes nothing here: every line is a fact's own words or the person's, and a
typed claim no source has is stored as said by the person.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from tayyari.contract.match import Bullet, Fact, ResumeVariant
from tayyari.contract.resume import Finding, FindingAnswer, FindingChoice, FindingKind
from tayyari.match.bullets import fallback_bullet
from tayyari.match.facts import user_fact
from tayyari.match.models import FactBook
from tayyari.match.verify import check_text, scope_verbs
from tayyari.resume.ops import entries_of, primary_fact

OPTIONS: Final[tuple[FindingChoice, ...]] = ("mine", "undo")
_PUFFERY_SUFFIX: Final = " (puffery)"
_WORD: Final = re.compile(r"[^\W_]+")


@dataclass(slots=True)
class FinaliseOutcome:
    """The draft after the checks and the answers, the book with what the person vouched for, and what still waits."""

    variant: ResumeVariant
    book: FactBook
    findings: list[Finding] = field(default_factory=list)
    new_facts: list[Fact] = field(default_factory=list)
    """The facts in the person's own words that ``mine`` answers made (to be stored as user facts)."""

    @property
    def done(self) -> bool:
        """Nothing waits for the person: the resume can be exported."""
        return not self.findings


def _passage(text: str, needle: str) -> str:
    """``needle`` as ``text`` writes it (its own capitals and spacing), or ``needle`` when the text has it another way."""
    found = re.search(re.escape(needle), text, re.IGNORECASE)
    if found is None:
        parts = [re.escape(p) for p in _WORD.findall(needle)]
        found = re.search(r"[\W_]*".join(parts), text, re.IGNORECASE) if parts else None
    return found.group() if found is not None else needle


def _verb_passage(text: str, verb: str) -> str:
    """The word of ``text`` that is the scope verb ``verb`` ("Leading" for the group ``led``)."""
    for found in _WORD.finditer(text):
        if verb in scope_verbs(found.group()):
            return found.group()
    return _passage(text, verb)


def check_line(line: Bullet, cited: Sequence[Fact], terms: Iterable[str] = ()) -> list[Finding]:
    """What ``line`` says that none of ``cited`` supports, in the order it reads (empty when all of it is supported).
    ``terms`` are the role's requirement words: using one needs a fact that supports it."""
    check = check_text(
        line.text, cited, posting_terms=terms, first_person_ok=True, max_words=None, allow_no_facts=False
    )
    found: list[tuple[FindingKind, str]] = [("number", slot.text) for slot in check.numbers]
    found += [("scope", _verb_passage(line.text, verb)) for verb in check.escalated]
    found += [("term", _passage(line.text, term)) for term in check.terms]
    found += [("name", _passage(line.text, name.removesuffix(_PUFFERY_SUFFIX))) for name in check.names]
    if check.problems and not found:
        found.append(("name", line.text))  # nothing to check it against: the whole line is unsupported
    seen: dict[tuple[FindingKind, str], None] = dict.fromkeys(found)

    def position(item: tuple[FindingKind, str]) -> int:
        at = line.text.casefold().find(item[1].casefold())
        return at if at >= 0 else len(line.text)

    return [
        Finding(bulletId=line.id, kind=kind, text=line.text, what=what, options=list(OPTIONS))
        for kind, what in sorted(seen, key=position)
    ]


def _verified(line: Bullet, *, stretch: bool = False) -> Bullet:
    return line.model_copy(
        update={"status": "verified", "verdict": "stretch" if stretch else "reframed", "placeholders": []}
    )


def finalise(
    variant: ResumeVariant,
    book: FactBook,
    answers: Iterable[FindingAnswer] = (),
    *,
    terms: Iterable[str] = (),
    key: str = "resume",
) -> FinaliseOutcome:
    """Check every edited line of ``variant`` and apply the person's ``answers`` (see the module docstring).

    ``key`` names where a ``mine`` fact was said (the resume), and with the line's words makes its id stable. An answer
    for a line that has no finding is ignored. Neither ``variant`` nor ``book`` is changed: the outcome holds copies.
    """
    work = variant.model_copy(deep=True)
    facts: dict[str, Fact] = book.by_id()
    choice: Mapping[str, FindingChoice] = {a.bulletId: a.choice for a in answers}
    words = list(terms)
    findings: list[Finding] = []
    new_facts: list[Fact] = []

    for entry in entries_of(work):
        settled: list[Bullet] = []
        for line in entry.bullets:
            if line.status != "needs_user":
                settled.append(line)
                continue
            cited = [facts[i] for i in line.factIds if i in facts]
            found = check_line(line, cited, words)
            if not found:
                settled.append(_verified(line))
                continue
            answer = choice.get(line.id)
            if answer == "undo":
                source = primary_fact(line, facts)
                if source is not None:
                    settled.append(fallback_bullet(line.id, source))
                continue  # with no block to go back to, the line leaves the resume
            if answer == "mine":
                said = user_fact(line.text, key=f"{key}/{line.id}")
                vouched = line.model_copy(update={"factIds": list(dict.fromkeys([*line.factIds, said.id]))})
                again = check_line(vouched, [*cited, said], words)
                if not again:
                    facts.setdefault(said.id, said)
                    new_facts.append(said)
                    settled.append(_verified(vouched, stretch=any(f.kind == "scope" for f in found)))
                    continue
                found = again
            findings.extend(found)
            settled.append(line)
        entry.bullets = settled

    return FinaliseOutcome(
        variant=work.model_copy(update={"lint": None}),
        book=book.with_facts(new_facts) if new_facts else book,
        findings=findings,
        new_facts=new_facts,
    )


__all__ = ["OPTIONS", "FinaliseOutcome", "check_line", "finalise"]
