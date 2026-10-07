"""``export``: write the decision log of a time window as a JSONL corpus, the input of ``replay``.

    python -m tayyari.cli export --since 7d [--until 1d] [--spec first_read.*] [--out evals/corpus/<date>.jsonl]
                                 [--include-sample] [--database-url URL] [--limit N] [--force]

One line per decision that can be asked again. The reads go through `DecisionLogRepo.export`, which joins each
decision with its Jev call and its stored state, so a line carries everything a replay needs:

    id, created_at, spec_id, spec_version, pack, subject_ref, mode, jev_model, state_hash, state,
    question, jev_answer, jev_confidence, claude_answer, incumbent, used, source_used

``state`` is the state as it was sent to Jev (contact data already masked unless ``TAYYARI_JEV__SEND_PII`` was
on), ``question`` is the wire question of this decision's key (``subject_ref``) taken from the stored call, and
``jev_answer`` is the normalised answer of the Jev model named in ``jev_model``.

Rows are skipped, and counted by reason, when they cannot be asked again: no Jev call was made (``no_jev_call``),
the retention purge already removed the state or the questions (``purged``, ``no_question``), or the row belongs
to the bundled sample persona (``sample``, kept with ``--include-sample``). ``--spec`` keeps matching spec ids only.

The database is opened for reading: this module never migrates, writes or deletes. Use a read-only role for
a production URL (``evals/README.md``), and prefer the ``DATABASE_URL`` variable to ``--database-url``: a URL on the
command line ends up in the shell history.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from sqlalchemy.engine import make_url

from tayyari.core.dburl import DbConfig, normalise_database_url
from tayyari.core.paths import BACKEND_DIR
from tayyari.core.settings import Settings, get_settings
from tayyari.storage.db import Database
from tayyari.storage.ports import DecisionLogRepo
from tayyari.storage.repos import build_repositories

EXIT_USAGE = 2

CORPUS_DIR = BACKEND_DIR / "evals" / "corpus"
"""Where ``export`` writes by default (``<date>.jsonl``)."""

SKIP_REASONS: dict[str, str] = {
    "sample": "belong to the sample persona",
    "spec": "do not match --spec",
    "no_jev_call": "had no Jev call (Jev off, circuit open, spend or fallback only)",
    "purged": "lost their state to the retention purge",
    "no_question": "have no stored question for their key",
    "not_json": "hold a value JSON cannot carry",
}
"""Why a decision row is left out of the corpus, with the wording of the summary line."""

_RELATIVE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhdw])$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86_400, "w": 604_800}


# ───────────────────────── arguments ─────────────────────────


def parse_when(text: str, *, now: datetime) -> datetime:
    """A moment from ``--since`` / ``--until``: ``7d``, ``12h``, ``90m``, ``2w`` (that long before ``now``),
    ``now``, or an ISO date or datetime (``2026-10-01``, ``2026-10-01T06:30``; no zone means UTC).

    Raises ``ValueError`` for anything else.
    """
    value = text.strip()
    if value.lower() == "now":
        return now
    relative = _RELATIVE.match(value)
    if relative is not None:
        return now - timedelta(seconds=float(relative.group(1)) * _UNIT_SECONDS[relative.group(2).lower()])
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"cannot read {text!r} as a time: use 7d, 12h, 90m, 2w, now or an ISO date such as 2026-10-01"
        ) from None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def spec_patterns(values: Sequence[str] | None) -> list[str]:
    """``--spec`` values (repeatable, each may hold comma-separated patterns) as a flat list of patterns."""
    return [part.strip() for value in values or () for part in value.split(",") if part.strip()]


def matches_spec(spec_id: str, patterns: Sequence[str]) -> bool:
    """True when ``spec_id`` fits one of the glob ``patterns`` (``first_read.*``), or there are none."""
    return not patterns or any(fnmatch.fnmatchcase(spec_id, pattern) for pattern in patterns)


# ───────────────────────── one decision row to one corpus line ─────────────────────────


def corpus_line(raw: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """The corpus line for an exported decision row, or ``(None, reason)`` when it cannot be asked again."""
    call = raw.get("call")
    if not isinstance(call, Mapping):
        return None, "no_jev_call"
    state = raw.get("state")
    if state is None or call.get("payload_purged_at") is not None:
        return None, "purged"
    key = raw.get("subject_ref") or raw["spec_id"]
    questions = call.get("questions")
    question = questions.get(key) if isinstance(questions, Mapping) else None
    if not isinstance(question, Mapping) or not question:
        return None, "no_question"
    extra = raw.get("extra") or {}
    jev_model = extra.get("jev_model") or call.get("model_served") or call.get("model_requested")
    line = {
        "id": raw["id"],
        "created_at": raw["created_at"],
        "spec_id": raw["spec_id"],
        "spec_version": raw["spec_version"],
        "pack": raw["pack"],
        "subject_ref": key,
        "mode": raw["mode"],
        "jev_model": jev_model,
        "state_hash": call.get("state_hash"),
        "state": state,
        "question": dict(question),
        "jev_answer": raw.get("jev_answer"),
        "jev_confidence": raw.get("jev_confidence"),
        "claude_answer": raw.get("claude_answer"),
        "incumbent": raw.get("incumbent"),
        "used": raw["used"],
        "source_used": raw["source_used"],
    }
    return line, None


# ───────────────────────── the export ─────────────────────────


@dataclass(slots=True)
class ExportStats:
    scanned: int = 0
    written: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    by_spec: Counter[str] = field(default_factory=Counter)
    first_at: str | None = None
    last_at: str | None = None


async def export_corpus(
    repo: DecisionLogRepo,
    sink: TextIO,
    *,
    since: datetime,
    until: datetime | None = None,
    specs: Sequence[str] = (),
    include_sample: bool = False,
    limit: int | None = None,
) -> ExportStats:
    """Stream the decision log of ``[since, until)`` into ``sink`` as JSONL (oldest first) and count what was left out.

    ``limit`` caps the lines written (after filtering); the log is read page by page, so memory stays small.
    """
    stats = ExportStats()
    rows = repo.export(since, until)
    try:
        async for raw in rows:
            stats.scanned += 1
            if raw.get("is_sample") and not include_sample:
                stats.skipped["sample"] += 1
                continue
            if not matches_spec(str(raw["spec_id"]), specs):
                stats.skipped["spec"] += 1
                continue
            line, reason = corpus_line(raw)
            if line is None:
                stats.skipped[str(reason)] += 1
                continue
            try:
                text = json.dumps(line, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError):
                stats.skipped["not_json"] += 1
                continue
            sink.write(text + "\n")
            stats.written += 1
            stats.by_spec[line["spec_id"]] += 1
            stats.first_at = stats.first_at or line["created_at"]
            stats.last_at = line["created_at"]
            if limit is not None and stats.written >= limit:
                break
    finally:
        close = getattr(
            rows, "aclose", None
        )  # an early stop (--limit) must release the generator's connection
        if close is not None:
            await close()
    return stats


async def _export_database(cfg: DbConfig, sink: TextIO, **options: Any) -> ExportStats:
    db = Database(cfg)
    try:
        return await export_corpus(build_repositories(db).decisions, sink, **options)
    finally:
        await db.dispose()


# ───────────────────────── the command ─────────────────────────


def _scrub(message: str, cfg: DbConfig) -> str:
    """The message with the database password masked (a driver error can quote the connection string)."""
    try:
        password = make_url(cfg.url).password
    except Exception:
        return message
    return message.replace(password, "***") if password else message


def _missing_sqlite_file(cfg: DbConfig) -> str | None:
    """The path of a SQLite file database that does not exist (connecting would create an empty one)."""
    if cfg.kind != "sqlite" or cfg.in_memory:
        return None
    database = make_url(cfg.url).database
    if database and not database.startswith("file:") and not Path(database).is_file():
        return database
    return None


def _summary(stats: ExportStats, out: Path, cfg: DbConfig) -> str:
    lines = [
        f"Wrote {stats.written:,} rows to {out} (read {stats.scanned:,} decision rows from {cfg.safe_url})."
    ]
    if stats.written:
        lines.append(f"  window of the rows: {stats.first_at} .. {stats.last_at}")
        lines.append("  by spec: " + ", ".join(f"{spec} {n:,}" for spec, n in sorted(stats.by_spec.items())))
    if stats.skipped:
        lines.append(
            "  left out: "
            + "; ".join(
                f"{n:,} {SKIP_REASONS.get(reason, reason)}" for reason, n in sorted(stats.skipped.items())
            )
        )
    lines.append(
        "  The file holds resume and page text (contact data is masked unless Jev was sent PII): keep it out of git."
    )
    return "\n".join(lines)


def run(
    *,
    since: str,
    until: str | None = None,
    specs: Sequence[str] | None = None,
    out: Path | None = None,
    include_sample: bool = False,
    database_url: str | None = None,
    limit: int | None = None,
    force: bool = False,
    settings: Settings | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    """Export the decision log to a JSONL corpus. Exit code 0 on success, 1 on a database or file problem, 2 on a bad argument."""
    moment = (now or (lambda: datetime.now(UTC)))()
    try:
        start = parse_when(since, now=moment)
        end = None if until is None else parse_when(until, now=moment)
    except ValueError as exc:
        print(f"export: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if end is not None and end <= start:
        print(
            f"export: --until ({end:%Y-%m-%d %H:%M}Z) must be after --since ({start:%Y-%m-%d %H:%M}Z)",
            file=sys.stderr,
        )
        return EXIT_USAGE

    target = out if out is not None else CORPUS_DIR / f"{moment:%Y-%m-%d}.jsonl"
    if target.exists() and not force:
        print(
            f"export: {target} already exists; choose another --out or pass --force to replace it",
            file=sys.stderr,
        )
        return 1

    base = settings if settings is not None else get_settings()
    try:
        cfg = (
            normalise_database_url(database_url, data_dir=base.data_dir, env=base.env)
            if database_url
            else base.db
        )
    except ValueError as exc:  # an unsupported or unparsable URL (the message never echoes it)
        print(f"export: invalid database configuration: {exc}", file=sys.stderr)
        return 1
    missing = _missing_sqlite_file(cfg)
    if missing is not None:
        print(
            f"export: there is no database file at {missing}; set DATABASE_URL (a read-only connection string) or pass --database-url",
            file=sys.stderr,
        )
        return 1

    print(
        f"Exporting decisions from {start:%Y-%m-%d %H:%M}Z{'' if end is None else f' to {end:%Y-%m-%d %H:%M}Z'} out of {cfg.safe_url} ..."
    )
    temp = target.with_name(target.name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temp.open("w", encoding="utf-8", newline="\n") as sink:
            stats = asyncio.run(
                _export_database(
                    cfg,
                    sink,
                    since=start,
                    until=end,
                    specs=spec_patterns(specs),
                    include_sample=include_sample,
                    limit=limit,
                )
            )
        os.replace(temp, target)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        print(f"export: failed: {type(exc).__name__}: {_scrub(str(exc), cfg)}", file=sys.stderr)
        return 1
    except BaseException:  # Ctrl-C: never leave a half-written .part file behind
        temp.unlink(missing_ok=True)
        raise
    print(_summary(stats, target, cfg))
    return 0


__all__ = [
    "CORPUS_DIR",
    "SKIP_REASONS",
    "ExportStats",
    "corpus_line",
    "export_corpus",
    "matches_spec",
    "parse_when",
    "run",
    "spec_patterns",
]
