"""Fixtures for the decisions tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tayyari.contract import Profile

SAMPLE_PROFILE_PATH = Path(__file__).resolve().parents[2] / "data" / "sample-profile.json"


@pytest.fixture(scope="session")
def sample_profile() -> Profile:
    """The frozen sample profile as a validated `Profile` (a facade input for the shadow tests)."""
    return Profile.model_validate(json.loads(SAMPLE_PROFILE_PATH.read_text(encoding="utf-8")))
