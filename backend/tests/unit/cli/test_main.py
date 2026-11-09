"""Argument parsing and dispatch of ``python -m tayyari.cli``."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tayyari.cli import __main__ as cli
from tayyari.cli import ingest_check
from tayyari.core.paths import BACKEND_DIR

ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - an argument the parser is given, never an address a test binds
COMMANDS = ("dev", "serve", "migrate", "smoke", "make-sample", "ingest-check", "portfolio-eval", "deploy")


def parse(*argv: str) -> Any:
    return cli.build_parser().parse_args(list(argv))


# ───────────────────────── parsing ─────────────────────────


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_parses_with_no_options(command: str) -> None:
    assert parse(command).command == command


def test_no_command_is_none() -> None:
    assert parse().command is None


def test_serve_defaults_and_options() -> None:
    defaults = parse("serve")
    assert (defaults.host, defaults.port) == ("127.0.0.1", None)
    chosen = parse("serve", "--host", ALL_INTERFACES, "--port", "9000")
    assert (chosen.host, chosen.port) == (ALL_INTERFACES, 9000)


@pytest.mark.parametrize("bad", ["0", "65536", "-1", "abc", ""])
def test_serve_rejects_a_bad_port(bad: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse("serve", "--port", bad)
    assert exit_info.value.code == 2
    assert "--port" in capsys.readouterr().err


def test_dev_takes_no_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse("dev", "--port", "1234")
    assert exit_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_smoke_flags() -> None:
    defaults = parse("smoke")
    assert (defaults.skip_claude, defaults.skip_jev) == (False, False)
    both = parse("smoke", "--skip-claude", "--skip-jev")
    assert (both.skip_claude, both.skip_jev) == (True, True)


def test_make_sample_out_is_a_path() -> None:
    assert parse("make-sample").out is None
    assert parse("make-sample", "--out", "somewhere").out == Path("somewhere")


def test_ingest_check_options() -> None:
    defaults = parse("ingest-check")
    assert (defaults.only, defaults.url) == (None, None)
    chosen = parse("ingest-check", "--only", "classify,terms", "--url", "https://example.com")
    assert (chosen.only, chosen.url) == ("classify,terms", "https://example.com")


def test_portfolio_eval_options() -> None:
    defaults = parse("portfolio-eval")
    assert (defaults.only, defaults.write, defaults.offline, defaults.details) == (None, False, False, False)
    chosen = parse("portfolio-eval", "--only", "riya,aarav", "--write", "--offline", "--details")
    assert (chosen.only, chosen.write, chosen.offline, chosen.details) == ("riya,aarav", True, True, True)


def test_deploy_flag() -> None:
    assert parse("deploy").skip_build is False
    assert parse("deploy", "--skip-build").skip_build is True


def test_unknown_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse("frobnicate")
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_help_lists_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse("--help")
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for command in COMMANDS:
        assert command in out


def test_ingest_check_help_names_every_section() -> None:
    for name in (*ingest_check.SECTIONS, *ingest_check.OPT_IN_SECTIONS):
        assert name in cli.INGEST_SECTIONS_HELP


# ───────────────────────── dispatch ─────────────────────────


class Recorder:
    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append((args, kwargs))
        return self.result


def patch_run(monkeypatch: pytest.MonkeyPatch, module: str, result: int = 0) -> Recorder:
    recorder = Recorder(result)
    monkeypatch.setattr(f"tayyari.cli.{module}.run", recorder)
    return recorder


def test_dev_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    run = patch_run(monkeypatch, "dev")
    assert cli.main(["dev"]) == 0
    assert run.calls == [((), {})]


def test_serve_dispatch_passes_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    run = patch_run(monkeypatch, "serve")
    assert cli.main(["serve", "--host", ALL_INTERFACES, "--port", "9000"]) == 0
    assert run.calls == [((ALL_INTERFACES, 9000), {})]
    run.calls.clear()
    cli.main(["serve"])
    assert run.calls == [(("127.0.0.1", None), {})]


def test_migrate_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    run = patch_run(monkeypatch, "migrate")
    assert cli.main(["migrate"]) == 0
    assert run.calls == [((), {})]


def test_smoke_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    run = patch_run(monkeypatch, "smoke")
    cli.main(["smoke"])
    cli.main(["smoke", "--skip-claude", "--skip-jev"])
    assert run.calls == [
        ((), {"skip_claude": False, "skip_jev": False}),
        ((), {"skip_claude": True, "skip_jev": True}),
    ]


def test_make_sample_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = patch_run(monkeypatch, "make_sample")
    cli.main(["make-sample"])
    cli.main(["make-sample", "--out", str(tmp_path)])
    assert run.calls == [((None,), {}), ((tmp_path,), {})]


def test_ingest_check_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    run = patch_run(monkeypatch, "ingest_check")
    cli.main(["ingest-check", "--only", "terms", "--url", "https://example.com"])
    assert run.calls == [(("terms", "https://example.com"), {})]


def test_portfolio_eval_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    run = patch_run(monkeypatch, "portfolio_eval")
    cli.main(["portfolio-eval"])
    cli.main(["portfolio-eval", "--only", "riya", "--write", "--offline", "--details"])
    assert run.calls == [
        ((None,), {"write": False, "offline": False, "details": False}),
        (("riya",), {"write": True, "offline": True, "details": True}),
    ]


def test_deploy_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    run = patch_run(monkeypatch, "deploy")
    cli.main(["deploy"])
    cli.main(["deploy", "--skip-build"])
    cli.main(["deploy", "--app-id", "app-123"])
    assert run.calls == [
        ((), {"skip_build": False, "app_id": None}),
        ((), {"skip_build": True, "app_id": None}),
        ((), {"skip_build": False, "app_id": "app-123"}),
    ]


def test_the_exit_code_of_the_command_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_run(monkeypatch, "migrate", result=7)
    assert cli.main(["migrate"]) == 7


def test_no_command_prints_help_and_returns_a_usage_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "usage:" in err
    assert "make-sample" in err


def test_ctrl_c_returns_130(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def interrupted(*_a: Any, **_k: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("tayyari.cli.migrate.run", interrupted)
    assert cli.main(["migrate"]) == cli.EXIT_INTERRUPTED == 130
    assert "Interrupted" in capsys.readouterr().err


# ───────────────────────── environment ─────────────────────────


def test_main_sets_utf8_mode_for_child_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_run(monkeypatch, "migrate")
    assert "PYTHONUTF8" not in os.environ
    cli.main(["migrate"])
    assert os.environ["PYTHONUTF8"] == "1"


def test_main_keeps_an_explicit_pythonutf8(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_run(monkeypatch, "migrate")
    monkeypatch.setenv("PYTHONUTF8", "0")
    cli.main(["migrate"])
    assert os.environ["PYTHONUTF8"] == "0"


def test_the_module_runs_with_dash_m() -> None:
    """The documented entry point, in a real child process: `--help` needs no key, no network and no database."""
    done = subprocess.run(
        [sys.executable, "-m", "tayyari.cli", "--help"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    for command in COMMANDS:
        assert command in done.stdout
