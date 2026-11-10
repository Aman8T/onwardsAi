"""Fixtures for the resume tests: the storage backends the repo contract runs on (the fake, a SQLite file and memory,
and Postgres when ``TEST_DATABASE_URL`` is set, as in tests/unit/storage)."""

from __future__ import annotations

import pytest


@pytest.fixture(params=["fake", "sqlite_file", "sqlite_memory", "postgres"])
def backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)
