"""Merging a second resume file into the person's blocks, in code (no model call).

`merge(existing_book, new_facts)` places every fact of the new file in exactly one section:

    same       the block exists with the same meaning and words up to trivial differences (a stop word, a plural,
               punctuation): the file becomes another reference of that block
    new        no counterpart: a new block (a new entry when the job, project or degree has none)
    near       the same thing in other words: the block keeps its wording, and the file's wording is kept beside it
    disagree   the same entry or line with a conflicting figure, date, title or employer: the block keeps its words, the
               file keeps its own (in its own resume) and the difference is listed

How a counterpart is found. Entries (job, project, degree) match when two of three signals agree: the employer, the title
(word overlap of the shorter one, `MERGE_ENTRY_NAME_MIN_OVERLAP`) and the dates (the periods overlap); an entry with no
employer and no dates matches on its title alone. A matched entry whose title, employer or dates (start or end more than
`MERGE_DATE_WINDOW_MONTHS` apart) conflict is a ``disagree``, else ``same``. A line is only compared with the lines of the
entry its entry matched (a line of a new entry is always ``new``); lines that belong to no entry (an award, a certificate,
a detail) are compared with each other. Two lines are about the same thing when the shorter one's content words are at least
`MERGE_NEAR_MIN_OVERLAP` in the other and `MERGE_NEAR_MIN_SHARED` are shared. Then the figures (`match.text.measures_of`:
a number with its unit, so ``38`` and ``40`` differ and ``10K`` and ``10,000`` agree) and the dates (years, fiscal years)
decide: each side having a figure the other lacks is a ``disagree``; equal figures and dates with a Jaccard of the content
words of `MERGE_SAME_MIN_JACCARD` is ``same``; anything else related is ``near``. A skill is ``same`` when it is the same term
(`match.aliases.same_term`), else ``new``. Each existing block is claimed by at most one new line, best pairs first, so
the order of the file never decides.

Ids. An existing block keeps its id for ever. A new block's id is ``m-`` and ten hex characters of a hash of its kind, its
words and its entry's id (`match.text.short_hash`), with ``-2``, ``-3`` for a repeat in one file: it depends on nothing that
a later file can change. The merged book carries a reference per file (``docId`` is the file's id, ``lines`` empty so the
order of the first file stays the order of the book), and the file's own book (``file_book``) holds the file's facts under the
block ids they were placed on, in its own words and order: `upload_variant` over it is the file's own resume.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from tayyari.contract.match import Fact, FactRef
from tayyari.contract.resume import MergeDiffer, MergeItem, MergeReport, MergeSection
from tayyari.core.limits import (
    MERGE_DATE_WINDOW_MONTHS,
    MERGE_ENTRY_NAME_MIN_OVERLAP,
    MERGE_NEAR_MIN_OVERLAP,
    MERGE_NEAR_MIN_SHARED,
    MERGE_SAME_MIN_JACCARD,
)
from tayyari.decisions.packs.evidence import fold
from tayyari.match.aliases import same_term
from tayyari.match.models import BookEntry, FactBook
from tayyari.match.text import fold_text, measures_of, short_hash, tokens_of

MAX_QUOTE: Final = 500
_STOP: Final = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "with",
        "on",
        "at",
        "by",
        "from",
        "as",
        "is",
        "was",
        "were",
        "be",
        "been",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "into",
        "over",
        "under",
        "using",
        "used",
        "use",
        "via",
        "per",
        "than",
        "then",
        "also",
        "while",
        "their",
        "our",
        "my",
        "we",
        "they",
        "across",
        "within",
        "about",
        "after",
        "before",
    ]
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_FY = re.compile(r"\bfy\s?'?(?:20)?(\d{2})\b")
_MONTH_YEAR = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:19|20)\d{2}\b"
)
_INF: Final = 10**9


# ───────────────────────── text ─────────────────────────


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and not word.endswith("ss") and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def words(text: str) -> frozenset[str]:
    """The content words of ``text``: folded, no number, no stop word, a plural or tense stripped."""
    return frozenset(
        _stem(t)
        for t in tokens_of(text)
        if len(t) > 1 and not t.isdigit() and t not in _STOP and not t.isdigit()
    )


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def dates_of(text: str) -> tuple[frozenset[str], str]:
    """The years and fiscal years ``text`` names, and ``text`` with every date taken out (so a year is not a figure)."""
    low = fold(text)
    found = {f"y{m}" for m in _YEAR.findall(low)} | {f"fy{m}" for m in _FY.findall(low)}
    stripped = _FY.sub(" ", _MONTH_YEAR.sub(lambda m: " " + m.group()[-4:] + " ", low))
    return frozenset(found), _YEAR.sub(" ", stripped)


def figures_of(text: str) -> frozenset[tuple[str, str, str]]:
    """The numbers of ``text`` with their units, dates left out (``10K`` and ``10,000`` agree, ``38`` and ``40`` do not)."""
    _, rest = dates_of(text)
    return frozenset((m.kind, str(m.value), m.currency) for m in measures_of(rest, strict=True))


def _clash(a: frozenset[object], b: frozenset[object]) -> bool:
    """Does each side have something the other lacks?"""
    return bool(a - b) and bool(b - a)


@dataclass(frozen=True, slots=True)
class Relation:
    section: MergeSection
    differs: tuple[MergeDiffer, ...]
    score: float


def relate(existing: str, new: str) -> Relation | None:
    """How the line ``new`` stands to the line ``existing``, or None when they are about different things."""
    if fold_text(existing) == fold_text(new):
        return Relation("same", (), 2.0)
    a, b = words(existing), words(new)
    shared = len(a & b)
    if (
        not a
        or not b
        or _overlap(a, b) < MERGE_NEAR_MIN_OVERLAP
        or shared < min(MERGE_NEAR_MIN_SHARED, len(a), len(b))
    ):
        return None
    jac = _jaccard(a, b)
    fa, fb = figures_of(existing), figures_of(new)
    da, db = dates_of(existing)[0], dates_of(new)[0]
    differs: list[MergeDiffer] = []
    if _clash(fa, fb):
        differs.append("figure")
    if _clash(da, db):
        differs.append("date")
    if differs:
        return Relation("disagree", tuple(differs), jac)
    if jac >= MERGE_SAME_MIN_JACCARD and fa == fb and da == db:
        return Relation("same", (), jac)
    return Relation("near", (), jac)


def _skill_relation(existing: str, new: str) -> Relation | None:
    if fold_text(existing) == fold_text(new) or same_term(existing, new):
        return Relation("same", (), 1.0)
    return None


# ───────────────────────── entries ─────────────────────────


def _month(value: str | None, *, end: bool) -> int | None:
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?", (value or "").strip())
    if not m:
        return None
    month = int(m.group(2)) if m.group(2) else (12 if end else 1)
    return int(m.group(1)) * 12 + min(max(month, 1), 12)


def _span(entry: BookEntry) -> tuple[int, int | None] | None:
    """``(start, end)`` in months; the end is `_INF` when the entry is current and None when it names none."""
    start = _month(entry.start, end=False)
    if start is None:
        return None
    if entry.current:
        return start, _INF
    return start, _month(entry.end, end=True)


def _entry_signals(a: BookEntry, b: BookEntry) -> tuple[int, float, bool, bool, bool]:
    """``(signals, title overlap, employer agrees, title agrees, dates overlap)`` of an existing and a new entry."""
    title_ov = _overlap(words(a.title), words(b.title))
    org_ok = (
        bool(a.org and b.org)
        and _overlap(words(a.org or ""), words(b.org or "")) >= MERGE_ENTRY_NAME_MIN_OVERLAP
    )
    title_ok = title_ov >= MERGE_ENTRY_NAME_MIN_OVERLAP
    sa, sb = _span(a), _span(b)
    date_ok = False
    if sa is not None and sb is not None:
        end_a, end_b = sa[1] if sa[1] is not None else sa[0], sb[1] if sb[1] is not None else sb[0]
        date_ok = sa[0] <= end_b and sb[0] <= end_a
    signals = int(org_ok) + int(title_ok) + int(date_ok)
    lone = (
        not (a.org and b.org)
        and not (a.start and b.start)
        and _jaccard(words(a.title), words(b.title)) >= MERGE_SAME_MIN_JACCARD
    )
    return (max(signals, 2) if lone else signals), title_ov, org_ok, title_ok, date_ok


def _ends_differ(a: int | None, b: int | None) -> bool:
    """Do two end dates (None: not named, `_INF`: current) disagree by more than the window?"""
    if a is None or b is None:
        return False
    if a == _INF or b == _INF:
        return a != b
    return abs(a - b) > MERGE_DATE_WINDOW_MONTHS


def _entry_differs(a: BookEntry, b: BookEntry) -> list[MergeDiffer]:
    out: list[MergeDiffer] = []
    if a.title and b.title and _jaccard(words(a.title), words(b.title)) < MERGE_SAME_MIN_JACCARD:
        out.append("title")
    if a.org and b.org and _overlap(words(a.org or ""), words(b.org or "")) < MERGE_ENTRY_NAME_MIN_OVERLAP:
        out.append("employer")
    sa, sb = _span(a), _span(b)
    if sa is not None and sb is not None:
        far = abs(sa[0] - sb[0]) > MERGE_DATE_WINDOW_MONTHS or _ends_differ(sa[1], sb[1])
        if far:
            out.append("date")
    return out


# ───────────────────────── the merge ─────────────────────────


@dataclass(frozen=True, slots=True)
class MergeResult:
    report: MergeReport
    """The wire report: every fact of the file in exactly one list."""
    merged: FactBook
    """The person's book after the file: every old block as it was (its id too), with the file as one more reference of the
    blocks it matches, and a new block for each line it brings."""
    file_book: FactBook
    """The file's own facts, in its words and its order, under the block ids they were placed on."""


@dataclass(frozen=True, slots=True)
class _Placed:
    block_id: str
    section: MergeSection
    existing: str | None
    differs: tuple[MergeDiffer, ...]


def _assign(
    new: Sequence[Fact], pool: Sequence[Fact], *, skills: bool = False
) -> dict[str, tuple[Fact, Relation]]:
    """Best pairs first, each existing block claimed once."""
    pairs: list[tuple[float, int, int, Relation]] = []
    for i, n in enumerate(new):
        for j, e in enumerate(pool):
            r = _skill_relation(e.text, n.text) if skills else relate(e.text, n.text)
            if r is not None:
                pairs.append((r.score, i, j, r))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    taken_new: set[int] = set()
    taken_old: set[int] = set()
    out: dict[str, tuple[Fact, Relation]] = {}
    for _, i, j, r in pairs:
        if i in taken_new or j in taken_old:
            continue
        taken_new.add(i)
        taken_old.add(j)
        out[new[i].id] = (pool[j], r)
    return out


def merge(
    existing_book: FactBook, new_facts: FactBook, *, file_id: str = "", filename: str = ""
) -> MergeResult:
    """Merge ``new_facts`` (the book of a file's own facts) into ``existing_book``. Pure: see the module docstring."""
    old = existing_book.by_id()
    new = new_facts.by_id()
    used_ids: set[str] = set(old)
    placed: dict[str, _Placed] = {}
    new_blocks: list[Fact] = []
    under: dict[str, str] = {}  # a new bullet's block id -> the existing entry it joins
    new_entries: list[BookEntry] = []

    def fresh(fact: Fact, parent: str) -> str:
        base = "m-" + short_hash(fact.kind, fold_text(fact.text), parent)
        block_id, n = base, 2
        while block_id in used_ids:
            block_id, n = f"{base}-{n}", n + 1
        used_ids.add(block_id)
        return block_id

    # entries
    entry_heads = {e.factId for e in new_facts.entries if e.factId in new}
    bullet_of = {b: e for e in new_facts.entries if e.factId in new for b in e.bulletIds}
    candidates: list[tuple[int, float, int, int]] = []
    for ni, ne in enumerate(new_facts.entries):
        if ne.factId not in new:
            continue
        for ei, ee in enumerate(existing_book.entries):
            if ee.kind != ne.kind or ee.factId not in old:
                continue
            signals, title_ov, *_ = _entry_signals(ee, ne)
            if signals >= 2:
                candidates.append((signals, title_ov, ni, ei))
    candidates.sort(key=lambda c: (-c[0], -c[1], c[2], c[3]))
    entry_map: dict[int, int] = {}
    claimed_entries: set[int] = set()
    for _signals, _ov, ni, ei in candidates:
        if ni in entry_map or ei in claimed_entries:
            continue
        entry_map[ni] = ei
        claimed_entries.add(ei)

    for ni, ne in enumerate(new_facts.entries):
        head = new.get(ne.factId)
        if head is None:
            continue
        if ni in entry_map:
            ee = existing_book.entries[entry_map[ni]]
            existing_head = old[ee.factId]
            differs = _entry_differs(ee, ne)
            section: MergeSection = "disagree" if differs else "same"
            placed[head.id] = _Placed(
                ee.factId, section, existing_head.text if differs else None, tuple(differs)
            )
            pool = [old[b] for b in ee.bulletIds if b in old]
            bullets = [new[b] for b in ne.bulletIds if b in new]
            got = _assign(bullets, pool)
            for b in bullets:
                if b.id in got:
                    e_fact, r = got[b.id]
                    placed[b.id] = _Placed(
                        e_fact.id, r.section, None if r.section == "same" else e_fact.text, r.differs
                    )
                else:
                    block_id = fresh(b, ee.factId)
                    placed[b.id] = _Placed(block_id, "new", None, ())
                    new_blocks.append(b)
                    under[block_id] = ee.factId
        else:
            block_id = fresh(head, "")
            placed[head.id] = _Placed(block_id, "new", None, ())
            new_blocks.append(head)
            mapped: list[str] = []
            for bid in ne.bulletIds:
                nb = new.get(bid)
                if nb is None:
                    continue
                b_id = fresh(nb, block_id)
                placed[nb.id] = _Placed(b_id, "new", None, ())
                new_blocks.append(nb)
                mapped.append(b_id)
            new_entries.append(ne.model_copy(update={"factId": block_id, "bulletIds": mapped}))

    # facts of no entry
    old_in_entry = {b for e in existing_book.entries for b in e.bulletIds} | {
        e.factId for e in existing_book.entries
    }
    rest_new = [
        f for f in new_facts.facts if f.id not in placed and f.id not in entry_heads and f.id not in bullet_of
    ]
    rest_old = [f for f in existing_book.facts if f.id not in old_in_entry]
    for skills in (True, False):
        group_new = [f for f in rest_new if (f.kind == "skill") == skills]
        group_old = [f for f in rest_old if (f.kind == "skill") == skills]
        got = _assign(group_new, group_old, skills=skills)
        for f in group_new:
            if f.id in got:
                e_fact, r = got[f.id]
                placed[f.id] = _Placed(
                    e_fact.id, r.section, None if r.section == "same" else e_fact.text, r.differs
                )
            else:
                placed[f.id] = _Placed(fresh(f, ""), "new", None, ())
                new_blocks.append(f)
    # a fact of an entry whose head the file lacks (an orphan bullet) is a block of no entry
    for f in new_facts.facts:
        if f.id not in placed:
            placed[f.id] = _Placed(fresh(f, ""), "new", None, ())
            new_blocks.append(f)

    report = _report(new_facts, placed, file_id, filename)
    merged = _merged_book(existing_book, new_facts, placed, new_blocks, new_entries, under, file_id)
    return MergeResult(report=report, merged=merged, file_book=_file_book(new_facts, placed))


def _report(new_facts: FactBook, placed: dict[str, _Placed], file_id: str, filename: str) -> MergeReport:
    lists: dict[str, list[MergeItem]] = {"same": [], "new": [], "near": [], "disagree": []}
    for fact in new_facts.facts:
        p = placed[fact.id]
        lists[p.section].append(
            MergeItem(
                blockId=p.block_id,
                kind=fact.kind,
                text=fact.text,
                existing=p.existing if p.section in ("near", "disagree") else None,
                differs=list(p.differs) if p.section == "disagree" else None,
            )
        )
    return MergeReport(fileId=file_id, filename=filename, **lists)


def _ref(fact: Fact, file_id: str, *, quote: str | None = None) -> list[FactRef]:
    """The file's references of ``fact`` for the merged book: the file's id, no line numbers."""
    source = fact.refs[0].source if fact.refs else "resume"
    return [
        FactRef(source=source, docId=file_id or "file", lines=None, quote=(quote or fact.text)[:MAX_QUOTE])
    ]


def _merged_book(
    existing: FactBook,
    new_facts: FactBook,
    placed: dict[str, _Placed],
    new_blocks: Iterable[Fact],
    new_entries: Iterable[BookEntry],
    under: dict[str, str],
    file_id: str,
) -> FactBook:
    facts = {f.id: f.model_copy(deep=True) for f in existing.facts}
    for fact in new_facts.facts:
        p = placed[fact.id]
        if p.section == "new":
            continue
        target = facts.get(p.block_id)
        if target is not None and all(r.docId != file_id for r in target.refs):
            target.refs.extend(_ref(fact, file_id))
    entries = [e.model_copy(deep=True) for e in existing.entries]
    by_entry = {e.factId: e for e in entries}
    appended: list[Fact] = []
    for fact in new_blocks:
        p = placed[fact.id]
        appended.append(
            Fact(
                id=p.block_id,
                text=fact.text,
                kind=fact.kind,
                refs=_ref(fact, file_id),
                evidenceTier=fact.evidenceTier,
            )
        )
        if p.block_id in under and under[p.block_id] in by_entry:
            by_entry[under[p.block_id]].bulletIds.append(p.block_id)
    entries.extend(new_entries)
    return existing.model_copy(update={"facts": [*facts.values(), *appended], "entries": entries})


def _file_book(new_facts: FactBook, placed: dict[str, _Placed]) -> FactBook:
    facts: list[Fact] = []
    seen: set[str] = set()
    for fact in new_facts.facts:
        block_id = placed[fact.id].block_id
        if block_id in seen:
            continue
        seen.add(block_id)
        facts.append(fact.model_copy(update={"id": block_id}))
    entries: list[BookEntry] = []
    for entry in new_facts.entries:
        if entry.factId not in placed:
            continue
        ids = [placed[b].block_id for b in entry.bulletIds if b in placed]
        entries.append(
            entry.model_copy(
                update={"factId": placed[entry.factId].block_id, "bulletIds": list(dict.fromkeys(ids))}
            )
        )
    return new_facts.model_copy(update={"facts": facts, "entries": entries})


__all__ = ["MergeResult", "Relation", "dates_of", "figures_of", "merge", "relate", "words"]
