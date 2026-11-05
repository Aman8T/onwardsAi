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
from typing import Any

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


def _count(minimum: int) -> Callable[[str], int]:
    """An argparse type for an integer that must be at least ``minimum``."""

    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
        if number < minimum:
            raise argparse.ArgumentTypeError(f"must be at least {minimum}, got {number}")
        return number

    return parse


def _fraction(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not 0.0 < number <= 1.0:
        raise argparse.ArgumentTypeError(f"must be above 0 and at most 1, got {number:g}")
    return number


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

    portfolio_eval = commands.add_parser(
        "portfolio-eval",
        help="build a portfolio for every fixture with the real clients and report grounding",
        description="Run PortfolioService.build on tests/data/portfolio/*.json (Haiku facts, Sonnet copy, Jev when a "
        "key is set) and print, per fixture, the facts kept and dropped, slots by origin, re-asks, fallbacks, "
        "ungrounded slots, cost and seconds. Exits 1 when any slot is ungrounded or a build fails. --offline "
        "makes no model call (fallback wording only).",
    )
    portfolio_eval.add_argument(
        "--only",
        default=None,
        metavar="A,B",
        help="run only these fixtures (comma separated names, e.g. riya)",
    )
    portfolio_eval.add_argument(
        "--write",
        action="store_true",
        help="write each Portfolio as tests/data/portfolio/rendered/<name>.portfolio.json",
    )
    portfolio_eval.add_argument(
        "--offline",
        action="store_true",
        help="no model calls: no facts, fallback wording only (needs no keys)",
    )
    portfolio_eval.add_argument(
        "--details", action="store_true", help="also list every fact, field and highlight that was dropped"
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

    _add_discover_commands(commands)
    _add_weekly_commands(commands)
    _add_apply_commands(commands)
    _add_eval_commands(commands)
    return parser


def _positive_hours(value: str) -> float:
    try:
        hours = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number of hours") from None
    if hours <= 0:
        raise argparse.ArgumentTypeError(f"must be above 0, got {hours:g}")
    return hours


def _add_discover_commands(commands: Any) -> None:
    """``discover-refresh`` and ``discover-worker``: read employers' own public job boards into the database."""
    refresh = commands.add_parser(
        "discover-refresh",
        help="read employers' own public job boards (Greenhouse, Lever) into the database, once",
        description="One refresh of Discover's listings: fetch each employer's public board from the registry "
        "(no key needed), keep the India listings, close the ones a board no longer lists. Uses DATABASE_URL (SQLite "
        "locally when unset). Exits 1 when no board could be read.",
    )
    refresh.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="read this registry file instead of the bundled employers.json",
    )
    worker = commands.add_parser(
        "discover-worker",
        help="run discover-refresh in a loop (its own process, next to or instead of the web app's background refresh)",
        description="Refresh Discover's listings every --every hours until stopped. Shares the single-flight rule with "
        "the web app, so the two never read a board at the same time.",
    )
    worker.add_argument(
        "--every",
        type=_positive_hours,
        default=None,
        metavar="HOURS",
        help="hours between refreshes (default: TAYYARI_DISCOVER__REFRESH_HOURS, 6)",
    )
    worker.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="read this registry file instead of the bundled employers.json",
    )


def _add_apply_commands(commands: Any) -> None:
    """``apply-worker`` and ``apply-metrics``: auto-apply's background worker and its learning-loop numbers."""
    worker = commands.add_parser(
        "apply-worker",
        help="run auto-apply's worker: fill queued applications, submit approved ones (needs AUTO_APPLY_ENABLED)",
    )
    worker.add_argument("--once", action="store_true", help="run until nothing is left to do, then stop")
    worker.add_argument("--headed", action="store_true", help="show the browser window")
    metrics = commands.add_parser(
        "apply-metrics", help="playbook vs AI runs, success and failure reasons per ATS"
    )
    metrics.add_argument("--days", type=_count(1), default=30)
    metrics.add_argument("--json", action="store_true")


def _apply_worker(args: argparse.Namespace) -> int:
    from . import apply

    return apply.run_worker(once=args.once, headed=args.headed)


def _apply_metrics(args: argparse.Namespace) -> int:
    from . import apply

    return apply.run_metrics(days=args.days, as_json=args.json)


def _add_weekly_commands(commands: Any) -> None:
    """``weekly run`` and ``weekly preview``: The Leafout Weekly by hand."""
    weekly = commands.add_parser(
        "weekly",
        help="The Leafout Weekly by hand: one scheduler pass, or one reader's edition",
        description="`weekly run` does one pass of the scheduler now (build and email what is due; it acts only on a "
        "reader's day, from 06:00 India time). `weekly preview EMAIL` builds this week's edition for that account "
        "(one paid run, a few cents, when ANTHROPIC_API_KEY is set), stores it, and writes its email as an HTML file.",
    )
    actions = weekly.add_subparsers(dest="weekly_command", metavar="<action>", required=True)
    actions.add_parser(
        "run",
        help="one scheduler pass now",
        description="Compose and email the editions that are due, exactly as the background loop does every 15 minutes.",
    )
    preview = actions.add_parser(
        "preview",
        help="build and store this week's edition for an account; print its id and the local HTML path",
        description="Build this week's edition for the account with this email address (the stored one when it is "
        "already built), then print its id and the path of its email as an HTML file under the data folder.",
    )
    preview.add_argument("email", help="the account's sign-in email address")


def _add_eval_commands(commands: Any) -> None:
    """``export``, ``replay`` and ``eval``: the Jev adoption tools (the procedure is in evals/README.md)."""
    export = commands.add_parser(
        "export",
        help="write the Jev decision log of a time window as a JSONL corpus",
        description="Read the decision log of DATABASE_URL (or --database-url; use a read-only connection string) "
        "and write one JSONL line per decision that can be asked again: the state as sent, the stored question, "
        "and the old Jev, Claude and incumbent answers. Skips rows whose payload was purged.",
    )
    export.add_argument(
        "--since", required=True, metavar="WHEN", help="start of the window: 7d, 12h, 2w or an ISO date"
    )
    export.add_argument("--until", default=None, metavar="WHEN", help="end of the window (default: now)")
    export.add_argument(
        "--spec",
        action="append",
        default=None,
        metavar="PATTERN",
        help="only these spec ids, a glob such as 'first_read.*' (repeat it, or separate patterns with commas)",
    )
    export.add_argument(
        "--out", type=Path, default=None, help="output file (default: evals/corpus/<today>.jsonl)"
    )
    export.add_argument(
        "--include-sample", action="store_true", help="also export the rows of the bundled sample persona"
    )
    export.add_argument(
        "--database-url",
        default=None,
        help="database to read (default: DATABASE_URL; the variable keeps it out of the shell history)",
    )
    export.add_argument("--limit", type=_count(1), default=None, metavar="N", help="stop after N rows")
    export.add_argument("--force", action="store_true", help="replace the output file if it exists")

    replay = commands.add_parser(
        "replay",
        help="ask a Jev model the questions of a corpus again (costs real Jev calls)",
        description="Re-send each original Jev call (grouped by state and pack) to --model with the real key from "
        "JEV_API_KEY, and write the new answer next to the old one, one line per decision. --dry-run prints the "
        "counts and the estimated cost without calling.",
    )
    replay.add_argument(
        "--corpus", type=Path, required=True, metavar="FILE", help="the JSONL written by `export`"
    )
    replay.add_argument(
        "--model", required=True, metavar="JEV_MODEL", help="the Jev model to ask, e.g. jev-1.14.0"
    )
    replay.add_argument(
        "--concurrency", type=_count(1), default=8, metavar="N", help="requests in flight (default: 8)"
    )
    replay.add_argument(
        "--shuffle",
        type=_count(0),
        default=0,
        metavar="N",
        help="also send the choice questions N times with the options shuffled, to measure order instability",
    )
    replay.add_argument(
        "--limit", type=_count(1), default=None, metavar="N", help="replay only the first N rows"
    )
    replay.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output file (default: evals/reports/replay-<model>-<timestamp>.jsonl)",
    )
    replay.add_argument("--seed", type=int, default=0, help="seed of the option shuffles (default: 0)")
    replay.add_argument(
        "--timeout", type=float, default=30.0, metavar="S", help="seconds per Jev attempt (default: 30)"
    )
    replay.add_argument(
        "--dry-run", action="store_true", help="print counts and the estimated cost; send nothing"
    )

    evaluate = commands.add_parser(
        "eval",
        help="judge a replay: report agreement, or propose thresholds",
        description="`eval report` compares the new answers with a reference (hand label, Claude answer, incumbent, "
        "old Jev). `eval promote` proposes the lowest confidence thresholds that meet a target agreement.",
    )
    actions = evaluate.add_subparsers(dest="eval_command", metavar="<action>", required=True)
    report = actions.add_parser(
        "report",
        help="agreement, accuracy by confidence, escalation, order instability and cost per spec",
        description="Print the table and write it as JSON next to the replay file (<replay>.report.json).",
    )
    report.add_argument(
        "--replay", type=Path, required=True, metavar="FILE", help="the JSONL written by `replay`"
    )
    report.add_argument(
        "--labels",
        nargs="*",
        default=None,
        metavar="FILE",
        help="hand label files or globs, e.g. evals/labels/*.jsonl",
    )
    promote = actions.add_parser(
        "promote",
        help="propose confidence thresholds for the primary specs (prints a defaults.toml patch)",
        description="For each primary spec, the lowest threshold whose answers at or above it agree with the "
        "reference at --target or better over at least --min-rows decisions. Prints a patch; never edits "
        "defaults.toml.",
    )
    promote.add_argument(
        "--replay", type=Path, required=True, metavar="FILE", help="the JSONL written by `replay`"
    )
    promote.add_argument(
        "--target", type=_fraction, default=0.9, metavar="X", help="agreement to reach (default: 0.9)"
    )
    promote.add_argument(
        "--min-rows",
        type=_count(1),
        default=30,
        metavar="N",
        help="rows needed above a threshold (default: 30)",
    )
    promote.add_argument(
        "--labels",
        nargs="*",
        default=None,
        metavar="FILE",
        help="hand label files or globs, e.g. evals/labels/*.jsonl",
    )
    promote.add_argument(
        "--dry-run",
        action="store_true",
        help="only print; without it the proposal is also written as JSON next to the replay",
    )


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


def _portfolio_eval(args: argparse.Namespace) -> int:
    from . import portfolio_eval

    return portfolio_eval.run(args.only, write=args.write, offline=args.offline, details=args.details)


def _ingest_check(args: argparse.Namespace) -> int:
    from . import ingest_check

    return ingest_check.run(args.only, args.url)


def _discover_refresh(args: argparse.Namespace) -> int:
    from . import discover

    return discover.run_refresh(args.registry)


def _discover_worker(args: argparse.Namespace) -> int:
    from . import discover

    return discover.run_worker(args.every, args.registry)


def _weekly(args: argparse.Namespace) -> int:
    from . import weekly

    if args.weekly_command == "run":
        return weekly.run_pass()
    return weekly.run_preview(args.email)


def _deploy(args: argparse.Namespace) -> int:
    from . import deploy

    return deploy.run(skip_build=args.skip_build)


def _export(args: argparse.Namespace) -> int:
    from . import export

    return export.run(
        since=args.since,
        until=args.until,
        specs=args.spec,
        out=args.out,
        include_sample=args.include_sample,
        database_url=args.database_url,
        limit=args.limit,
        force=args.force,
    )


def _replay(args: argparse.Namespace) -> int:
    from . import replay

    return replay.run(
        corpus=args.corpus,
        model=args.model,
        concurrency=args.concurrency,
        shuffle=args.shuffle,
        limit=args.limit,
        out=args.out,
        dry_run=args.dry_run,
        seed=args.seed,
        timeout_s=args.timeout,
    )


def _eval(args: argparse.Namespace) -> int:
    from . import evals

    if args.eval_command == "report":
        return evals.run_report(replay=args.replay, labels=args.labels)
    return evals.run_promote(
        replay=args.replay,
        target=args.target,
        min_rows=args.min_rows,
        labels=args.labels,
        dry_run=args.dry_run,
    )


_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "dev": _dev,
    "serve": _serve,
    "migrate": _migrate,
    "smoke": _smoke,
    "make-sample": _make_sample,
    "ingest-check": _ingest_check,
    "portfolio-eval": _portfolio_eval,
    "deploy": _deploy,
    "discover-refresh": _discover_refresh,
    "discover-worker": _discover_worker,
    "weekly": _weekly,
    "apply-worker": _apply_worker,
    "apply-metrics": _apply_metrics,
    "export": _export,
    "replay": _replay,
    "eval": _eval,
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
