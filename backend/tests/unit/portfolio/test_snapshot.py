"""Snapshot helpers: the frozen graph, proof stats from source summaries, the person and the open-to lines."""

from __future__ import annotations

from datetime import date

import pytest

from tayyari.contract.models import Evidence, Role, Skill
from tayyari.contract.portfolio import GraphSnapshot
from tayyari.portfolio.snapshot import build_person, graph_snapshot, monogram, open_to, proof_stats
from tests.unit.portfolio._fixtures_b import ALL_FIXTURES, AS_OF, load_context, load_profile


def with_name(profile, name):
    return profile.model_copy(update={"person": profile.person.model_copy(update={"name": name})})


# ───────────────────────── graph snapshot ─────────────────────────


def test_snapshot_copies_the_graph_exactly():
    profile = load_profile("riya")
    snap = graph_snapshot(profile, AS_OF)
    assert snap.asOf == "2026-10-08"
    assert [(d.id, d.name, d.short, d.score) for d in snap.domains] == [
        (d.id, d.name, d.short, d.score) for d in profile.domains
    ]
    assert snap.skillCount == 21 == len(profile.skills)
    assert snap.mastery is not None and (snap.mastery.score, snap.mastery.role) == (78, "Frontend Engineer")
    assert snap.sources == ["Resume", "GitHub", "Lovable", "Portfolio"]
    assert GraphSnapshot.model_validate(snap.model_dump()) == snap


def test_skills_are_strongest_first_with_the_first_evidence_line():
    profile = load_profile("riya")
    snap = graph_snapshot(profile, AS_OF)
    for domain in snap.domains:
        scores = [s.score for s in domain.skills]
        assert scores == sorted(scores, reverse=True)
        source = {s.id: s for s in profile.skills if s.domainId == domain.id}
        assert {s.id for s in domain.skills} == set(source)
        for s in domain.skills:
            ev = source[s.id].evidence
            assert s.evidence == (ev[0].text if ev else None)
    assert sum(len(d.skills) for d in snap.domains) == len(profile.skills)


def test_equal_scores_keep_the_profile_order_and_long_evidence_is_cut():
    profile = load_profile("aarav")
    long = "x" * 40 + " " + "word " * 80
    skills = [
        Skill(
            id="a",
            name="A",
            domainId="python",
            score=50,
            evidence=[Evidence(sourceId="resume", text=long)],
            unlocks=[],
        ),
        Skill(id="b", name="B", domainId="python", score=50, evidence=[], unlocks=[]),
        Skill(id="c", name="C", domainId="python", score=60, evidence=[], unlocks=[]),
    ]
    snap = graph_snapshot(profile.model_copy(update={"skills": skills}), AS_OF)
    python = next(d for d in snap.domains if d.id == "python")
    assert [s.id for s in python.skills] == ["c", "a", "b"]
    assert python.skills[1].evidence is not None and len(python.skills[1].evidence) <= 220
    assert python.skills[2].evidence is None
    assert snap.skillCount == 3


def test_only_sources_that_were_read_are_listed_and_private_text_stays_out():
    scanned = graph_snapshot(load_profile("stress-scanned"), date(2026, 1, 2))
    assert scanned.sources == ["GitHub"] and scanned.asOf == "2026-01-02"
    profile = load_profile("vikram")
    dump = graph_snapshot(profile, AS_OF).model_dump_json()
    assert profile.mastery.insight not in dump
    assert all(r.why not in dump for r in profile.roles)
    assert all(n not in dump for s in profile.sources for n in s.notes)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_fixture_snapshots_with_exact_numbers(name):
    profile = load_profile(name)
    snap = graph_snapshot(profile, AS_OF)
    assert [(d.name, d.score) for d in snap.domains] == [(d.name, d.score) for d in profile.domains]
    assert snap.skillCount == len(profile.skills)


# ───────────────────────── proof stats ─────────────────────────


def stats(name):
    return [(s.value, s.label, s.sourceId) for s in proof_stats(load_profile(name), load_context(name))]


def test_riya_gets_four_non_resume_counts_stars_first():
    got = stats("riya")
    assert len(got) == 4
    assert got[0] == ("389", "GitHub stars", "github")
    assert ("34", "GitHub repos", "github") in got
    assert ("5", "case studies", "portfolio-1") in got
    assert all(sid != "resume" for _, _, sid in got)


def test_a_student_gets_the_github_counts_then_a_resume_count():
    got = stats("aarav")
    assert got[:3] == [
        ("3", "GitHub stars", "github"),
        ("2", "GitHub repos", "github"),
        ("2", "GitHub languages", "github"),
    ]
    assert got[3] == ("1", "project", "resume")  # "0 roles" and "1 page" are not stats
    assert len(got) == 4


def test_labels_name_the_platform_unless_the_source_label_is_generic():
    sana = stats("sana")
    assert ("12", "clips", "portfolio-1") in sana  # "Personal site" is generic
    assert ("40+", "Substack posts", "link-1") in sana
    meera = stats("meera")
    assert ("2", "Tableau Public dashboards", "portfolio-1") in meera
    assert ("3", "Medium posts", "link-1") in meera


def test_a_count_after_its_name_is_read_too():
    vikram = stats("vikram")
    assert ("2", "Conference talks", "link-1") in vikram
    assert ("152", "GitHub stars", "github") in vikram


def test_resume_counts_are_used_only_when_there_is_room_and_zero_or_pages_never():
    assert stats("stress-missing") == []  # "0 projects · 0 roles · 1 page"
    two = stats("stress-two-domains")  # "0 projects · 1 role · 1 page"
    assert two == [("1", "role", "resume")]
    assert stats("stress-scanned")[0][1] == "GitHub stars"  # the unreadable resume contributes nothing
    assert all(sid == "github" for _, _, sid in stats("stress-scanned"))


def test_stats_are_capped_and_deduplicated():
    for name in ALL_FIXTURES:
        got = stats(name)
        assert len(got) <= 4
        assert len({(v, label.lower()) for v, label, _ in got}) == len(got)


def test_the_reader_summary_fills_in_a_missing_sub_line():
    profile = load_profile("sana")
    ctx = load_context("sana")
    sources = [s.model_copy(update={"sub": ""}) if s.id == "link-1" else s for s in profile.sources]
    ctx.readers["link-1"] = {"sub": "Newsletter · 18,000 subscribers"}
    got = proof_stats(profile.model_copy(update={"sources": sources}), ctx)
    assert any(s.value == "18,000" and s.label == "Substack subscribers" for s in got)
    assert got[0].label == "Substack subscribers"  # subscribers rank above clips


# ───────────────────────── person ─────────────────────────


def test_build_person_copies_the_profile():
    profile = load_profile("riya")
    person = build_person(profile)
    assert (person.name, person.role, person.monogram) == ("Riya Kapoor", "Frontend Engineer", "RK")
    assert person.headline == profile.person.headline
    assert person.location == "Bengaluru, India" and person.yearsExperience == 2.4
    assert build_person(load_profile("stress-missing")).location is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Riya Kapoor", "RK"),
        ("riya kapoor", "RK"),
        ("Prince", "P"),
        ("Mary Jane Watson", "MJ"),
        ("Anne-Marie Smith", "AM"),
        ("  Élodie   Žižek ", "ÉŽ"),
        ("🌟 Riya Kapoor", "RK"),
        ("अनन्या शर्मा", "अ"),
        ("Ananya अनन्या", "A"),
        ("क्रिया शर्मा", "क"),
        ("李小龙", "李"),
        ("", "?"),
        ("---", "-"),
    ],
)
def test_monogram(name, expected):
    assert monogram(name) == expected


def test_the_unicode_fixture_gets_its_first_character():
    person = build_person(load_profile("stress-unicode"))
    assert person.monogram == person.name[0]


def test_non_latin_names_flow_through_build_person():
    assert build_person(with_name(load_profile("riya"), "Aiko 田中")).monogram == "A"


# ───────────────────────── open to ─────────────────────────


def test_open_to_is_the_two_best_aligned_role_titles():
    assert open_to(load_profile("riya")) == ["Design Engineer", "Design Systems Engineer"]
    assert open_to(load_profile("meera")) == ["Learning Analytics Specialist", "Data Analyst"]
    assert open_to(load_profile("stress-two-domains")) == ["Senior QA Tester", "QA Automation Engineer"]


def test_open_to_skips_duplicates_and_handles_no_roles():
    profile = load_profile("riya")
    roles = [
        Role(id="1", title="Data Analyst", alignment=90, eta="now", why="because", requirements=[]),
        Role(id="2", title="data analyst", alignment=80, eta="now", why="because", requirements=[]),
        Role(id="3", title="BI Analyst", alignment=70, eta="now", why="because", requirements=[]),
    ]
    assert open_to(profile.model_copy(update={"roles": roles})) == ["Data Analyst", "BI Analyst"]
    assert open_to(profile.model_copy(update={"roles": []})) == []
