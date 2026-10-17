"""The radar (role expectation against the graph) and the three questions."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.contract.match import Coverage, Gate, GateAnswer, Posting, Radar, Requirement
from tayyari.contract.models import Profile
from tayyari.match.questions import make_questions
from tayyari.match.radar import build_radar, map_requirement
from tests.unit.match.support import meera, riya


def req(
    rid: str, tier: str = "required", hint: str | None = None, terms: list[str] | None = None
) -> Requirement:
    return Requirement(
        id=rid,
        text=f"Requirement {rid}",
        quote=f"Requirement {rid}",
        tier=tier,  # type: ignore[arg-type]
        kind="skill",
        provenance="stated",
        domainHint=hint,
        terms=terms or [],
    )


def cov(rid: str, status: str = "covered", level: int = 4) -> Coverage:
    return Coverage(
        requirementId=rid,
        factIds=[] if status in ("missing_gap", "unknown") else ["f1"],
        status=status,  # type: ignore[arg-type]
        level=level,
        note="",
    )


def profile_with(
    domains: list[tuple[str, str, str, int]], skills: list[dict[str, Any]] | None = None
) -> Profile:
    base = meera().profile.to_dict()
    base["domains"] = [
        {"id": i, "name": n, "short": s, "score": sc, "summary": ""} for i, n, s, sc in domains
    ]
    base["skills"] = skills or []
    return Profile.from_dict(base)


DOMAINS = [("front", "Frontend Engineering", "Frontend", 80), ("back", "Backend & APIs", "Backend", 50)]


def test_role_is_the_tier_weighted_share_scaled_to_the_busiest_axis_and_you_is_the_graphs_mastery() -> None:
    profile = profile_with(DOMAINS)
    reqs = [req("a", "must", "Frontend"), req("b", "nice", "front end"), req("c", "required", "APIs")]
    radar = build_radar(profile, reqs)
    assert [a.domain for a in radar.axes] == ["Frontend", "Backend"]
    assert [a.you for a in radar.axes] == [80, 50]  # the profile's own domain scores
    # Frontend weighs 1.0 + 0.25 = 1.25 (100); Backend weighs 0.8 (64)
    assert [a.role for a in radar.axes] == [100, 64]
    assert [a.asks for a in radar.axes] == [["a", "b"], ["c"]]
    assert radar.outside == []


def test_requirements_outside_the_graph_are_listed_never_an_axis_and_never_outweigh_a_real_area() -> None:
    profile = profile_with(DOMAINS)
    radar = build_radar(
        profile,
        [
            req("a", "nice", "Frontend"),
            req("x", "must", "Underwater basket weaving"),
            req("y", "must", "Beekeeping"),
            req("z", "required", "Education"),
        ],
    )
    assert [a.domain for a in radar.axes] == ["Frontend", "Backend"]  # no "Other" axis, however many
    assert radar.outside == ["x", "y", "z"]
    assert [a.role for a in radar.axes] == [100, 0]  # the one real ask is the busiest axis


def test_the_areas_placement_wins_over_code_and_none_means_outside() -> None:
    profile = profile_with(DOMAINS)
    reqs = [req("a", "must", "Frontend"), req("b", "required", "Cloud"), req("c", "required", "Frontend")]
    radar = build_radar(profile, reqs, {"a": "back", "b": "back", "c": None})
    assert [a.asks for a in radar.axes] == [[], ["a", "b"]]
    assert radar.outside == ["c"]  # the model said none, though the hint names Frontend


def test_an_unplaced_requirement_and_an_unknown_area_fall_back_to_code() -> None:
    profile = profile_with(DOMAINS)
    reqs = [req("a", "must", "Frontend"), req("b", "required", "APIs")]
    radar = build_radar(profile, reqs, {"b": "nowhere"})  # "a" missing: code; "b" an id the graph lacks
    assert [a.asks for a in radar.axes] == [["a"], []]
    assert radar.outside == ["b"]


def test_a_requirement_finds_its_domain_by_its_terms_when_the_hint_does_not() -> None:
    profile = profile_with(
        DOMAINS,
        [{"id": "ts", "name": "TypeScript", "domainId": "front", "score": 80, "evidence": [], "unlocks": []}],
    )
    by_term = req("a", "must", "Something vague", terms=["TS"])  # an alias of TypeScript
    mapped = map_requirement(by_term, profile)
    assert mapped is not None
    assert mapped.id == "front"
    assert map_requirement(req("b", "must", "Something vague"), profile) is None


AI_DOMAINS = [
    ("eval", "LLM Evaluation & Optimization", "LLM Eval", 80),
    ("design", "Design Systems & Motion", "Design", 70),
    ("agents", "Agent & RAG Systems", "Agents & RAG", 76),
    ("backend", "Backend & Cloud", "Backend & Cloud", 58),
    ("gov", "Observability & Governance", "Observability", 55),
    ("practice", "Engineering Practice", "Practice", 50),
]
AI_SKILLS = [
    {
        "id": "rag",
        "name": "RAG & retrieval",
        "domainId": "agents",
        "score": 70,
        "evidence": [],
        "unlocks": [],
    },
    {"id": "py", "name": "Python", "domainId": "backend", "score": 80, "evidence": [], "unlocks": []},
    {
        "id": "k8s",
        "name": "Cloud & containers",
        "domainId": "backend",
        "score": 60,
        "evidence": [],
        "unlocks": [],
    },
]


@pytest.mark.parametrize(
    ("hint", "terms", "domain"),
    [
        ("Cloud infrastructure", [], "backend"),  # a distinctive word the names share
        ("Agentic AI", [], "agents"),  # "agentic" begins with "agent", over the AI alias of LLM Eval
        ("Retrieval", ["RAG"], "agents"),  # a term inside a skill's name
        ("Data storage", ["Python"], "backend"),  # the skill the term names
        ("Software engineering", [], None),  # only a generic word in common
        ("Systems", ["Rust"], None),  # a generic word alone names no area, even inside a name
        ("Engineering practices", [], "practice"),  # generic words still name an area of the very same name
        ("Education", [], None),
    ],
)
def test_code_places_a_hint_the_alias_table_does_not_know(
    hint: str, terms: list[str], domain: str | None
) -> None:
    profile = profile_with(AI_DOMAINS, AI_SKILLS)
    found = map_requirement(req("a", "required", hint, terms), profile)
    assert (found.id if found else None) == domain


def test_no_requirements_gives_flat_axes() -> None:
    radar = build_radar(profile_with(DOMAINS), [])
    assert [a.role for a in radar.axes] == [0, 0]
    assert radar.outside == []


def test_the_personas_radars_have_one_axis_per_domain_and_a_busiest_axis_of_one_hundred() -> None:
    for persona in (meera(), riya()):
        from tayyari.match.posting import parse_posting
        from tests.unit.match.support import posting_answer

        posting = parse_posting(posting_answer(persona), persona.posting_text).posting
        radar = build_radar(persona.profile, posting.requirements)
        domains = [d.short for d in persona.profile.domains]
        assert [a.domain for a in radar.axes] == domains
        assert max(a.role for a in radar.axes) == 100
        assert all(0 <= a.role <= 100 and 0 <= a.you <= 100 for a in radar.axes)
        placed = {r for a in radar.axes for r in a.asks or []} | set(radar.outside or [])
        assert placed == {r.id for r in posting.requirements}  # every requirement is on one axis or outside


def test_a_stored_radar_with_an_other_axis_is_read_without_it_and_scaled_again() -> None:
    stored = {
        "axes": [
            {"domain": "Frontend", "you": 80, "role": 20},
            {"domain": "Backend", "you": 50, "role": 40},
            {"domain": "Other", "you": 100, "role": 100},
        ]
    }
    radar = Radar.from_dict(stored)
    assert [(a.domain, a.role) for a in radar.axes] == [("Frontend", 50), ("Backend", 100)]
    assert radar.outside is None  # which requirements were outside is not known for an old radar
    new = Radar.from_dict({"axes": stored["axes"], "outside": []})
    assert [a.domain for a in new.axes][
        -1
    ] == "Other"  # a new radar is read as it is (a graph may name an area so)


# ───────────────────────── questions ─────────────────────────


def posting_of(reqs: list[Requirement], gates: list[Gate] | None = None) -> Posting:
    return Posting(
        hash="h",
        title="t",
        workMode="unspecified",
        rawText="x",
        requirements=reqs,
        gates=gates or [],
        anomalies=[],
        links=[],
    )


def test_at_most_three_questions_whatever_the_number_of_gaps() -> None:
    reqs = [req(f"r{i}", "must") for i in range(1, 11)]
    rows = [cov(f"r{i}", "missing_gap", 0) for i in range(1, 11)]
    questions = make_questions(posting_of(reqs), rows, [])
    assert len(questions) == 3
    assert len({q.id for q in questions}) == 3
    assert (
        len(make_questions(posting_of(reqs), rows, [], limit=99)) == 3
    )  # the cap is not a parameter you can raise


def test_uncertain_gates_come_first_then_must_before_required_and_unknown_before_gap() -> None:
    gates = [
        Gate(id="g1", question="Are you eligible?", quote="eligible", severity=9),
        Gate(id="g2", question="Can you relocate?", quote="relocate", severity=7),
    ]
    answers = [
        GateAnswer(gateId="g1", factIds=[], verdict="uncertain", note=""),
        GateAnswer(gateId="g2", factIds=["f1"], verdict="pass", note=""),
    ]
    reqs = [req("a", "required"), req("b", "must"), req("c", "must"), req("d", "preferred")]
    rows = [
        cov("a", "unknown", 0),
        cov("b", "missing_gap", 0),
        cov("c", "unknown", 0),
        cov("d", "partial", 2),
    ]
    questions = make_questions(posting_of(reqs, gates), rows, answers, limit=10)
    assert [q.id for q in questions] == ["q-g1", "q-c", "q-b"]  # g2 passed, so it is not asked; limit still 3


def test_covered_and_surfaced_rows_and_nice_to_haves_ask_nothing() -> None:
    reqs = [req("a", "must"), req("b", "must"), req("c", "nice"), req("d", "required")]
    rows = [
        cov("a", "covered", 4),
        cov("b", "missing_have_it", 3),
        cov("c", "missing_gap", 0),
        cov("d", "partial", 2),
    ]
    questions = make_questions(posting_of(reqs), rows, [])
    assert [q.id for q in questions] == ["q-d"]
    assert questions[0].requirementId == "d"
    assert questions[0].gateId is None


def test_each_question_points_at_a_requirement_or_a_gate_and_says_why() -> None:
    p = meera()
    from tayyari.match.posting import parse_posting
    from tests.unit.match.support import posting_answer

    posting = parse_posting(posting_answer(p), p.posting_text).posting
    questions = make_questions(
        posting,
        [cov(r.id, "missing_gap", 0) for r in posting.requirements],
        [GateAnswer(gateId="g1", factIds=[], verdict="uncertain", note="")],
    )
    assert len(questions) == 3
    assert questions[0].gateId == "g1"
    for q in questions:
        assert (q.requirementId is None) != (q.gateId is None)
        assert q.text.strip()
        assert q.why.strip()


@pytest.mark.parametrize("limit", [0, -1])
def test_a_zero_limit_asks_nothing(limit: int) -> None:
    reqs = [req("a", "must")]
    assert make_questions(posting_of(reqs), [cov("a", "missing_gap", 0)], [], limit=limit) == []
