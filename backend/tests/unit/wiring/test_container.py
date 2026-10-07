"""`Container.build` and `Container.aclose`: what gets wired, what policy applies, and that shutdown closes it all."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text

from tayyari import container as container_module
from tayyari.analysis.ports import AnalysisService
from tayyari.analysis.service import PipelineAnalysisService
from tayyari.chat.ports import ChatService
from tayyari.chat.service import CoachChatService
from tayyari.container import IP_HASH_KEY, Container, ContainerOverrides
from tayyari.core.settings import JevSettings, Settings
from tayyari.decisions.packs.service_impl import JevDecisionService
from tayyari.decisions.recorder import DecisionRecorder
from tayyari.decisions.runner import DecisionRunner
from tayyari.decisions.service import NullDecisionService
from tayyari.guard.ratelimit import RateLimiter
from tayyari.guard.spend import SpendGuard
from tayyari.llm.errors import SpendCeilingReached
from tayyari.llm.guarded import GuardedClaudeClient
from tayyari.llm.jev_http import HttpJevClient
from tayyari.llm.types import CallContext, TextBlock
from tayyari.sample.provider import SampleProvider
from tayyari.storage.db import Database
from tayyari.storage.maintenance import LAST_RUN_KEY
from tayyari.storage.migrate import head_revision
from tayyari.storage.repos import SqlProfileRepo
from tayyari.storage.tables import ProfileRow, RateLimitHitRow
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.clock import FakeClock
from tests.fakes.decisions import FakeDecisionService
from tests.fakes.jev import FakeJev
from tests.fakes.repos import FakeRepositories
from tests.unit.api.helpers import RecordingLog, eventually
from tests.unit.wiring.support import FakeAnalysis, FakeChat, roomy_guard, spy, spy_all

MakeSettings = Callable[..., Settings]
Build = Callable[..., Awaitable[Container]]


@pytest.fixture
async def build() -> AsyncIterator[Build]:
    """``await build(settings, claude=...)``: a container with fake analysis and chat services (override them
    to test something else); every container built is closed at the end of the test."""
    built: list[Container] = []

    async def factory(settings: Settings, **overrides: Any) -> Container:
        values: dict[str, Any] = {"analysis": FakeAnalysis(), "chat": FakeChat()}
        values.update(overrides)
        container = await Container.build(settings, overrides=ContainerOverrides(**values))
        built.append(container)
        return container

    yield factory
    for container in built:
        await container.aclose()


# ───────────────────────────── build ─────────────────────────────


async def test_build_wires_everything(make_settings: MakeSettings, build: Build) -> None:
    settings = make_settings(guard=roomy_guard())
    container = await build(settings)

    assert container.settings is settings
    assert isinstance(container.db, Database)
    assert isinstance(container.claude, GuardedClaudeClient)
    assert isinstance(container.rate_limiter, RateLimiter)
    assert isinstance(container.spend_guard, SpendGuard)
    assert isinstance(container.runner, DecisionRunner)
    assert isinstance(container.recorder, DecisionRecorder)
    assert isinstance(container.jev, HttpJevClient)
    assert isinstance(container.sample, SampleProvider)
    assert container.analysis_slots._value == settings.concurrency.max_concurrent_analyses
    assert "ip_hash_key" not in repr(container)
    assert container.storage_kind is None  # a durable local SQLite file


async def test_build_migrates_a_fresh_database_to_head(make_settings: MakeSettings, build: Build) -> None:
    container = await build(make_settings())
    assert container.db is not None
    async with container.db.session() as s:
        version = (await s.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert version == head_revision()


async def test_build_seeds_the_sample_profile_and_context_once(
    make_settings: MakeSettings, build: Build, monkeypatch: pytest.MonkeyPatch, container_log: RecordingLog
) -> None:
    settings = make_settings()
    container = await build(settings)
    sample_id = container.sample.sample_profile_id()
    assert sample_id is not None
    assert container.db is not None

    async with container.db.session() as s:
        is_sample = (
            await s.execute(select(ProfileRow.is_sample).where(ProfileRow.id == sample_id))
        ).scalar_one()
    stored = await container.repos.profiles.get_for_chat(sample_id)
    assert is_sample is True
    assert stored is not None
    profile, context = stored
    assert profile["id"] == sample_id
    assert context.sources, "the sample context (the sources chat reads) is seeded too"
    assert "sample_seeded" in container_log.events("info")

    # A second start (same data folder) finds it and does not write again.
    async def not_again(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the sample was seeded twice")

    monkeypatch.setattr(SqlProfileRepo, "save", not_again)
    container_log.records.clear()
    await build(settings)
    assert "sample_seed_failed" not in container_log.events()
    assert "sample_seeded" not in container_log.events()


async def test_a_failed_seed_is_logged_and_does_not_stop_startup(
    make_settings: MakeSettings, build: Build, monkeypatch: pytest.MonkeyPatch, container_log: RecordingLog
) -> None:
    async def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(SqlProfileRepo, "save", broken)
    container = await build(make_settings())
    assert container.db is not None
    assert "sample_seed_failed" in container_log.events("error")


async def test_no_sample_fixtures_means_nothing_to_seed(
    make_settings: MakeSettings, build: Build, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(container_module, "SampleProvider", lambda: SampleProvider(tmp_path / "none"))
    container = await build(make_settings())
    assert container.sample.has_sample() is False


async def test_the_ip_hash_key_is_generated_once_and_survives_a_restart(
    make_settings: MakeSettings, build: Build, tmp_path: Any
) -> None:
    first = await build(make_settings())
    assert len(first.ip_hash_key) == 32
    assert await first.repos.kv.get(IP_HASH_KEY) == first.ip_hash_key.hex()

    restarted = await build(make_settings())  # same data folder
    elsewhere = await build(make_settings(data_dir=tmp_path / "other"))
    assert restarted.ip_hash_key == first.ip_hash_key
    assert elsewhere.ip_hash_key != first.ip_hash_key


@pytest.mark.parametrize(
    ("url", "expected"),
    [(None, None), ("sqlite:///:memory:", "ephemeral")],
)
async def test_storage_kind(
    make_settings: MakeSettings, build: Build, url: str | None, expected: str | None
) -> None:
    container = await build(make_settings(database_url=url))
    assert container.storage_kind == expected


async def test_storage_kind_is_postgres_for_a_postgres_url_even_without_a_connection(
    make_settings: MakeSettings, build: Build
) -> None:
    settings = make_settings(database_url="postgresql://u:p@ep-x-pooler.neon.tech/db?sslmode=require")
    container = await build(settings, repos=FakeRepositories().bundle)
    assert container.storage_kind == "postgres"


async def test_an_ephemeral_database_is_warned_about_loudly(
    make_settings: MakeSettings, build: Build, container_log: RecordingLog
) -> None:
    await build(make_settings(database_url="sqlite:///:memory:"))
    warnings = [fields for level, event, fields in container_log.records if event == "ephemeral_storage"]
    assert len(warnings) == 1
    assert "lost on restart" in warnings[0]["effect"]


async def test_a_durable_database_gets_no_ephemeral_warning(
    make_settings: MakeSettings, build: Build, container_log: RecordingLog
) -> None:
    await build(make_settings())
    assert "ephemeral_storage" not in container_log.events()


# ───────────────────────────── Jev policy ─────────────────────────────


async def test_without_a_jev_key_decisions_are_null(
    make_settings: MakeSettings, build: Build, container_log: RecordingLog
) -> None:
    container = await build(make_settings())
    assert container.jev_active is False
    assert isinstance(container.decisions, NullDecisionService)
    (inactive,) = [f for _, event, f in container_log.records if event == "jev_inactive"]
    assert inactive["reason"] == "no JEV_API_KEY"


async def test_the_kill_switch_wins_over_a_jev_key(
    make_settings: MakeSettings, build: Build, container_log: RecordingLog
) -> None:
    settings = make_settings(jev_api_key=SecretStr("jev-key"), jev=JevSettings(enabled=False))
    container = await build(settings)
    assert container.jev_active is False
    assert isinstance(container.decisions, NullDecisionService)
    (inactive,) = [f for _, event, f in container_log.records if event == "jev_inactive"]
    assert "TAYYARI_JEV__ENABLED" in inactive["reason"]


async def test_a_jev_key_switches_decisions_on(make_settings: MakeSettings, build: Build) -> None:
    fake = FakeDecisionService()
    container = await build(make_settings(jev_api_key=SecretStr("jev-key")), decisions=fake)
    assert container.jev_active is True
    assert container.decisions is fake  # an injected service always wins


async def test_an_injected_jev_client_switches_decisions_on_without_a_key(
    make_settings: MakeSettings, build: Build
) -> None:
    fake_jev = FakeJev()
    container = await build(make_settings(), jev=fake_jev, decisions=FakeDecisionService())
    assert container.jev_active is True
    assert container.jev is fake_jev


async def test_an_injected_jev_client_does_not_beat_the_kill_switch(
    make_settings: MakeSettings, build: Build
) -> None:
    container = await build(make_settings(jev=JevSettings(enabled=False)), jev=FakeJev())
    assert container.jev_active is False
    assert isinstance(container.decisions, NullDecisionService)


async def test_the_jev_decision_service_is_built_from_the_runner(
    make_settings: MakeSettings, build: Build
) -> None:
    container = await build(make_settings(jev_api_key=SecretStr("jev-key")))
    assert isinstance(container.decisions, JevDecisionService)
    assert container.jev_active is True


# ───────────────────────────── overrides ─────────────────────────────


async def test_an_injected_claude_client_is_wrapped_by_the_spend_guard(
    make_settings: MakeSettings, build: Build
) -> None:
    fake = FakeClaudeClient().script_structured("reader", {"skills": []})
    container = await build(make_settings(guard=roomy_guard()), claude=fake)
    assert isinstance(container.claude, GuardedClaudeClient)

    result = await container.claude.structured(
        model="claude-haiku-4-5",
        system=[TextBlock("rules")],
        user=[TextBlock("resume")],
        schema={"type": "object"},
        max_tokens=100,
        effort=None,
        ctx=CallContext(purpose="reader"),
    )
    assert result.data == {"skills": []}
    assert len(fake.structured_calls) == 1


async def test_the_spend_ceiling_stops_a_call_before_it_reaches_the_injected_client(
    make_settings: MakeSettings, build: Build
) -> None:
    fake = FakeClaudeClient().script_structured("reader", {"skills": []}, repeat=True)
    container = await build(make_settings(guard=roomy_guard(daily_ceiling_usd=0.0)), claude=fake)
    with pytest.raises(SpendCeilingReached):
        await container.claude.structured(
            model="claude-haiku-4-5",
            system=[TextBlock("rules")],
            user=[TextBlock("resume")],
            schema={"type": "object"},
            max_tokens=100,
            effort=None,
            ctx=CallContext(purpose="reader"),
        )
    assert fake.structured_calls == []


async def test_the_injected_clock_is_used_everywhere(make_settings: MakeSettings, build: Build) -> None:
    clock = FakeClock()  # 2026-10-07 12:00 UTC, a day the real clock is not on
    container = await build(make_settings(guard=roomy_guard()), clock=clock)
    assert container.clock is clock

    await container.rate_limiter.check("chat", "some-ip-hash")  # the window it counts in comes from the clock
    assert container.db is not None
    async with container.db.session() as s:
        starts = (await s.execute(select(RateLimitHitRow.window_start))).scalars().all()
    assert starts
    assert {(start.year, start.month, start.day) for start in starts} == {(2026, 10, 7)}


async def test_injected_repositories_without_a_database(make_settings: MakeSettings, build: Build) -> None:
    fakes = FakeRepositories()
    container = await build(make_settings(), repos=fakes.bundle)
    sample_id = container.sample.sample_profile_id()
    assert sample_id is not None

    assert container.db is None
    assert container.repos is fakes.bundle
    assert container.maintenance is None  # nothing to purge without a database
    assert await fakes.profiles.get_profile(sample_id) is not None  # the sample is seeded into the fakes
    assert container.ip_hash_key == bytes.fromhex(await fakes.kv.get(IP_HASH_KEY))


async def test_an_injected_database_is_migrated_and_stays_open_at_shutdown(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings()
    db = Database(settings.db)
    calls = spy_all(monkeypatch)
    container = await Container.build(
        settings, overrides=ContainerOverrides(db=db, analysis=FakeAnalysis(), chat=FakeChat())
    )
    try:
        assert container.db is db
        async with db.session() as s:
            assert (
                await s.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one() == head_revision()
    finally:
        await container.aclose()
        await db.dispose()
    assert [label for label, _, _ in calls].count("db.dispose") == 1  # only the test's own dispose


# ───────────────────────────── background maintenance ─────────────────────────────


async def test_maintenance_does_not_block_startup(make_settings: MakeSettings, build: Build) -> None:
    container = await build(make_settings())
    assert container.maintenance is not None
    assert not container.maintenance.done()  # still waiting out its start delay


async def test_maintenance_runs_in_the_background(
    make_settings: MakeSettings, build: Build, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(container_module, "MAINTENANCE_DELAY_S", 0.0)
    container = await build(make_settings())

    async def ran() -> bool:
        return await container.repos.kv.get(LAST_RUN_KEY) is not None

    for _ in range(200):
        if await ran():
            break
        await asyncio.sleep(0.02)
    assert await ran(), "run_maintenance never claimed its interval"


async def test_a_failing_maintenance_run_is_logged_and_retried(
    make_settings: MakeSettings, build: Build, monkeypatch: pytest.MonkeyPatch, container_log: RecordingLog
) -> None:
    attempts: list[int] = []

    async def failing(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        attempts.append(1)
        raise RuntimeError("purge blew up")

    monkeypatch.setattr(container_module, "MAINTENANCE_DELAY_S", 0.0)
    monkeypatch.setattr(container_module, "MAINTENANCE_INTERVAL_S", 0.01)
    monkeypatch.setattr(container_module, "run_maintenance", failing)
    container = await build(make_settings())
    await eventually(lambda: len(attempts) >= 3)
    assert container.maintenance is not None
    assert not container.maintenance.done()  # an error never ends the loop
    assert "maintenance_failed" in container_log.events("error")


async def test_shutdown_cancels_the_maintenance_task(make_settings: MakeSettings, build: Build) -> None:
    container = await build(make_settings())
    task = container.maintenance
    assert task is not None
    await container.aclose()
    assert task.cancelled()
    assert container.maintenance is None


# ───────────────────────────── shutdown ─────────────────────────────


async def test_aclose_closes_everything_the_container_built_in_order(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = spy_all(monkeypatch)
    container = await Container.build(
        make_settings(), overrides=ContainerOverrides(analysis=FakeAnalysis(), chat=FakeChat())
    )
    assert calls == []
    await container.aclose()

    assert [label for label, _, _ in calls] == [
        "runner.drain",
        "recorder.aclose",
        "jev.aclose",
        "claude.aclose",
        "spend_guard.aclose",
        "db.dispose",
    ]
    (_, drain_args, drain_kwargs) = calls[0]
    assert drain_args == (5.0,)
    assert drain_kwargs == {"cancel_remaining": True}


async def test_one_failing_shutdown_step_does_not_skip_the_rest(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, container_log: RecordingLog
) -> None:
    calls = spy_all(monkeypatch)
    spy(
        monkeypatch, calls, DecisionRecorder, "aclose", "recorder.aclose", raises=RuntimeError("flush failed")
    )
    spy(monkeypatch, calls, HttpJevClient, "aclose", "jev.aclose", raises=OSError("socket"))
    container = await Container.build(
        make_settings(), overrides=ContainerOverrides(analysis=FakeAnalysis(), chat=FakeChat())
    )
    await container.aclose()

    assert [label for label, _, _ in calls] == [
        "runner.drain",
        "recorder.aclose",
        "jev.aclose",
        "claude.aclose",
        "spend_guard.aclose",
        "db.dispose",
    ]
    failed = [
        fields["step"] for level, event, fields in container_log.records if event == "shutdown_step_failed"
    ]
    assert failed == ["decision_recorder", "jev"]


async def test_aclose_twice_closes_once(make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = spy_all(monkeypatch)
    container = await Container.build(
        make_settings(), overrides=ContainerOverrides(analysis=FakeAnalysis(), chat=FakeChat())
    )
    await container.aclose()
    count = len(calls)
    await container.aclose()
    assert len(calls) == count


async def test_parts_handed_in_are_not_closed(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ClosableClaude(FakeClaudeClient):
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    calls = spy_all(monkeypatch)
    claude, jev = ClosableClaude(), FakeJev()
    container = await Container.build(
        make_settings(),
        overrides=ContainerOverrides(claude=claude, jev=jev, analysis=FakeAnalysis(), chat=FakeChat()),
    )
    await container.aclose()

    assert claude.closed is False
    assert jev.closed is False
    labels = [label for label, _, _ in calls]
    assert "claude.aclose" not in labels
    assert "jev.aclose" not in labels
    assert labels == ["runner.drain", "recorder.aclose", "spend_guard.aclose", "db.dispose"]


async def test_a_failed_build_closes_what_it_had_built(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure late in the build (here: the analysis service cannot be constructed) tears down every client,
    the recorder and the database that were already created."""
    calls = spy_all(monkeypatch)

    class Exploding:
        def __init__(self, **_kwargs: Any) -> None:
            raise RuntimeError("cannot construct the analysis service")

    monkeypatch.setattr("tayyari.analysis.service.PipelineAnalysisService", Exploding)
    with pytest.raises(RuntimeError, match="cannot construct the analysis service"):
        await Container.build(make_settings())  # no analysis override, so the class is used

    assert [label for label, _, _ in calls] == [
        "runner.drain",
        "recorder.aclose",
        "jev.aclose",
        "claude.aclose",
        "spend_guard.aclose",
        "db.dispose",
    ]


async def test_a_failed_migration_stops_the_build_and_closes_the_database(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = spy_all(monkeypatch)

    async def failing(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(container_module, "migrate_to_head", failing)
    with pytest.raises(RuntimeError, match="migration failed"):
        await Container.build(
            make_settings(), overrides=ContainerOverrides(analysis=FakeAnalysis(), chat=FakeChat())
        )
    assert [label for label, _, _ in calls] == ["db.dispose"]


# ───────────────────────────── the services ─────────────────────────────


async def test_the_real_analysis_service_is_built_when_none_is_injected(
    make_settings: MakeSettings, build: Build
) -> None:
    container = await build(
        make_settings(), analysis=None, decisions=FakeDecisionService(), claude=FakeClaudeClient()
    )
    assert isinstance(container.analysis, PipelineAnalysisService)
    assert isinstance(container.analysis, AnalysisService)


async def test_the_real_chat_service_is_built_when_none_is_injected(
    make_settings: MakeSettings, build: Build
) -> None:
    container = await build(
        make_settings(), chat=None, decisions=FakeDecisionService(), claude=FakeClaudeClient()
    )
    assert isinstance(container.chat, CoachChatService)
    assert isinstance(container.chat, ChatService)


async def test_the_shipped_decision_defaults_name_only_registered_specs(
    make_settings: MakeSettings, build: Build, container_log: RecordingLog
) -> None:
    """defaults.toml and the packs agree: the startup check has nothing to report."""
    await build(make_settings())
    assert "decision_config_unknown_ids" not in container_log.events()
    assert "decision_registry_check_failed" not in container_log.events()


async def test_an_override_for_an_unknown_spec_is_reported_at_startup(
    make_settings: MakeSettings, build: Build, container_log: RecordingLog
) -> None:
    settings = make_settings(decision_overrides={"evidence.suport": {"mode": "off"}})  # a typo
    await build(settings)
    (warning,) = [f for level, event, f in container_log.records if event == "decision_config_unknown_ids"]
    assert warning["ids"] == ["evidence.suport"]


def test_container_overrides_are_frozen() -> None:
    overrides = ContainerOverrides()
    with pytest.raises(dataclasses.FrozenInstanceError):
        overrides.claude = FakeClaudeClient()  # type: ignore[misc]
