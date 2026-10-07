from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from tayyari.contract import (
    SOURCE_KINDS,
    ChatOrigin,
    Evidence,
    Health,
    HealthModels,
    Person,
    Profile,
    ReaderState,
    RoleRequirement,
    SourceSummary,
    is_source_kind,
)

try:  # the legacy app was deleted in Wave 4; parity with it was proven at the wave3b tag
    from tayyari.legacy import types as legacy_types
except ImportError:
    legacy_types = None
needs_legacy = pytest.mark.skipif(
    legacy_types is None, reason="legacy app deleted; parity proven at tag wave3b"
)


def test_sample_profile_parses(sample_dict: dict[str, Any]) -> None:
    profile = Profile.from_dict(sample_dict)
    assert profile.id.startswith("sample-")
    assert profile.person.name == "Riya Kapoor"
    assert profile.person.yearsExperience == 2.4
    assert profile.sources[0].kind == "resume"
    assert len(profile.skills) > 4
    assert len(profile.roles) >= 1
    assert all(0 <= s.score <= 100 for s in profile.skills)


def test_sample_profile_round_trips_to_an_equal_dict(sample_dict: dict[str, Any]) -> None:
    original = copy.deepcopy(sample_dict)
    assert Profile.from_dict(sample_dict).to_dict() == original


def test_sample_profile_round_trips_through_json_text(sample_dict: dict[str, Any]) -> None:
    profile = Profile.from_dict(sample_dict)
    text = json.dumps(profile.to_dict(), ensure_ascii=False)
    assert Profile.from_dict(json.loads(text)) == profile


@needs_legacy
def test_to_dict_matches_the_legacy_models(sample_dict: dict[str, Any]) -> None:
    assert (
        Profile.from_dict(sample_dict).to_dict() == legacy_types.Profile.model_validate(sample_dict).to_dict()
    )


def test_to_dict_is_json_serialisable(sample_dict: dict[str, Any]) -> None:
    json.dumps(Profile.from_dict(sample_dict).to_dict())


def test_unknown_keys_are_ignored_at_every_level(sample_dict: dict[str, Any]) -> None:
    original = copy.deepcopy(sample_dict)
    sample_dict["surprise"] = 1
    sample_dict["person"]["surprise"] = 2
    sample_dict["skills"][0]["evidence"][0]["surprise"] = 3
    sample_dict["models"]["surprise"] = 4
    assert Profile.from_dict(sample_dict).to_dict() == original


def test_optional_none_fields_are_omitted(sample_dict: dict[str, Any]) -> None:
    sample_dict["goal"] = None
    sample_dict["person"]["location"] = None
    sample_dict["person"]["yearsExperience"] = None
    out = Profile.from_dict(sample_dict).to_dict()
    assert "goal" not in out
    assert "location" not in out["person"]
    assert "yearsExperience" not in out["person"]


def test_unset_optional_fields_are_omitted() -> None:
    assert Person(name="A", headline="h", currentRole="r", summary="s").to_dict() == {
        "name": "A",
        "headline": "h",
        "currentRole": "r",
        "summary": "s",
    }
    assert RoleRequirement(name="n", have=True, current=1, target=2).to_dict() == {
        "name": "n",
        "have": True,
        "current": 1,
        "target": 2,
    }
    assert ChatOrigin(kind="free", title="t", caption="c").to_dict() == {
        "kind": "free",
        "title": "t",
        "caption": "c",
    }
    assert SourceSummary(
        id="resume", kind="resume", label="Resume", sub="", summary="", notes=[], ok=True
    ).to_dict() == {
        "id": "resume",
        "kind": "resume",
        "label": "Resume",
        "sub": "",
        "summary": "",
        "notes": [],
        "ok": True,
    }


def test_empty_lists_and_falsy_values_are_kept() -> None:
    out = SourceSummary(
        id="x", kind="notes", label="Notes", sub="", summary="", notes=[], ok=False, error=""
    ).to_dict()
    assert out["notes"] == []
    assert out["ok"] is False
    assert out["error"] == ""


def test_required_fields_are_enforced() -> None:
    with pytest.raises(ValidationError):
        Evidence.from_dict({"text": "no source id"})
    with pytest.raises(ValidationError):
        ChatOrigin.from_dict({"kind": "free"})  # title and caption are required by the contract
    with pytest.raises(ValidationError):
        Profile.from_dict({})


def test_literal_values_are_enforced() -> None:
    with pytest.raises(ValidationError):
        ChatOrigin.from_dict({"kind": "banana", "title": "", "caption": ""})
    with pytest.raises(ValidationError):
        ReaderState.from_dict(
            {
                "sourceId": "resume",
                "kind": "resume",
                "src": "cv.pdf",
                "kindLabel": "Resume",
                "agent": "Resume reader",
                "status": "paused",
                "line": "",
                "progress": 0,
            }
        )


@needs_legacy
def test_source_kinds_match_the_legacy_tuple_and_guard() -> None:
    assert SOURCE_KINDS == legacy_types.SOURCE_KINDS
    assert is_source_kind("github")
    assert not is_source_kind("sonnet")
    assert not is_source_kind("")


class TestHealth:
    def test_storage_is_omitted_when_unknown(self) -> None:
        health = Health(ok=True, models=HealthModels(reader="r", synth="s", chat="c"), hasSample=True)
        assert health.to_dict() == {
            "ok": True,
            "models": {"reader": "r", "synth": "s", "chat": "c"},
            "hasSample": True,
        }

    @pytest.mark.parametrize("storage", ["postgres", "ephemeral"])
    def test_storage_is_reported(self, storage: str) -> None:
        out = Health.from_dict(
            {
                "ok": True,
                "models": {"reader": "r", "synth": "s", "chat": "c"},
                "hasSample": False,
                "storage": storage,
            }
        ).to_dict()
        assert out["storage"] == storage

    def test_storage_rejects_other_values(self) -> None:
        with pytest.raises(ValidationError):
            Health.from_dict(
                {
                    "ok": True,
                    "models": {"reader": "r", "synth": "s", "chat": "c"},
                    "hasSample": False,
                    "storage": "disk",
                }
            )

    def test_the_old_shape_without_storage_still_parses(self) -> None:
        health = Health.from_dict(
            {"ok": True, "models": {"reader": "r", "synth": "s", "chat": "c"}, "hasSample": True}
        )
        assert health.storage is None
