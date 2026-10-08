"""Applies the owner's `PortfolioPatch` to a portfolio, with the kit's rules.

`apply_patch` returns a new `Portfolio` (the input is never changed) or raises `BadRequest` with a message that
says what to fix. What is checked:

    lookIndex   must name one of the stored looks; the look replaces composition, style, block order and variants
                but keeps the owner's visibility / public flags (where block ids match), privacy and hidden domains
    blocks      the same ids as now, none missing or doubled, no kind change, every variant from the catalog, the
                hero first, the Skill Graph within the first three
    graph       known domain ids only, and at least min(3, n) domains left on the page
    slots       known slot ids (``exp:`` / ``proj:`` need their fact), within the slot's character limit, plain text
    resetSlots  put the generated wording back (from ``generated``); a slot with none is removed so the fact
                wording shows through
    style, privacy, defaultView   typed by the contract already

``refreshGraph`` is not handled here: re-snapshotting needs the profile, which the service owns.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime

from tayyari.contract.portfolio import (
    BLOCK_VARIANTS,
    FACT_SLOT_PREFIXES,
    GRAPH_MAX_INDEX,
    BlockSpec,
    CopySlot,
    GraphSnapshot,
    GraphSpec,
    Portfolio,
    PortfolioPatch,
    PortfolioSpec,
    SlotEdit,
    slot_limit,
    spec_problems,
)
from tayyari.core.errors import BadRequest

SECTION_LABELS = {
    "hero": "Hero",
    "graph": "Skill Graph",
    "proof": "Proof",
    "about": "About",
    "experience": "Experience",
    "projects": "Selected work",
    "clips": "Clips",
    "achievements": "Recognition",
    "education": "Education",
    "openTo": "Open to",
    "contact": "Contact",
}
MIN_GRAPH_DOMAINS = 3
_HTML = re.compile(r"</?[A-Za-z!?]|&(?:[A-Za-z][A-Za-z0-9]{1,31}|#\d{1,7}|#[xX][0-9A-Fa-f]{1,6});")


def _label(kind: str) -> str:
    return SECTION_LABELS.get(kind, kind)


def _slot_label(slot_id: str) -> str:
    fixed = {
        "tagline": "The tagline",
        "aboutLede": "The first line of About",
        "aboutBody": "The rest of About",
        "pressHeadline": "The newspaper headline",
        "pressDeck": "The newspaper deck",
    }
    if slot_id in fixed:
        return fixed[slot_id]
    prefix = slot_id.partition(":")[0]
    return {"exp": "This experience entry", "proj": "This project blurb"}.get(prefix, f"'{slot_id}'")


# ───────────────────────── looks ─────────────────────────


def _switch_look(p: Portfolio, index: int) -> PortfolioSpec:
    if index >= len(p.looks):
        raise BadRequest(f"There is no look number {index + 1}; this page has {len(p.looks)}.")
    look = p.looks[index].model_copy(deep=True)
    owner = {b.id: b for b in p.spec.blocks}
    for block in look.blocks:
        if (old := owner.get(block.id)) is not None:
            block.visible, block.public = old.visible, old.public
    look.privacy = p.spec.privacy.model_copy()
    look.graph = p.spec.graph.model_copy(deep=True)
    return look


# ───────────────────────── blocks ─────────────────────────


def _check_blocks(current: Sequence[BlockSpec], blocks: Sequence[BlockSpec]) -> list[BlockSpec]:
    kinds = {b.id: b.kind for b in current}
    seen: set[str] = set()
    for b in blocks:
        if b.id in seen:
            raise BadRequest(f"The section '{b.id}' appears twice.")
        seen.add(b.id)
        if b.id not in kinds:
            raise BadRequest(f"There is no section called '{b.id}'.")
        if b.kind != kinds[b.id]:
            raise BadRequest(
                f"The '{b.id}' section is a {_label(kinds[b.id])} section and cannot become a {_label(b.kind)} section."
            )
        allowed = BLOCK_VARIANTS.get(b.kind, ())
        if b.variant not in allowed:
            raise BadRequest(
                f"'{b.variant}' is not a style of the {_label(b.kind)} section (choose one of: {', '.join(allowed)})."
            )
    missing = [i for i in kinds if i not in seen]
    if missing:
        names = ", ".join(_label(kinds[i]) for i in missing)
        raise BadRequest(f"Every section must stay in the list (missing: {names}).")
    if blocks[0].kind != "hero":
        raise BadRequest("The hero must stay the first section.")
    graph_at = next((i for i, b in enumerate(blocks) if b.kind == "graph"), 0)
    if graph_at > GRAPH_MAX_INDEX:
        raise BadRequest("The Skill Graph must stay within the first three sections.")
    return [b.model_copy() for b in blocks]


# ───────────────────────── graph ─────────────────────────


def _check_graph(snapshot: GraphSnapshot, graph: GraphSpec) -> GraphSpec:
    known = {d.id for d in snapshot.domains}
    hidden = list(dict.fromkeys(graph.hiddenDomainIds))
    for domain_id in hidden:
        if domain_id not in known:
            raise BadRequest(f"There is no graph area called '{domain_id}'.")
    keep = min(MIN_GRAPH_DOMAINS, len(known))
    if len(known) - len(hidden) < keep:
        spare = len(known) - keep
        hint = f"you can hide {spare}" if spare else "none can be hidden"
        raise BadRequest(f"At least {keep} areas must stay on the graph ({hint}).")
    return GraphSpec(hiddenDomainIds=hidden, stats=graph.stats)


# ───────────────────────── slots ─────────────────────────


def _clean_text(slot_id: str, raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    label = _slot_label(slot_id)
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text):
        raise BadRequest(f"{label} contains characters that cannot be shown.")
    if _HTML.search(text):
        raise BadRequest(f"{label} must be plain text: remove the HTML or markup.")
    return text


def _check_slot_edit(p: Portfolio, edit: SlotEdit) -> str:
    limit = slot_limit(edit.id)
    if limit is None:
        raise BadRequest(f"'{edit.id}' is not a piece of text you can edit.")
    prefix, _, fact_id = edit.id.partition(":")
    if fact_id:
        kind = FACT_SLOT_PREFIXES[prefix]
        if not any(f.id == fact_id and f.kind == kind for f in p.content.facts):
            raise BadRequest(f"There is no {kind} entry '{fact_id}' to write about.")
    text = _clean_text(edit.id, edit.text)
    if len(text) > limit:
        raise BadRequest(
            f"{_slot_label(edit.id)} is limited to {limit} characters (this one is {len(text)})."
        )
    return text


def _replace_slot(slots: list[CopySlot], new: CopySlot) -> None:
    for i, s in enumerate(slots):
        if s.id == new.id:
            slots[i] = new
            return
    slots.append(new)


def _apply_slots(p: Portfolio, patch: PortfolioPatch, generated: Sequence[CopySlot]) -> list[CopySlot]:
    slots = [s.model_copy(deep=True) for s in p.content.slots]
    edits = patch.slots or []
    resets = patch.resetSlots or []
    edit_ids = [e.id for e in edits]
    if len(edit_ids) != len(set(edit_ids)):
        raise BadRequest("The same piece of text appears twice in one request.")
    clash = sorted(set(edit_ids) & set(resets))
    if clash:
        raise BadRequest(f"'{clash[0]}' cannot be edited and reset in the same request.")
    for edit in edits:
        text = _check_slot_edit(p, edit)
        _replace_slot(slots, CopySlot(id=edit.id, text=text, origin="user", factIds=[]))
    originals = {s.id: s for s in generated}
    for slot_id in resets:
        if slot_limit(slot_id) is None:
            raise BadRequest(f"'{slot_id}' is not a piece of text you can reset.")
        if (original := originals.get(slot_id)) is not None:
            _replace_slot(slots, original.model_copy(deep=True))
        else:
            slots = [s for s in slots if s.id != slot_id]
    return slots


# ───────────────────────── apply ─────────────────────────


def _stamp(now: datetime) -> str:
    moment = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_patch(
    p: Portfolio, patch: PortfolioPatch, now: datetime, generated: Sequence[CopySlot]
) -> Portfolio:
    """The portfolio with ``patch`` applied (see the module docstring); raises `BadRequest` when it does not fit."""
    spec = p.spec.model_copy(deep=True)
    look_index = p.lookIndex
    if patch.lookIndex is not None:
        spec = _switch_look(p, patch.lookIndex)
        look_index = patch.lookIndex
    if patch.style is not None:
        spec.style = patch.style.model_copy()
    if patch.blocks is not None:
        spec.blocks = _check_blocks(spec.blocks, patch.blocks)
    if patch.graph is not None:
        spec.graph = _check_graph(p.graph, patch.graph)
    if patch.privacy is not None:
        spec.privacy = patch.privacy.model_copy()

    content = p.content.model_copy(deep=True)
    if patch.slots or patch.resetSlots:
        content.slots = _apply_slots(p, patch, generated)

    problems = spec_problems(spec)
    if problems:
        raise BadRequest(f"That layout is not allowed: {problems[0]}.")

    out = p.model_copy(deep=True)
    out.spec = spec
    out.lookIndex = look_index
    out.content = content
    if patch.defaultView is not None:
        out.publish.defaultView = patch.defaultView
    out.updatedAt = _stamp(now)
    return out


__all__ = ["SECTION_LABELS", "apply_patch"]
