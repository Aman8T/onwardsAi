"""Fallback wording: slots built from the facts themselves, with no model.

Used when a model slot fails the grounding check twice, and for the whole page when every model call fails. The
text is taken verbatim from a fact (trimmed at a word or clause boundary to the slot's limit), so it is grounded by
construction. Each slot is built from an ordered list of candidates and the first one that passes
`ground.check_slot` wins, so the guarantee does not depend on the wording of any one fact: the last candidate is
an empty text, which makes no claim.

    tagline         the first ``quote`` fact (the person's own words), else the person's headline
    aboutLede       the first highlight of the first experience that has one, else "<role> based in <place>."
    aboutBody       the next one or two highlights (the same role first, then the other roles); empty when there
                    is no second sentence to draw on
    pressHeadline   "<name>, <role>"
    pressDeck       the same sentence as the lede
    exp:<id>        up to 3 highlights of that role, one per line, within the slot's limit
    proj:<id>       the project's description, else its first highlight

A slot with no wording comes back with empty text (origin ``resume``, no facts): consumers treat it as absent. A slot
built from the person's own details (headline, role, name) cites no facts; ``check_slot(..., person=person)``
accepts that for origin ``resume`` slots.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from tayyari.contract.portfolio import (
    FACT_SLOT_PREFIXES,
    CopySlot,
    Fact,
    GraphSnapshot,
    PortfolioPerson,
    slot_limit,
)
from tayyari.portfolio.ground import check_slot, tidy_text, vocabulary

MAX_LINES: Final = 3
FACT_KIND_OF: Final = FACT_SLOT_PREFIXES
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_TAG = re.compile(r"<[^>]*>")
_LEADING_MARK = re.compile(r"^\s*(?:[-*+•>]|#{1,6})\s+")
_CLAUSE_ENDS: Final = (". ", "! ", "? ", "; ", ": ")


def plain(text: str) -> str:
    """``text`` as plain prose: markdown links, tags, backticks, emphasis marks and list markers removed."""
    out = _MD_LINK.sub(r"\1", text)
    out = _TAG.sub("", out)
    for mark in ("`", "**", "__", "~~"):
        out = out.replace(mark, "")
    return tidy_text(_LEADING_MARK.sub("", out))


def trim(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters at a sentence end, clause end or word boundary (never mid-token)."""
    text = tidy_text(text)
    if len(text) <= limit:
        return text
    window = text[: limit + 1]
    cut = -1
    for end in _CLAUSE_ENDS:
        pos = window.rfind(end)
        if pos >= limit * 0.45 and pos > cut:
            cut = pos if end[0] not in ".!?" else pos + 1
    if cut < 0:
        pos = window.rfind(", ")
        cut = pos if pos >= limit * 0.6 else window.rfind(" ")
    head = text[:cut] if cut > 0 else text[:limit]
    if head.count("(") > head.count(")") and "(" in head:
        head = head[: head.rfind("(")]
    head = head.rstrip(" ,;:-–—(")
    return _drop_dangling(head)


_DANGLING: Final = vocabulary(
    "a an the and or but of to in on at by for from with without that which who whose as into onto about over "
    "under between behind through than so if is are was were be"
)


def _drop_dangling(text: str) -> str:
    """Cut function words a trim left at the end ("... and a" / "... behind"), so the text ends on a content word."""
    words = text.split(" ")
    while len(words) > 3 and words[-1].casefold().strip(".,;:") in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(" ,;:-–—(")


def _sentence(text: str) -> str:
    """A highlight as a sentence: plain, and closed with a full stop."""
    out = plain(text)
    return out if not out or out[-1] in ".!?" else f"{out}."


Candidate = tuple[str, list[str]]


def _first_passing(
    slot_id: str,
    candidates: Sequence[Candidate],
    facts_by_id: dict[str, Fact],
    person: PortfolioPerson,
) -> CopySlot:
    limit = slot_limit(slot_id) or 0
    for text, fact_ids in candidates:
        text = text.strip()
        if not text or (limit and len(text) > limit):
            continue
        slot = CopySlot(id=slot_id, text=text, origin="resume", factIds=list(fact_ids))
        if check_slot(slot, facts_by_id, person=person).ok:
            return slot
    return CopySlot(id=slot_id, text="", origin="resume", factIds=[])


def _lede_candidates(facts: Sequence[Fact], person: PortfolioPerson, limit: int) -> list[Candidate]:
    out: list[Candidate] = []
    for f in facts:
        if f.kind == "experience" and f.highlights:
            out.append((trim(plain(f.highlights[0]), limit), [f.id]))
    where = f" based in {person.location}" if person.location else ""
    if person.role:
        out.append((trim(f"{person.role}{where}.", limit), []))
        out.append((trim(f"{person.role}.", limit), []))
    out.append((trim(person.headline, limit), []))
    return out


def _body_candidates(facts: Sequence[Fact], limit: int) -> list[Candidate]:
    """One or two further sentences: the rest of the lede's role first, then the first line of the other roles."""
    experiences = [f for f in facts if f.kind == "experience" and f.highlights]
    if not experiences:
        return []
    first = experiences[0]
    pool = [(_sentence(h), first.id) for h in first.highlights[1:]]
    pool += [(_sentence(f.highlights[0]), f.id) for f in experiences[1:]]
    pool = [(t, i) for t, i in pool if t and len(t) <= limit]
    out: list[Candidate] = []
    for count in (2, 1):
        chosen = pool[:count]
        if not chosen:
            continue
        text = " ".join(t for t, _ in chosen)
        if len(text) <= limit:
            out.append((text, list(dict.fromkeys(i for _, i in chosen))))
    return out


def _experience_candidates(fact: Fact, limit: int) -> list[Candidate]:
    lines = [plain(h) for h in fact.highlights if plain(h)]
    out: list[Candidate] = []
    for count in (MAX_LINES, 2, 1):
        chosen: list[str] = []
        used = 0
        for line in lines[:count]:
            room = limit - used - (1 if chosen else 0)
            if len(line) > room:
                if chosen and room < 80:
                    break
                line = trim(line, room)
            if not line:
                break
            chosen.append(line)
            used += len(line) + (1 if len(chosen) > 1 else 0)
        if chosen:
            out.append(("\n".join(chosen), [fact.id]))
    if fact.detail:
        out.append((trim(plain(fact.detail), limit), [fact.id]))
    return out


def _project_candidates(fact: Fact, limit: int) -> list[Candidate]:
    out: list[Candidate] = []
    if fact.detail:
        out.append((trim(plain(fact.detail), limit), [fact.id]))
    if fact.highlights:
        out.append((trim(plain(fact.highlights[0]), limit), [fact.id]))
    return out


def fallback_slots(
    facts: Sequence[Fact],
    person: PortfolioPerson,
    graph: GraphSnapshot,
    slot_ids: Sequence[str],
) -> list[CopySlot]:
    """Deterministic, grounded wording for ``slot_ids`` (origin ``resume``), in the order asked.

    ``graph`` is accepted for symmetry with the model call; no wording is taken from it (area names and scores are
    not facts of the resume).
    """
    del graph
    by_id = {f.id: f for f in facts}
    quotes = [f for f in facts if f.kind == "quote"]
    out: list[CopySlot] = []

    for slot_id in dict.fromkeys(slot_ids):
        limit = slot_limit(slot_id)
        if limit is None:
            continue
        candidates: list[Candidate] = []
        if slot_id == "tagline":
            # The person's own words only when they fit whole: a cut-off quote reads as a mistake.
            candidates = [(plain(q.title), [q.id]) for q in quotes if len(plain(q.title)) <= limit]
            candidates += [(trim(person.headline, limit), []), (trim(person.role, limit), [])]
        elif slot_id == "aboutLede":
            candidates = _lede_candidates(facts, person, limit)
        elif slot_id == "aboutBody":
            candidates = _body_candidates(facts, limit)
        elif slot_id == "pressHeadline":
            candidates = [(trim(f"{person.name}, {person.role}", limit), []), (trim(person.name, limit), [])]
        elif slot_id == "pressDeck":
            lede_limit = slot_limit("aboutLede") or limit
            candidates = [
                (trim(text, limit), ids) for text, ids in _lede_candidates(facts, person, lede_limit)
            ]
        elif slot_id.startswith(("exp:", "proj:")):
            prefix, _, fact_id = slot_id.partition(":")
            fact = by_id.get(fact_id)
            if fact is None or fact.kind != FACT_KIND_OF[prefix]:
                continue  # a slot for a fact that does not exist (or is another kind) has nothing to say
            candidates = (_experience_candidates if prefix == "exp" else _project_candidates)(fact, limit)
        slot = _first_passing(slot_id, candidates, by_id, person)
        out.append(slot)
    return out


__all__ = ["fallback_slots", "plain", "trim"]
