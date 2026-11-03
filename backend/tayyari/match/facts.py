"""The facts a match may cite: the portfolio's line-cited facts, regrouped into bullets and skills, plus what the
person told us.

Nothing here extracts anything new from a document. The portfolio extractor (`portfolio.facts`, reused, not
forked) already returns facts whose text code copied from cited source lines; this module

  - re-checks every quote against the source *lines* with the match's own folding (whitespace, quote marks, dashes)
    and finds the lines it sits on (``FactRef.lines``). A quote that is not a passage of its source is dropped and
    recorded in ``FactBook.dropped``;
  - splits a job, project or degree into an entry fact plus one ``accomplishment`` fact per bullet, which is the
    unit the resume is built from;
  - joins a bullet that a PDF wrapped onto the next line back into one fact, so its numbers are not cut off;
  - adds a ``skill`` fact for each skill of the stored graph that is evidenced (score 45 and over) by a line of a
    source, never for a gap skill, with the strongest evidence tier its lines earn;
  - adds a ``detail`` fact (kind ``cert`` or ``other``) for each resume line no other fact quotes: registrations,
    licences, languages, notice period, CTC, the size of a hospital, a tools line. The text is the line itself, so it
    is its own quote. Personal details a hiring decision must not rest on (birth date, sex, marital status, religion,
    a parent's name) and the declaration are never taken;
  - appends ``user`` facts (answers to gap questions, chat), whose source is the person's own words.

Evidence tiers (strongest first): ``artifact`` when a GitHub or Lovable source backs the fact, ``built`` for a
project with a stated outcome, ``used`` for a bullet of the resume (or a line of a page), ``listed`` for a bare
mention in a skills list.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from tayyari.contract.match import EvidenceTier, FactSource, MatchFactKind
from tayyari.contract.models import Profile
from tayyari.contract.portfolio import Fact as PortfolioFact
from tayyari.contract.stored import StoredContext
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.aliases import term_variants
from tayyari.match.models import BookEntry, EntryKind, Fact, FactBook, FactRef
from tayyari.match.text import clip, fold_text, short_hash, tidy
from tayyari.portfolio.facts import FactsResult, Source, extract_facts, usable_sources
from tayyari.portfolio.ground import tokens_of

MIN_SKILL_SCORE: Final = 45
"""Skills of the graph below this score are gaps: they never become facts (the graph says the evidence is absent)."""
MAX_SKILL_FACTS: Final = 40
MAX_FACTS: Final = 160
MAX_REFS_PER_SKILL: Final = 2
MAX_REF_QUOTE: Final = 500
"""A quote is cut to this many characters (a prefix of the passage is still a passage)."""
WINDOW: Final = 6
"""A quote is looked for on one line, then over up to this many wrapped lines."""
OVERLAP_MIN_TOKENS: Final = 3
MAX_DETAIL_FACTS: Final = 30
"""Resume lines no other fact quotes, taken as ``detail`` facts, at most this many (in the order of the resume)."""
MAX_WRAP_LINES: Final = 3
"""A bullet is joined with at most this many following lines that continue it."""

_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_CERT = re.compile(
    r"certif|licen[sc]e|diploma|nanodegree|chartered|\bcpa\b|\bpmp\b|registration|registered|\bcouncil\b",
    re.IGNORECASE,
)
_DANGLING = re.compile(
    r"(?:\b(?:and|or|of|to|the|a|an|in|for|with|by|from|at|on|as|via|into|than|while|including)|[,(&/-])$",
    re.IGNORECASE,
)
"""A line that ends on one of these does not finish its sentence: the next line continues it."""
_NOT_FOR_HIRING = re.compile(
    r"date of birth|\bd\.?\s?o\.?\s?b\b|\bsex\b|gender|marital|religion|\bcaste\b|father'?s|mother'?s|husband'?s|"
    r"spouse|height|weight|blood group|hereby declare|declaration|signature|^\s*(?:place|date)\s*[:\-]",
    re.IGNORECASE,
)
"""Personal details a hiring decision must not rest on, and the declaration: never a fact."""
_CONTACT_LINE = re.compile(
    r"@|https?://|www\.|linkedin\.com|github\.com|(?:\+91[\s-]?)?\b[6-9]\d{4}[\s-]?\d{5}\b|\+\d{1,3}[\s-]?\d",
    re.IGNORECASE,
)
_OUTCOME = re.compile(r"\d")
_LEAD_MARK = re.compile(r"^\s*(?:[-*+•>]|\d{1,3}[.)])\s+")
_LIST_PREFIX = re.compile(r"^[\w &/+\-]{2,30}:\s*")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
_SPLIT_ITEMS = re.compile(r"[,;|•·]")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "over",
        "under",
        "about",
        "using",
        "used",
        "use",
        "built",
        "build",
        "led",
        "own",
        "owned",
        "owns",
        "across",
        "their",
        "them",
        "they",
        "have",
        "has",
        "had",
        "were",
        "was",
        "are",
        "also",
        "than",
        "then",
        "such",
        "each",
        "per",
        "via",
        "not",
        "but",
        "you",
        "your",
        "our",
        "out",
        "its",
        "all",
        "any",
        "can",
        "more",
        "most",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
    ]
)
_GENERIC_NAME = frozenset(
    ["marketing", "management", "testing", "design", "development", "engineering", "analysis", "skills"]
)
"""Words of a skill's name that appear in half the lines of a resume and so prove nothing alone."""
_TIER_RANK: Final = {"artifact": 3, "built": 2, "used": 1, "listed": 0}


def source_of_kind(kind: str) -> FactSource:
    """The match's source name for a stored source kind: notes are the person's own words, pages are links."""
    if kind == "resume":
        return "resume"
    if kind == "github":
        return "github"
    if kind == "lovable":
        return "lovable"
    return "user" if kind == "notes" else "link"


# ───────────────────────── dates ─────────────────────────


def _month_year(value: str | None) -> str | None:
    if not value:
        return None
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?", value.strip())
    if not m:
        return None
    if m.group(2) and 1 <= int(m.group(2)) <= 12:
        return f"{_MONTHS[int(m.group(2)) - 1]} {m.group(1)}"
    return m.group(1)


def format_period(start: str | None, end: str | None, current: bool | None) -> str | None:
    """``Jan 2021 - Present``, ``Jan 2024 - Apr 2024``, ``2022 - 2023`` or one date. ASCII, both ends or one date."""
    first, last = _month_year(start), _month_year(end)
    if first and current:
        return f"{first} - Present"
    if first and last:
        return first if first == last else f"{first} - {last}"
    return first or last


def _recency(end: str | None, start: str | None, current: bool | None) -> int:
    if current:
        return 999_999
    for value in (end, start):
        m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?", (value or "").strip())
        if m:
            return int(m.group(1)) * 100 + (int(m.group(2)) if m.group(2) else 12)
    return 0


# ───────────────────────── locating a quote in its source ─────────────────────────


class _Lines:
    """The folded lines of one source, so a quote can be located on the lines it sits on."""

    def __init__(self, src: Source) -> None:
        self.src = src
        self.folded = [fold_text(line) for line in src.lines]

    def locate(self, quote: str) -> tuple[int, int] | None:
        """``(first, last)`` 1-based lines whose folded text holds the folded ``quote``, else None."""
        needle = fold_text(quote)
        if len(needle) < 3:
            return None
        n = len(self.folded)
        for i in range(n):
            if needle in self.folded[i]:
                return i + 1, i + 1
        for i in range(n):
            if not self.folded[i]:
                continue
            for j in range(i + 1, min(i + WINDOW, n)):
                if not self.folded[j]:
                    break
                joined = " ".join(self.folded[i : j + 1])
                if needle in joined and needle not in " ".join(self.folded[i + 1 : j + 1]):
                    return i + 1, j + 1
        return None


def locate_quote(src: Source, quote: str) -> tuple[int, int] | None:
    """The lines of ``src`` that hold ``quote`` (folded comparison), or None when it is not a passage of them."""
    return _Lines(src).locate(quote)


# ───────────────────────── portfolio facts → match facts ─────────────────────────


class _Builder:
    def __init__(self, profile: Profile, context: StoredContext) -> None:
        self.profile = profile
        self.context = context
        self.sources = {s.id: s for s in usable_sources(context)}
        self.order = list(self.sources)
        self.kinds = {s.id: s.kind for s in context.sources}
        self.lines = {sid: _Lines(src) for sid, src in self.sources.items()}
        self.facts: list[Fact] = []
        self.entries: list[BookEntry] = []
        self.contact: list[str] = []
        self.dropped: list[str] = []
        self.ids: set[str] = set()

    # ── refs ──

    def ref(self, source_id: str, quote: str) -> FactRef | None:
        lines = self.lines.get(source_id)
        if lines is None:
            return None
        found = lines.locate(quote)
        if found is None:
            return None
        return FactRef(
            source=source_of_kind(self.kinds.get(source_id, "link")),
            docId=source_id,
            lines=[found[0], found[1]],
            quote=tidy(quote)[:MAX_REF_QUOTE],
        )

    def ref_anywhere(self, quote: str, prefer: Sequence[str]) -> FactRef | None:
        for source_id in [*prefer, *(s for s in self.order if s not in prefer)]:
            found = self.ref(source_id, quote)
            if found is not None:
                return found
        return None

    def add(self, fact: Fact) -> None:
        if fact.id in self.ids:
            return
        self.ids.add(fact.id)
        self.facts.append(fact)

    def drop(self, reason: str) -> None:
        self.dropped.append(reason)

    # ── one portfolio fact ──

    def portfolio_fact(self, pf: PortfolioFact) -> None:
        if pf.kind == "contact":
            self._contact(pf)
            return
        if pf.kind in ("experience", "project", "education"):
            self._entry(pf)
        elif pf.kind in ("achievement", "clip", "quote"):
            self._standalone(pf)

    def _contact(self, pf: PortfolioFact) -> None:
        ref = pf.sourceRefs[0] if pf.sourceRefs else None
        written = ref.quote if ref else (pf.detail or "")
        if written and self.ref_anywhere(written, [ref.sourceId] if ref else []) is not None:
            self.contact.append(tidy(written))
        else:
            self.drop(f"contact {pf.id}: not a passage of its source")

    def _refs_of(self, pf: PortfolioFact) -> list[FactRef]:
        refs: list[FactRef] = []
        for sref in pf.sourceRefs:
            found = self.ref(sref.sourceId, sref.quote)
            if found is None:
                self.drop(f"{pf.id}: quote is not a passage of source {sref.sourceId}")
            else:
                refs.append(found)
        return refs

    def _standalone(self, pf: PortfolioFact) -> None:
        refs = self._refs_of(pf)
        if not refs:
            self.drop(f"{pf.id}: no quote left")
            return
        kind: MatchFactKind
        if pf.kind == "achievement":
            kind = "cert" if _CERT.search(pf.title) else "accomplishment"
            text = pf.title + (f", {pf.org}" if pf.org else "") + (f" ({pf.start})" if pf.start else "")
        elif pf.kind == "clip":
            kind = "other"
            text = (
                f"“{pf.title}”" + (f", {pf.org}" if pf.org else "") + (f" ({pf.start})" if pf.start else "")
            )
        else:
            kind = "other"
            text = pf.title
        self.add(Fact(id=pf.id, text=tidy(text), kind=kind, refs=refs, evidenceTier=None))

    def _entry(self, pf: PortfolioFact) -> None:
        refs = self._refs_of(pf)
        if not refs:
            self.drop(f"{pf.id}: no quote left, so the entry and its bullets are dropped")
            return
        dates = format_period(pf.start, pf.end, pf.current)
        backed = any(r.source in ("github", "lovable") for r in refs)
        outcome = any(_OUTCOME.search(h) for h in pf.highlights)
        detail = pf.detail if pf.detail and self.ref_anywhere(pf.detail, [r.docId for r in refs]) else None

        kind: EntryKind
        if pf.kind == "experience":
            text = (
                pf.title + (f" at {pf.org}" if pf.org else "") + (f", {pf.location}" if pf.location else "")
            )
            text += f" ({dates})" if dates else ""
            kind = "experience"
        elif pf.kind == "education":
            text = pf.title + (f", {pf.org}" if pf.org else "") + (f" ({dates})" if dates else "")
            kind = "education"
        else:
            text = pf.title + (f": {detail}" if detail else "")
            kind = "project"
        if kind in ("experience", "project") and pf.tags:
            text += f" Tools: {', '.join(pf.tags)}."
        tier: EvidenceTier | None = None
        if kind == "project":
            tier = "artifact" if backed else "built" if outcome else "used"
        self.add(Fact(id=pf.id, text=tidy(text), kind=kind, refs=refs, evidenceTier=tier))

        bullet_ids: list[str] = []
        prefer = [r.docId for r in refs]
        for n, highlight in enumerate(pf.highlights, start=1):
            ref = self.ref_anywhere(highlight, prefer)
            if ref is None:
                self.drop(f"{pf.id}.{n}: bullet is not a passage of any source")
                continue
            highlight, ref = self._unwrapped(highlight, ref)
            bullet_tier: EvidenceTier = "used"
            if ref.source in ("github", "lovable"):
                bullet_tier = "artifact"
            elif kind == "project" and _OUTCOME.search(highlight):
                bullet_tier = "built"
            fid = f"{pf.id}.{n}"
            self.add(
                Fact(
                    id=fid, text=tidy(highlight), kind="accomplishment", refs=[ref], evidenceTier=bullet_tier
                )
            )
            bullet_ids.append(fid)
        self.entries.append(
            BookEntry(
                factId=pf.id,
                kind=kind,
                title=pf.title,
                org=pf.org,
                location=pf.location,
                start=pf.start,
                end=pf.end,
                current=bool(pf.current),
                dates=dates,
                bulletIds=bullet_ids,
                recency=_recency(pf.end, pf.start, pf.current),
            )
        )

    def _unwrapped(self, text: str, ref: FactRef) -> tuple[str, FactRef]:
        """``text`` joined with the lines that continue it when a PDF wrapped it ("Redesigned the dispute status API
        and" + "reduced average response time from 1.8 seconds to 600 ms ..."), with the reference of the whole
        passage; as it was when nothing continues it."""
        lines = self.lines.get(ref.docId)
        if lines is None or not ref.lines:
            return text, ref
        raw = lines.src.lines
        joined, last = text, ref.lines[1]
        for _ in range(MAX_WRAP_LINES):
            if last >= len(raw) or not _continues(joined, raw[last]):
                break
            joined, last = f"{joined.rstrip()} {raw[last].strip()}", last + 1
        if joined == text:
            return text, ref
        whole = self.ref(ref.docId, joined)
        return (tidy(joined), whole) if whole is not None else (text, ref)

    # ── resume lines no fact quotes ──

    def detail_facts(self) -> None:
        """A ``detail`` fact for each resume line (with the lines that continue it) that no fact quotes yet."""
        made = 0
        for source_id in self.order:
            if self.kinds.get(source_id) != "resume" or made >= MAX_DETAIL_FACTS:
                continue
            raw = self.lines[source_id].src.lines
            covered = self._covered(source_id)
            said = [fold_text(f.text) for f in self.facts]
            i = 0
            while i < len(raw) and made < MAX_DETAIL_FACTS:
                tail_of_quoted = i in covered and _continues(
                    raw[i - 1], raw[i]
                )  # the rest of a quoted sentence
                if i + 1 in covered or tail_of_quoted or not _detail_line(raw[i]):
                    i += 1
                    continue
                text, last = _unmarked(raw[i]), i + 1
                while last < len(raw) and last - i <= MAX_WRAP_LINES and last + 1 not in covered:
                    if not _continues(text, raw[last]) or not _detail_line(raw[last]):
                        break
                    text, last = f"{text} {raw[last].strip()}", last + 1
                i = last
                folded = fold_text(text)
                if any(folded in s for s in said):
                    continue
                ref = self.ref(source_id, text)
                if ref is None:
                    continue
                kind: MatchFactKind = "cert" if _CERT.search(text) else "other"
                fid = f"line-{short_hash(source_id, length=6)}-{ref.lines[0] if ref.lines else i}"
                self.add(
                    Fact(id=fid, text=tidy(text)[:MAX_REF_QUOTE], kind=kind, refs=[ref], evidenceTier=None)
                )
                made += 1

    def _covered(self, source_id: str) -> set[int]:
        """The 1-based lines of ``source_id`` that a fact already quotes."""
        out: set[int] = set()
        for fact in self.facts:
            for ref in fact.refs:
                if ref.docId == source_id and ref.lines:
                    out.update(range(ref.lines[0], ref.lines[1] + 1))
        return out

    # ── skills ──

    def skill_facts(self) -> None:
        taken = {f.id for f in self.facts}
        ranked = sorted(self.profile.skills, key=lambda s: -s.score)
        made = 0
        for skill in ranked:
            if made >= MAX_SKILL_FACTS:
                break
            if skill.score < MIN_SKILL_SCORE:
                continue
            refs = self._skill_refs(skill.name, [(e.sourceId, e.text) for e in skill.evidence])
            if not refs:
                self.drop(f"skill {skill.name}: no line of any source shows it")
                continue
            base = "skill-" + (
                re.sub(r"[^a-z0-9]+", "-", skill.name.casefold()).strip("-") or short_hash(skill.name)
            )
            fid, n = base, 2
            while fid in taken:
                fid, n = f"{base}-{n}", n + 1
            taken.add(fid)
            tier = max((_ref_tier(r, self.lines) for r in refs), key=lambda t: _TIER_RANK[t])
            self.add(Fact(id=fid, text=skill.name, kind="skill", refs=refs, evidenceTier=tier))
            made += 1

    def _skill_refs(self, name: str, evidence: Sequence[tuple[str, str]]) -> list[FactRef]:
        hits: list[tuple[int, FactRef]] = []
        variants = sorted(term_variants(name), key=len, reverse=True)
        for source_id in self.order:
            lines = self.lines[source_id]
            raw_lines = lines.src.lines
            for i, folded in enumerate(lines.folded):
                if not folded or not _has_term(name, variants, raw_lines[i], folded):
                    continue
                ref = self.ref(source_id, _unmarked(raw_lines[i]))
                if ref is None:
                    continue
                hits.append((_rank_line(ref, raw_lines[i]), ref))
        if not hits:
            for source_id, text in evidence:
                best = self._by_overlap(source_id, name, text)
                if best is not None:
                    hits.append(
                        (_rank_line(best, self.sources[source_id].lines[(best.lines or [1])[0] - 1]), best)
                    )
        hits.sort(key=lambda item: -item[0])
        out: list[FactRef] = []
        seen: set[tuple[str, int]] = set()
        for _, ref in hits:
            key = (ref.docId, (ref.lines or [0])[0])
            if key in seen:
                continue
            seen.add(key)
            out.append(ref)
            if len(out) >= MAX_REFS_PER_SKILL:
                break
        return out

    def _by_overlap(self, source_id: str, name: str, evidence: str) -> FactRef | None:
        """The line of a source that best shows a skill the graph names in other words than the source does.

        A line qualifies only if it holds at least one distinctive word of the skill's name (``whatsapp`` for
        ``WhatsApp marketing``) and shares enough words with the graph's evidence sentence; the line with the most
        shared words wins. Words that name an employer or the whole field alone never decide it."""
        lines = self.lines.get(source_id)
        if lines is None:
            return None
        name_words = {t for t in tokens_of(name) if len(t) > 2 and t not in _STOP and t not in _GENERIC_NAME}
        wanted = {t for t in tokens_of(evidence) if len(t) > 2 and t not in _STOP and not t.isdigit()}
        best: tuple[int, int] | None = None
        for i, raw in enumerate(lines.src.lines):
            if not raw.strip():
                continue
            have = set(tokens_of(raw))
            hits = len(name_words & have)
            shared = len(wanted & have)
            if not hits or hits + shared < OVERLAP_MIN_TOKENS:
                continue
            score = 3 * hits + shared
            if best is None or score > best[0]:
                best = (score, i)
        if best is None:
            return None
        return self.ref(source_id, _unmarked(lines.src.lines[best[1]]))


def _unmarked(line: str) -> str:
    """A source line without its list marker or quote mark and spare whitespace: the passage a skill fact quotes."""
    return _LEAD_MARK.sub("", line, count=1).strip()


def _continues(text: str, next_line: str) -> bool:
    """Does ``next_line`` carry on the sentence ``text`` ends with (a line a PDF wrapped), rather than start a new one?

    It does when ``text`` stops without ending its sentence and the next line is not a new bullet: it starts in lower
    case or with a digit, or ``text`` ends on a word like "and" or "of"."""
    body, nxt = text.rstrip(), next_line.strip()
    if not body or not nxt or body.endswith((".", "!", "?", ":", ";")) or _LEAD_MARK.match(next_line):
        return False
    return nxt[0].islower() or nxt[0].isdigit() or bool(_DANGLING.search(body))


def _is_heading(text: str) -> bool:
    """A section heading ("WORK EXPERIENCE", "Skills:"): no lower-case letters, or a few words ending in a colon."""
    words = text.split()
    return (not any(ch.islower() for ch in text) and len(words) <= 6) or (
        text.endswith(":") and len(words) <= 4
    )


def _detail_line(line: str) -> bool:
    """Could ``line`` be a detail fact: words, not a heading, contact details or something a hiring decision must not
    rest on."""
    text = _unmarked(line)
    if len(text) < 4 or len(text.split()) < 2 or _is_heading(text):
        return False
    return not _NOT_FOR_HIRING.search(text) and not _CONTACT_LINE.search(text)


def _has_term(name: str, variants: Sequence[str], raw_line: str, folded_line: str) -> bool:
    if len(name.strip()) <= 2:
        return re.search(rf"(?<![\w+#.&]){re.escape(name.strip())}(?![\w+#&])", raw_line) is not None
    for variant in variants:
        if len(variant) < 2:
            continue
        if re.search(rf"(?<![a-z0-9+#]){re.escape(variant)}(?![a-z0-9+#])", folded_line):
            return True
    return False


def _is_skills_list(line: str) -> bool:
    """A line that only lists names (``Frontend: React, Next.js, Storybook``): a mention, not a use."""
    body = _LIST_PREFIX.sub("", line.strip().lstrip("-*• "))
    parts = [p.strip() for p in _SPLIT_ITEMS.split(body) if p.strip()]
    if len(parts) < 3 or _SENTENCE_END.search(body.rstrip(".")):
        return False
    short = sum(1 for p in parts if len(p.split()) <= 4)
    return short / len(parts) >= 0.8


def _ref_tier(ref: FactRef, lines: dict[str, _Lines]) -> EvidenceTier:
    if ref.source in ("github", "lovable"):
        return "artifact"
    source = lines.get(ref.docId)
    first = (ref.lines or [1])[0]
    raw = source.src.lines[first - 1] if source and first - 1 < len(source.src.lines) else ref.quote
    return "listed" if _is_skills_list(raw) else "used"


def _rank_line(ref: FactRef, raw_line: str) -> int:
    if ref.source in ("github", "lovable"):
        return 3
    return 0 if _is_skills_list(raw_line) else 2


# ───────────────────────── public API ─────────────────────────


def user_fact(
    text: str, *, key: str, kind: MatchFactKind = "accomplishment", quote: str | None = None
) -> Fact:
    """A fact in the person's own words (an answer to a gap question, or something said in chat).

    ``key`` names where it came from (a question id or a chat id) and, with the text, makes the id stable.
    """
    cleaned = tidy(text)
    return Fact(
        id=f"user-{short_hash(key, cleaned, length=8)}",
        text=cleaned,
        kind=kind,
        refs=[FactRef(source="user", docId=key, lines=None, quote=tidy(quote) if quote else cleaned)],
        evidenceTier="used",
    )


def build_facts(
    profile: Profile,
    context: StoredContext,
    portfolio_facts: Sequence[PortfolioFact],
    user_facts: Sequence[Fact] = (),
) -> FactBook:
    """The `FactBook` of one person: ``portfolio_facts`` regrouped and re-checked against ``context``, the skills of
    ``profile`` that a source line evidences, and ``user_facts`` as they are (their source is the person)."""
    b = _Builder(profile, context)
    for pf in portfolio_facts:
        b.portfolio_fact(pf)
    b.skill_facts()
    b.detail_facts()
    for fact in user_facts:
        if fact.refs and all(r.source == "user" and r.quote.strip() for r in fact.refs):
            b.add(fact)
        else:
            b.drop(f"{fact.id}: a user fact needs a user reference with words")
    facts = b.facts
    if len(facts) > MAX_FACTS:
        extra = len(facts) - MAX_FACTS
        keep_ids = {f.id for f in facts if f.kind != "skill"}
        skills = [f for f in facts if f.kind == "skill"]
        skills.sort(key=lambda f: -_TIER_RANK.get(f.evidenceTier or "listed", 0))
        allowed = {f.id for f in skills[: max(0, MAX_FACTS - len(keep_ids))]}
        facts = [f for f in facts if f.id in keep_ids or f.id in allowed]
        b.drop(f"{extra} skill facts over the cap of {MAX_FACTS} were left out")
    person = profile.person
    return FactBook(
        name=person.name,
        headline=clip(person.headline, 120) if person.headline else None,
        contact=list(dict.fromkeys(b.contact)),
        facts=facts,
        entries=b.entries,
        dropped=b.dropped,
    )


async def extract_portfolio_facts(
    claude: ClaudeClient,
    *,
    model: str,
    context: StoredContext,
    profile: Profile,
    call_ctx: CallContext,
) -> FactsResult:
    """The portfolio's own extraction (one Haiku call per source, line-cited, verified by code), reused as it is."""
    return await extract_facts(claude, model=model, ctx=context, profile=profile, call_ctx=call_ctx)


__all__ = [
    "MAX_FACTS",
    "MIN_SKILL_SCORE",
    "build_facts",
    "extract_portfolio_facts",
    "format_period",
    "locate_quote",
    "source_of_kind",
    "user_fact",
]
