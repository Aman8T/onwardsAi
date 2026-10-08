"""Features: graph shape, content counts and the shape of a career, from heuristics and from Jev's first read."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tayyari.portfolio.features import STAGES, Features, compute_features
from tests.unit.portfolio._fixtures_b import (
    ALL_FIXTURES,
    PERSONAS,
    fact,
    facts_for,
    features_for,
    load_profile,
)


def test_graph_shape_numbers_are_read_from_the_profile():
    riya = features_for("riya")
    assert (riya.n_domains, riya.n_skills, riya.top, riya.second) == (6, 21, 85, 81)
    assert riya.median == 56.5
    assert riya.n_rich == 5  # frontend 5, ai-llm 5, design 3, backend 3, product 3
    assert riya.has_mastery
    assert riya.source_kinds == ("github", "lovable", "portfolio", "resume")
    assert riya.n_sources == 4

    sana = features_for("sana")
    assert (sana.top, sana.second, sana.median) == (78, 34, 29.0)

    aarav = features_for("aarav")
    assert (aarav.n_domains, aarav.n_skills) == (3, 5)
    assert aarav.shape.sparse


def test_content_counts_come_from_the_facts():
    riya = features_for("riya")
    assert (riya.n_experience, riya.n_projects, riya.n_education) == (6, 4, 1)
    assert (riya.n_achievements, riya.n_clips, riya.n_links, riya.n_quotes) == (2, 0, 2, 1)
    assert features_for("sana").n_clips == 4
    assert features_for("aarav").n_experience == 0


@pytest.mark.parametrize(
    ("name", "stage", "lean"),
    [
        ("riya", "early", "design"),
        ("aarav", "student", "hardware"),
        ("meera", "switcher", "data"),
        ("vikram", "senior", "systems"),
        ("sana", "early", "words"),
    ],
)
def test_the_five_personas_read_correctly_without_a_first_read(name, stage, lean):
    f = features_for(name)
    assert (f.stage, f.lean) == (stage, lean)
    assert not f.from_first_read
    assert f.career_family is None


def test_career_flags_for_the_personas():
    assert features_for("sana").writing_led
    assert not features_for("riya").writing_led
    assert not any(features_for(n).project_led for n in PERSONAS)
    assert all(features_for(n).has_public_proof for n in PERSONAS)
    assert features_for("riya").measured and features_for("vikram").measured
    assert features_for("vikram").leads and features_for("riya").leads
    assert not features_for("aarav").leads


def test_years_come_from_the_profile_or_else_from_the_dated_facts():
    assert features_for("riya").years_experience == 2.4
    assert features_for("vikram").years_experience == 11
    meera = features_for(
        "meera"
    )  # the profile has no yearsExperience: Jun 2018 to the profile date (Oct 2026)
    assert 8.0 <= meera.years_experience <= 8.6


def test_a_dated_history_with_no_shared_title_words_and_wording_reads_as_a_switch():
    profile = load_profile("meera")
    facts = facts_for("meera")
    assert compute_features(profile, facts).stage == "switcher"
    # the wording alone is not enough (one job) and neither are unrelated titles with no wording
    assert compute_features(profile, facts[:1]).stage != "switcher"
    plain = profile.model_copy(
        update={"person": profile.person.model_copy(update={"summary": "Analyst.", "headline": "Analyst"})}
    )
    assert compute_features(plain, facts).stage == "switcher"  # mismatch (0.4) + 5 earlier years (0.3)
    related = [
        fact("exp-1", "experience", "Junior Data Analyst", start="2025-02", current=True),
        fact("exp-2", "experience", "Data Analyst Intern", start="2023-01", end="2024-12"),
    ]
    assert compute_features(plain, related).stage != "switcher"


def test_without_facts_the_profile_still_gives_a_stage():
    aarav = compute_features(load_profile("aarav"), [], None)
    assert aarav.stage == "student" and aarav.n_experience == 0
    riya = compute_features(load_profile("riya"), [], None)
    assert riya.stage == "early" and riya.years_experience == 2.4


# ───────────────────────── Jev's first read ─────────────────────────


@pytest.mark.parametrize(
    ("answer", "stage"),
    [
        ("student_or_new_grad", "student"),
        ("early_career", "early"),
        ("established", "mid"),
        ("senior_or_lead", "senior"),
        ("executive_or_founder", "senior"),
        ("career_changer", "switcher"),
    ],
)
def test_the_first_read_stage_wins_over_the_heuristics(answer, stage):
    f = features_for("riya", {"career_stage": answer})
    assert f.stage == stage
    assert f.from_first_read


def test_an_unclear_stage_falls_back_to_the_heuristics():
    for answer in ("unclear", None, "", 3, ["senior_or_lead"], "not_a_stage"):
        f = features_for("riya", {"career_stage": answer})
        assert f.stage == "early", answer
        assert not f.from_first_read


def test_a_field_switch_answer_makes_a_switcher_only_when_the_stage_is_open():
    assert features_for("riya", {"progression_switch": True}).stage == "switcher"
    assert features_for("riya", {"progression_switch": False}).stage == "early"
    assert (
        features_for("riya", {"career_stage": "senior_or_lead", "progression_switch": True}).stage == "senior"
    )
    # a switch needs a work history to switch from
    assert compute_features(load_profile("aarav"), [], {"progression_switch": True}).stage == "student"


def test_the_first_read_career_family_steers_the_lean():
    assert features_for("riya", {"career_family": "data_ai"}).lean == "data"
    assert features_for("riya", {"career_family": "creative_media"}).lean == "words"
    assert features_for("riya", {"career_family": "trades_skilled_services"}).lean == "hardware"
    assert features_for("riya", {"career_family": "data_ai"}).career_family == "data_ai"
    # software engineering is split by what the graph is made of
    assert features_for("riya", {"career_family": "software_engineering"}).lean == "design"
    assert features_for("vikram", {"career_family": "software_engineering"}).lean == "systems"
    # unclear answers change nothing
    assert features_for("riya", {"career_family": "other_or_unclear"}) == features_for("riya", None)


def test_the_first_read_flags_win_when_they_are_booleans():
    base = features_for("riya")
    assert base.has_public_proof and base.measured and base.leads
    f = features_for("riya", {"public_proof": False, "measured_outcomes": False, "leads_or_mentors": False})
    assert not (f.has_public_proof or f.measured or f.leads)
    quiet = compute_features(load_profile("stress-missing"), [], {"public_proof": True})
    assert quiet.has_public_proof


def test_a_malformed_first_read_is_ignored():
    junk = {
        "career_stage": 7,
        "career_family": ["x"],
        "public_proof": "yes",
        "measured_outcomes": None,
        "leads_or_mentors": {},
        "progression_switch": 1,
        "depth": "deep",
        "confidence": "high",
    }
    for name in PERSONAS:
        assert features_for(name, junk) == features_for(name, None)
    assert features_for("riya", {}) == features_for("riya", None)


# ───────────────────────── every fixture ─────────────────────────


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_fixture_has_sane_features(name):
    f = features_for(name)
    assert f.stage in STAGES
    assert f.n_domains == len(load_profile(name).domains)
    assert f.top >= f.second >= 0
    assert 0 <= f.median <= 100
    assert f.n_rich <= f.n_domains
    assert f.years_experience >= 0


def test_defaults_describe_a_typical_profile_and_replace_works():
    f = Features()
    assert not f.shape.sparse
    assert replace(f, n_domains=3).shape.sparse
    assert f.count("experience") == 2 and f.count("clip") == 0
