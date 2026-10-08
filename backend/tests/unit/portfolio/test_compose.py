"""The composer: personas get the canvas looks from rules over features, and no feature set can break a spec."""

from __future__ import annotations

import hashlib
import random
from dataclasses import replace

import pytest

from tayyari.contract.portfolio import (
    BLOCK_VARIANTS,
    FAMILY_HUES,
    GraphSpec,
    PortfolioPrivacy,
    PortfolioSpec,
    spec_problems,
)
from tayyari.portfolio.compose import LOOK_COUNT, carry_owner_choices, compose, default_spec, person_seed
from tayyari.portfolio.features import STAGES, Features
from tayyari.portfolio.kit import (
    COMPOSITION_NAMES,
    COMPOSITIONS,
    FAMILIES,
    GRAPH_VARIANTS,
    LEANS,
    block_has_content,
)
from tests.unit.portfolio._fixtures_b import (
    ALL_FIXTURES,
    PERSONAS,
    blank_content,
    blank_graph,
    build_content,
    build_graph,
    fact,
    features_for,
    load_profile,
    looks_for,
    make_portfolio,
)

# composition, graph variant, family, type, field, corners, density: the five canvas boards
CANVAS = {
    "riya": ("signature", "peaks", "iris", "studio", "dawn", "soft", "airy"),
    "aarav": ("foundations", "focus", "sage", "warm", "horizon", "round", "airy"),
    "meera": ("pivot", "peaks", "clay", "editorial", "corner", "square", "airy"),
    "vikram": ("ledger", "atlas", "lagoon", "technical", "horizon", "square", "compact"),
    "sana": ("narrative", "spotlight", "rose", "studio", "corner", "soft", "airy"),
}


def graph_variant(spec: PortfolioSpec) -> str:
    return next(b.variant for b in spec.blocks if b.kind == "graph")


def signature_of(spec: PortfolioSpec) -> tuple[str, ...]:
    s = spec.style
    return (spec.composition, graph_variant(spec), s.family, s.type, s.field, s.corners, s.density)


def assert_valid(spec: PortfolioSpec, content, graph) -> None:
    assert spec_problems(spec) == []
    kinds = [b.kind for b in spec.blocks]
    assert kinds[0] == "hero"
    assert kinds.count("graph") == 1
    assert "graph" in kinds[:3]
    assert [b.id for b in spec.blocks] == kinds
    assert kinds.index("openTo") < kinds.index("contact")
    for b in spec.blocks:
        assert b.variant in BLOCK_VARIANTS[b.kind]
        if b.kind == "openTo":
            assert (b.visible, b.public) == (False, False)
        else:
            has = block_has_content(b.kind, content, graph)
            assert (b.visible, b.public) == (has, has), b.kind
    assert spec.privacy.model_dump() == {"email": False, "phone": False, "grades": False}
    assert spec.graph.hiddenDomainIds == []
    assert spec.graph.stats == (len(content.stats) >= 2)
    assert [r.dimension for r in spec.reasons] == [
        "composition",
        "graph",
        "family",
        "type",
        "field",
        "corners",
        "density",
    ]
    assert all(r.because.strip() and r.choice for r in spec.reasons)
    assert spec.style.family in FAMILY_HUES


# ───────────────────────── the canvas personas ─────────────────────────


@pytest.mark.parametrize("name", PERSONAS)
def test_first_look_reproduces_the_canvas_board(name):
    first = looks_for(name)[0]
    assert signature_of(first) == CANVAS[name]


@pytest.mark.parametrize("name", PERSONAS)
def test_the_first_look_does_not_depend_on_the_seed(name):
    wanted = signature_of(looks_for(name, seed=0)[0])
    for seed in range(1, 60):
        assert signature_of(looks_for(name, seed=seed)[0]) == wanted, seed


def test_the_five_first_looks_are_pairwise_distinct():
    firsts = [signature_of(looks_for(n)[0]) for n in PERSONAS]
    assert len(set(firsts)) == 5
    assert len({(s[0], s[2]) for s in firsts}) == 5


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_three_distinct_valid_looks_for_every_fixture(name):
    profile = load_profile(name)
    content, graph = build_content(name), build_graph(name)
    looks = compose(features_for(name), content, graph, person_seed(profile.id))
    assert len(looks) == LOOK_COUNT == 3
    for spec in looks:
        assert_valid(spec, content, graph)
    assert len({(s.composition, s.style.family) for s in looks}) == 3
    assert len({s.composition for s in looks}) == 3
    assert len({s.style.family for s in looks}) == 3


def test_blocks_without_content_are_hidden_and_the_rest_shown():
    aarav = looks_for("aarav")[0]
    state = {b.kind: b.visible for b in aarav.blocks}
    assert state["experience"] is False and state["clips"] is False and state["openTo"] is False
    assert state["education"] and state["projects"] and state["achievements"] and state["contact"]
    sana = {b.kind: b.visible for b in looks_for("sana")[0].blocks}
    assert sana["clips"] and sana["experience"] and not sana["projects"] and not sana["openTo"]
    riya = {b.kind: b.visible for b in looks_for("riya")[0].blocks}
    assert riya["proof"] and not riya["clips"]


def test_the_block_variants_of_the_canvas_first_looks():
    def variants(name):
        return {b.kind: b.variant for b in looks_for(name)[0].blocks}

    riya = variants("riya")
    assert (riya["hero"], riya["about"], riya["experience"], riya["projects"], riya["contact"]) == (
        "split",
        "lede",
        "timeline",
        "featureList",
        "signOff",
    )
    aarav = variants("aarav")
    assert (aarav["hero"], aarav["graph"], aarav["education"], aarav["contact"]) == (
        "centered",
        "focus",
        "row",
        "links",
    )
    meera = variants("meera")
    assert (meera["experience"], meera["projects"], meera["education"]) == ("thenNow", "grid", "row")
    vikram = variants("vikram")
    assert (vikram["experience"], vikram["projects"]) == ("ledger", "list")
    assert vikram["achievements"] == "numbers"  # the facts carry numbers
    assert variants("riya")["achievements"] == "list"
    sana = variants("sana")
    assert (sana["hero"], sana["about"]) == ("statement", "prose")


def test_the_canvas_block_orders():
    def order(name):
        return [b.kind for b in looks_for(name)[0].blocks]

    assert order("riya")[:4] == ["hero", "graph", "proof", "about"]
    assert order("meera")[:3] == ["hero", "about", "graph"]
    foundations = order("aarav")
    assert foundations.index("education") < foundations.index("experience")
    assert order("vikram")[:2] == ["hero", "graph"]


# ───────────────────────── determinism and seeds ─────────────────────────


@pytest.mark.parametrize("name", ["riya", "stress-unicode", "stress-two-domains"])
def test_same_seed_same_output(name):
    a = looks_for(name, seed=1234)
    b = looks_for(name, seed=1234)
    assert [s.model_dump() for s in a] == [s.model_dump() for s in b]


def test_the_seed_only_breaks_ties():
    # lean "other" has no clear hue: the seed decides between equal fits, and always the same way for one seed
    base = Features(lean="other")
    content, graph = blank_content(), blank_graph()
    families = {compose(base, content, graph, seed)[0].style.family for seed in range(80)}
    assert len(families) > 1
    # a clear lean leaves nothing to the seed
    design = replace(base, lean="design")
    assert {compose(design, content, graph, seed)[0].style.family for seed in range(80)} == {"iris"}


def test_person_seed_is_stable_and_per_person_and_round():
    expected = int.from_bytes(hashlib.sha256(b"profile-1:0").digest()[:8], "big")
    assert person_seed("profile-1") == expected == person_seed("profile-1", 0)
    assert person_seed("profile-1", 1) != person_seed("profile-1", 0)
    assert person_seed("profile-2") != person_seed("profile-1")
    assert 0 <= person_seed("x") < 2**64


# ───────────────────────── the rules ─────────────────────────


def first(features: Features, **kw) -> PortfolioSpec:
    return compose(features, kw.get("content") or blank_content(), kw.get("graph") or blank_graph(), 7)[0]


def test_each_career_shape_picks_its_composition():
    typical = Features()
    assert first(typical).composition == "signature"
    assert first(replace(typical, stage="student")).composition == "foundations"
    assert first(replace(typical, stage="switcher")).composition == "pivot"
    assert first(replace(typical, stage="senior", n_experience=4)).composition == "ledger"
    assert first(replace(typical, project_led=True, n_projects=6, n_experience=1)).composition == "builder"
    assert first(replace(typical, writing_led=True, n_clips=4)).composition == "narrative"


def test_a_sparse_graph_always_gets_focus():
    for stage in STAGES:
        spec = first(Features(stage=stage, n_domains=3, n_skills=5))
        assert graph_variant(spec) == "focus"


def test_graph_variant_follows_the_shape():
    balanced = Features(top=80, second=75, median=72.0)
    assert graph_variant(first(balanced)) == "halo"
    assert graph_variant(first(replace(balanced, has_mastery=False, n_domains=5))) == "ledger"
    assert graph_variant(first(replace(balanced, has_mastery=False, n_domains=7))) == "stellar"
    assert graph_variant(first(Features(top=85, second=80, median=56.0))) == "peaks"
    assert (
        graph_variant(first(Features(top=90, second=50, median=40.0, writing_led=True, n_clips=3)))
        == "spotlight"
    )
    rich = Features(stage="senior", n_skills=30, n_rich=6, n_domains=7, top=90, second=88, median=84.0)
    assert graph_variant(first(rich)) == "atlas"


def test_variants_adapt_to_the_content():
    facts = [fact(f"proj-{i}", "project", f"P{i}") for i in range(9)]
    facts += [fact(f"exp-{i}", "experience", f"E{i}", start="2020") for i in range(7)]
    facts += [fact("ach-1", "achievement", "Won a prize"), fact("ach-2", "achievement", "Spoke at a meetup")]
    spec = compose(Features(stage="senior", n_experience=7), blank_content(facts), blank_graph(), 3)[0]
    variants = {b.kind: b.variant for b in spec.blocks}
    assert variants["projects"] == "list"  # 9 projects
    assert variants["achievements"] == "list"  # "numbers" needs numeric facts
    numeric = [fact("ach-1", "achievement", "Cut costs 40%"), fact("ach-2", "achievement", "Won 3 awards")]
    spec = compose(Features(stage="senior", n_experience=1), blank_content(numeric), blank_graph(), 3)[0]
    assert {b.kind: b.variant for b in spec.blocks}["achievements"] == "numbers"
    one_job = compose(
        Features(stage="switcher", n_experience=1),
        blank_content([fact("exp-1", "experience", "E")]),
        blank_graph(),
        3,
    )[0]
    assert {b.kind: b.variant for b in one_job.blocks}["experience"] == "timeline"  # then/now needs two


def test_looks_two_and_three_differ_in_more_than_the_composition():
    for name in PERSONAS:
        looks = looks_for(name)
        assert len({s.style.family for s in looks}) == 3
        assert len({(s.style.type, s.style.field) for s in looks}) >= 2, name


def test_n_controls_the_number_of_looks_and_they_stay_distinct():
    f, c, g = features_for("riya"), build_content("riya"), build_graph("riya")
    assert compose(f, c, g, 1, n=0) == []
    assert len(compose(f, c, g, 1, n=1)) == 1
    six = compose(f, c, g, 1, n=6)
    assert {s.composition for s in six} == set(COMPOSITION_NAMES)
    twelve = compose(f, c, g, 1, n=12)
    assert len(twelve) == 12
    assert len({(s.composition, s.style.family) for s in twelve}) == 12
    everything = compose(f, c, g, 1, n=500)
    assert len(everything) == len(COMPOSITION_NAMES) * len(FAMILIES)
    assert len({(s.composition, s.style.family) for s in everything}) == len(everything)


def test_reasons_are_written_from_the_numbers():
    riya = looks_for("riya")[0]
    reasons = {r.dimension: r for r in riya.reasons}
    assert reasons["graph"].because == "The top area is 28 above the median"
    assert "6 roles" in reasons["composition"].because
    assert reasons["family"].because.startswith("Suits")
    second = {r.dimension: r for r in looks_for("riya")[1].reasons}
    assert second["composition"].because.startswith("A different take: ")
    sana = {r.dimension: r for r in looks_for("sana")[0].reasons}
    assert sana["graph"].because == "One area leads the next by 44"
    assert "4 writing samples" in sana["composition"].because


# ───────────────────────── default page ─────────────────────────


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_default_spec_is_a_valid_signature_page(name):
    content, graph = build_content(name), build_graph(name)
    spec = default_spec(content, graph)
    assert spec_problems(spec) == []
    assert spec.composition == "signature"
    assert (spec.style.family, spec.style.type, spec.style.corners, spec.style.density) == (
        "iris",
        "studio",
        "soft",
        "airy",
    )
    assert spec.blocks[0].variant == "split" and graph_variant(spec) in GRAPH_VARIANTS
    assert all(b.public == b.visible for b in spec.blocks)
    assert spec.privacy.model_dump() == {"email": False, "phone": False, "grades": False}


def test_default_spec_picks_the_graph_variant_from_the_shape():
    assert graph_variant(default_spec(build_content("aarav"), build_graph("aarav"))) == "focus"
    assert graph_variant(default_spec(build_content("riya"), build_graph("riya"))) == "peaks"
    assert graph_variant(default_spec(build_content("sana"), build_graph("sana"))) in ("peaks", "spotlight")


# ───────────────────────── property loop ─────────────────────────


def random_features(rng: random.Random) -> Features:
    n_domains = rng.randint(0, 9)
    scores = sorted((rng.randint(0, 100) for _ in range(n_domains)), reverse=True)
    n_skills = rng.randint(0, 40)
    return Features(
        n_domains=n_domains,
        n_skills=n_skills,
        n_rich=rng.randint(0, n_domains),
        top=scores[0] if scores else 0,
        second=scores[1] if len(scores) > 1 else 0,
        median=float(scores[len(scores) // 2]) if scores else 0.0,
        has_mastery=rng.random() < 0.8,
        n_sources=rng.randint(0, 5),
        source_kinds=tuple(
            rng.sample(["resume", "github", "portfolio", "link", "lovable"], rng.randint(0, 4))
        ),
        n_experience=rng.randint(0, 8),
        n_projects=rng.randint(0, 14),
        n_education=rng.randint(0, 3),
        n_achievements=rng.randint(0, 5),
        n_clips=rng.randint(0, 6),
        n_links=rng.randint(0, 4),
        n_quotes=rng.randint(0, 1),
        years_experience=round(rng.uniform(0, 25), 1),
        stage=rng.choice(STAGES),
        lean=rng.choice(LEANS),
        career_family=rng.choice([None, "data_ai", "design_ux", "software_engineering"]),
        has_public_proof=rng.random() < 0.5,
        writing_led=rng.random() < 0.2,
        project_led=rng.random() < 0.2,
        measured=rng.random() < 0.5,
        leads=rng.random() < 0.3,
        from_first_read=rng.random() < 0.3,
    )


def random_content(rng: random.Random):
    kinds = ["experience", "project", "education", "achievement", "clip", "link", "contact", "quote"]
    facts = []
    for kind in kinds:
        for i in range(rng.choice([0, 0, 1, 2, 5])):
            facts.append(
                fact(
                    f"{kind[:4]}-{i}",
                    kind,
                    f"{kind} {rng.randint(0, 99)} item",
                    start=rng.choice([None, "2019", "2024-03"]),
                )
            )
    titles = rng.choice([[], ["Engineer"], ["Engineer", "Analyst"]])
    graph = blank_graph(
        [rng.randint(0, 100) for _ in range(rng.randint(0, 8))], skills_each=rng.randint(0, 6)
    )
    return blank_content(facts, stats=rng.randint(0, 4), open_to_titles=titles), graph


def test_500_random_feature_sets_always_give_valid_specs():
    rng = random.Random(20261008)
    for i in range(500):
        features = random_features(rng)
        content, graph = random_content(rng)
        seed = rng.randrange(2**63)
        looks = compose(features, content, graph, seed)
        assert len(looks) == 3, i
        for spec in looks:
            assert spec_problems(spec) == [], (i, features)
            graph_blocks = [b for b in spec.blocks if b.kind == "graph"]
            assert len(graph_blocks) == 1 and graph_blocks[0] in spec.blocks[:3]
            assert_valid(spec, content, graph)
        assert len({(s.composition, s.style.family) for s in looks}) == 3
        assert [s.model_dump() for s in looks] == [
            s.model_dump() for s in compose(features, content, graph, seed)
        ]


def test_every_composition_and_graph_variant_is_reachable():
    rng = random.Random(5)
    seen_comp: set[str] = set()
    seen_graph: set[str] = set()
    for _ in range(400):
        features = random_features(rng)
        content, graph = random_content(rng)
        for spec in compose(features, content, graph, rng.randrange(2**32)):
            seen_comp.add(spec.composition)
            seen_graph.add(graph_variant(spec))
    assert seen_comp == set(COMPOSITION_NAMES)
    assert seen_graph == set(GRAPH_VARIANTS)


def test_compositions_table_is_what_compose_builds_from():
    for name in COMPOSITION_NAMES:
        spec = compose(Features(), blank_content(), blank_graph(), 1, n=6)
        built = {s.composition: [b.kind for b in s.blocks] for s in spec}
        assert built[name] == [k for k, _ in COMPOSITIONS[name]]


# ───────────────────────── the hero, and the owner's choices after a rebuild ─────────────────────────


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_the_hero_is_always_visible_and_public(name):
    for look in looks_for(name):
        hero = next(b for b in look.blocks if b.kind == "hero")
        assert hero.visible and hero.public
    content, graph = build_content(name), build_graph(name)
    hero = next(b for b in default_spec(content, graph).blocks if b.kind == "hero")
    assert hero.visible and hero.public


def test_the_hero_is_shown_even_on_an_empty_page():
    spec = default_spec(blank_content(), blank_graph())
    hero = next(b for b in spec.blocks if b.kind == "hero")
    assert hero.visible and hero.public


def previous_page(name="riya"):
    return make_portfolio(name)


def rebuilt_page(name="riya", seed_shift=1):
    """The same person built again: new looks from the same content, as a regenerate makes them."""
    page = make_portfolio(name)
    return page.model_copy(deep=True, update={"looks": looks_for(name, seed=seed_shift)})


def test_a_rebuild_keeps_each_blocks_visible_and_public_flags_by_id():
    old = previous_page()
    for b in old.spec.blocks:
        if b.kind == "projects":
            b.public = False
        if b.kind == "education":
            b.visible = False
        if b.kind == "openTo":
            b.visible = b.public = True
    new = rebuilt_page()
    out = carry_owner_choices(new, old)
    owner = {b.id: (b.visible, b.public) for b in old.spec.blocks}
    for spec in (out.spec, *out.looks):
        for b in spec.blocks:
            if b.id in owner and b.kind not in ("hero",):
                assert (b.visible, b.public) == owner[b.id], b.id
        assert spec_problems(spec) == []
    assert out.spec.privacy == old.spec.privacy


def test_a_rebuild_keeps_the_privacy_switches_and_the_default_view():
    old = previous_page()
    old.spec.privacy = PortfolioPrivacy(email=True, phone=False, grades=True)
    old.publish.defaultView = "press"
    out = carry_owner_choices(rebuilt_page(), old)
    for spec in (out.spec, *out.looks):
        assert spec.privacy == PortfolioPrivacy(email=True, phone=False, grades=True)
    assert out.publish.defaultView == "press"


def test_a_rebuild_never_shows_a_block_the_new_content_cannot_fill():
    old = previous_page()
    for b in old.spec.blocks:
        b.visible = b.public = True
    new = rebuilt_page()
    new.content = new.content.model_copy(
        update={"facts": [f for f in new.content.facts if f.kind != "education"]}
    )
    out = carry_owner_choices(new, old)
    education = next(b for b in out.spec.blocks if b.kind == "education")
    assert not education.visible and not education.public


def test_a_hero_the_old_page_had_hidden_is_shown_again():
    old = previous_page()
    for b in old.spec.blocks:
        if b.kind == "hero":
            b.visible = b.public = False
    out = carry_owner_choices(rebuilt_page(), old)
    hero = next(b for b in out.spec.blocks if b.kind == "hero")
    assert hero.visible and hero.public


def test_a_rebuild_keeps_only_the_hidden_graph_areas_that_still_exist_and_leaves_three():
    old = previous_page()
    ids = [d.id for d in old.graph.domains]
    old.spec.graph = GraphSpec(hiddenDomainIds=[*ids[:2], "gone-area"], stats=True)
    out = carry_owner_choices(rebuilt_page(), old)
    assert out.spec.graph.hiddenDomainIds == ids[:2]

    crowded = previous_page()
    crowded.spec.graph = GraphSpec(
        hiddenDomainIds=ids[:-2], stats=True
    )  # leaves two: not allowed on a new page
    out = carry_owner_choices(rebuilt_page(), crowded)
    assert len(ids) - len(out.spec.graph.hiddenDomainIds) >= 3


def test_carrying_choices_leaves_both_pages_untouched_and_is_repeatable():
    old, new = previous_page(), rebuilt_page()
    old.spec.privacy = PortfolioPrivacy(email=True, phone=True, grades=False)
    before_old, before_new = old.model_dump(), new.model_dump()
    once = carry_owner_choices(new, old)
    assert old.model_dump() == before_old and new.model_dump() == before_new
    assert carry_owner_choices(once, old) == once
