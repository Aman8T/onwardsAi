"""The app lifespan: startup builds the container (migrations, sample seed), exposes it, and shutdown closes it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic import SecretStr
from sqlalchemy import text

import tayyari
from tayyari import container as container_module
from tayyari.api import lifespan as lifespan_module
from tayyari.api.app import create_app
from tayyari.container import Container, ContainerOverrides
from tayyari.core.settings import Settings
from tayyari.storage.migrate import head_revision
from tests.unit.api.helpers import RecordingLog
from tests.unit.wiring.support import (
    BASE_URL,
    FakeAnalysis,
    FakeChat,
    make_overrides,
    roomy_guard,
    running_app,
    spy_all,
)

MakeSettings = Callable[..., Settings]


async def test_startup_migrates_seeds_and_publishes_the_container(make_settings: MakeSettings) -> None:
    settings = make_settings(guard=roomy_guard())
    async with running_app(settings) as h:
        state = h.app.state
        assert isinstance(state.container, Container)
        assert state.container is h.container
        assert state.settings is settings
        assert state.clock is h.container.clock
        assert isinstance(state.ip_hash_key, bytes)
        assert state.ip_hash_key == h.container.ip_hash_key
        assert len(state.ip_hash_key) == 32

        assert h.container.db is not None
        async with h.container.db.session() as s:
            version = (await s.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        assert version == head_revision()
        assert await h.container.repos.profiles.get_profile(h.sample_id()) is not None


async def test_the_sample_can_be_chatted_about_right_after_startup(make_settings: MakeSettings) -> None:
    """The seeded sample comes back with its context (the sources the chat reads), in one round trip."""
    async with running_app(make_settings()) as h:
        stored = await h.container.repos.profiles.get_for_chat(h.sample_id())
    assert stored is not None
    _profile, context = stored
    assert context.sources
    assert context.readers


async def test_a_restart_keeps_the_ip_hash_key_so_a_client_hashes_the_same(
    make_settings: MakeSettings,
) -> None:
    settings = make_settings(guard=roomy_guard())
    hashes: list[str | None] = []
    for _ in range(2):  # two "processes" on the same database
        async with running_app(settings) as h:
            await h.analyze_sample(**{"cf-connecting-ip": "203.0.113.50"})
            hashes.append(h.analysis.requests[-1][1].ip_hash)
    assert hashes[0] is not None
    assert hashes[0] == hashes[1]


async def test_startup_logs_one_summary_line_without_secrets(
    make_settings: MakeSettings, lifespan_log: RecordingLog
) -> None:
    settings = make_settings(
        anthropic_api_key=SecretStr("sk-ant-very-secret"), jev_api_key=SecretStr("jev-secret")
    )
    async with running_app(settings):
        pass
    (facts,) = [fields for _level, event, fields in lifespan_log.records if event == "startup"]
    assert facts["version"] == tayyari.__version__
    assert facts["storage"] == "sqlite"
    assert facts["has_sample"] is True
    assert isinstance(facts["static_present"], bool)
    assert facts["anthropic_key"] is True
    assert facts["jev_key"] is True
    assert facts["jev_active"] is True  # enabled, and a key is present
    assert "sk-ant-very-secret" not in repr(lifespan_log.records)
    assert "jev-secret" not in repr(lifespan_log.records)
    assert "anthropic_key_missing" not in lifespan_log.events()
    assert "shutdown" in lifespan_log.events()


async def test_startup_warns_when_the_anthropic_key_is_missing(
    make_settings: MakeSettings, lifespan_log: RecordingLog
) -> None:
    async with running_app(make_settings()):
        pass
    assert "anthropic_key_missing" in lifespan_log.events("warning")


async def test_shutdown_closes_the_container(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = spy_all(monkeypatch)
    overrides, _ = make_overrides()
    app = create_app(make_settings(), overrides=overrides)
    async with LifespanManager(app):
        assert calls == []  # nothing is closed while the app serves
    assert [label for label, _, _ in calls] == [
        "runner.drain",
        "recorder.aclose",
        "jev.aclose",
        "spend_guard.aclose",
        "db.dispose",
    ]


async def test_startup_failure_raises_and_closes_what_was_built(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database that cannot be migrated stops the app from starting: the process never reports ready."""
    calls = spy_all(monkeypatch)

    async def failing(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(container_module, "migrate_to_head", failing)
    overrides, _ = make_overrides()
    app = create_app(make_settings(), overrides=overrides)
    with pytest.raises(RuntimeError, match="migration failed"):
        async with LifespanManager(app):
            pytest.fail("the app must not start")  # pragma: no cover
    assert [label for label, _, _ in calls] == ["db.dispose"]
    assert not hasattr(app.state, "container")


async def test_the_container_is_closed_even_when_the_server_stops_after_a_crash(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = spy_all(monkeypatch)
    overrides, _ = make_overrides()
    app = create_app(make_settings(), overrides=overrides)
    with pytest.raises(ZeroDivisionError):
        async with LifespanManager(app):
            raise ZeroDivisionError
    assert "db.dispose" in [label for label, _, _ in calls]


async def test_get_container_is_a_503_before_the_lifespan_ran(make_settings: MakeSettings) -> None:
    app = create_app(make_settings(), overrides=ContainerOverrides(analysis=FakeAnalysis(), chat=FakeChat()))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await client.get("/api/sample-profile")
    assert response.status_code == 503
    assert response.json() == {"error": lifespan_module.STARTING_MESSAGE}
