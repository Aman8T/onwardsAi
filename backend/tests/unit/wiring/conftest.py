"""Fixtures for the wiring tests."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from tayyari import container as container_module
from tayyari.api import lifespan as lifespan_module
from tests.unit.api.helpers import RecordingLog


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """`Container.build` installs the structlog pipeline and a root handler; put the process back afterwards,
    so the other test areas see the logging set-up they started with."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    config: dict[str, Any] = dict(structlog.get_config())
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.configure(**config)


@pytest.fixture(autouse=True)
def _no_background_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retention task waits this long before it first runs; tests that want it set the delay to 0."""
    monkeypatch.setattr(container_module, "MAINTENANCE_DELAY_S", 3600.0)


@pytest.fixture
def container_log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    """Records what the container logs (``(level, event, fields)``) instead of writing it."""
    recording = RecordingLog()
    monkeypatch.setattr(container_module, "log", recording)
    return recording


@pytest.fixture
def lifespan_log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    """Records what the lifespan logs."""
    recording = RecordingLog()
    monkeypatch.setattr(lifespan_module, "log", recording)
    return recording
