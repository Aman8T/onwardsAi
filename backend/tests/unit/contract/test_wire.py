from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from tayyari.contract import Profile, profile_from_json, profile_to_json


def test_profile_from_json_returns_a_profile(sample_dict: dict[str, Any]) -> None:
    profile = profile_from_json(sample_dict)
    assert isinstance(profile, Profile)
    assert profile.id == sample_dict["id"]


def test_the_sample_profile_round_trips_to_an_equal_dict(sample_dict: dict[str, Any]) -> None:
    original = copy.deepcopy(sample_dict)
    assert profile_to_json(profile_from_json(sample_dict)) == original


def test_profile_to_json_is_plain_json(sample_dict: dict[str, Any]) -> None:
    out = profile_to_json(profile_from_json(sample_dict))
    assert json.loads(json.dumps(out)) == out
    assert type(out) is dict


def test_profile_to_json_omits_unset_optionals(sample_dict: dict[str, Any]) -> None:
    del sample_dict["goal"]
    for source in sample_dict["sources"]:
        source.pop("url", None)
        source.pop("error", None)
    out = profile_to_json(profile_from_json(sample_dict))
    assert "goal" not in out
    assert all("url" not in s and "error" not in s for s in out["sources"])


def test_profile_from_json_accepts_any_mapping(sample_dict: dict[str, Any]) -> None:
    from types import MappingProxyType

    assert profile_from_json(MappingProxyType(sample_dict)) == profile_from_json(sample_dict)


def test_profile_from_json_does_not_modify_its_input(sample_dict: dict[str, Any]) -> None:
    before = copy.deepcopy(sample_dict)
    profile_from_json(sample_dict)
    assert sample_dict == before


def test_profile_from_json_rejects_a_malformed_profile(sample_dict: dict[str, Any]) -> None:
    del sample_dict["person"]
    with pytest.raises(ValidationError):
        profile_from_json(sample_dict)


def test_profile_from_json_rejects_a_bad_literal(sample_dict: dict[str, Any]) -> None:
    sample_dict["sources"][0]["kind"] = "facebook"
    with pytest.raises(ValidationError):
        profile_from_json(sample_dict)
