"""Discover in the composition root, the settings and the command line: the container builds the service from the settings, a
refresh in flight is stopped at shutdown, a bad registry does not stop the app, and the two commands run the same service."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from tayyari.cli import __main__ as cli
from tayyari.cli import discover as discover_cli
from tayyari.container import Container, ContainerOverrides
from tayyari.core.settings import DiscoverSettings, Settings
from tayyari.discover.refresh import RefreshReport
from tayyari.discover.service import DiscoverService
from tests.fakes.discover import DATA, FakeBoards, FakeDiscoverRepo
from tests.unit.wiring.support import FakeAnalysis, FakeChat

MakeSettings = Callable[..., Settings]


async def build(settings: Settings, **overrides: Any) -> Container:
    values: dict[str, Any] = {"analysis": FakeAnalysis(), "chat": FakeChat(), **overrides}
    return await Container.build(settings, overrides=ContainerOverrides(**values))


# ───────────────────────── settings ─────────────────────────


def test_the_defaults() -> None:
    settings = DiscoverSettings()
    assert (settings.enabled, settings.refresh_hours, settings.fetch_concurrency) == (True, 6.0, 4)
    assert (settings.request_timeout_s, settings.max_listings_per_employer, settings.registry_path) == (
        15.0,
        300,
        None,
    )


def test_the_environment_names_follow_the_group_and_blank_values_keep_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAYYARI_DISCOVER__ENABLED", "false")
    monkeypatch.setenv("TAYYARI_DISCOVER__REFRESH_HOURS", "2.5")
    monkeypatch.setenv("TAYYARI_DISCOVER__FETCH_CONCURRENCY", "8")
    monkeypatch.setenv("TAYYARI_DISCOVER__REQUEST_TIMEOUT_S", "9")
    monkeypatch.setenv("TAYYARI_DISCOVER__MAX_LISTINGS_PER_EMPLOYER", "50")
    monkeypatch.setenv("TAYYARI_DISCOVER__REGISTRY_PATH", "/tmp/employers.json")
    settings = Settings(_env_file=None).discover
    assert (settings.enabled, settings.refresh_hours, settings.fetch_concurrency) == (False, 2.5, 8)
    assert (settings.request_timeout_s, settings.max_listings_per_employer) == (9.0, 50)
    assert settings.registry_path == Path("/tmp/employers.json")
    for name in (
        "ENABLED",
        "REFRESH_HOURS",
        "FETCH_CONCURRENCY",
        "REQUEST_TIMEOUT_S",
        "MAX_LISTINGS_PER_EMPLOYER",
    ):
        monkeypatch.setenv(f"TAYYARI_DISCOVER__{name}", " ")
    monkeypatch.setenv("TAYYARI_DISCOVER__REGISTRY_PATH", " ")
    assert Settings(_env_file=None).discover == DiscoverSettings()


@pytest.mark.parametrize(
    "bad",
    [
        {"refresh_hours": 0},
        {"fetch_concurrency": 0},
        {"fetch_concurrency": 99},
        {"request_timeout_s": 0},
        {"max_listings_per_employer": 0},
    ],
)
def test_nonsense_settings_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DiscoverSettings(**bad)


def test_there_is_no_secret_in_the_discover_group() -> None:
    assert not any(
        "key" in name or "secret" in name or "token" in name for name in DiscoverSettings.model_fields
    )
    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    for name in DiscoverSettings.model_fields:
        assert f"TAYYARI_DISCOVER__{name.upper()}" in example, name


# ───────────────────────── the container ─────────────────────────


async def test_the_container_builds_the_service_from_the_settings(make_settings: MakeSettings) -> None:
    settings = make_settings(discover={"refresh_hours": 3, "registry_path": str(DATA / "registry.json")})
    container = await build(settings)
    try:
        assert isinstance(container.discover, DiscoverService)
        assert (
            container.discover._settings is settings.discover
            and container.discover._repo is container.repos.discover
        )
        assert container.discover._clock is container.clock
        assert len(container.discover.registry.employers) == 9
    finally:
        await container.aclose()


async def test_the_bundled_registry_is_used_when_no_path_is_set(make_settings: MakeSettings) -> None:
    container = await build(make_settings())
    try:
        assert len(container.discover.registry.employers) >= 1
    finally:
        await container.aclose()


async def test_an_injected_service_is_used_as_given_and_left_open(make_settings: MakeSettings) -> None:
    closed: list[str] = []

    class Mine:
        async def aclose(self) -> None:
            closed.append("mine")

    mine: Any = Mine()
    container = await build(make_settings(), discover=mine)
    assert container.discover is mine
    await container.aclose()
    assert closed == []  # the container closes only what it built


async def test_a_bad_registry_does_not_stop_the_app_and_the_refresh_says_why(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    broken = tmp_path / "employers.json"
    broken.write_text('{"version": 1, "employers": [{"id": "x", "ats": "ashby"}]}', encoding="utf-8")
    boards = FakeBoards()
    container = await build(
        make_settings(discover={"registry_path": str(broken)}), discover_transport=boards.transport
    )
    try:
        assert container.discover.registry.employers == ()
        report = await container.discover.refresh()
        assert (
            report is not None and report.status == "failed" and "ats must be one of" in (report.note or "")
        )
        assert boards.requests == []
        latest = await container.repos.discover.latest_run()
        assert latest is not None and latest.status == "failed"
    finally:
        await container.aclose()


async def test_shutdown_stops_a_refresh_that_is_still_reading(make_settings: MakeSettings) -> None:
    started = asyncio.Event()

    async def never(request: httpx2.Request) -> httpx2.Response:
        started.set()
        await asyncio.Event().wait()
        return httpx2.Response(200)

    container = await build(
        make_settings(discover={"registry_path": str(DATA / "registry.json")}),
        discover_transport=httpx2.MockTransport(never),
    )
    assert await container.discover.maybe_refresh() is True
    await asyncio.wait_for(started.wait(), timeout=5)
    task = container.discover._task
    assert task is not None and not task.done()
    await asyncio.wait_for(container.aclose(), timeout=5)
    assert task.done()


async def test_nothing_is_read_at_start_up(make_settings: MakeSettings) -> None:
    boards = FakeBoards()
    container = await build(
        make_settings(discover={"registry_path": str(DATA / "registry.json")}),
        discover_transport=boards.transport,
    )
    try:
        await asyncio.sleep(0.05)
        assert boards.requests == [] and container.discover._task is None
    finally:
        await container.aclose()


# ───────────────────────── the commands ─────────────────────────


def test_the_commands_parse() -> None:
    parser = cli.build_parser()
    refresh = parser.parse_args(["discover-refresh", "--registry", "x.json"])
    assert (refresh.command, refresh.registry) == ("discover-refresh", Path("x.json"))
    worker = parser.parse_args(["discover-worker", "--every", "2.5"])
    assert (worker.command, worker.every, worker.registry) == ("discover-worker", 2.5, None)
    assert parser.parse_args(["discover-worker"]).every is None
    for bad in ("0", "-1", "soon"):
        with pytest.raises(SystemExit):
            parser.parse_args(["discover-worker", "--every", bad])


def test_a_report_is_printed_in_plain_lines() -> None:
    report = RefreshReport(
        "run1",
        "ok",
        8,
        1,
        17,
        2,
        inserted=17,
        updated=1,
        dropped=3,
        failures={"goneco": "the board was not found"},
        note="1 of 9 boards could not be read.",
    )
    lines = discover_cli.describe(report)
    assert lines[0] == "Discover refresh ok: 8 boards read, 1 failed."
    assert (
        "Listings open now: 17. Added 17, changed 1, closed 2, left out over the per-employer cap 3."
        == lines[1]
    )
    assert lines[-1] == "  goneco: the board was not found"
    assert discover_cli.describe(None) == ["Another refresh is already running; nothing was done."]


def test_discover_refresh_reads_the_boards_into_the_configured_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    boards = FakeBoards()
    from tests.fakes.discover import read_json
    from tests.unit.discover.conftest import BOARD_FILES

    for token, name in BOARD_FILES.items():
        boards.serve(token, read_json(name))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.sqlite3').as_posix()}")
    monkeypatch.setenv("TAYYARI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        discover_cli, "DiscoverService", functools.partial(DiscoverService, transport=boards.transport)
    )
    from tayyari.core.settings import get_settings

    get_settings.cache_clear()
    assert discover_cli.run_refresh(DATA / "registry.json") == 0
    out = capsys.readouterr().out
    assert "Discover refresh ok: 8 boards read, 1 failed." in out and "goneco: the board was not found" in out
    assert len(boards.requests) == 9
    # A second run in the same database sees the first and changes nothing.
    assert discover_cli.run_refresh(DATA / "registry.json") == 0
    assert "Added 0, changed 0, closed 0" in capsys.readouterr().out


def test_discover_refresh_exits_1_when_no_board_could_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    nothing = FakeBoards()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.sqlite3').as_posix()}")
    monkeypatch.setattr(
        discover_cli, "DiscoverService", functools.partial(DiscoverService, transport=nothing.transport)
    )
    from tayyari.core.settings import get_settings

    get_settings.cache_clear()
    assert discover_cli.run_refresh(DATA / "registry.json") == 1
    assert "Discover refresh failed" in capsys.readouterr().out


def test_discover_refresh_when_switched_off_reads_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    boards = FakeBoards()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.sqlite3').as_posix()}")
    monkeypatch.setenv("TAYYARI_DISCOVER__ENABLED", "false")
    monkeypatch.setattr(
        discover_cli, "DiscoverService", functools.partial(DiscoverService, transport=boards.transport)
    )
    from tayyari.core.settings import get_settings

    get_settings.cache_clear()
    assert discover_cli.run_refresh() == 0 and boards.requests == []
    assert "switched off" in capsys.readouterr().out


async def test_the_worker_refreshes_in_a_loop_and_survives_a_bad_round(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rounds: list[str] = []

    class Loop(DiscoverService):
        async def refresh(self) -> RefreshReport | None:
            rounds.append("round")
            if len(rounds) == 1:
                raise RuntimeError("the network is down")
            if len(rounds) >= 3:
                raise asyncio.CancelledError
            return None

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.sqlite3').as_posix()}")
    monkeypatch.setattr(discover_cli, "DiscoverService", Loop)
    monkeypatch.setattr(discover_cli, "SECONDS_PER_HOUR", 0.0)
    from tayyari.core.settings import get_settings

    get_settings.cache_clear()
    with pytest.raises(asyncio.CancelledError):
        await discover_cli._work(get_settings(), 1.0)
    assert len(rounds) == 3
    captured = capsys.readouterr()
    assert "Refresh failed: RuntimeError" in captured.err and "the network is down" not in captured.err
    assert "Another refresh is already running" in captured.out


def test_the_repository_bundle_carries_discover() -> None:
    from tests.fakes.repos import FakeRepositories

    fakes = FakeRepositories()
    assert isinstance(fakes.bundle.discover, FakeDiscoverRepo)
