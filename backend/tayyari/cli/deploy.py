"""``deploy``: build the web app, then ``fastapi deploy`` from backend/ (the FastAPI Cloud deploy root).

    npm run build:static                                       at the repo root (skipped with --skip-build)
    uvx --from fastapi[standard] fastapi deploy --json .       in backend/, UTF-8 forced for the child

The child's output goes straight to this terminal (it streams as it arrives; with ``--json`` it is JSON lines).
Our own progress lines go to stderr, so stdout stays exactly what ``fastapi deploy`` printed. On Windows
``npm`` is ``npm.cmd``: both tools are resolved with `shutil.which`, which finds the right file.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from tayyari.core.paths import BACKEND_DIR, REPO_ROOT

BUILD_SCRIPT = "build:static"
FASTAPI_SPEC = "fastapi[standard]"
PYTHON_VERSION = (BACKEND_DIR / ".python-version").read_text(encoding="utf-8").strip() or "3.13"
"""The interpreter the deploy CLI runs on: the backend's own (`.python-version`). Left to itself, ``uvx`` picks the newest
CPython on the machine, and on 2026-11-09 that was 3.15, for which pyyaml (a fastapi[standard] dependency) ships no wheel,
so the deploy died compiling it without a C toolchain."""
EXIT_NOT_FOUND = 127
"""The shell's exit code for a command that does not exist."""


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def build_command(npm: str) -> list[str]:
    """``npm run build:static`` (``vite build`` then a copy of dist/ into backend/static/)."""
    return [npm, "run", BUILD_SCRIPT]


APP_ID_ENV = "FASTAPI_CLOUD_APP_ID"
"""Environment variable holding the FastAPI Cloud app id when backend/ is not linked to an app on this machine."""


def deploy_command(launcher: list[str], app_id: str | None = None) -> list[str]:
    """``<launcher> --python <backend's version> --from fastapi[standard] fastapi deploy [--app-id <id>] --json .``; the
    launcher is ``uvx`` or ``uv tool run``. Without an app id, ``fastapi deploy`` needs backend/ to have been linked once
    with ``fastapi cloud apps create --link`` (or ``fastapi cloud apps link``) on this machine."""
    target = ["--app-id", app_id] if app_id else []
    return [
        *launcher,
        "--python",
        PYTHON_VERSION,
        "--from",
        FASTAPI_SPEC,
        "fastapi",
        "deploy",
        *target,
        "--json",
        ".",
    ]


def deploy_env() -> dict[str, str]:
    """This process's environment with UTF-8 forced, whatever shell started us (cmd, PowerShell, Git Bash)."""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def uv_launcher() -> list[str] | None:
    """``["<path>/uvx"]``, else ``["<path>/uv", "tool", "run"]`` (the same thing), else None."""
    uvx = shutil.which("uvx")
    if uvx is not None:
        return [uvx]
    uv = shutil.which("uv")
    if uv is not None:
        return [uv, "tool", "run"]
    return None


def _call(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> int:
    """Run ``command`` with the terminal's stdout and stderr; its exit code, or 127 when it cannot start."""
    _say(f"> {shlex.join(command)}   (in {cwd})")
    try:
        return subprocess.run(command, cwd=cwd, env=env, check=False).returncode
    except OSError as exc:
        _say(f"Could not start {command[0]}: {exc}")
        return EXIT_NOT_FOUND


def run(skip_build: bool = False, app_id: str | None = None) -> int:
    """Build (unless ``skip_build``) and deploy to ``app_id`` (else ``$FASTAPI_CLOUD_APP_ID``, else the linked app).
    Returns the exit code of the first step that fails, else 0."""
    index = BACKEND_DIR / "static" / "index.html"
    app_id = app_id or os.environ.get(APP_ID_ENV) or None

    # Resolve every tool first: a missing uvx should not be discovered after a two-minute build.
    npm: str | None = None
    if not skip_build:
        npm = shutil.which("npm")
        if npm is None:
            _say(
                "npm was not found on PATH. Install Node.js, or build the web app yourself and pass --skip-build."
            )
            return EXIT_NOT_FOUND
    launcher = uv_launcher()
    if launcher is None:
        _say("Neither uvx nor uv was found on PATH. Install uv (https://docs.astral.sh/uv/) and try again.")
        return EXIT_NOT_FOUND

    if npm is not None:
        code = _call(build_command(npm), cwd=REPO_ROOT)
        if code != 0:
            _say(f"npm run {BUILD_SCRIPT} failed (exit code {code}); nothing was deployed.")
            return code
        if not index.is_file():
            _say(f"The build finished but {index} does not exist; nothing was deployed.")
            return 1
    elif not index.is_file():
        _say(f"Warning: {index} does not exist, so the deployed app will have no web UI. Build it first.")

    code = _call(deploy_command(launcher, app_id), cwd=BACKEND_DIR, env=deploy_env())
    if code != 0:
        _say(f"fastapi deploy failed (exit code {code}).")
    return code
