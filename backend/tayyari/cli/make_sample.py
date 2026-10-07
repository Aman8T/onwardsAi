"""``make-sample``: regenerate the bundled sample profile with the real pipeline (costs a few cents).

Runs the analysis service on the sample persona's fixtures (Riya Kapoor: Haiku readers, Sonnet synthesis and,
when a Jev key is set, the Jev checks) inside a `Container` built on a throw-away SQLite database. One compact
line is printed per event. When the stream ends in ``done`` it writes, to ``--out`` (default: the bundled
``tayyari/sample`` folder):

    sample-profile.json   the profile, pretty-printed (indent 2, UTF-8 characters kept, trailing newline)
    sample-context.json   the chat context stored for it (sources and reader outputs), one compact line

An ``error`` event, a stream that never finishes or a profile that was not stored fails the command and
leaves the existing files untouched. The container is always closed.

The throw-away copy of the settings has no ``DATABASE_URL`` (so a configured Neon database is never touched), a
temporary data folder, the cost guard off (a developer tool, an empty ledger) and no UI pacing.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

from tayyari.container import Container
from tayyari.contract import (
    AnalyzeRequest,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    PhaseEvent,
    Profile,
    ReaderEvent,
    ReadersEvent,
    RequestContext,
    SignalEvent,
    SynthEvent,
)
from tayyari.core.paths import SAMPLE_DIR
from tayyari.core.settings import Settings, get_settings
from tayyari.sample import SAMPLE_GOAL

DEFAULT_OUT = SAMPLE_DIR
"""Where the files go without ``--out`` (the folder `SampleProvider` and the container read the sample from)."""
PROFILE_FILE = "sample-profile.json"
CONTEXT_FILE = "sample-context.json"
SYNTH_STEP_CHARS = 2000
"""A synth event with a character count is printed when it is this much beyond the last printed one."""


def sample_settings(base: Settings, data_dir: Path) -> Settings:
    """A copy of ``base`` for a sample run: SQLite under ``data_dir``, no pacing, cost guard off, quiet logs.

    ``Settings.db`` is a cached property, and `model_copy` carries a cached value along: drop it so it is
    derived again from the new ``database_url`` and ``data_dir``.
    """
    copy = base.model_copy(
        update={
            "data_dir": data_dir,
            "database_url": None,
            "pacing_scale": 0.0,
            "log_level": "WARNING",
            "guard": base.guard.model_copy(update={"enabled": False}),
        }
    )
    copy.__dict__.pop("db", None)
    return copy


class EventPrinter:
    """One compact line per event, with the seconds since the run started."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self._last_synth_chars = 0

    def stamp(self) -> str:
        return f"{time.monotonic() - self.started:6.1f}s"

    def line(self, event: object) -> str | None:
        """The text to print for ``event``, or None for the events that are not worth a line."""
        if isinstance(event, ReadersEvent):
            return "readers: " + ", ".join(f"{r.agent} ({r.src})" for r in event.readers)
        if isinstance(event, ReaderEvent):
            if event.reader.status in ("done", "error"):
                return f"[{event.reader.sourceId}] {event.reader.status.upper()} - {event.reader.line}"
            return None
        if isinstance(event, SignalEvent):
            return f"  + {event.label} ({event.src})"
        if isinstance(event, LogEvent):
            return f"{event.who:<8} {event.text}"
        if isinstance(event, PhaseEvent):
            return f"== {event.phase}: {event.label}"
        if isinstance(event, SynthEvent):
            chars = event.chars or 0
            if chars and chars - self._last_synth_chars < SYNTH_STEP_CHARS and event.step != 3:
                return None
            self._last_synth_chars = chars or self._last_synth_chars
            return f"synth step {event.step}" + (f" - {chars} chars" if chars else "")
        if isinstance(event, DoneEvent):
            return f"done - profile {event.profile.id}"
        if isinstance(event, ErrorEvent):
            return f"ERROR: {event.message}"
        return None

    def show(self, event: object) -> None:
        text = self.line(event)
        if text is not None:
            print(f"{self.stamp()}  {text}", flush=True)


def _write_atomically(files: dict[Path, str]) -> None:
    """Write every file next to its target first, then move them into place (LF line ends, UTF-8)."""
    pending: dict[Path, Path] = {}
    try:
        for target, text in files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(text, encoding="utf-8", newline="\n")
            pending[target] = temporary
        for target, temporary in pending.items():
            os.replace(temporary, target)
    finally:
        for temporary in pending.values():
            temporary.unlink(missing_ok=True)


def render_profile(profile: Profile) -> str:
    """``indent=2``, ``ensure_ascii=False`` and a trailing newline."""
    return json.dumps(profile.to_dict(), indent=2, ensure_ascii=False) + "\n"


def render_context(context: dict[str, object]) -> str:
    """One compact line (the format of the bundled file) with a trailing newline."""
    return json.dumps(context, ensure_ascii=False, separators=(",", ":")) + "\n"


def _summary(profile: Profile) -> list[str]:
    lines = [
        f"{profile.person.name} - {profile.person.headline} - mastery {profile.mastery.score} - "
        f"{len(profile.domains)} domains - {len(profile.skills)} skills - {len(profile.roles)} roles"
    ]
    lines.extend(f"  {role.alignment:>3}%  {role.title} ({role.eta})" for role in profile.roles)
    return lines


async def _spend_line(container: Container) -> str | None:
    """What this run cost (Claude plus Jev), read from the throw-away ledger. Cosmetic: never an error."""
    try:
        micro = await container.repos.limits.spent_today(container.clock.now().date())
    except Exception:
        return None
    return f"Spent this run: ${micro / 1_000_000:.4f}"


async def _run_analysis(container: Container, out: Path) -> int:
    printer = EventPrinter()
    request = AnalyzeRequest(sample=True, goal=SAMPLE_GOAL)
    context = RequestContext(
        request_id=f"cli-make-sample-{uuid.uuid4().hex[:8]}",
        ip_hash=None,
        client_ip=None,
        started_at=container.clock.now(),
    )
    done: Profile | None = None
    failure: str | None = None
    async for event in container.analysis.run(request, context):
        printer.show(event)
        if isinstance(event, DoneEvent):
            done = event.profile
        elif isinstance(event, ErrorEvent):
            failure = event.message

    if failure is not None:
        print(f"\nmake-sample failed: {failure}", file=sys.stderr)
        return 1
    if done is None:
        print("\nmake-sample failed: the analysis ended without a profile.", file=sys.stderr)
        return 1

    stored = await container.repos.profiles.get_for_chat(done.id)
    if stored is None:
        print(
            f"\nmake-sample failed: profile {done.id} was not stored, so there is no chat context.",
            file=sys.stderr,
        )
        return 1
    _, stored_context = stored

    profile_path, context_path = out / PROFILE_FILE, out / CONTEXT_FILE
    await asyncio.to_thread(
        _write_atomically,
        {
            profile_path: render_profile(done),
            context_path: render_context(stored_context.to_dict()),
        },
    )
    print(f"\nWrote {profile_path}")
    print(f"Wrote {context_path}")
    for line in _summary(done):
        print(line)
    spent = await _spend_line(container)
    if spent:
        print(spent)
    return 0


async def generate(base: Settings, out: Path) -> int:
    """Build the throw-away container, run the analysis, write the files, close everything."""
    # Windows can keep a SQLite file locked for a moment after the engine is disposed.
    with tempfile.TemporaryDirectory(prefix="tayyari-sample-", ignore_cleanup_errors=True) as tmp:
        settings = sample_settings(base, Path(tmp))
        print(f"Running the real pipeline on the sample persona (throw-away database in {tmp})", flush=True)
        container = await Container.build(settings)
        try:
            return await _run_analysis(container, out)
        finally:
            await container.aclose()


def run(out: Path | None = None, *, settings: Settings | None = None) -> int:
    """Regenerate the sample files in ``out`` (default `DEFAULT_OUT`). Returns 1 when the run fails."""
    base = settings if settings is not None else get_settings()
    target = Path(out) if out is not None else DEFAULT_OUT
    try:
        return asyncio.run(generate(base, target))
    except Exception as exc:
        print(f"\nmake-sample failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
