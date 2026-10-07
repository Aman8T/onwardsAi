"""Fixtures for the CLI tests: nothing here touches the network, a real key or the developer's data folder.

The root conftest already hides the environment and the repo's ``.env`` from `Settings`, and provides the
``settings`` / ``make_settings`` fixtures (env ``test``, no pacing, a data folder under ``tmp_path``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """`configure_logging` (run by smoke, ingest-check and `Container.build`) installs the structlog pipeline and
    a root handler; put the process back afterwards so the other test areas see the set-up they started with."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    config: dict[str, Any] = dict(structlog.get_config())
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.configure(**config)


@pytest.fixture(autouse=True)
def _no_utf8_env_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main()` sets PYTHONUTF8 for its children; start every test without it and restore the original after."""
    monkeypatch.setenv("PYTHONUTF8", "placeholder")
    monkeypatch.delenv("PYTHONUTF8")
