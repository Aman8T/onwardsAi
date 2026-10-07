"""The shadow pack: bound questions, incumbents from the Sonnet profile, the level-to-score map, and that none of it is used."""

from __future__ import annotations

import gc
import time
import warnings
from collections.abc import Callable
from typing import Any

import pytest

from tayyari.contract import Profile
from tayyari.core.limits import JEV_FIRST_READ_RESUME_CHARS, JEV_MAX_QUESTIONS_PER_CALL
from tayyari.decisions.packs.shadow import (
    BAND_EDGES,
    CLOSEST_INSTANCE,
    FIT_LEVELS,
    LEVEL_TO_SCORE,
    MAX_STATE_CHARS,
    REQUIREMENT_HAVE_ID,
    ROLE_CLOSEST_ID,
    ROLE_FIT_ID,
    SHADOW_PACK,
    SKILL_LEVEL_ID,
    SKILL_LEVELS,
    LevelValue,
    band_of,
    build_plan,
    build_state,
    interpret_level,
    ordered_roles,
)
from tayyari.decisions.service import ShadowMaterials
from tayyari.decisions.spec import question_key, split_key
from tayyari.llm.errors import JevError
from tests.fakes.jev import choice_answer, noul_answer, score_answer
from tests.unit.decisions.packs.support import CTX, PackHarness

Harness = Callable[..., PackHarness]


def materials(profile: Profile, resume: str | None = "Jane Doe resume text") -> ShadowMaterials:
    readers = {s.id: {"sub": f"{s.id} stats", "summary": "x", "skills": []} for s in profile.sources}
    return ShadowMaterials(profile=profile, reader_outputs=readers, resume_text=resume)


def answers_for(profile: Profile) -> dict[str, Any]:
    return {
        SKILL_LEVEL_ID: score_answer(3, n_levels=6, p=0.8),
        REQUIREMENT_HAVE_ID: noul_answer(0.9),
        ROLE_FIT_ID: score_answer(4, n_levels=6, p=0.8),
        ROLE_CLOSEST_ID: choice_answer(profile.roles[1].id, options=[r.id for r in profile.roles], p=0.8),
    }


def expected_keys(profile: Profile) -> set[str]:
    keys = {question_key(SKILL_LEVEL_ID, s.id) for s in profile.skills}
    for role in profile.roles:
        keys.add(question_key(ROLE_FIT_ID, role.id))
        keys.update(
            question_key(REQUIREMENT_HAVE_ID, f"{role.id}#{n}") for n in range(len(role.requirements))
        )
    keys.add(question_key(ROLE_CLOSEST_ID, CLOSEST_INSTANCE))
    return keys


# ───────────────────────── the specs ─────────────────────────


def test_the_pack_has_the_four_agreed_shadow_specs() -> None:
    assert [s.id for s in SHADOW_PACK.specs] == [
        "skill.level",
        "requirement.have",
        "role.closest",
        "role.fit",
    ]
    assert {s.pack for s in SHADOW_PACK.specs} == {"shadow"}
    assert {s.default.mode for s in SHADOW_PACK.specs} == {"shadow"}
    assert {s.default.threshold for s in SHADOW_PACK.specs} == {1.0}
    assert {s.default.fallback for s in SHADOW_PACK.specs} == {"skip"}
    assert all(s.bind is not None for s in SHADOW_PACK.specs)
    kinds = {s.id: s.question.kind for s in SHADOW_PACK.specs}
    assert kinds == {
        "skill.level": "score",
        "requirement.have": "noul",
        "role.closest": "choice",
        "role.fit": "score",
    }


def test_skill_and_fit_have_six_field_neutral_levels() -> None:
    assert len(SKILL_LEVELS) == 6 == len(FIT_LEVELS) == len(LEVEL_TO_SCORE)
    assert SKILL_LEVELS[0].startswith("None") and "recognised" in SKILL_LEVELS[5]
    assert FIT_LEVELS[0].startswith("Not at all") and FIT_LEVELS[5].startswith("Ready now")


def test_levels_map_to_the_agreed_scores_and_bands() -> None:
    assert LEVEL_TO_SCORE == (5, 20, 38, 58, 78, 92)
    assert BAND_EDGES == (40, 60, 75, 90)
    table = [(0, 0), (39, 0), (40, 1), (59, 1), (60, 2), (74, 2), (75, 3), (89, 3), (90, 4), (100, 4)]
    assert [(score, band_of(score)) for score, _ in table] == table
    assert [band_of(s) for s in LEVEL_TO_SCORE] == [0, 0, 0, 1, 3, 4]


def test_an_answer_is_interpreted_as_level_score_band_and_expectation() -> None:
    from tayyari.llm.jev import normalise_answer

    answer = normalise_answer("score", score_answer(3, n_levels=6, p=0.8))
    value = interpret_level(answer)
    assert isinstance(value, LevelValue)
    assert (value.level, value.score, value.band) == (3, 58, 1)
    assert value.expected is not None and 2.5 < value.expected < 3.6


def test_every_bound_question_is_a_valid_jev_question() -> None:
    skill = SHADOW_PACK.spec("skill.level").bind({"skill": "React"})  # type: ignore[misc]
    assert skill.kind == "score" and skill.level_count == 6
    assert skill.instructions == {"question": skill.instructions["question"], "skill": "React"}  # type: ignore[index]
    req = SHADOW_PACK.spec("requirement.have").bind({"requirement": "Motion", "role": "Design Engineer"})  # type: ignore[misc]
    assert (
        req.kind == "noul"
        and req.instructions["requirement"] == "Motion"
        and req.instructions["role"] == "Design Engineer"
    )  # type: ignore[index]
    fit = SHADOW_PACK.spec("role.fit").bind({"role": "Design Engineer"})  # type: ignore[misc]
    assert fit.kind == "score" and fit.level_count == 6 and fit.instructions["role"] == "Design Engineer"  # type: ignore[index]
    closest = SHADOW_PACK.spec("role.closest").bind(  # type: ignore[misc]
        {
            "roles": [
                {"id": "a", "title": "A role", "why": "because"},
                {"id": "b", "title": "B role", "why": ""},
            ]
        }
    )
    assert closest.kind == "choice" and closest.option_labels == ("a", "b")
    assert closest.criteria == {"a": "A role: because", "b": "B role"}  # type: ignore[comparison-overlap]


def test_a_long_description_is_clipped_to_the_choice_limit() -> None:
    closest = SHADOW_PACK.spec("role.closest").bind(  # type: ignore[misc]
        {"roles": [{"id": "a", "title": "A role", "why": "word " * 200}]}
    )
    assert all(len(str(d)) <= 255 for d in closest.criteria.values())  # type: ignore[union-attr]


# ───────────────────────── the state ─────────────────────────


def test_the_state_is_the_reader_outputs_and_the_clipped_resume(sample_profile: Profile) -> None:
    mats = materials(sample_profile, resume="r" * (JEV_FIRST_READ_RESUME_CHARS + 10))
    state = build_state(mats)
    assert set(state) == {"readers", "resume"}
    assert set(state["readers"]) == {s.id for s in sample_profile.sources}
    assert len(state["resume"]) == JEV_FIRST_READ_RESUME_CHARS


def test_empty_reader_outputs_and_a_missing_resume_are_left_out(sample_profile: Profile) -> None:
    mats = ShadowMaterials(sample_profile, {"a": {"sub": "x"}, "b": {}}, None)
    assert build_state(mats) == {"readers": {"a": {"sub": "x"}}, "resume": ""}


def test_the_state_stays_under_the_size_cap(sample_profile: Profile) -> None:
    big = {f"s{i}": {"summary": "x" * 20_000} for i in range(4)}
    state = build_state(ShadowMaterials(sample_profile, big, "r" * JEV_FIRST_READ_RESUME_CHARS))
    assert len(str(state["readers"])) + len(state["resume"]) <= MAX_STATE_CHARS + 100


# ───────────────────────── the plan ─────────────────────────


def test_the_plan_asks_every_skill_requirement_and_role(sample_profile: Profile) -> None:
    plan = build_plan(sample_profile)
    assert set(plan.bindings) == expected_keys(sample_profile) == set(plan.incumbent)
    assert len([k for k in plan.bindings if k.startswith("skill.level#")]) == len(sample_profile.skills)
    assert len([k for k in plan.bindings if k.startswith("role.fit#")]) == len(sample_profile.roles)
    assert [k for k in plan.bindings if k.startswith("role.closest")] == ["role.closest#all"]
    # the instance may hold a "#": the spec id is everything before the first one
    key = question_key(REQUIREMENT_HAVE_ID, f"{sample_profile.roles[0].id}#0")
    assert split_key(key) == ("requirement.have", f"{sample_profile.roles[0].id}#0")


def test_the_incumbents_are_sonnets_numbers(sample_profile: Profile) -> None:
    plan = build_plan(sample_profile)
    skill = sample_profile.skills[0]
    assert plan.incumbent[f"skill.level#{skill.id}"] == {
        "score": skill.score,
        "band": band_of(skill.score),
        "skill_id": skill.id,
    }
    assert plan.bindings[f"skill.level#{skill.id}"] == {"skill": skill.name}

    role = sample_profile.roles[0]
    req = role.requirements[4]
    key = f"requirement.have#{role.id}#4"
    assert plan.incumbent[key] == {
        "have": req.have,
        "current": req.current,
        "target": req.target,
        "skill_id": req.skillId,
    }
    assert plan.bindings[key] == {"requirement": req.name, "role": role.title}

    fit = plan.incumbent[f"role.fit#{role.id}"]
    assert fit == {"score": role.alignment, "band": band_of(role.alignment)}
    assert plan.bindings[f"role.fit#{role.id}"] == {"role": role.title}


def test_the_closest_role_is_the_one_with_the_best_alignment(sample_profile: Profile) -> None:
    plan = build_plan(sample_profile)
    incumbent = plan.incumbent["role.closest#all"]
    best = max(sample_profile.roles, key=lambda r: r.alignment)
    assert incumbent["role_id"] == best.id
    assert incumbent["ranking"][0] == best.id
    assert sorted(incumbent["ranking"]) == sorted(r.id for r in sample_profile.roles)
    offered = plan.bindings["role.closest#all"]["roles"]
    assert [r["id"] for r in offered] == incumbent["option_order"]
    assert {r["title"] for r in offered} == {r.title for r in sample_profile.roles}


def test_the_option_order_is_seeded_by_the_profile_id(sample_profile: Profile) -> None:
    roles = list(sample_profile.roles)
    first = [r.id for r in ordered_roles(roles, "profile-a")]
    assert first == [r.id for r in ordered_roles(roles, "profile-a")]  # stable
    assert first == [r.id for r in ordered_roles(list(reversed(roles)), "profile-a")]  # not input order
    orders = {tuple(r.id for r in ordered_roles(roles, f"profile-{n}")) for n in range(20)}
    assert len(orders) > 5  # different profiles see different orders
    assert sorted(first) == sorted(r.id for r in roles)


def test_the_plan_copes_with_duplicate_ids_and_a_key_separator(sample_profile: Profile) -> None:
    skills = [sample_profile.skills[0], sample_profile.skills[0].model_copy(update={"id": "c#"})]
    profile = sample_profile.model_copy(update={"skills": skills, "roles": sample_profile.roles[:1]})
    plan = build_plan(profile)
    assert [k for k in plan.bindings if k.startswith("skill.level#")] == [
        f"skill.level#{skills[0].id}",
        "skill.level#c-",
    ]
    duplicate = sample_profile.model_copy(update={"skills": [skills[0], skills[0]]})
    assert len([k for k in build_plan(duplicate).bindings if k.startswith("skill.level#")]) == 1


def test_a_profile_with_no_roles_has_no_closest_question(sample_profile: Profile) -> None:
    plan = build_plan(sample_profile.model_copy(update={"roles": []}))
    assert not any(k.startswith(("role.", "requirement.")) for k in plan.bindings)
    assert len(plan.bindings) == len(sample_profile.skills)


# ───────────────────────── through the service ─────────────────────────


async def test_the_questions_are_batched_over_one_state(harness: Harness, sample_profile: Profile) -> None:
    h = harness(answers_for(sample_profile))
    h.service.shadow_compare(materials(sample_profile), CTX)
    await h.drain()

    keys = expected_keys(sample_profile)
    asked = [qid for call in h.jev.calls for qid in call.question_ids]
    assert set(asked) == keys and len(asked) == len(keys)
    assert len(h.jev.calls) == -(-len(keys) // JEV_MAX_QUESTIONS_PER_CALL) >= 2
    assert all(len(c.question_ids) <= JEV_MAX_QUESTIONS_PER_CALL for c in h.jev.calls)
    first_state = h.jev.calls[0].state
    assert all(c.state == first_state for c in h.jev.calls)
    assert set(first_state) == {"readers", "resume"}  # type: ignore[arg-type]
    assert h.fallback_calls == []  # shadow decisions have no Claude fallback


async def test_the_bound_questions_carry_what_they_are_about(
    harness: Harness, sample_profile: Profile
) -> None:
    h = harness(answers_for(sample_profile))
    h.service.shadow_compare(materials(sample_profile), CTX)
    await h.drain()

    questions = {qid: q for call in h.jev.calls for qid, q in call.questions.items()}
    skill = sample_profile.skills[2]
    wire = questions[f"skill.level#{skill.id}"].to_wire()
    assert (
        wire["type"] == "score" and wire["instructions"]["skill"] == skill.name and len(wire["criteria"]) == 6
    )
    role = sample_profile.roles[0]
    req_wire = questions[f"requirement.have#{role.id}#1"].to_wire()
    assert req_wire["instructions"]["requirement"] == role.requirements[1].name
    assert req_wire["instructions"]["role"] == role.title
    closest = questions["role.closest#all"]
    plan = build_plan(sample_profile)
    assert list(closest.option_labels) == plan.incumbent["role.closest#all"]["option_order"]
    assert all(
        str(d).startswith(next(r.title for r in sample_profile.roles if r.id == k))
        for k, d in closest.criteria.items()
    )  # type: ignore[union-attr]


async def test_every_question_is_logged_next_to_its_incumbent_and_never_used(
    harness: Harness, sample_profile: Profile
) -> None:
    h = harness(answers_for(sample_profile))
    h.service.shadow_compare(materials(sample_profile), CTX)
    await h.drain()

    plan = build_plan(sample_profile)
    rows = {r.subject_ref: r for r in h.rows}
    assert set(rows) == set(plan.incumbent)
    assert all(r.mode == "shadow" and r.used is False and r.pack == "shadow" for r in rows.values())
    assert all(
        r.spec_version == "1" and r.source_used == "jev" and r.escalated is False for r in rows.values()
    )
    assert all(r.is_sample is False and r.profile_id == "profile-1" for r in rows.values())
    for key, incumbent in plan.incumbent.items():
        assert rows[key].incumbent == incumbent

    skill = sample_profile.skills[0]
    row = rows[f"skill.level#{skill.id}"]
    assert row.extra["value"] == {
        "level": 3,
        "score": 58,
        "band": 1,
        "expected": pytest.approx(row.extra["value"]["expected"]),
    }
    assert row.jev_answer["score_level"] == 3 and row.jev_confidence is not None
    assert rows[f"role.fit#{sample_profile.roles[0].id}"].extra["value"]["score"] == 78  # level 4
    assert rows[f"requirement.have#{sample_profile.roles[0].id}#0"].extra["value"] is True
    assert rows["role.closest#all"].extra["value"] == sample_profile.roles[1].id


async def test_the_level_to_score_map_is_recorded_for_agreement_analysis(
    harness: Harness, sample_profile: Profile
) -> None:
    levels = [0, 1, 2, 3, 4, 5]
    skills = sample_profile.skills[:6]
    script = {
        f"skill.level#{s.id}": score_answer(level, n_levels=6, p=0.9)
        for s, level in zip(skills, levels, strict=True)
    }
    h = harness(script)
    profile = sample_profile.model_copy(update={"skills": skills, "roles": []})
    h.service.shadow_compare(materials(profile), CTX)
    await h.drain()

    rows = {r.subject_ref: r for r in h.rows}
    got = [rows[f"skill.level#{s.id}"].extra["value"] for s in skills]
    assert [v["level"] for v in got] == levels
    assert [v["score"] for v in got] == [5, 20, 38, 58, 78, 92]
    assert [v["band"] for v in got] == [0, 0, 0, 1, 3, 4]


async def test_contact_details_in_the_resume_never_reach_jev(
    harness: Harness, sample_profile: Profile
) -> None:
    h = harness(answers_for(sample_profile))
    resume = "Jane Doe. jane.doe@example.com. +91 98765 43210. Designed checkout flows."
    h.service.shadow_compare(materials(sample_profile, resume=resume), CTX)
    await h.drain()
    sent = h.jev.calls[0].state["resume"]  # type: ignore[index]
    assert "jane.doe@example.com" not in sent and "98765" not in sent and "Designed checkout flows" in sent


async def test_shadow_compare_returns_at_once_and_the_work_finishes_in_the_background(
    harness: Harness, sample_profile: Profile
) -> None:
    h = harness(answers_for(sample_profile), slow_jev_s=0.3)
    started = time.perf_counter()
    result = h.service.shadow_compare(materials(sample_profile), CTX)
    assert result is None
    assert time.perf_counter() - started < 0.1
    assert h.runner.pending_tasks >= 1
    assert h.jev.calls == []  # nothing has been answered yet

    await h.service.drain(10)
    assert h.runner.pending_tasks == 0
    assert len(h.rows) == len(expected_keys(sample_profile))


async def test_drain_gives_up_after_its_timeout_without_raising(
    harness: Harness, sample_profile: Profile
) -> None:
    h = harness(answers_for(sample_profile), slow_jev_s=5.0)
    h.service.shadow_compare(materials(sample_profile), CTX)
    await h.service.drain(0.05)
    assert h.runner.pending_tasks >= 1
    await h.runner.drain(0.05, cancel_remaining=True)
    assert h.runner.pending_tasks == 0


async def test_a_profile_with_nothing_to_ask_starts_nothing(
    harness: Harness, sample_profile: Profile
) -> None:
    h = harness(answers_for(sample_profile))
    h.service.shadow_compare(materials(sample_profile.model_copy(update={"skills": [], "roles": []})), CTX)
    await h.drain()
    assert h.jev.calls == [] and h.runner.pending_tasks == 0


async def test_switching_the_specs_off_asks_nothing(harness: Harness, sample_profile: Profile) -> None:
    h = harness(answers_for(sample_profile), overrides={"*": {"mode": "off"}})
    h.service.shadow_compare(materials(sample_profile), CTX)
    await h.drain()
    assert h.jev.calls == []


@pytest.mark.parametrize(
    "kind", ["timeout", "connection", "server", "rate_limit", "auth", "invalid_response"]
)
async def test_a_jev_error_never_raises_and_nothing_is_used(
    harness: Harness, sample_profile: Profile, kind: str
) -> None:
    h = harness(answers_for(sample_profile))
    h.jev.fail_with(JevError(kind))  # type: ignore[arg-type]
    assert h.service.shadow_compare(materials(sample_profile), CTX) is None
    await h.drain()
    assert all(r.used is False for r in h.rows)
    assert h.fallback_calls == []


async def test_a_bad_profile_never_raises(harness: Harness, sample_profile: Profile) -> None:
    h = harness(answers_for(sample_profile))
    broken = ShadowMaterials(profile=None, reader_outputs={}, resume_text=None)  # type: ignore[arg-type]
    assert h.service.shadow_compare(broken, CTX) is None
    await h.drain()
    assert h.jev.calls == []


async def test_a_crash_inside_the_background_task_is_contained(
    harness: Harness, sample_profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness(answers_for(sample_profile))

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(h.runner, "run", boom)
    assert h.service.shadow_compare(materials(sample_profile), CTX) is None
    await h.drain()
    assert h.runner.pending_tasks == 0


def test_shadow_compare_without_a_running_loop_does_not_raise_or_leak_a_coroutine(
    make_settings: Callable[..., Any], sample_profile: Profile
) -> None:
    from tests.unit.decisions.packs.support import build_pack_harness

    h = build_pack_harness(make_settings, answers_for(sample_profile))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert h.service.shadow_compare(materials(sample_profile), CTX) is None
        gc.collect()
    assert not [w for w in caught if "never awaited" in str(w.message)]
    assert h.jev.calls == []


def test_a_level_outside_the_six_is_not_interpreted() -> None:
    from tayyari.llm.jev import normalise_answer

    assert interpret_level(normalise_answer("score", score_answer(7, n_levels=8, p=0.9))) is None


def test_two_roles_with_one_id_are_asked_once(sample_profile: Profile) -> None:
    role = sample_profile.roles[0]
    profile = sample_profile.model_copy(update={"roles": [role, role.model_copy(update={"title": "Twin"})]})
    plan = build_plan(profile)
    assert len([k for k in plan.bindings if k.startswith("role.fit#")]) == 1
    assert len([k for k in plan.bindings if k.startswith("requirement.have#")]) == len(role.requirements)
    assert plan.bindings[f"role.fit#{role.id}"] == {"role": role.title}
