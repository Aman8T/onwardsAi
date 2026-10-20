"""``weekly run`` and ``weekly preview``: parsing, dispatch, and both commands over a real SQLite file with no key and no network."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tayyari.cli import __main__ as cli
from tayyari.cli import weekly
from tayyari.container import Container
from tayyari.core.settings import Settings


def parse(*argv: str) -> Any:
    return cli.build_parser().parse_args(list(argv))


def test_the_weekly_commands_parse() -> None:
    assert parse("weekly", "run").weekly_command == "run"
    chosen = parse("weekly", "preview", "ana@example.com")
    assert (chosen.command, chosen.weekly_command, chosen.email) == ("weekly", "preview", "ana@example.com")


def test_weekly_needs_an_action_and_preview_needs_an_email(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["weekly"], ["weekly", "preview"]):
        with pytest.raises(SystemExit) as exit_info:
            parse(*argv)
        assert exit_info.value.code == 2
    capsys.readouterr()


def test_main_dispatches_to_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(weekly, "run_pass", lambda: seen.append(("run",)) or 0)
    monkeypatch.setattr(weekly, "run_preview", lambda email: seen.append(("preview", email)) or 0)
    assert cli.main(["weekly", "run"]) == 0
    assert cli.main(["weekly", "preview", "ana@example.com"]) == 0
    assert seen == [("run",), ("preview", "ana@example.com")]


def test_the_commands_run_with_the_background_loop_off(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tayyari.core.settings import WeeklySettings

    on = settings.model_copy(update={"weekly": WeeklySettings(enabled=True)})
    monkeypatch.setattr(weekly, "get_settings", lambda: on)
    assert on.weekly.enabled is True and weekly._settings().weekly.enabled is False


@pytest.fixture
def cli_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(weekly, "_settings", lambda: settings)
    return settings


def test_preview_says_so_when_no_account_has_that_email(
    cli_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert weekly.run_preview("nobody@example.com") == 1
    assert "No account has that email address." in capsys.readouterr().out


def test_preview_builds_stores_and_writes_the_email_html(
    cli_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    import asyncio

    async def make_account() -> None:
        container = await Container.build(cli_settings)
        try:
            await container.repos.users.sign_in_email(email="ana@example.com", now=datetime.now(UTC))
        finally:
            await container.aclose()

    asyncio.run(make_account())
    assert weekly.run_preview("Ana@Example.com") == 0
    out = capsys.readouterr().out
    assert "is stored." in out and "Email HTML:" in out
    path = Path(out.split("Email HTML: ")[1].strip().splitlines()[0])
    assert path.is_file() and "The Leafout Weekly" in path.read_text(encoding="utf-8")
    assert path.parent == cli_settings.data_dir / "weekly"
    # the same week again is the same edition: nothing is built twice
    assert weekly.run_preview("ana@example.com") == 0
    again = capsys.readouterr().out
    assert path.name.removesuffix(".html") in again


def test_run_says_when_it_is_not_a_sending_day(
    cli_settings: Settings, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fakes.clock import FakeClock

    wednesday = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)
    monkeypatch.setattr("tayyari.container.SystemClock", lambda: FakeClock(wednesday))
    assert weekly.run_pass() == 0
    assert "Not a sending day" in capsys.readouterr().out


def test_run_reports_a_pass_on_a_sending_day(
    cli_settings: Settings, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fakes.clock import FakeClock

    monday = datetime(2026, 10, 19, 1, 0, tzinfo=UTC)
    monkeypatch.setattr("tayyari.container.SystemClock", lambda: FakeClock(monday))
    assert weekly.run_pass() == 0
    assert "Weekly pass for mon: composed 0, emailed 0, failed 0" in capsys.readouterr().out
