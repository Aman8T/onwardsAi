"""``portfolio-eval``: build a portfolio for every fixture with the real clients and report how well it grounded.

For each ``tests/data/portfolio/*.json`` (``{name, about, profile, context}``; ``--only a,b`` picks some) it runs
`PortfolioService.build` (Haiku facts, Jev's first read and claim checks when a key is set, the composer, Sonnet
copy, the grounding checks and the fallback wording) inside a `Container` built on a throw-away SQLite database,
and prints one table row per fixture::

    name   facts kept/dropped   slots model/resume   re-asked   fallbacks   ungrounded   cost   seconds

``cost`` is the spend ledger's growth during the build (Claude and Jev). ``ungrounded`` counts generated slots
that failed the final strict check, and must be 0: the command exits 1 when any is not, or when a build fails.
``--write`` stores the `Portfolio` JSON of each fixture as ``tests/data/portfolio/rendered/<name>.portfolio.json``
(what ``scripts/portfolio-parity.ts`` renders). ``--offline`` makes no model call (no facts, fallback wording
only) for a quick look at the design and the rendering; it needs no keys. ``--details`` lists what was dropped.

The online run needs ``ANTHROPIC_API_KEY`` (``JEV_API_KEY`` is optional) and costs a few cents per fixture. The
throw-away copy of the settings has the cost guard off and no ``DATABASE_URL``, like ``make-sample``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tayyari.cli.make_sample import _write_atomically, sample_settings
from tayyari.container import Container
from tayyari.contract.models import Profile
from tayyari.contract.stored import StoredContext
from tayyari.core.paths import BACKEND_DIR
from tayyari.core.settings import Settings, get_settings
from tayyari.decisions.service import DecisionContext
from tayyari.llm.types import CallContext
from tayyari.portfolio.service import BuildReport

FIXTURES_DIR = BACKEND_DIR / "tests" / "data" / "portfolio"
RENDERED_NAME = "rendered"
EXIT_USAGE = 2
DRAIN_TIMEOUT_S = 5.0

ContainerFactory = Callable[[Settings], Awaitable[Container]]


@dataclass(frozen=True)
class Row:
    name: str
    report: BuildReport | None
    cost_micro: int
    seconds: float
    error: str | None = None


class UsageError(Exception):
    """A request the command cannot start (an unknown fixture, no key)."""


def usd(micro: int) -> str:
    return f"${micro / 1_000_000:.4f}"


def fixture_paths(folder: Path, only: Sequence[str]) -> list[Path]:
    """The fixture files to run (all of them, or the named ones in the order asked); raises `UsageError`."""
    found = {p.stem: p for p in sorted(folder.glob("*.json"))}
    if not found:
        raise UsageError(f"no fixtures in {folder}")
    if not only:
        return list(found.values())
    unknown = [name for name in only if name not in found]
    if unknown:
        raise UsageError(f"unknown fixture(s): {', '.join(unknown)} (have: {', '.join(found)})")
    return [found[name] for name in dict.fromkeys(only)]


def load_fixture(path: Path) -> tuple[Profile, StoredContext]:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return Profile.from_dict(raw["profile"]), StoredContext.model_validate(raw["context"])


async def default_container(settings: Settings) -> Container:
    return await Container.build(settings)


async def _spent(container: Container) -> int:
    """Micro-USD in today's ledger (cosmetic: 0 when it cannot be read)."""
    try:
        await container.decisions.drain(
            DRAIN_TIMEOUT_S
        )  # the Jev rows reach the ledger when the log is flushed
        return await container.repos.limits.spent_today(container.clock.now().date())
    except Exception:
        return 0


async def _evaluate_one(
    container: Container, name: str, path: Path, *, offline: bool
) -> tuple[Row, Any | None]:
    started = time.monotonic()
    before = await _spent(container)
    try:
        profile, ctx = load_fixture(path)
        request_id = f"cli-portfolio-eval-{name}"
        result = await container.portfolio.build(
            profile,
            ctx,
            profile_id=profile.id,
            existing=None,
            call_ctx=CallContext(purpose="portfolio", request_id=request_id, profile_id=profile.id),
            decision_ctx=DecisionContext(profile.id, None, True, request_id, purpose_tag="portfolio"),
            offline=offline,
        )
    except (ValidationError, KeyError, ValueError, OSError) as exc:
        return Row(
            name, None, 0, time.monotonic() - started, f"bad fixture: {type(exc).__name__}: {exc}"
        ), None
    except Exception as exc:
        return Row(name, None, 0, time.monotonic() - started, f"{type(exc).__name__}: {exc}"), None
    seconds = time.monotonic() - started
    cost = max(0, await _spent(container) - before)
    return Row(name, result.report, cost, seconds), result.portfolio


def _notes(report: BuildReport) -> str:
    notes: list[str] = []
    if report.facts_failed:
        notes.append("facts failed")
    if report.facts_reused:
        notes.append("facts reused")
    if report.copy_failed:
        notes.append("copy failed")
    if report.slots_missing:
        notes.append(f"{report.slots_missing} slot{'' if report.slots_missing == 1 else 's'} empty")
    if report.parts_dropped:
        notes.append(f"{report.parts_dropped} parts dropped")
    return ", ".join(notes)


def render_table(rows: Sequence[Row]) -> list[str]:
    """The report table as lines (header, one row per fixture, a total)."""
    width = max([len("name"), *(len(r.name) for r in rows)])
    head = (
        f"{'name':<{width}}  {'facts k/d':>9}  {'slots m/r':>9}  {'re-asked':>8}  {'fallbacks':>9}  "
        f"{'ungrounded':>10}  {'cost':>8}  {'seconds':>7}  notes"
    )
    lines = [head, "-" * len(head)]
    total_cost = 0
    total_ungrounded = 0
    for r in rows:
        total_cost += r.cost_micro
        rep = r.report
        if rep is None:
            lines.append(f"{r.name:<{width}}  ERROR  {r.error}")
            continue
        total_ungrounded += rep.ungrounded
        lines.append(
            f"{r.name:<{width}}  {f'{rep.facts_kept}/{rep.facts_dropped}':>9}  "
            f"{f'{rep.slots_model}/{rep.slots_resume}':>9}  {rep.reasked:>8}  {rep.fallbacks:>9}  "
            f"{rep.ungrounded:>10}  {usd(r.cost_micro):>8}  {r.seconds:>7.1f}  {_notes(rep)}"
        )
    lines.append("-" * len(head))
    lines.append(f"total: ungrounded {total_ungrounded}, cost {usd(total_cost)}")
    return lines


def _detail_lines(rows: Sequence[Row]) -> list[str]:
    lines: list[str] = []
    for r in rows:
        if r.report is None or not r.report.dropped:
            continue
        lines.append(f"{r.name}: dropped")
        lines.extend(f"  {d.part:<9} {d.kind:<11} {d.title!r}: {d.reason}" for d in r.report.dropped)
    return lines


async def evaluate(
    base: Settings,
    *,
    only: Sequence[str] = (),
    write: bool = False,
    offline: bool = False,
    details: bool = False,
    fixtures: Path | None = None,
    out: Path | None = None,
    container_factory: ContainerFactory | None = None,
) -> int:
    """Build every selected fixture, print the table and return 1 when anything failed or is ungrounded."""
    folder = fixtures if fixtures is not None else FIXTURES_DIR
    target = out if out is not None else folder / RENDERED_NAME
    paths = fixture_paths(folder, only)
    if not offline and base.anthropic_api_key is None and container_factory is None:
        raise UsageError("ANTHROPIC_API_KEY is not set (put it in .env or the environment), or use --offline")

    factory = container_factory or default_container
    rows: list[Row] = []
    rendered: dict[Path, str] = {}
    with tempfile.TemporaryDirectory(prefix="tayyari-portfolio-eval-", ignore_cleanup_errors=True) as tmp:
        settings = sample_settings(base, Path(tmp))
        mode = "offline (no model calls)" if offline else "real clients"
        print(f"portfolio-eval: {len(paths)} fixture(s), {mode}, throw-away database in {tmp}", flush=True)
        container = await factory(settings)
        try:
            for path in paths:
                row, portfolio = await _evaluate_one(container, path.stem, path, offline=offline)
                rows.append(row)
                print(
                    f"  {row.name}: {'ERROR ' + (row.error or '') if row.report is None else 'done'}",
                    flush=True,
                )
                if portfolio is not None:
                    text = json.dumps(portfolio.to_dict(), indent=2, ensure_ascii=False) + "\n"
                    rendered[target / f"{path.stem}.portfolio.json"] = text
        finally:
            await container.aclose()

    print()
    for line in render_table(rows):
        print(line)
    if details:
        extra = _detail_lines(rows)
        if extra:
            print()
            for line in extra:
                print(line)
    if write and rendered:
        await asyncio.to_thread(_write_atomically, rendered)
        print(f"\nWrote {len(rendered)} file(s) to {target}")

    failed = [r for r in rows if r.report is None]
    ungrounded = sum(r.report.ungrounded for r in rows if r.report is not None)
    if failed or ungrounded:
        print(
            f"\nportfolio-eval failed: {len(failed)} build error(s), {ungrounded} ungrounded slot(s)",
            file=sys.stderr,
        )
        return 1
    return 0


def run(
    only: str | None = None,
    *,
    write: bool = False,
    offline: bool = False,
    details: bool = False,
    settings: Settings | None = None,
    fixtures: Path | None = None,
    out: Path | None = None,
    container_factory: ContainerFactory | None = None,
) -> int:
    """Run the evaluation (see the module docstring). ``only`` is a comma separated list of fixture names."""
    base = settings if settings is not None else get_settings()
    names = [n.strip() for n in (only or "").split(",") if n.strip()]
    try:
        return asyncio.run(
            evaluate(
                base,
                only=names,
                write=write,
                offline=offline,
                details=details,
                fixtures=fixtures,
                out=out,
                container_factory=container_factory,
            )
        )
    except UsageError as exc:
        print(f"portfolio-eval: {exc}", file=sys.stderr)
        return EXIT_USAGE


__all__ = ["FIXTURES_DIR", "Row", "UsageError", "evaluate", "fixture_paths", "render_table", "run"]
