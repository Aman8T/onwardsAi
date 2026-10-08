"""The closed kit: compositions are valid pages, graph-variant rules follow the plan, minimum content gates blocks."""

from __future__ import annotations

import pytest

from tayyari.contract.portfolio import (
    BLOCK_VARIANTS,
    FAMILY_HUES,
    BlockSpec,
    CopySlot,
    GraphSpec,
    PortfolioPrivacy,
    PortfolioSpec,
    PortfolioStyle,
    spec_problems,
)
from tayyari.portfolio.kit import (
    COMPOSITION_NAMES,
    COMPOSITIONS,
    FACT_BLOCKS,
    FAMILIES,
    FAMILY_FOR_LEAN,
    GRAPH_AFFINITY,
    GRAPH_VARIANTS,
    LEANS,
    TYPE_FOR_FAMILY,
    GraphShape,
    block_has_content,
    default_variant,
    graph_variant_fit,
    slot_owner,
)
from tests.unit.portfolio._fixtures_b import blank_content, blank_graph, fact


def _spec(name: str) -> PortfolioSpec:
    return PortfolioSpec(
        composition=name,  # type: ignore[arg-type]
        style=PortfolioStyle(family="iris", field="dawn", type="studio", corners="soft", density="airy"),
        blocks=[
            BlockSpec(id=k, kind=k, variant=v[0], visible=True, public=True) for k, v in COMPOSITIONS[name]
        ],  # type: ignore[index]
        graph=GraphSpec(hiddenDomainIds=[], stats=True),
        privacy=PortfolioPrivacy(email=False, phone=False, grades=False),
        reasons=[],
    )


@pytest.mark.parametrize("name", COMPOSITION_NAMES)
def test_every_composition_is_a_valid_page(name):
    plan = COMPOSITIONS[name]
    kinds = [k for k, _ in plan]
    assert spec_problems(_spec(name)) == []
    assert kinds[0] == "hero"
    assert kinds.count("graph") == 1
    assert kinds.index("graph") <= 2
    assert len(set(kinds)) == len(kinds), "one block per kind: the block id is its kind"
    assert kinds[-1] == "contact"
    assert kinds.index("openTo") < kinds.index("contact")
    for kind, variants in plan:
        assert variants, kind
        assert len(set(variants)) == len(variants)
        assert set(variants) <= set(BLOCK_VARIANTS[kind]), (name, kind)


def test_the_plan_defaults_are_the_brief_compositions():
    expected = {
        "signature": {
            "hero": "split",
            "about": "lede",
            "experience": "timeline",
            "projects": "featureList",
            "contact": "signOff",
        },
        "narrative": {"hero": "statement", "about": "prose", "experience": "timeline", "projects": "list"},
        "builder": {"hero": "index", "projects": "grid", "experience": "ledger", "achievements": "numbers"},
        "foundations": {
            "hero": "centered",
            "about": "lede",
            "projects": "featureList",
            "education": "row",
            "contact": "links",
        },
        "ledger": {
            "hero": "statement",
            "experience": "ledger",
            "achievements": "numbers",
            "projects": "list",
            "contact": "links",
        },
        "pivot": {
            "hero": "statement",
            "about": "lede",
            "experience": "thenNow",
            "projects": "grid",
            "education": "row",
        },
    }
    for composition, wanted in expected.items():
        for kind, variant in wanted.items():
            assert default_variant(composition, kind) == variant, (composition, kind)  # type: ignore[arg-type]


def test_composition_orders_match_the_canvas():
    def kinds(name):
        return [k for k, _ in COMPOSITIONS[name]]

    assert kinds("foundations").index("education") < kinds("foundations").index("experience")
    assert kinds("pivot")[:3] == ["hero", "about", "graph"]
    assert kinds("builder").index("projects") < kinds("builder").index("experience")
    assert "about" not in kinds("ledger")
    assert kinds("ledger")[1] == "graph"


@pytest.mark.parametrize("name", COMPOSITION_NAMES)
def test_every_composition_ranks_all_seven_graph_variants_favourite_first(name):
    affinity = GRAPH_AFFINITY[name]
    assert set(affinity) == set(GRAPH_VARIANTS)
    graph_variants = dict(COMPOSITIONS[name])["graph"]
    assert set(graph_variants) == set(GRAPH_VARIANTS)
    assert graph_variants[0] == max(affinity, key=lambda v: affinity[v])


def test_style_tables_cover_the_catalog():
    assert set(FAMILIES) == set(FAMILY_HUES)
    assert set(FAMILY_FOR_LEAN) == set(LEANS)
    for prefs in FAMILY_FOR_LEAN.values():
        assert set(prefs) == set(FAMILIES)
    assert set(TYPE_FOR_FAMILY) == set(FAMILIES)
    assert all(0 < bonus <= 0.15 for bonuses in TYPE_FOR_FAMILY.values() for bonus in bonuses.values())


# ───────────────────────── graph variants ─────────────────────────


def fits(shape: GraphShape, type_pairing: str | None = None) -> dict[str, float]:
    return {v: graph_variant_fit(v, shape, type_pairing) for v in GRAPH_VARIANTS}


def test_a_sparse_graph_is_focus_only():
    for shape in (
        GraphShape(n_domains=3, n_skills=12),
        GraphShape(n_domains=6, n_skills=5),
        GraphShape(n_domains=6, n_skills=12, top=45, single_source=True),
    ):
        f = fits(shape)
        assert shape.sparse
        assert f["focus"] == 1.0
        assert [v for v in ("peaks", "halo", "spotlight", "atlas", "stellar") if f[v] > 0] == []
        assert f["ledger"] > 0


def test_one_source_alone_does_not_make_a_strong_graph_sparse():
    assert not GraphShape(n_domains=6, n_skills=12, top=80, single_source=True).sparse


def test_peaks_needs_the_top_domain_15_above_the_median():
    assert fits(GraphShape(top=80, median=65.0))["peaks"] == 1.0
    assert fits(GraphShape(top=80, median=65.1))["peaks"] == 0.0


def test_halo_is_for_balanced_graphs_with_a_mastery():
    f = fits(GraphShape(top=80, median=70.0, second=75))
    assert f["halo"] == 1.0 and f["peaks"] == 0.0
    assert fits(GraphShape(top=80, median=70.0, second=75, has_mastery=False))["halo"] == 0.0


def test_spotlight_needs_a_lead_of_20_over_the_second_and_30_over_the_median():
    assert fits(GraphShape(top=78, second=58, median=48.0))["spotlight"] == 1.0
    assert fits(GraphShape(top=78, second=59, median=48.0))["spotlight"] == 0.0
    assert fits(GraphShape(top=78, second=58, median=49.0))["spotlight"] == 0.0


def test_atlas_needs_a_rich_graph_and_ramps_up_to_24_skills():
    assert fits(GraphShape(n_skills=21, n_rich=7))["atlas"] == 0.0
    assert fits(GraphShape(n_skills=23, n_rich=3))["atlas"] == 0.0
    assert fits(GraphShape(n_skills=23, n_rich=7))["atlas"] == pytest.approx(23 / 24)
    assert fits(GraphShape(n_skills=40, n_rich=7))["atlas"] == 1.0


def test_stellar_is_for_six_to_eight_axes_and_likes_editorial_and_technical():
    assert fits(GraphShape(n_domains=5))["stellar"] == 0.0
    assert fits(GraphShape(n_domains=9))["stellar"] == 0.0
    assert fits(GraphShape(n_domains=6), "studio")["stellar"] == pytest.approx(0.7)
    assert fits(GraphShape(n_domains=6), "technical")["stellar"] == pytest.approx(0.95)


# ───────────────────────── minimum content ─────────────────────────


def test_hero_graph_and_contact_always_have_content():
    content, graph = blank_content(), blank_graph()
    assert all(block_has_content(k, content, graph) for k in ("hero", "graph", "contact"))


def test_proof_needs_two_stats():
    graph = blank_graph()
    assert not block_has_content("proof", blank_content(stats=1), graph)
    assert block_has_content("proof", blank_content(stats=2), graph)


def test_about_needs_a_lede_or_facts_to_write_one_from():
    graph = blank_graph()
    assert not block_has_content("about", blank_content(), graph)
    assert not block_has_content("about", blank_content([fact("link-1", "link", "Site")]), graph)
    assert block_has_content("about", blank_content([fact("exp-1", "experience", "Engineer")]), graph)
    assert block_has_content("about", blank_content([fact("quote-1", "quote", "I like it")]), graph)
    lede = CopySlot(id="aboutLede", text="Hello.", origin="resume", factIds=[])
    assert block_has_content("about", blank_content(slots=[lede]), graph)
    empty = CopySlot(id="aboutLede", text="  ", origin="resume", factIds=[])
    assert not block_has_content("about", blank_content(slots=[empty]), graph)


@pytest.mark.parametrize(
    ("kind", "fact_kind"),
    [
        ("experience", "experience"),
        ("projects", "project"),
        ("clips", "clip"),
        ("achievements", "achievement"),
        ("education", "education"),
    ],
)
def test_list_blocks_need_one_fact_of_their_kind(kind, fact_kind):
    graph = blank_graph()
    other = "project" if fact_kind != "project" else "clip"
    assert not block_has_content(kind, blank_content(), graph)
    assert not block_has_content(kind, blank_content([fact("x-1", other, "Something")]), graph)
    assert block_has_content(kind, blank_content([fact("x-1", fact_kind, "Something")]), graph)


def test_open_to_needs_a_title():
    graph = blank_graph()
    assert not block_has_content("openTo", blank_content(), graph)
    assert not block_has_content("openTo", blank_content(open_to_titles=["  "]), graph)
    assert block_has_content("openTo", blank_content(open_to_titles=["Data Analyst"]), graph)


def test_every_fact_kind_and_slot_has_an_owner_block():
    assert set(FACT_BLOCKS) == {
        "experience",
        "project",
        "education",
        "achievement",
        "clip",
        "link",
        "contact",
        "quote",
    }
    assert slot_owner("tagline") == "hero"
    assert slot_owner("aboutLede") == "about" == slot_owner("pressDeck")
    assert slot_owner("exp:exp-1") == "experience"
    assert slot_owner("proj:proj-3") == "projects"
    assert slot_owner("exp:") is None
    assert slot_owner("nonsense") is None
