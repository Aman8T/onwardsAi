"""Fixtures for the pack tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tayyari.core.settings import Settings
from tests.unit.decisions.packs.support import PackHarness, build_pack_harness


@pytest.fixture
def harness(make_settings: Callable[..., Settings]) -> Callable[..., PackHarness]:
    """``harness(answers, overrides=..., slow_jev_s=...)``: a `PackHarness` on the real ``defaults.toml``."""

    def build(*args: Any, **kwargs: Any) -> PackHarness:
        return build_pack_harness(make_settings, *args, **kwargs)

    return build
