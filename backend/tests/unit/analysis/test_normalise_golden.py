"""`clean_reader`, `to_profile` and `activity_lines` against the baselines captured from the legacy code.

The goldens live in tests/golden/legacy. The legacy `to_profile` inputs are not stored there (only a summary), so
they were extracted once from tools/capture_golden.py into tests/unit/analysis/fixtures/to_profile_inputs.json.

Intentional differences from the legacy output, all in `to_profile`:
- ``id`` is a 128-bit random hex id (``sample-`` prefixed for sample runs), not a base-36 timestamp plus 24 bits;
- ``createdAt`` is the injected time instead of the wall clock.
Everything else, nested ids included, must be identical.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tayyari.analysis.normalise import ReadResult, activity_lines, clean_reader, to_profile
from tayyari.contract import ModelsUsed, Profile
from tayyari.ingest import PlannedSource, SourceDoc
from tayyari.sample import load_sample_sources

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "legacy"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MODELS = ModelsUsed(reader="claude-haiku-4-5", synth="claude-sonnet-5-5")


def _cases(name: str) -> list[dict[str, Any]]:
    data = json.loads((GOLDEN / name).read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = data["cases"]
    return cases


def _id(case: dict[str, Any]) -> str:
    return str(case["name"])


# ───────────────────────── clean_reader ─────────────────────────


@pytest.mark.parametrize("case", _cases("clean_reader.json"), ids=_id)
def test_clean_reader_matches_legacy(case: dict[str, Any]) -> None:
    assert clean_reader(case["input"]) == case["output"]


def test_clean_reader_golden_covers_the_interesting_inputs() -> None:
    names = {c["name"] for c in _cases("clean_reader.json")}
    assert {"normal", "overlong_strings", "too_many_items", "bad_strength", "wrong_types", "unicode"} <= names


# ───────────────────────── activity_lines ─────────────────────────


@pytest.mark.parametrize("case", _cases("activity_lines.json"), ids=_id)
def test_activity_lines_match_legacy(case: dict[str, Any]) -> None:
    sources = {s.id: s for s in load_sample_sources()}
    assert activity_lines(sources[case["input"]["source_id"]]) == case["output"]


def test_activity_lines_for_kinds_the_golden_does_not_cover() -> None:
    def doc(kind: str, **kw: Any) -> SourceDoc:
        return SourceDoc(id=kind, kind=kind, label=kind.title(), src="some.src", kind_label=kind, **kw)

    assert activity_lines(doc("linkedin")) == [
        "Reading headline and about",
        "Reading experience entries",
        "Checking featured work",
    ]
    assert activity_lines(doc("notes")) == ["Reading your notes", "Separating claims from goals"]
    assert activity_lines(doc("link", text="- h2: Ten posts on testing\n")) == [
        "Reading · Ten posts on testing",
        "Extracting evidence · some.src",
    ]
    assert activity_lines(doc("github")) == [
        "Reading repositories",
        "Scanning READMEs",
        "Checking tests and CI",
    ]
    assert activity_lines(doc("resume")) == [
        "Weighing impact in each bullet",
        "Separating listed skills from used ones",
    ]
    assert activity_lines(doc("portfolio")) == ["Looking for outcomes and numbers"]


# ───────────────────────── to_profile ─────────────────────────


def _result(item: dict[str, Any]) -> ReadResult:
    planned = PlannedSource(**item["planned"], ingest=None)  # type: ignore[arg-type]
    raw = SourceDoc(**item["raw"]) if item["raw"] is not None else None
    return ReadResult(planned=planned, raw=raw, reader=item["reader"], error=item["error"])


_INPUTS = {
    c["name"]: c for c in json.loads((FIXTURES / "to_profile_inputs.json").read_text(encoding="utf-8"))
}
_FROZEN = datetime(2026, 10, 7, 17, 9, 12, 976000, tzinfo=UTC)


def _convert(case: dict[str, Any]) -> Profile:
    inp = _INPUTS[case["name"]]
    return to_profile(
        inp["synth"],
        [_result(r) for r in inp["results"]],
        inp["goal"],
        inp["sample"],
        models=MODELS,
        created_at=_FROZEN,
    )


@pytest.mark.parametrize("case", _cases("to_profile.json"), ids=_id)
def test_to_profile_matches_legacy(case: dict[str, Any]) -> None:
    got = _convert(case).to_dict()
    expected = dict(case["output"])
    assert got.pop("id") and expected.pop("id")
    assert got.pop("createdAt") == "2026-10-07T17:09:12.976Z"
    expected.pop("createdAt")
    assert got == expected


def test_to_profile_goldens_and_inputs_line_up() -> None:
    assert [c["name"] for c in _cases("to_profile.json")] == list(_INPUTS)


@pytest.mark.parametrize("case", _cases("to_profile.json"), ids=_id)
def test_to_profile_output_validates_as_profile(case: dict[str, Any]) -> None:
    profile = _convert(case)
    assert Profile.from_dict(profile.to_dict()) == profile


def test_profile_ids_are_random_hex_and_sample_ids_are_prefixed() -> None:
    plain = _convert({"name": "goal_none"})
    sample = _convert({"name": "sample_mode"})
    assert re.fullmatch(r"[0-9a-f]{32}", plain.id)
    assert re.fullmatch(r"sample-[0-9a-f]{32}", sample.id)
    assert plain.id != _convert({"name": "goal_none"}).id


def test_an_explicit_profile_id_and_time_are_used() -> None:
    inp = _INPUTS["goal_set"]
    profile = to_profile(
        inp["synth"],
        [],
        "A goal",
        False,
        models=MODELS,
        profile_id="abc123",
        created_at=datetime(2026, 1, 2, 3, 4, 5, 6000, tzinfo=UTC),
    )
    assert profile.id == "abc123"
    assert profile.createdAt == "2026-01-02T03:04:05.006Z"
    assert profile.goal == "A goal"
    assert profile.models == MODELS


def test_created_at_defaults_to_now_and_converts_other_zones() -> None:
    from datetime import timedelta, timezone

    inp = _INPUTS["goal_set"]
    zone = timezone(timedelta(hours=5, minutes=30))
    profile = to_profile(
        inp["synth"],
        [],
        None,
        False,
        models=MODELS,
        created_at=datetime(2026, 1, 2, 8, 34, 5, tzinfo=zone),
    )
    assert profile.createdAt == "2026-01-02T03:04:05.000Z"
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z",
        to_profile(inp["synth"], [], None, False, models=MODELS).createdAt,
    )
