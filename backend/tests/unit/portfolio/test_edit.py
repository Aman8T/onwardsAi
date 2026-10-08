"""apply_patch: every owner edit is checked against the kit and answered with a clear BadRequest when it does not fit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tayyari.contract.portfolio import (
    SLOT_EDIT_MAX_CHARS,
    BlockSpec,
    CopySlot,
    GraphSpec,
    Portfolio,
    PortfolioPatch,
    PortfolioPrivacy,
    PortfolioStyle,
    SlotEdit,
    spec_problems,
)
from tayyari.core.errors import BadRequest
from tayyari.portfolio.edit import apply_patch
from tests.unit.portfolio._fixtures_b import make_portfolio

NOW = datetime(2026, 10, 9, 8, 30, 15, tzinfo=UTC)

GENERATED = [
    CopySlot(id="tagline", text="Springs, not durations.", origin="model", factIds=["quote-1"]),
    CopySlot(id="aboutLede", text="Frontend engineer at Kitebird.", origin="model", factIds=["exp-1"]),
    CopySlot(id="exp:exp-1", text="Rebuilt checkout.\nOwns Kite UI.", origin="model", factIds=["exp-1"]),
]


def riya(**kw) -> Portfolio:
    return make_portfolio("riya", slots=[s.model_copy(deep=True) for s in GENERATED], **kw)


def patch(**kw) -> PortfolioPatch:
    return PortfolioPatch(**kw)


def apply(p: Portfolio, **kw) -> Portfolio:
    return apply_patch(p, patch(**kw), NOW, GENERATED)


def blocks_of(p: Portfolio) -> list[BlockSpec]:
    return [b.model_copy() for b in p.spec.blocks]


def reject(p: Portfolio, **kw) -> str:
    with pytest.raises(BadRequest) as info:
        apply(p, **kw)
    assert info.value.status == 400
    return info.value.user_message


def slot_text(p: Portfolio, slot_id: str) -> str | None:
    return next((s.text for s in p.content.slots if s.id == slot_id), None)


# ───────────────────────── plain fields ─────────────────────────


def test_an_empty_patch_changes_nothing_but_the_timestamp():
    p = riya()
    before = p.model_dump()
    out = apply(p)
    assert p.model_dump() == before, "the input is never changed"
    assert out.updatedAt == "2026-10-09T08:30:15Z"
    assert out.model_dump() | {"updatedAt": p.updatedAt} == before
    assert Portfolio.model_validate(out.model_dump()) == out


def test_timestamps_are_utc_whatever_the_clock():
    p = riya()
    ist = timezone(timedelta(hours=5, minutes=30))
    assert (
        apply_patch(p, patch(), datetime(2026, 10, 9, 14, 0, 15, tzinfo=ist), []).updatedAt
        == "2026-10-09T08:30:15Z"
    )
    assert (
        apply_patch(p, patch(), datetime(2026, 10, 9, 8, 30, 15), []).updatedAt == "2026-10-09T08:30:15Z"
    )  # naive = UTC


def test_style_privacy_and_default_view_are_applied():
    p = riya()
    style = PortfolioStyle(family="rose", field="corner", type="warm", corners="round", density="compact")
    privacy = PortfolioPrivacy(email=True, phone=False, grades=True)
    out = apply(p, style=style, privacy=privacy, defaultView="press")
    assert out.spec.style == style
    assert out.spec.privacy == privacy
    assert out.publish.defaultView == "press"
    assert p.spec.style != style and p.publish.defaultView == "standard"


def test_refresh_graph_is_left_to_the_service():
    p = riya()
    out = apply(p, refreshGraph=True)
    assert out.graph == p.graph and out.graphOutdated == p.graphOutdated


# ───────────────────────── blocks ─────────────────────────


def test_blocks_can_be_reordered_hidden_and_given_another_variant():
    p = riya()
    blocks = blocks_of(p)
    by_kind = {b.kind: b for b in blocks}
    by_kind["projects"].variant = "grid"
    by_kind["education"].visible = False
    by_kind["achievements"].public = False
    order = [
        "hero",
        "graph",
        "proof",
        "about",
        "projects",
        "experience",
        "clips",
        "achievements",
        "education",
        "openTo",
        "contact",
    ]
    out = apply(p, blocks=[by_kind[k] for k in order])
    assert [b.kind for b in out.spec.blocks] == order
    assert next(b for b in out.spec.blocks if b.kind == "projects").variant == "grid"
    assert not next(b for b in out.spec.blocks if b.kind == "education").visible
    assert not next(b for b in out.spec.blocks if b.kind == "achievements").public
    assert spec_problems(out.spec) == []


def test_the_graph_may_move_within_the_first_three_and_every_graph_variant_is_allowed():
    p = riya()
    blocks = blocks_of(p)
    reordered = [blocks[0], blocks[2], blocks[1], *blocks[3:]]  # hero, proof, graph
    out = apply(p, blocks=reordered)
    assert [b.kind for b in out.spec.blocks][:3] == ["hero", "proof", "graph"]
    for variant in ("peaks", "halo", "ledger", "stellar", "atlas", "spotlight", "focus"):
        graph = next(b for b in blocks if b.kind == "graph").model_copy(update={"variant": variant})
        assert (
            next(
                b for b in apply(p, blocks=[blocks[0], graph, *blocks[2:]]).spec.blocks if b.kind == "graph"
            ).variant
            == variant
        )


def test_an_unknown_section_is_refused_by_name():
    p = riya()
    blocks = blocks_of(p)
    blocks[3] = BlockSpec(id="portfolio-extras", kind="about", variant="lede", visible=True, public=True)
    msg = reject(p, blocks=blocks)
    assert "portfolio-extras" in msg and msg.startswith("There is no section")


def test_a_missing_section_is_named():
    p = riya()
    blocks = [b for b in blocks_of(p) if b.kind not in ("education", "clips")]
    msg = reject(p, blocks=blocks)
    assert "missing" in msg and "Education" in msg and "Clips" in msg


def test_a_doubled_section_is_refused():
    p = riya()
    blocks = blocks_of(p)
    msg = reject(p, blocks=[*blocks, blocks[3]])
    assert "appears twice" in msg and "about" in msg


def test_a_section_cannot_change_kind():
    p = riya()
    blocks = blocks_of(p)
    index = next(i for i, b in enumerate(blocks) if b.id == "projects")
    blocks[index] = blocks[index].model_copy(update={"kind": "clips", "variant": "list"})
    msg = reject(p, blocks=blocks)
    assert "'projects'" in msg and "Selected work" in msg and "Clips" in msg and "cannot become" in msg


def test_a_variant_must_come_from_the_catalog():
    p = riya()
    blocks = blocks_of(p)
    index = next(i for i, b in enumerate(blocks) if b.id == "about")
    blocks[index] = blocks[index].model_copy(update={"variant": "poster"})
    msg = reject(p, blocks=blocks)
    assert "'poster'" in msg and "About" in msg and "lede" in msg
    blocks[index] = blocks[index].model_copy(
        update={"variant": "grid"}
    )  # a projects variant on an about block
    assert "not a style of the About section" in reject(p, blocks=blocks)


def test_the_hero_stays_first():
    p = riya()
    blocks = blocks_of(p)
    msg = reject(p, blocks=[blocks[1], blocks[0], *blocks[2:]])
    assert "hero must stay the first" in msg


def test_the_graph_cannot_move_past_the_third_place():
    p = riya()
    blocks = blocks_of(p)
    graph = blocks.pop(1)
    blocks.insert(3, graph)  # hero, proof, about, graph
    assert "within the first three" in reject(p, blocks=blocks)


# ───────────────────────── hidden domains ─────────────────────────


def test_domains_can_be_hidden_down_to_three():
    p = riya()
    ids = [d.id for d in p.graph.domains]
    out = apply(p, graph=GraphSpec(hiddenDomainIds=ids[3:], stats=False))
    assert out.spec.graph.hiddenDomainIds == ids[3:] and out.spec.graph.stats is False
    out = apply(p, graph=GraphSpec(hiddenDomainIds=[ids[0], ids[0]], stats=True))
    assert out.spec.graph.hiddenDomainIds == [ids[0]], "a repeated id counts once"
    msg = reject(p, graph=GraphSpec(hiddenDomainIds=ids[2:], stats=True))
    assert "At least 3 areas" in msg and "you can hide 3" in msg


def test_a_graph_with_few_domains_keeps_them_all():
    p = make_portfolio("stress-two-domains")
    ids = [d.id for d in p.graph.domains]
    assert len(ids) == 2
    msg = apply_patch_error(p, GraphSpec(hiddenDomainIds=ids[:1], stats=True))
    assert "At least 2 areas" in msg and "none can be hidden" in msg
    three = make_portfolio("aarav")
    assert "At least 3 areas" in apply_patch_error(
        three, GraphSpec(hiddenDomainIds=[three.graph.domains[0].id], stats=True)
    )


def apply_patch_error(p: Portfolio, graph: GraphSpec) -> str:
    return reject(p, graph=graph)


def test_an_unknown_domain_is_refused():
    msg = reject(riya(), graph=GraphSpec(hiddenDomainIds=["quantum-gardening"], stats=True))
    assert "quantum-gardening" in msg


# ───────────────────────── text slots ─────────────────────────


def test_an_edit_is_stored_as_the_owners_own_words():
    p = riya()
    out = apply(p, slots=[SlotEdit(id="tagline", text="  I make things move.\r\n")])
    s = next(s for s in out.content.slots if s.id == "tagline")
    assert (s.text, s.origin, s.factIds) == ("I make things move.", "user", [])
    assert slot_text(p, "tagline") == "Springs, not durations."


def test_a_slot_that_did_not_exist_yet_is_added_and_others_are_kept():
    out = apply(
        riya(),
        slots=[SlotEdit(id="aboutBody", text="More about me."), SlotEdit(id="exp:exp-1", text="One line.")],
    )
    assert [s.id for s in out.content.slots] == ["tagline", "aboutLede", "exp:exp-1", "aboutBody"]
    assert slot_text(out, "exp:exp-1") == "One line."
    assert slot_text(out, "aboutLede") == "Frontend engineer at Kitebird."


def test_every_fixed_slot_and_per_fact_slot_can_be_edited():
    p = riya()
    edits = [
        SlotEdit(id=i, text="Plain words.")
        for i in ("tagline", "aboutLede", "aboutBody", "pressHeadline", "pressDeck")
    ]
    edits += [SlotEdit(id="exp:exp-2", text="Plain words."), SlotEdit(id="proj:proj-1", text="Plain words.")]
    out = apply(p, slots=edits)
    assert all(slot_text(out, e.id) == "Plain words." for e in edits)


def test_unknown_slots_are_refused():
    p = riya()
    assert "'bio'" in reject(p, slots=[SlotEdit(id="bio", text="x")])
    assert "'exp:'" in reject(p, slots=[SlotEdit(id="exp:", text="x")])
    assert "no experience entry 'exp-99'" in reject(p, slots=[SlotEdit(id="exp:exp-99", text="x")])
    assert "no project entry 'exp-1'" in reject(p, slots=[SlotEdit(id="proj:exp-1", text="x")])
    assert "no experience entry 'proj-1'" in reject(p, slots=[SlotEdit(id="exp:proj-1", text="x")])


@pytest.mark.parametrize(
    ("slot_id", "limit"),
    [
        ("tagline", 90),
        ("aboutLede", 160),
        ("aboutBody", 420),
        ("pressHeadline", 80),
        ("pressDeck", 180),
        ("exp:exp-1", 300),
        ("proj:proj-1", 200),
    ],
)
def test_a_slot_may_fill_its_limit_but_not_pass_it(slot_id, limit):
    p = riya()
    assert slot_text(apply(p, slots=[SlotEdit(id=slot_id, text="a" * limit)]), slot_id) == "a" * limit
    if limit == SLOT_EDIT_MAX_CHARS:  # the request model itself stops anything longer than the biggest slot
        with pytest.raises(ValidationError):
            SlotEdit(id=slot_id, text="a" * (limit + 1))
        return
    msg = reject(p, slots=[SlotEdit(id=slot_id, text="a" * (limit + 1))])
    assert f"limited to {limit} characters" in msg and f"{limit + 1}" in msg


def test_the_limit_counts_the_text_the_page_would_show():
    p = riya()
    out = apply(p, slots=[SlotEdit(id="tagline", text="  " + "a" * 90 + "  ")])
    assert len(slot_text(out, "tagline") or "") == 90


@pytest.mark.parametrize(
    "text",
    [
        "<b>bold</b>",
        "Hello <script>alert(1)</script>",
        "<!-- note -->",
        "text </p>",
        "<img src=x onerror=alert(1)>",
        "Fish &amp; chips",
        "&#60;b&#62;",
        "&lt;i&gt;",
    ],
)
def test_html_is_refused(text):
    msg = reject(riya(), slots=[SlotEdit(id="aboutLede", text=text)])
    assert "plain text" in msg and "first line of About" in msg


@pytest.mark.parametrize(
    "text",
    [
        "I <3 data & coffee",
        "x < y and y > z",
        "R&D at 5% < 10%",
        "Line one\nLine two",
        "Tab\tseparated",
        "Rs. 1,200 crore & more",
        "a <  b",
    ],
)
def test_plain_text_with_symbols_is_fine(text):
    assert slot_text(apply(riya(), slots=[SlotEdit(id="aboutLede", text=text)]), "aboutLede") == text


def test_control_characters_are_refused():
    assert "cannot be shown" in reject(riya(), slots=[SlotEdit(id="tagline", text="a\x00b")])
    assert "cannot be shown" in reject(riya(), slots=[SlotEdit(id="tagline", text="a\x1bb")])


def test_the_same_slot_twice_in_one_request_is_refused():
    msg = reject(riya(), slots=[SlotEdit(id="tagline", text="one"), SlotEdit(id="tagline", text="two")])
    assert "twice" in msg


def test_clearing_a_slot_keeps_it_as_empty_user_text():
    out = apply(riya(), slots=[SlotEdit(id="tagline", text="   ")])
    s = next(s for s in out.content.slots if s.id == "tagline")
    assert (s.text, s.origin) == ("", "user")


# ───────────────────────── reset ─────────────────────────


def test_reset_puts_the_generated_wording_back():
    p = apply(
        riya(), slots=[SlotEdit(id="tagline", text="Mine."), SlotEdit(id="aboutLede", text="Also mine.")]
    )
    out = apply_patch(p, patch(resetSlots=["tagline"]), NOW, GENERATED)
    s = next(s for s in out.content.slots if s.id == "tagline")
    assert (s.text, s.origin, s.factIds) == ("Springs, not durations.", "model", ["quote-1"])
    assert slot_text(out, "aboutLede") == "Also mine."
    assert [s.id for s in out.content.slots] == [s.id for s in p.content.slots], "the slot keeps its place"


def test_reset_of_a_slot_with_no_generated_wording_removes_it():
    p = apply(riya(), slots=[SlotEdit(id="aboutBody", text="Mine.")])
    assert slot_text(p, "aboutBody") == "Mine."
    out = apply_patch(p, patch(resetSlots=["aboutBody"]), NOW, GENERATED)
    assert slot_text(out, "aboutBody") is None


def test_reset_rejects_unknown_ids_and_clashes_with_an_edit():
    p = riya()
    assert "'bio'" in reject(p, resetSlots=["bio"])
    msg = reject(p, slots=[SlotEdit(id="tagline", text="x")], resetSlots=["tagline"])
    assert "edited and reset" in msg


def test_generated_slots_are_copied_not_shared():
    p = apply(riya(), slots=[SlotEdit(id="tagline", text="Mine.")])
    out = apply_patch(p, patch(resetSlots=["tagline"]), NOW, GENERATED)
    next(s for s in out.content.slots if s.id == "tagline").text = "changed afterwards"
    assert GENERATED[0].text == "Springs, not durations."


# ───────────────────────── looks ─────────────────────────


def test_switching_the_look_keeps_the_owners_flags_privacy_and_hidden_domains():
    p = riya()
    owner = p.model_copy(deep=True)
    for b in owner.spec.blocks:
        if b.kind == "education":
            b.visible = False
        if b.kind == "achievements":
            b.public = False
        if b.kind == "openTo":
            b.visible, b.public = True, True
    owner.spec.privacy = PortfolioPrivacy(email=True, phone=False, grades=True)
    owner.spec.graph = GraphSpec(hiddenDomainIds=["ai-llm"], stats=False)

    out = apply(owner, lookIndex=1)
    look = owner.looks[1]
    assert out.lookIndex == 1
    assert (out.spec.composition, out.spec.style) == (look.composition, look.style)
    assert [b.id for b in out.spec.blocks] == [b.id for b in look.blocks]
    assert [b.variant for b in out.spec.blocks] == [b.variant for b in look.blocks]
    state = {b.id: (b.visible, b.public) for b in out.spec.blocks}
    assert state["education"][0] is False
    assert state["achievements"][1] is False
    assert state["openTo"] == (True, True)
    assert out.spec.privacy == PortfolioPrivacy(email=True, phone=False, grades=True)
    assert out.spec.graph == GraphSpec(hiddenDomainIds=["ai-llm"], stats=False)
    assert out.spec.reasons == look.reasons
    assert spec_problems(out.spec) == []
    assert owner.lookIndex == 0 and owner.spec.composition != look.composition


def test_a_block_the_old_look_did_not_have_keeps_the_new_looks_flags():
    p = riya()
    trimmed = p.model_copy(deep=True)
    trimmed.spec.blocks = [b for b in trimmed.spec.blocks if b.kind != "clips"]
    out = apply(trimmed, lookIndex=1)
    look_clips = next(b for b in p.looks[1].blocks if b.kind == "clips")
    assert next(b for b in out.spec.blocks if b.kind == "clips") == look_clips


def test_a_look_that_does_not_exist_is_refused():
    p = riya()
    p.looks = p.looks[:1]
    assert "no look number 2" in reject(p, lookIndex=1)


def test_later_fields_in_one_patch_win_over_the_look():
    p = riya()
    style = PortfolioStyle(family="rose", field="corner", type="warm", corners="round", density="compact")
    out = apply(p, lookIndex=2, style=style, slots=[SlotEdit(id="tagline", text="Mine.")])
    assert out.spec.composition == p.looks[2].composition
    assert out.spec.style == style
    assert slot_text(out, "tagline") == "Mine."


def test_a_failed_patch_leaves_the_input_alone():
    p = riya()
    before = p.model_dump()
    reject(p, slots=[SlotEdit(id="tagline", text="fine"), SlotEdit(id="aboutLede", text="<b>no</b>")])
    assert p.model_dump() == before
