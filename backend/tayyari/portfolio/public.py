"""The viewer's portfolio: what a stranger on the public page (or the owner's "Preview public view") may see.

`to_public` builds a new `PublicPortfolio` from a `Portfolio`; it removes rather than hides:

    blocks      only visible + public ones (a hidden block's existence, facts and copy are gone)
    facts       only kinds a shown block displays; contact facts only when the matching privacy switch is on;
                a link that is itself a phone or messenger deep link (``wa.me``, ``t.me``, ``tel:``, ``sms:`` ...) or
                carries a phone number follows the phone switch, one that carries an e-mail address follows the
                e-mail switch; the grade only when ``privacy.grades``; source quotes never (they can repeat the
                resume header)
    slots       only those that belong to a shown block (and, for ``exp:`` / ``proj:`` slots, to a shown fact)
    openTo      emptied unless the "open to" block is shown
    graph       hidden domains removed (also from ``spec.graph``), skills under `GRAPH_PUBLIC_MIN_SKILL` removed,
                every skill's evidence line removed (viewers get skills and scores only), the skill count and
                the mastery kept for a shown graph; a graph block that is not shown sends no graph data at all
    never       profileId, looks, version, timestamps, design reasons

Every remaining string is cleaned on its way out: an e-mail address that is still private is scrubbed, and so is a
phone number when the phone switch is off, and the PII scrubber of `core.redact` takes out whatever it still finds
(an address line is never shown, an e-mail or phone only when its switch is on). With ``privacy.grades`` off the
grade is removed from every string of every fact and slot, however it is written (``8.6``, ``8.60 / 10``, ``CGPA:
8.6/10``), and so are the words CGPA and GPA. The owner's own edited copy keeps its e-mail and phone number (they
chose those words) but never a grade they hid.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from urllib.parse import unquote, urlsplit

from tayyari.contract.portfolio import (
    GRAPH_PUBLIC_MIN_SKILL,
    BlockKind,
    CopySlot,
    Fact,
    GraphDomain,
    GraphSnapshot,
    GraphSpec,
    Portfolio,
    PortfolioContent,
    PortfolioPrivacy,
    PortfolioSpec,
    PublicPortfolio,
)
from tayyari.core.redact import ADDRESS_MASK, EMAIL_MASK, PHONE_MASK, redact_pii

from .ground import grade_scores
from .kit import FACT_BLOCKS, slot_owner

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{8,}\d(?![\w])")
_MIN_PHONE_DIGITS = 10
_PHONE_BLOB_DIGITS = 7

_PHONE_SCHEMES = frozenset(
    {
        "tel",
        "sms",
        "smsto",
        "mms",
        "mmsto",
        "viber",
        "whatsapp",
        "tg",
        "telegram",
        "signal",
        "sgnl",
        "facetime",
        "facetime-audio",
        "callto",
        "imessage",
    }
)
_MESSENGER_HOSTS = frozenset(
    {
        "wa.me",
        "wa.link",
        "api.whatsapp.com",
        "web.whatsapp.com",
        "t.me",
        "telegram.me",
        "telegram.dog",
        "signal.me",
    }
)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:")


# ───────────────────────── contact links ─────────────────────────


def _url_kinds(url: str | None) -> frozenset[str]:
    """What a link gives away: ``email`` and / or ``phone`` (a messenger deep link counts as a phone number)."""
    if not url:
        return frozenset()
    raw = url.strip()
    scheme = _SCHEME.match(raw.lower())
    name = scheme.group(0).rstrip(":") if scheme else ""
    kinds: set[str] = set()
    if name == "mailto":
        kinds.add("email")
    elif name in _PHONE_SCHEMES:
        kinds.add("phone")
    has_authority = scheme is not None and raw[scheme.end() :].startswith("//")
    target = raw if has_authority else f"//{raw}" if scheme is None else ""  # "wa.me/9198..." has no scheme
    try:
        parts = urlsplit(target)
        host = (parts.hostname or "").removeprefix("www.")
    except ValueError:
        parts, host = urlsplit(""), ""
    if host in _MESSENGER_HOSTS or (host == "whatsapp.com" and parts.path.lower().startswith("/send")):
        kinds.add("phone")
    seen = redact_pii(unquote(unquote(raw)))
    if EMAIL_MASK in seen:
        kinds.add("email")
    if PHONE_MASK in seen:
        kinds.add("phone")
    return frozenset(kinds)


def _contact_kind(f: Fact) -> str:
    """ "email", "phone" or "other" for a contact (or link) fact."""
    url = (f.url or "").lower()
    if url.startswith("mailto:"):
        return "email"
    if url.startswith("tel:"):
        return "phone"
    if f.kind in ("contact", "link"):
        kinds = _url_kinds(f.url)
        if "email" in kinds:
            return "email"
        if "phone" in kinds:
            return "phone"
    if f.kind != "contact":
        return "other"
    blob = " ".join(filter(None, [f.title, f.url, f.detail, *(r.quote for r in f.sourceRefs)]))
    if "@" in blob:
        return "email"
    if sum(c.isdigit() for c in blob) >= _PHONE_BLOB_DIGITS:
        return "phone"
    return "other"


def _safe_url(f: Fact, privacy: PortfolioPrivacy) -> str | None:
    """The fact's link, or None when it would give away an e-mail or phone number that is private."""
    kinds = _url_kinds(f.url)
    if ("email" in kinds and not privacy.email) or ("phone" in kinds and not privacy.phone):
        return None
    return f.url


# ───────────────────────── text cleaning ─────────────────────────

_DANGLING = " .,;:·|–—-"
_STASH = re.compile("(k+)")
_GAP = re.compile(r"[ 	]{2,}")


def _tidy(text: str) -> str:
    """Close the gap a removal left (runs of spaces, edge whitespace) without touching line breaks."""
    return _GAP.sub(" ", text).strip()


def _mend(text: str) -> str:
    """The text after a removal: no empty brackets, doubled separators or separators at the edges."""
    out = re.sub(r"[(\[]\s*[,;:·|–—-]*\s*[)\]]", "", text)
    out = re.sub(r"([,;:·|])(?:\s*[,;:·|])+", r"\1", out)
    out = re.sub(r"\s+([,;:.])", r"\1", out)
    return _tidy(out).lstrip(_DANGLING).rstrip(" ,;:·|–—-")


def _is_phone(candidate: str) -> bool:
    """Is this digit run a phone number (not a list of years, a date or a short number)?"""
    return sum(c.isdigit() for c in candidate) >= _MIN_PHONE_DIGITS and redact_pii(candidate) == PHONE_MASK


def _scrub(text: str, privacy: PortfolioPrivacy) -> str:
    """Remove a still-private e-mail address (and phone number) from running text."""
    out = text
    if not privacy.email:
        out = _EMAIL.sub("", out)
    if not privacy.phone:
        out = _PHONE.sub(lambda m: "" if _is_phone(m.group(0)) else m.group(0), out)
    return _mend(out) if out != text else text


def _redact(text: str, privacy: PortfolioPrivacy) -> str:
    """``text`` without what the PII scrubber finds: an address line always, an e-mail or phone unless its switch is on."""
    if not text:
        return text
    kept: list[str] = []

    def stash(m: re.Match[str]) -> str:
        kept.append(m.group(0))
        return f"{'k' * len(kept)}"  # letters only: never read as digits

    work = text
    if privacy.email:
        work = _EMAIL.sub(stash, work)
    if privacy.phone:
        work = _PHONE.sub(lambda m: stash(m) if _is_phone(m.group(0)) else m.group(0), work)
    masked = redact_pii(work)
    if masked == work:
        return text
    lines: list[str] = []
    for line in masked.splitlines(keepends=True):
        if line.strip() == ADDRESS_MASK:
            continue
        lines.append(line.replace(ADDRESS_MASK, "").replace(EMAIL_MASK, "").replace(PHONE_MASK, ""))
    out = _STASH.sub(lambda m: kept[len(m.group(1)) - 1], "".join(lines))
    return _mend(out)


_GRADE_WORDS = re.compile(
    r"(?<![A-Za-z])(?:c\.?g\.?p\.?a|g\.?p\.?a|grade\s+point\s+average)(?![A-Za-z])", re.IGNORECASE
)
_MIN_GRADE_TEXT = 4


class _Grades:
    """Takes a hidden grade out of any string, however it is written (see the module docstring).

    Removes the grade text itself (spacing and case ignored), then every use of its score (``8.6``, ``8.60``,
    ``8.6 / 10``, ``8.6 out of 10``, ``86%``), then the words CGPA and GPA. A score followed by letters
    (``8.6s``, ``8.6M``) is another quantity and stays.
    """

    def __init__(self, facts: Iterable[Fact]) -> None:
        grades = [f.grade.strip() for f in facts if f.grade and f.grade.strip()]
        self._texts = [
            g for g in dict.fromkeys(grades) if len(g) >= _MIN_GRADE_TEXT or any(c.isdigit() for c in g)
        ]
        scores = dict.fromkeys(score for g in grades for score in grade_scores(g))
        self._scores = [self._score_pattern(s) for s in scores]
        self._any = bool(grades)

    @staticmethod
    def _text_pattern(grade: str) -> re.Pattern[str]:
        pieces = [
            r"\s*/\s*" if p == "/" else r"\s*" if p.isspace() else re.escape(p)
            for p in re.split(r"(\s+|/)", grade)
            if p
        ]
        return re.compile(rf"(?<![\w]){''.join(pieces)}(?![\w])", re.IGNORECASE)

    @staticmethod
    def _score_pattern(score: str) -> re.Pattern[str]:
        whole, _, fraction = score.partition(".")
        whole = whole.lstrip("0") or "0"
        fraction = fraction.rstrip("0")
        decimal = rf"[.,]{re.escape(fraction)}0*" if fraction else r"(?:[.,]0+)?"
        return re.compile(
            rf"(?<![\w.,])0*{whole}{decimal}(?:\s*(?:/|out\s+of)\s*\d+(?:[.,]\d+)?)?(?:\s*%)?(?!\w|[.,]\d)",
            re.IGNORECASE,
        )

    @property
    def active(self) -> bool:
        """Whether any fact has a grade (a short one such as "A" is still removed from its own fact)."""
        return self._any

    def strip(self, text: str, own: str | None = None) -> str:
        """``text`` without the grade. ``own`` is the grade of the fact the text belongs to (removed even when short)."""
        if not text:
            return text
        out = text
        own = own.strip() if own else ""
        texts = self._texts if not own or own in self._texts else [*self._texts, own]
        for grade in texts:
            out = self._text_pattern(grade).sub("", out)
        out = _GRADE_WORDS.sub("", out)
        for pattern in self._scores:
            out = pattern.sub("", out)
        return _mend(out) if out != text else text


class _Cleaner:
    """One portfolio's text cleaning: the grade, a private e-mail or phone number, and whatever the scrubber finds."""

    def __init__(self, privacy: PortfolioPrivacy, facts: Iterable[Fact]) -> None:
        self.privacy = privacy
        grades = None if privacy.grades else _Grades(facts)
        self._grades = grades if grades is not None and grades.active else None
        self._hide_grade_words = not privacy.grades

    def grade(self, text: str, own: str | None = None) -> str:
        if self._grades is not None:
            return self._grades.strip(text, own)
        if self._hide_grade_words:
            stripped = _GRADE_WORDS.sub("", text)
            return _mend(stripped) if stripped != text else text
        return text

    def pii(self, text: str) -> str:
        """``text`` without a private e-mail or phone number and without what the PII scrubber finds."""
        return _redact(_scrub(text, self.privacy), self.privacy)

    def __call__(self, text: str, own: str | None = None) -> str:
        return self.pii(self.grade(text, own))

    def line(self, text: str, own: str | None = None) -> str:
        """A list entry (highlight, tag): grade removed, but dropped altogether (``""``) when it held personal details."""
        graded = self.grade(text, own)
        return graded if self.pii(graded) == graded else ""


# ───────────────────────── facts, slots, graph ─────────────────────────


def _public_fact(f: Fact, privacy: PortfolioPrivacy, clean: _Cleaner) -> Fact | None:
    """The fact as a viewer sees it, or None when it is private (or nothing is left of it)."""
    kind = _contact_kind(f)
    if (kind == "email" and not privacy.email) or (kind == "phone" and not privacy.phone):
        return None
    if f.kind == "contact" and kind == "other":
        return None

    def cleaned(text: str) -> str:
        return clean(text, f.grade)

    title = cleaned(f.title)
    if not title:
        return None
    return f.model_copy(
        update={
            "title": title,
            "org": cleaned(f.org) if f.org else f.org,
            "location": cleaned(f.location) if f.location else f.location,
            "detail": cleaned(f.detail) if f.detail else f.detail,
            "url": _safe_url(f, privacy),
            "grade": f.grade if privacy.grades else None,
            "highlights": [t for h in f.highlights if (t := clean.line(h, f.grade))],
            "tags": [t for tag in f.tags if (t := clean.line(tag, f.grade))],
            "sourceRefs": [],
        }
    )


def _public_slot(s: CopySlot, clean: _Cleaner, kept_fact_ids: Collection[str]) -> CopySlot:
    text = clean.grade(s.text) if s.origin == "user" else clean(s.text)
    return CopySlot(id=s.id, text=text, origin=s.origin, factIds=[i for i in s.factIds if i in kept_fact_ids])


def _public_graph(graph: GraphSnapshot, spec: PortfolioSpec, shown: Collection[BlockKind]) -> GraphSnapshot:
    """The graph a viewer gets: skills and scores only (never the evidence lines), nothing at all when it is hidden."""
    if "graph" not in shown:
        return GraphSnapshot(
            asOf=graph.asOf, domains=[], skillCount=0, mastery=None, sources=list(graph.sources)
        )
    hidden = set(spec.graph.hiddenDomainIds)
    domains = [
        GraphDomain(
            id=d.id,
            name=d.name,
            short=d.short,
            score=d.score,
            skills=[
                s.model_copy(update={"evidence": None}) for s in d.skills if s.score >= GRAPH_PUBLIC_MIN_SKILL
            ],
        )
        for d in graph.domains
        if d.id not in hidden
    ]
    return GraphSnapshot(
        asOf=graph.asOf,
        domains=domains,
        skillCount=sum(len(d.skills) for d in domains),
        mastery=graph.mastery.model_copy() if graph.mastery else None,
        sources=list(graph.sources),
    )


def to_public(p: Portfolio) -> PublicPortfolio:
    """The filtered, viewer-safe copy of a portfolio (see the module docstring for exactly what goes)."""
    spec, content = p.spec, p.content
    privacy = spec.privacy
    clean = _Cleaner(privacy, content.facts)
    blocks = [b.model_copy() for b in spec.blocks if b.visible and b.public]
    shown: set[BlockKind] = {b.kind for b in blocks}

    facts: list[Fact] = []
    for f in content.facts:
        if not any(block in shown for block in FACT_BLOCKS.get(f.kind, ())):
            continue
        if (public := _public_fact(f, privacy, clean)) is not None:
            facts.append(public)
    kept = {f.id for f in facts}

    slots: list[CopySlot] = []
    for s in content.slots:
        owner = slot_owner(s.id)
        if owner is None or owner not in shown:
            continue
        if owner in ("experience", "projects") and s.id.partition(":")[2] not in kept:
            continue
        slots.append(_public_slot(s, clean, kept))

    person = content.person.model_copy(
        update={
            "headline": clean(content.person.headline),
            "role": clean(content.person.role),
            "location": clean(content.person.location) if content.person.location else None,
        }
    )
    show_stats = "proof" in shown or ("graph" in shown and spec.graph.stats)
    public_content = PortfolioContent(
        person=person,
        facts=facts,
        slots=slots,
        stats=[s.model_copy() for s in content.stats] if show_stats else [],
        practice=content.practice.model_copy()
        if content.practice and "graph" in shown and spec.graph.stats
        else None,
        openTo=[t for title in content.openTo if (t := clean(title))] if "openTo" in shown else [],
    )
    public_spec = PortfolioSpec(
        composition=spec.composition,
        style=spec.style.model_copy(),
        blocks=blocks,
        graph=GraphSpec(hiddenDomainIds=[], stats=spec.graph.stats),
        privacy=privacy.model_copy(),
        reasons=[],
    )
    return PublicPortfolio(
        publicId=p.publicId,
        kitVersion=p.kitVersion,
        spec=public_spec,
        content=public_content,
        graph=_public_graph(p.graph, spec, shown),
        publishedAt=p.publish.publishedAt,
        defaultView=p.publish.defaultView,
    )


__all__ = ["to_public"]
