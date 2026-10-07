"""Fixtures and the ``integration`` marker for the end-to-end tests.

These tests start the real app (`create_app`) through ``asgi-lifespan``, with a real SQLite file in the test's
temp folder, the real services and a real `DecisionRunner`; only the network edges are faked (Claude, Jev and the
web/GitHub fetchers). They run by default with a plain ``uv run pytest``; the marker only lets you select
(``-m integration``) or skip (``-m "not integration"``) them. It is added here, to every test below this folder,
so ``pyproject.toml`` stays untouched.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog

from tayyari import container as container_module
from tayyari.core.settings import Settings
from tests.integration.support import FakeWeb, install_fake_web, quiet_audits, roomy_guard

_HERE = Path(__file__).resolve().parent


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: end-to-end tests of the real app over a real SQLite file (only the network edges are faked)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if _HERE in item.path.resolve().parents:
            item.add_marker(pytest.mark.integration)


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
    """The retention task waits this long before its first run; no integration test wants it."""
    monkeypatch.setattr(container_module, "MAINTENANCE_DELAY_S", 3600.0)


@pytest.fixture
def build_settings(make_settings: Callable[..., Settings]) -> Callable[..., Settings]:
    """Settings for an end-to-end run: roomy limits (nothing trips by accident), no pacing, a data folder under
    ``tmp_path`` and no random Claude audits of Jev answers (they would make call counts non-deterministic).
    Pass any `Settings` field to override: ``build_settings(guard=roomy_guard(analyze_per_minute=2))``."""

    def build(**overrides: Any) -> Settings:
        values: dict[str, Any] = {"guard": roomy_guard(), "decision_overrides": quiet_audits()}
        values.update(overrides)
        return make_settings(**values)

    return build


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    """The link and GitHub fetchers replaced by in-memory pages: nothing here touches the network."""
    return install_fake_web(monkeypatch)
