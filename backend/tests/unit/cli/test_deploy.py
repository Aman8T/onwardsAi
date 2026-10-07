"""``deploy`` builds the right command lines with the right working directories (subprocess is replaced)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tayyari.cli import deploy

NPM = r"C:\tools\node\npm.cmd"
UVX = r"C:\tools\uv\uvx.exe"
UV = r"C:\tools\uv\uv.exe"


class Subprocess:
    """Stands in for `subprocess.run`: records every call and returns scripted exit codes."""

    def __init__(self, codes: list[int] | None = None, *, raises: BaseException | None = None) -> None:
        self.codes = list(codes or [])
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[None]:
        self.calls.append({"command": command, **kwargs})
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(command, self.codes.pop(0) if self.codes else 0)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake repo: `<root>/backend/static/index.html` exists, as after a build."""
    backend = tmp_path / "repo" / "backend"
    (backend / "static").mkdir(parents=True)
    (backend / "static" / "index.html").write_text("<html></html>")
    monkeypatch.setattr(deploy, "REPO_ROOT", backend.parent)
    monkeypatch.setattr(deploy, "BACKEND_DIR", backend)
    return backend


def tools(monkeypatch: pytest.MonkeyPatch, **found: str | None) -> None:
    """`shutil.which` knows only these tools (a missing name returns None)."""
    monkeypatch.setattr(deploy.shutil, "which", lambda name: found.get(name))


@pytest.fixture
def run_calls(monkeypatch: pytest.MonkeyPatch) -> Subprocess:
    fake = Subprocess()
    monkeypatch.setattr(deploy.subprocess, "run", fake)
    return fake


# ───────────────────────── the happy path ─────────────────────────


def test_builds_then_deploys(layout: Path, monkeypatch: pytest.MonkeyPatch, run_calls: Subprocess) -> None:
    tools(monkeypatch, npm=NPM, uvx=UVX)
    assert deploy.run() == 0

    build, ship = run_calls.calls
    assert build["command"] == [NPM, "run", "build:static"]
    assert build["cwd"] == layout.parent  # the repo root, where package.json is
    assert ship["command"] == [UVX, "--from", "fastapi[standard]", "fastapi", "deploy", "--json", "."]
    assert ship["cwd"] == layout  # backend/, the deploy root


def test_the_deploy_child_gets_utf8_forced(
    layout: Path, monkeypatch: pytest.MonkeyPatch, run_calls: Subprocess
) -> None:
    tools(monkeypatch, npm=NPM, uvx=UVX)
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("SOME_OTHER_VARIABLE", "kept")
    deploy.run()
    env = run_calls.calls[1]["env"]
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["SOME_OTHER_VARIABLE"] == "kept"


def test_the_output_streams_to_the_terminal(
    layout: Path, monkeypatch: pytest.MonkeyPatch, run_calls: Subprocess
) -> None:
    """No capture_output, no pipes: the child writes straight to our stdout and stderr, so it streams."""
    tools(monkeypatch, npm=NPM, uvx=UVX)
    deploy.run()
    for call in run_calls.calls:
        for redirected in ("capture_output", "stdout", "stderr", "input", "text", "shell"):
            assert redirected not in call


def test_skip_build_only_deploys(
    layout: Path, monkeypatch: pytest.MonkeyPatch, run_calls: Subprocess
) -> None:
    tools(monkeypatch, uvx=UVX)  # npm is not even needed
    assert deploy.run(skip_build=True) == 0
    (ship,) = run_calls.calls
    assert ship["command"][-3:] == ["deploy", "--json", "."]
    assert ship["cwd"] == layout


def test_uv_tool_run_stands_in_for_a_missing_uvx(
    layout: Path, monkeypatch: pytest.MonkeyPatch, run_calls: Subprocess
) -> None:
    tools(monkeypatch, uv=UV)
    deploy.run(skip_build=True)
    assert run_calls.calls[0]["command"] == [
        UV,
        "tool",
        "run",
        "--from",
        "fastapi[standard]",
        "fastapi",
        "deploy",
        "--json",
        ".",
    ]


# ───────────────────────── exit codes ─────────────────────────


def test_a_failed_build_stops_before_deploying(layout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools(monkeypatch, npm=NPM, uvx=UVX)
    fake = Subprocess([3])
    monkeypatch.setattr(deploy.subprocess, "run", fake)
    assert deploy.run() == 3
    assert len(fake.calls) == 1


def test_the_deploy_exit_code_is_returned(layout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools(monkeypatch, npm=NPM, uvx=UVX)
    monkeypatch.setattr(deploy.subprocess, "run", Subprocess([0, 5]))
    assert deploy.run() == 5


def test_a_build_that_leaves_no_index_html_does_not_deploy(
    layout: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (layout / "static" / "index.html").unlink()
    tools(monkeypatch, npm=NPM, uvx=UVX)
    fake = Subprocess()
    monkeypatch.setattr(deploy.subprocess, "run", fake)
    assert deploy.run() == 1
    assert len(fake.calls) == 1
    assert "does not exist" in capsys.readouterr().err


def test_skipping_the_build_without_a_build_only_warns(
    layout: Path, monkeypatch: pytest.MonkeyPatch, run_calls: Subprocess, capsys: pytest.CaptureFixture[str]
) -> None:
    (layout / "static" / "index.html").unlink()
    tools(monkeypatch, uvx=UVX)
    assert deploy.run(skip_build=True) == 0
    assert len(run_calls.calls) == 1
    assert "no web UI" in capsys.readouterr().err


def test_npm_missing_fails_before_anything_runs(
    layout: Path, monkeypatch: pytest.MonkeyPatch, run_calls: Subprocess, capsys: pytest.CaptureFixture[str]
) -> None:
    tools(monkeypatch, uvx=UVX)
    assert deploy.run() == deploy.EXIT_NOT_FOUND
    assert run_calls.calls == []
    assert "npm was not found" in capsys.readouterr().err


def test_uv_missing_is_found_out_before_the_build(
    layout: Path, monkeypatch: pytest.MonkeyPatch, run_calls: Subprocess, capsys: pytest.CaptureFixture[str]
) -> None:
    tools(monkeypatch, npm=NPM)
    assert deploy.run() == deploy.EXIT_NOT_FOUND
    assert run_calls.calls == []
    assert "uvx" in capsys.readouterr().err


def test_a_tool_that_cannot_start_returns_127(
    layout: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tools(monkeypatch, uvx=UVX)
    monkeypatch.setattr(deploy.subprocess, "run", Subprocess(raises=FileNotFoundError("gone")))
    assert deploy.run(skip_build=True) == deploy.EXIT_NOT_FOUND
    assert "Could not start" in capsys.readouterr().err


# ───────────────────────── pieces ─────────────────────────


def test_progress_lines_go_to_stderr_so_stdout_is_only_the_child_output(
    layout: Path, monkeypatch: pytest.MonkeyPatch, run_calls: Subprocess, capsys: pytest.CaptureFixture[str]
) -> None:
    tools(monkeypatch, npm=NPM, uvx=UVX)
    deploy.run()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "npm" in captured.err
    assert "fastapi" in captured.err


def test_the_real_paths_point_at_this_repo() -> None:
    assert (deploy.BACKEND_DIR / "main.py").is_file()
    assert (deploy.REPO_ROOT / "package.json").is_file()
    assert deploy.BACKEND_DIR.parent == deploy.REPO_ROOT
