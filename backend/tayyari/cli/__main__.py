"""Entry point: ``python -m tayyari.cli <command> [options]`` (see ``--help``).

Command modules are imported only when their command runs, so ``--help`` and ``dev`` do not pay for the
Anthropic SDK, the ingesters or the container.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

INGEST_SECTIONS_HELP = "classify, terms, resume, notes, github, web, errors (opt-in: github-repo)"
"""Shown in ``ingest-check --help``; `ingest_check.SECTIONS` is the real list (a test keeps them equal)."""

EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a port number") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port must be between 1 and 65535, got {port}")
    return port


def build_parser() -> argparse.ArgumentParser:
    """The argument parser with one sub-command per tool."""
    parser = argparse.ArgumentParser(
        prog="python -m tayyari.cli",
        description="Tayyari backend tools. Run from backend/ with `uv run python -m tayyari.cli <command>`.",
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    commands.add_parser(
        "dev",
        help="API with auto-reload on 127.0.0.1 (port: TAYYARI_API_PORT, default 8787)",
        description="Run the API with auto-reload (uvicorn, reload_dirs=the tayyari package) on 127.0.0.1.",
    )

    serve = commands.add_parser(
        "serve",
        help="API without auto-reload",
        description="Run the API without reload (the process a container or a service manager would run).",
    )
    serve.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1)")
    serve.add_argument(
        "--port", type=_port, default=None, help="port to bind (default: TAYYARI_API_PORT, else 8787)"
    )

    commands.add_parser(
        "migrate",
        help="migrate the configured database to the newest schema",
        description="Run the Alembic migrations against DATABASE_URL (SQLite under the user data folder when unset).",
    )

    smoke = commands.add_parser(
        "smoke",
        help="real API calls: Haiku, Sonnet + prompt cache, Jev, DOCX parse",
        description="Real calls with the configured keys: a Haiku structured call (no effort 400), two Sonnet "
        "chat turns sharing a cached system block (the second must read the cache), one Jev noul and a DOCX "
        "resume parse. Prints PASS/FAIL/SKIP per check and the token and cost totals; exits 1 on any FAIL.",
    )
    smoke.add_argument("--skip-claude", action="store_true", help="skip the two Claude checks")
    smoke.add_argument("--skip-jev", action="store_true", help="skip the Jev check")

    sample = commands.add_parser(
        "make-sample",
        help="regenerate the bundled sample profile with the real pipeline",
        description="Run the real pipeline (Haiku readers, Sonnet synthesis, Jev checks) on the sample persona's "
        "fixtures, using a throw-away SQLite database, then write sample-profile.json and sample-context.json.",
    )
    sample.add_argument(
        "--out", type=Path, default=None, help="output folder (default: backend/tayyari/sample)"
    )

    ingest = commands.add_parser(
        "ingest-check",
        help="deterministic ingestion checks (no AI; github and web use the network)",
        description="Checks of the ingesters: link classification, term dictionary, resume and notes parsing "
        "(in-memory TXT, PDF and DOCX), live GitHub and web fetches, and the expected failure cases. "
        f"Sections: {INGEST_SECTIONS_HELP}. Exits 1 when a check fails.",
    )
    ingest.add_argument(
        "--only",
        default=None,
        metavar="A,B",
        help=f"run only these sections (comma separated): {INGEST_SECTIONS_HELP}",
    )
    ingest.add_argument(
        "--url", default=None, help="fetch just this URL and show what the ingester makes of it"
    )

    deploy = commands.add_parser(
        "deploy",
        help="build the web app and deploy backend/ to FastAPI Cloud",
        description="Run `npm run build:static` at the repo root, then `uvx --from fastapi[standard] fastapi "
        "deploy --json .` in backend/ (with UTF-8 forced for the child on any shell).",
    )
    deploy.add_argument("--skip-build", action="store_true", help="do not run `npm run build:static` first")
    return parser


# One small function per command, importing its module on demand (and looked up at call time, so tests can patch it).


def _dev(_args: argparse.Namespace) -> int:
    from . import dev

    return dev.run()


def _serve(args: argparse.Namespace) -> int:
    from . import serve

    return serve.run(args.host, args.port)


def _migrate(_args: argparse.Namespace) -> int:
    from . import migrate

    return migrate.run()


def _smoke(args: argparse.Namespace) -> int:
    from . import smoke

    return smoke.run(skip_claude=args.skip_claude, skip_jev=args.skip_jev)


def _make_sample(args: argparse.Namespace) -> int:
    from . import make_sample

    return make_sample.run(args.out)


def _ingest_check(args: argparse.Namespace) -> int:
    from . import ingest_check

    return ingest_check.run(args.only, args.url)


def _deploy(args: argparse.Namespace) -> int:
    from . import deploy

    return deploy.run(skip_build=args.skip_build)


_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "dev": _dev,
    "serve": _serve,
    "migrate": _migrate,
    "smoke": _smoke,
    "make-sample": _make_sample,
    "ingest-check": _ingest_check,
    "deploy": _deploy,
}


def _use_utf8_streams() -> None:
    """Switch this process's stdout and stderr to UTF-8, and never crash on a character they cannot show.

    PYTHONUTF8 is read once, at launch, so setting it here cannot change this process. Redirected output on
    Windows would otherwise be cp1252, turning the page and model text in a report into mojibake (or an error).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):  # a stream a test harness replaced, or one that is already closed
            pass


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` (default: ``sys.argv[1:]``), run the command and return its exit code."""
    # Child processes (uvicorn's reloader, `fastapi deploy`) inherit UTF-8 mode, so Windows consoles and pipes
    # (cp1252) cannot turn page or model text into an encoding error. setdefault: an explicit value wins.
    os.environ.setdefault("PYTHONUTF8", "1")
    _use_utf8_streams()

    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS.get(args.command or "")
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
