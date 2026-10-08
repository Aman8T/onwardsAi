"""The viewer's portfolio: what a stranger on the public page (or the owner's "Preview public view") may see.

`to_public` builds a new `PublicPortfolio` from a `Portfolio`; it removes rather than hides:

    blocks      only visible + public ones (a hidden block's existence, facts and copy are gone)
    facts       only kinds a shown block displays; contact facts only when the matching privacy switch is on;
                the grade only when ``privacy.grades``; source quotes never (they can repeat the resume header)
    slots       only those that belong to a shown block (and, for ``exp:`` / ``proj:`` slots, to a shown fact)
    openTo      emptied unless the "open to" block is shown
    graph       hidden domains removed (also from ``spec.graph``), skills under `GRAPH_PUBLIC_MIN_SKILL` removed,
                the mastery kept; the whole graph is dropped when its block is hidden
    never       profileId, looks, version, timestamps, design reasons

An e-mail address that is still private is also scrubbed from any remaining text, and so is a phone number when
the phone switch is off (the owner's own edited copy is left alone: they chose those words).
"""

from __future__ import annotations

import re
from collections.abc import Collection

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

from .kit import FACT_BLOCKS, slot_owner

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{8,}\d(?![\w])")
_MIN_PHONE_DIGITS = 10
_PHONE_BLOB_DIGITS = 7


def _contact_kind(f: Fact) -> str:
    """ "email", "phone" or "other" for a contact (or mailto/tel link) fact."""
    url = (f.url or "").lower()
    if url.startswith("mailto:"):
        return "email"
    if url.startswith("tel:"):
        return "phone"
    if f.kind != "contact":
        return "other"
    blob = " ".join(filter(None, [f.title, f.url, f.detail, *(r.quote for r in f.sourceRefs)]))
    if "@" in blob:
        return "email"
    if sum(c.isdigit() for c in blob) >= _PHONE_BLOB_DIGITS:
        return "phone"
    return "other"


def _tidy(text: str) -> str:
    """Close the gap a removal left (runs of spaces, edge whitespace) without touching line breaks."""
    return re.sub(r"[ 	]{2,}", " ", text).strip()


def _scrub(text: str, privacy: PortfolioPrivacy) -> str:
    """Remove a still-private e-mail address (and phone number) from running text."""
    out = text
    if not privacy.email:
        out = _EMAIL.sub("", out)
    if not privacy.phone:
        out = _PHONE.sub(
            lambda m: "" if sum(c.isdigit() for c in m.group(0)) >= _MIN_PHONE_DIGITS else m.group(0), out
        )
    return _tidy(out) if out != text else text


def _drop(text: str, secret: str) -> str:
    """The text without a private phrase, and without the separators the removal left dangling."""
    if not secret or secret not in text:
        return text
    return _tidy(text.replace(secret, "")).lstrip(" .,;:·|–—-").rstrip(" ,;:·|–—-")


def _public_fact(f: Fact, privacy: PortfolioPrivacy) -> Fact | None:
    """The fact as a viewer sees it, or None when it is private (or nothing is left of it)."""
    kind = _contact_kind(f)
    if (kind == "email" and not privacy.email) or (kind == "phone" and not privacy.phone):
        return None
    if f.kind == "contact" and kind == "other":
        return None
    grade = f.grade if privacy.grades else None
    secret = f.grade if (f.grade and not privacy.grades) else ""

    def clean(text: str) -> str:
        return _scrub(_drop(text, secret), privacy)

    title = clean(f.title)
    if not title:
        return None
    return f.model_copy(
        update={
            "title": title,
            "org": clean(f.org) if f.org else f.org,
            "location": clean(f.location) if f.location else f.location,
            "detail": clean(f.detail) if f.detail else f.detail,
            "grade": grade,
            "highlights": [t for h in f.highlights if (t := clean(h))],
            "tags": [t for tag in f.tags if (t := clean(tag))],
            "sourceRefs": [],
        }
    )


def _public_slot(s: CopySlot, privacy: PortfolioPrivacy, kept_fact_ids: Collection[str]) -> CopySlot:
    text = s.text if s.origin == "user" else _scrub(s.text, privacy)
    return CopySlot(id=s.id, text=text, origin=s.origin, factIds=[i for i in s.factIds if i in kept_fact_ids])


def _public_graph(graph: GraphSnapshot, spec: PortfolioSpec, shown: Collection[BlockKind]) -> GraphSnapshot:
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
            skills=[s.model_copy() for s in d.skills if s.score >= GRAPH_PUBLIC_MIN_SKILL],
        )
        for d in graph.domains
        if d.id not in hidden
    ]
    return GraphSnapshot(
        asOf=graph.asOf,
        domains=domains,
        skillCount=graph.skillCount,
        mastery=graph.mastery.model_copy() if graph.mastery else None,
        sources=list(graph.sources),
    )


def to_public(p: Portfolio) -> PublicPortfolio:
    """The filtered, viewer-safe copy of a portfolio (see the module docstring for exactly what goes)."""
    spec, content = p.spec, p.content
    privacy = spec.privacy
    blocks = [b.model_copy() for b in spec.blocks if b.visible and b.public]
    shown: set[BlockKind] = {b.kind for b in blocks}

    facts: list[Fact] = []
    for f in content.facts:
        if not any(block in shown for block in FACT_BLOCKS.get(f.kind, ())):
            continue
        if (public := _public_fact(f, privacy)) is not None:
            facts.append(public)
    kept = {f.id for f in facts}

    slots: list[CopySlot] = []
    for s in content.slots:
        owner = slot_owner(s.id)
        if owner is None or owner not in shown:
            continue
        if owner in ("experience", "projects") and s.id.partition(":")[2] not in kept:
            continue
        slots.append(_public_slot(s, privacy, kept))

    show_stats = "proof" in shown or ("graph" in shown and spec.graph.stats)
    public_content = PortfolioContent(
        person=content.person.model_copy(),
        facts=facts,
        slots=slots,
        stats=[s.model_copy() for s in content.stats] if show_stats else [],
        practice=content.practice.model_copy()
        if content.practice and "graph" in shown and spec.graph.stats
        else None,
        openTo=list(content.openTo) if "openTo" in shown else [],
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
