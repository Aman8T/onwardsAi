"""The small helpers of `analysis.normalise`, and the parts of `to_profile` the legacy goldens do not reach."""

from __future__ import annotations

import math
from typing import Any

import pytest

from tayyari.analysis.normalise import (
    ReadResult,
    base36,
    clamp,
    content_headings,
    fact_strings,
    is_num,
    quick_detail,
    result_line,
    round1,
    source_kind_or_link,
    split_signal,
    to_profile,
    unique_id,
)
from tayyari.contract import ModelsUsed
from tayyari.ingest import PlannedSource, SourceDoc

MODELS = ModelsUsed(reader="r", synth="s")


# ───────────────────────── numbers ─────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (50, 50),
        (50.4, 50),
        (50.5, 51),  # halves round up, like JavaScript's Math.round
        (-3, 0),
        (140, 100),
        ("72", 72),
        (" 72.6 ", 73),
        ("", 0),
        ("abc", 0),
        (None, 0),
        (math.nan, 0),
        (math.inf, 0),
        (True, 1),
        ([], 0),
    ],
)
def test_clamp_defaults(value: Any, expected: int) -> None:
    assert clamp(value) == expected


def test_clamp_with_bounds_and_fallback() -> None:
    assert clamp(10, 1, 5, 2) == 5
    assert clamp(0, 1, 5, 2) == 1
    assert clamp("x", 1, 5, 2) == 2
    assert clamp(None, 0, 100, 70) == 70


def test_round1_rounds_halves_up() -> None:
    assert round1(2.25) == 2.3
    assert round1(3.0) == 3.0
    assert round1(1.04) == 1.0


@pytest.mark.parametrize("value", [1, 2.5, 0, -1])
def test_is_num_accepts_finite_numbers(value: Any) -> None:
    assert is_num(value)


@pytest.mark.parametrize("value", [True, False, None, "1", math.nan, math.inf, [1]])
def test_is_num_rejects_everything_else(value: Any) -> None:
    assert not is_num(value)


def test_unique_id_numbers_collisions_and_records_the_winner() -> None:
    used = {"you"}
    assert unique_id("react", used) == "react"
    assert unique_id("react", used) == "react-2"
    assert unique_id("react", used) == "react-3"
    assert unique_id("you", used) == "you-2"
    assert used == {"you", "react", "react-2", "react-3", "you-2"}


def test_base36() -> None:
    assert base36(0) == "0"
    assert base36(35) == "z"
    assert base36(36) == "10"
    assert base36(1728304245123) == "m1yzq2df"  # the legacy id of the captured timestamp


# ───────────────────────── facts, headings, signals ─────────────────────────


def test_fact_strings_takes_the_first_usable_list() -> None:
    facts = {"headings": [], "sections": [" A  b ", "", {"title": "Two"}, {"name": "N"}, {"label": "L"}, 5]}
    assert fact_strings(facts, ["headings", "sections"]) == ["A b", "Two", "N", "L"]
    assert fact_strings({"x": "not a list"}, ["x"]) == []
    assert fact_strings({}, ["x"]) == []
    assert fact_strings({"x": [{"full_name": "Full"}]}, ["x"]) == ["Full"]


def test_content_headings_from_page_and_markdown_lines() -> None:
    text = "\n".join(
        [
            "- h1: Checkout, rebuilt",
            '- h2: "Quoted heading"',
            "- h2: ab",  # too short
            "- h3: " + "x" * 80,  # too long
            "## 1. Experience",
            "### Projects",
            "## Page text",  # one of the section names the ingesters add
            "- h2: Checkout, rebuilt",  # duplicate
        ]
    )
    assert content_headings(text) == ["Checkout, rebuilt", "Quoted heading", "Experience", "Projects"]


def test_content_headings_keeps_at_most_ten() -> None:
    text = "\n".join(f"- h2: Heading number {i}" for i in range(15))
    assert len(content_headings(text)) == 10


def test_split_signal() -> None:
    assert split_signal("TypeScript · 17 repos", "fallback") == {"label": "TypeScript", "detail": "17 repos"}
    assert split_signal("Just a label", "github") == {"label": "Just a label", "detail": "github"}
    long = split_signal("L" * 50 + " · " + "d" * 60, "x")
    assert len(long["label"]) <= 32
    assert len(long["detail"]) <= 40


def test_quick_detail() -> None:
    def doc(kind: str) -> SourceDoc:
        return SourceDoc(id=kind, kind=kind, label="Some Label")

    assert [quick_detail(doc(k)) for k in ("resume", "portfolio", "notes")] == [
        "resume",
        "portfolio",
        "notes",
    ]
    assert quick_detail(doc("github")) == "Some Label"


def test_result_line() -> None:
    assert (
        result_line({"sub": "6 roles · 4 projects · 23 skills", "skills": []})
        == "6 roles · 4 projects · 23 skills extracted"
    )
    assert (
        result_line({"sub": "34 repos · 9 languages", "skills": [1] * 18})
        == "34 repos · 9 languages · 18 skills extracted"
    )
    assert result_line({"sub": "", "skills": [1]}) == "1 skill extracted"
    assert result_line({"sub": "a · b · c · d", "skills": [1, 2]}) == "a · b · 2 skills extracted"
    assert result_line({"sub": "a · 3 skills · c · d", "skills": []}) == "a · 3 skills · c extracted"


def test_source_kind_or_link() -> None:
    assert source_kind_or_link("github") == "github"
    assert source_kind_or_link("mystery") == "link"


# ───────────────────────── to_profile details ─────────────────────────


def _planned(sid: str, kind: str = "link", label: str = "Label") -> PlannedSource:
    return PlannedSource(sid, kind, label, f"{label} page", sid, None, None)  # type: ignore[arg-type]


def _ok(sid: str, kind: str = "link", **reader: Any) -> ReadResult:
    planned = _planned(sid, kind)
    return ReadResult(
        planned=planned,
        raw=SourceDoc(id=sid, kind=kind, label=planned.label, src=sid, kind_label=planned.kind_label),
        reader={"sub": "2 things", "summary": "sum", "notes": ["from the reader"], **reader},
    )


BASE_SYNTH: dict[str, Any] = {
    "person": {"name": "P", "currentRole": "R"},
    "mastery": {"score": 50, "insight": "i"},
    "domains": [{"id": "d1", "name": "D1", "short": "D1", "score": 50, "summary": ""}],
    "skills": [],
    "roles": [],
    "sources": [],
}


def test_extra_notes_are_kept_after_the_model_notes_within_the_cap() -> None:
    result = _ok("link-1")
    result.extra_notes = ["Jev: not about one person", "  ", "Jev: second"]
    synth = {**BASE_SYNTH, "sources": [{"id": "link-1", "notes": ["n1", "n2", "n3", "n4"]}]}
    profile = to_profile(synth, [result], None, False, models=MODELS)
    assert profile.sources[0].notes == ["n1", "n2", "Jev: not about one person", "Jev: second"]


def test_extra_notes_without_model_notes() -> None:
    result = _ok("link-1")
    result.extra_notes = ["only the triage note"]
    result.reader = {"sub": "s", "summary": "x", "notes": []}
    profile = to_profile(BASE_SYNTH, [result], None, False, models=MODELS)
    assert profile.sources[0].notes == ["only the triage note"]


def test_a_source_without_extra_notes_is_unchanged() -> None:
    profile = to_profile(BASE_SYNTH, [_ok("link-1")], None, False, models=MODELS)
    assert profile.sources[0].notes == ["from the reader"]
    assert profile.sources[0].ok is True


def test_non_mapping_entries_in_the_synth_lists_are_skipped() -> None:
    synth = {
        **BASE_SYNTH,
        "domains": ["oops", *BASE_SYNTH["domains"]],
        "skills": [
            None,
            {"id": "s1", "name": "S1", "domainId": "d1", "score": 60, "evidence": ["x"], "unlocks": []},
        ],
        "roles": ["oops", {"id": "r1", "title": "Role", "alignment": 50, "requirements": ["x"]}],
        "sources": ["oops"],
    }
    profile = to_profile(synth, [_ok("link-1")], None, False, models=MODELS)
    assert [s.id for s in profile.skills] == ["s1"]
    assert [r.id for r in profile.roles] == ["r1"]
    assert profile.skills[0].evidence == []


def test_a_graph_without_domains_gets_a_core_domain() -> None:
    synth = {**BASE_SYNTH, "domains": [], "mastery": {"score": 61, "insight": ""}}
    profile = to_profile(synth, [], None, False, models=MODELS)
    assert [(d.id, d.name, d.score) for d in profile.domains] == [("core", "Core skills", 61)]


def test_a_failed_source_is_listed_with_its_error_and_a_fallback_sub() -> None:
    planned = _planned("github", "github", "GitHub")
    failed = ReadResult(planned=planned, raw=None, reader=None, error="Rate limited by GitHub")
    profile = to_profile(BASE_SYNTH, [failed], None, False, models=MODELS)
    source = profile.sources[0]
    assert (source.ok, source.error, source.sub) == (False, "Rate limited by GitHub", "Could not be read")
    assert source.summary == "Could not be read: Rate limited by GitHub"


def test_the_fallback_sub_counts_what_the_fetch_found() -> None:
    def sub(meta: dict[str, Any]) -> str:
        planned = _planned("x", "link", "X")
        raw = SourceDoc(id="x", kind="link", label="X", meta=meta, kind_label="X page")
        result = ReadResult(planned=planned, raw=raw, reader=None, error="e")
        return to_profile(BASE_SYNTH, [result], None, False, models=MODELS).sources[0].sub

    assert sub({"repos": 34, "languages": ["a", "b"]}) == "34 repos · 2 languages"
    assert sub({"publicRepos": 1}) == "1 repo"
    assert sub({"pages": 8, "caseStudies": 5}) == "8 pages · 5 case studies"
    assert sub({"pages": 3, "repos": 2}) == "2 repos"
    assert sub({"caseStudies": 1}) == "1 case study"
    assert sub({}) == "Could not be read"
