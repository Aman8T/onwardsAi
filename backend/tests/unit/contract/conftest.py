"""Fixtures for the contract tests: the frozen sample profile (a copy of tayyari/sample/sample-profile.json)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

SAMPLE_PROFILE_PATH = Path(__file__).resolve().parents[2] / "data" / "sample-profile.json"


@pytest.fixture(scope="session")
def _sample_profile_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SAMPLE_PROFILE_PATH.read_text(encoding="utf-8"))
    return data


@pytest.fixture
def sample_dict(_sample_profile_json: dict[str, Any]) -> dict[str, Any]:
    """The sample profile as parsed JSON; a fresh deep copy per test, so a test may edit it."""
    return copy.deepcopy(_sample_profile_json)
