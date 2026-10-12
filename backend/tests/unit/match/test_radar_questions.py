"""The radar (role expectation against the graph) and the three questions."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.contract.match import Coverage, Gate, GateAnswer, Posting, Requirement
from tayyari.contract.models import Profile
from tayyari.match.questions import make_questions
from tayyari.match.radar import OTHER_AXIS, build_radar, map_requirement
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


def test_unmapped_requirements_form_an_other_axis_only_from_two() -> None:
    profile = profile_with(DOMAINS)
    one = build_radar(
        profile, [req("a", "must", "Frontend"), req("x", "required", "Underwater basket weaving")]
    )
    assert [a.domain for a in one.axes] == ["Frontend", "Backend"]

    two = build_radar(
        profile,
        [
            req("a", "must", "Frontend"),
            req("x", "required", "Underwater basket weaving"),
            req("y", "must", "Beekeeping"),
        ],
        [cov("x", "covered", 4), cov("y", "partial", 2)],
    )
    other = two.axes[-1]
    assert other.domain == OTHER_AXIS
    assert other.role == 100  # 0.8 + 1.0 = 1.8 against 1.0 for Frontend: the busiest axis
    assert two.axes[0].role == round(100 * 1.0 / 1.8)
    assert other.you == round(100 * (4 + 2) / (4 * 2))  # the mean coverage of those requirements


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


def test_no_requirements_gives_flat_axes() -> None:
    radar = build_radar(profile_with(DOMAINS), [])
    assert [a.role for a in radar.axes] == [0, 0]


def test_the_personas_radars_have_one_axis_per_domain_and_a_busiest_axis_of_one_hundred() -> None:
    for persona in (meera(), riya()):
        from tayyari.match.posting import parse_posting
        from tests.unit.match.support import posting_answer

        posting = parse_posting(posting_answer(persona), persona.posting_text).posting
        radar = build_radar(persona.profile, posting.requirements)
        domains = [d.short for d in persona.profile.domains]
        assert [a.domain for a in radar.axes][: len(domains)] == domains
        assert max(a.role for a in radar.axes) == 100
        assert all(0 <= a.role <= 100 and 0 <= a.you <= 100 for a in radar.axes)


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
