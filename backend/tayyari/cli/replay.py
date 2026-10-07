"""``replay``: ask a Jev model the questions of an exported corpus again, to compare it with the one that answered them.

    python -m tayyari.cli replay --corpus evals/corpus/2026-10-08.jsonl --model jev-1.14.0
                                 [--concurrency 8] [--shuffle N] [--limit N] [--out FILE] [--seed 0]
                                 [--timeout 30] [--dry-run]

What it sends: decisions are grouped by ``(state hash, pack)``, so each original Jev call is sent once with its
questions (identical questions of the same state are asked once and shared by their rows; more than
`JEV_MAX_QUESTIONS_PER_CALL` questions over one state are split into several calls, as the runner does).
The calls go through `HttpJevClient` with the real key from the settings (``JEV_API_KEY``), at ``--concurrency``
at a time. A rate-limited call waits for ``Retry-After`` and tries again a few times; an ``auth`` error stops the
run. ``--dry-run`` sends nothing: it prints the counts and the estimated cost, and needs no key.

``--shuffle N`` sends the choice questions of every call N more times with their options in a seeded random
order (``--seed``), to measure how often the answer follows the order of the options instead of the content.

What it writes: one line per decision, in corpus order (``--out``, default
``evals/reports/replay-<model>-<timestamp>.jsonl``)::

    id, spec_id, spec_version, pack, subject_ref, key, state_hash, kind, mode, used, source_used, replay_model,
    old {model, answer, confidence}, new {model, answer, confidence} or null, claude_answer, incumbent,
    shuffle {runs, answers, flips} (choice questions), cost_micro_usd, latency_ms, error

``new.answer`` is the normalised answer (`JevAnswer`), like ``old.answer``. The line carries no state, so the report
is a small file; the corpus keeps the text. A Ctrl-C writes the decisions that were answered so far.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tayyari.core.hashing import canonical_json, sha256_hex
from tayyari.core.limits import JEV_MAX_QUESTIONS_PER_CALL
from tayyari.core.paths import BACKEND_DIR
from tayyari.core.settings import Settings, get_settings
from tayyari.llm.errors import JevError
from tayyari.llm.jev import JevAnswer, JevClient, JevQuestion, JevResponse, JsonContent
from tayyari.llm.jev_http import HttpJevClient
from tayyari.llm.models import JEV_PRICES, jev_cost_micro, jev_price_per_mtok
from tayyari.llm.types import CallContext

EXIT_USAGE = 2

REPORTS_DIR = BACKEND_DIR / "evals" / "reports"
"""Where ``replay`` writes by default."""
DEFAULT_CONCURRENCY = 8
DEFAULT_TIMEOUT_S = 30.0
"""One attempt of a replayed call: far above the live budgets (2 to 8 s), because nobody is waiting."""
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT_S = 2.0
RATE_LIMIT_MAX_WAIT_S = 60.0
NO_SUCCESS_LIMIT = 10
"""This many failed calls before the first success stop the run: a misspelled model id would otherwise fail the whole corpus."""
FATAL_KINDS = frozenset({"auth", "disabled"})
"""Errors that every further call would repeat: the run stops instead of spending the whole corpus on them."""
BYTES_PER_TOKEN = 4
"""The estimate `HttpJevClient` uses when the API reports no usage."""
CTX = CallContext(purpose="eval")

ClientFactory = Callable[[Settings], JevClient | None]
"""Builds the Jev client, or returns None when there is no key (the replay then stops with a message)."""
Sleep = Callable[[float], Awaitable[None]]


class CorpusError(ValueError):
    """The corpus file cannot be read (a line is not JSON, or lacks a field). The message names the line."""


# ───────────────────────── the corpus ─────────────────────────


def question_from_wire(wire: Mapping[str, Any]) -> JevQuestion:
    """The `JevQuestion` of a wire question (``{"type", "instructions", "criteria"}``). Raises on an invalid one."""
    return JevQuestion(
        kind=wire.get("type"),  # type: ignore[arg-type]  # validated by the model
        instructions=wire.get("instructions"),
        criteria=wire.get("criteria"),
    )


@dataclass(slots=True)
class CorpusRow:
    """One corpus line, validated. ``question`` is None (and ``error`` says why) when it cannot be asked."""

    index: int
    raw: dict[str, Any]
    id: str
    spec_id: str
    key: str
    pack: str
    state_hash: str
    question: JevQuestion | None
    error: str | None = None


_REQUIRED = ("id", "spec_id", "pack", "state", "question")


def load_corpus(path: Path, limit: int | None = None) -> list[CorpusRow]:
    """Read ``path`` (JSONL from ``export``): the first ``limit`` lines if given. Raises `CorpusError`.

    Rows that share a state keep one copy of it in memory.
    """
    rows: list[CorpusRow] = []
    states: dict[str, Any] = {}
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        raise CorpusError(f"cannot open {path}: {exc.strerror or exc}") from exc
    with handle:
        for number, text in enumerate(handle, start=1):
            if not text.strip():
                continue
            if limit is not None and len(rows) >= limit:
                break
            where = f"{path}:{number}"
            try:
                raw = json.loads(text)
            except ValueError:
                raise CorpusError(f"{where}: not valid JSON") from None
            if not isinstance(raw, dict):
                raise CorpusError(f"{where}: expected a JSON object")
            missing = [name for name in _REQUIRED if name not in raw]
            if missing:
                raise CorpusError(f"{where}: missing field(s) {', '.join(missing)}")
            error: str | None = None
            try:
                state_hash = str(raw.get("state_hash") or sha256_hex(canonical_json(raw["state"])))
            except (TypeError, ValueError):
                state_hash = ""
                error = "the state is not JSON"
            if state_hash:
                raw["state"] = states.setdefault(state_hash, raw["state"])
            question: JevQuestion | None = None
            if error is None:
                try:
                    question = question_from_wire(raw["question"])
                except (ValidationError, AttributeError, TypeError):
                    error = "bad_question: the stored question is not a valid Jev question"
            rows.append(
                CorpusRow(
                    index=len(rows),
                    raw=raw,
                    id=str(raw["id"]),
                    spec_id=str(raw["spec_id"]),
                    key=str(raw.get("subject_ref") or raw["spec_id"]),
                    pack=str(raw["pack"]),
                    state_hash=state_hash,
                    question=question,
                    error=error,
                )
            )
    return rows


# ───────────────────────── the plan: which calls to make ─────────────────────────


@dataclass(slots=True)
class UnitResult:
    """What came back for one question: the base answer, and the answers of the shuffled runs."""

    done: bool = False
    answer: JevAnswer | None = None
    error: str | None = None
    model: str | None = None
    latency_ms: int = 0
    cost_micro_usd: int = 0
    shuffled: list[str | None] = field(default_factory=list)
    """One label per shuffled run (None: the run failed); empty when the question is not shuffled."""
    shuffle_cost_micro_usd: int = 0


@dataclass(slots=True)
class Unit:
    """One question over one state, shared by every row that asked it."""

    key: str
    question: JevQuestion
    rows: list[int] = field(default_factory=list)
    result: UnitResult = field(default_factory=UnitResult)


@dataclass(slots=True)
class PlannedCall:
    """One Jev request: the questions of ``units`` over ``state``."""

    state_hash: str
    pack: str
    state: JsonContent
    units: list[Unit]

    def questions(self) -> dict[str, JevQuestion]:
        return {unit.key: unit.question for unit in self.units}


def plan_calls(
    rows: Sequence[CorpusRow], *, max_questions: int = JEV_MAX_QUESTIONS_PER_CALL
) -> tuple[list[PlannedCall], dict[int, Unit]]:
    """Group the rows by ``(state hash, pack)`` into calls, and map each row index to its unit.

    A question asked by several rows (same state, pack, key and wording) is one unit. Within a group the units keep
    the order of the corpus and fill calls of at most ``max_questions``; a call never holds one key twice (two
    different questions under one key go to different calls). Rows without a valid question have no unit.
    """
    groups: dict[tuple[str, str], list[Unit]] = {}
    states: dict[tuple[str, str], JsonContent] = {}
    seen: dict[tuple[str, str, str, str], Unit] = {}
    by_row: dict[int, Unit] = {}
    for row in rows:
        if row.question is None:
            continue
        group = (row.state_hash, row.pack)
        wording = sha256_hex(canonical_json(row.question.to_wire()))
        unit = seen.get((*group, row.key, wording))
        if unit is None:
            unit = seen[(*group, row.key, wording)] = Unit(row.key, row.question)
            groups.setdefault(group, []).append(unit)
            states.setdefault(group, row.raw["state"])
        unit.rows.append(row.index)
        by_row[row.index] = unit

    calls: list[PlannedCall] = []
    for (state_hash, pack), units in groups.items():
        chunks: list[list[Unit]] = []
        for unit in units:
            if not chunks or len(chunks[-1]) >= max_questions or any(u.key == unit.key for u in chunks[-1]):
                chunks.append([])
            chunks[-1].append(unit)
        calls.extend(PlannedCall(state_hash, pack, states[(state_hash, pack)], chunk) for chunk in chunks)
    return calls, by_row


def shuffled_units(call: PlannedCall) -> list[Unit]:
    """The units of ``call`` that a shuffle changes: choice questions with at least two options."""
    return [u for u in call.units if u.question.kind == "choice" and len(u.question.option_labels) >= 2]


def shuffle_question(question: JevQuestion, rng: random.Random) -> JevQuestion:
    """``question`` (a choice) with its options in a random order; wording and descriptions are unchanged."""
    labels = list(question.option_labels)
    rng.shuffle(labels)
    criteria = question.criteria if isinstance(question.criteria, dict) else {}
    return JevQuestion(
        kind="choice",
        instructions=question.instructions,
        criteria={label: criteria[label] for label in labels},
    )


def shuffle_rng(seed: int, call: PlannedCall, unit: Unit, run: int) -> random.Random:
    """The generator of one shuffled question: the same seed, state, key and run always give the same order."""
    return random.Random(f"{seed}|{call.state_hash}|{unit.key}|{run}")


@dataclass(frozen=True, slots=True)
class PlanSummary:
    rows: int
    replayable_rows: int
    states: int
    questions: int
    calls: int
    shuffle_calls: int
    input_tokens: int
    cost_micro_usd: int


def request_size(call: PlannedCall, model: str, questions: Mapping[str, JevQuestion]) -> int:
    """Bytes of the request body for ``questions`` over the state of ``call``."""
    body = {
        "state": call.state,
        "model": model,
        "questions": {key: q.to_wire() for key, q in questions.items()},
    }
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def summarise_plan(
    rows: Sequence[CorpusRow], calls: Sequence[PlannedCall], model: str, shuffle: int
) -> PlanSummary:
    """Counts and an estimated cost: input tokens are the request bytes over `BYTES_PER_TOKEN`, as the client estimates."""
    tokens = 0
    cost = 0
    shuffle_calls = 0
    for call in calls:
        sizes = [request_size(call, model, call.questions())]
        extra = shuffled_units(call)
        if shuffle and extra:
            shuffle_calls += shuffle
            sizes.extend([request_size(call, model, {u.key: u.question for u in extra})] * shuffle)
        for size in sizes:
            estimated = max(1, size // BYTES_PER_TOKEN)
            tokens += estimated
            cost += jev_cost_micro(model, estimated)
    return PlanSummary(
        rows=len(rows),
        replayable_rows=sum(1 for r in rows if r.question is not None),
        states=len({c.state_hash for c in calls}),
        questions=sum(len(c.units) for c in calls),
        calls=len(calls),
        shuffle_calls=shuffle_calls,
        input_tokens=tokens,
        cost_micro_usd=cost,
    )


# ───────────────────────── asking ─────────────────────────


def _share(total: int, n: int, index: int) -> int:
    """The ``index``-th of ``n`` integer shares of ``total`` (the shares add up to ``total``)."""
    base, rest = divmod(total, max(n, 1))
    return base + (1 if index < rest else 0)


class Replayer:
    """Sends planned calls through one client and fills each unit's `UnitResult`. Counts calls and cost."""

    def __init__(
        self, client: JevClient, *, model: str, timeout_s: float, sleep: Sleep = asyncio.sleep
    ) -> None:
        self.client = client
        self.model = model
        self.timeout_s = timeout_s
        self._sleep = sleep
        self.fatal: str | None = None
        self.calls = 0
        self.failed_calls = 0
        self.cost_micro_usd = 0
        self.served: set[str] = set()

    async def _send(
        self, call: PlannedCall, questions: Mapping[str, JevQuestion]
    ) -> tuple[JevResponse | None, str | None]:
        """One request with rate-limit retries. Returns the response, or the reason there is none. Never raises (except cancellation)."""
        if self.fatal is not None:
            self.failed_calls += 1
            return None, f"skipped after a fatal error ({self.fatal})"
        error = "no attempt was made"
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                response = await self.client.system_one(
                    call.state, questions, model=self.model, timeout_s=self.timeout_s, ctx=CTX
                )
            except JevError as exc:
                error = f"{exc.kind}: {exc}"[:300]
                if exc.kind == "rate_limit" and attempt < RATE_LIMIT_RETRIES:
                    wait = (
                        exc.retry_after_s
                        if exc.retry_after_s is not None
                        else RATE_LIMIT_WAIT_S * (attempt + 1)
                    )
                    await self._sleep(min(max(wait, 0.0), RATE_LIMIT_MAX_WAIT_S))
                    continue
                if exc.kind in FATAL_KINDS:
                    self.fatal = error
                break
            except Exception as exc:  # a client bug: this call fails, the run goes on
                error = f"{type(exc).__name__}: {exc}"[:300]
                break
            self.calls += 1
            self.cost_micro_usd += response.cost_micro_usd
            self.served.add(response.model)
            return response, None
        self._count_failure(error)
        return None, error

    def _count_failure(self, error: str) -> None:
        self.failed_calls += 1
        if self.calls == 0 and self.failed_calls >= NO_SUCCESS_LIMIT and self.fatal is None:
            self.fatal = f"{NO_SUCCESS_LIMIT} calls failed and none succeeded, last: {error}"

    async def base(self, call: PlannedCall) -> None:
        """Ask all questions of ``call`` once, in their original option order."""
        response, error = await self._send(call, call.questions())
        for index, unit in enumerate(call.units):
            result = unit.result
            result.done = True
            if response is None:
                result.error = error
                continue
            result.model = response.model
            result.latency_ms = response.latency_ms
            result.cost_micro_usd = _share(response.cost_micro_usd, len(call.units), index)
            answer = response.answers.get(unit.key)
            if answer is None:
                result.error = "no_answer: the response has no answer for this question"
            else:
                result.answer = answer

    async def shuffled(self, call: PlannedCall, run: int, seed: int) -> None:
        """Ask the choice questions of ``call`` with their options shuffled (run number ``run``)."""
        units = shuffled_units(call)
        if not units:
            return
        questions = {u.key: shuffle_question(u.question, shuffle_rng(seed, call, u, run)) for u in units}
        response, _error = await self._send(call, questions)
        if response is None:
            return
        for index, unit in enumerate(units):
            unit.result.shuffle_cost_micro_usd += _share(response.cost_micro_usd, len(units), index)
            answer = response.answers.get(unit.key)
            if answer is not None and answer.choice and run < len(unit.result.shuffled):
                unit.result.shuffled[run] = answer.choice


async def replay_calls(
    replayer: Replayer, calls: Sequence[PlannedCall], *, concurrency: int, shuffle: int, seed: int
) -> None:
    """Send every base call, then every shuffled one, with at most ``concurrency`` requests in flight."""
    for call in calls:
        for unit in shuffled_units(call):
            unit.result.shuffled = [None] * shuffle
    jobs: deque[Callable[[], Awaitable[None]]] = deque(partial(replayer.base, call) for call in calls)
    if shuffle:
        jobs.extend(partial(replayer.shuffled, call, run, seed) for run in range(shuffle) for call in calls)

    async def worker() -> None:
        while jobs:
            await jobs.popleft()()

    try:
        await asyncio.gather(*(worker() for _ in range(max(1, min(concurrency, len(jobs))))))
    finally:
        await replayer.client.aclose()


# ───────────────────────── the output ─────────────────────────


def result_line(
    row: CorpusRow, unit: Unit | None, *, cost: int, shuffle: int, requested_model: str
) -> dict[str, Any]:
    """The output line of one decision (see the module docstring)."""
    raw = row.raw
    result = unit.result if unit is not None else None
    answer = result.answer if result is not None else None
    line: dict[str, Any] = {
        "id": row.id,
        "spec_id": row.spec_id,
        "spec_version": raw.get("spec_version"),
        "pack": row.pack,
        "subject_ref": raw.get("subject_ref"),
        "key": row.key,
        "state_hash": row.state_hash,
        "kind": row.question.kind if row.question is not None else None,
        "mode": raw.get("mode"),
        "used": raw.get("used"),
        "source_used": raw.get("source_used"),
        "replay_model": requested_model,
        "old": {
            "model": raw.get("jev_model"),
            "answer": raw.get("jev_answer"),
            "confidence": raw.get("jev_confidence"),
        },
        "new": None
        if answer is None
        else {
            "model": (result.model if result is not None else None) or requested_model,
            "answer": answer.model_dump(exclude_none=True),
            "confidence": answer.confidence,
        },
        "claude_answer": raw.get("claude_answer"),
        "incumbent": raw.get("incumbent"),
        "shuffle": None,
        "cost_micro_usd": cost,
        "latency_ms": result.latency_ms if result is not None else 0,
        "error": row.error if result is None else result.error,
    }
    if answer is not None and result is not None and answer.kind == "choice" and shuffle and result.shuffled:
        got = [label for label in result.shuffled if label is not None]
        line["shuffle"] = {
            "runs": shuffle,
            "answers": list(result.shuffled),
            "flips": sum(1 for label in got if label != answer.choice),
        }
    return line


def result_lines(
    rows: Sequence[CorpusRow], by_row: Mapping[int, Unit], *, shuffle: int, requested_model: str
) -> list[dict[str, Any]]:
    """The output lines for every row that has an outcome (an interrupted run leaves the rest out), in corpus order."""
    costs: dict[int, int] = {}
    for shared in {id(u): u for u in by_row.values()}.values():
        total = shared.result.cost_micro_usd + shared.result.shuffle_cost_micro_usd
        for position, index in enumerate(shared.rows):
            costs[index] = _share(total, len(shared.rows), position)
    lines: list[dict[str, Any]] = []
    for row in rows:
        unit = by_row.get(row.index)
        if unit is not None and not unit.result.done:
            continue
        lines.append(
            result_line(
                row, unit, cost=costs.get(row.index, 0), shuffle=shuffle, requested_model=requested_model
            )
        )
    return lines


def write_lines(path: Path, lines: Sequence[Mapping[str, Any]]) -> None:
    """Write ``lines`` as JSONL (UTF-8, ``\\n`` line ends on every platform), creating the folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")


def usd(micro: int) -> str:
    """Micro-USD as dollars: four decimals, six for amounts under a tenth of a cent."""
    dollars = micro / 1_000_000
    return f"${dollars:.4f}" if micro == 0 or micro >= 1_000 else f"${dollars:.6f}"


def default_out(model: str, now: datetime) -> Path:
    """``evals/reports/replay-<model>-<UTC timestamp>.jsonl`` (the model id made safe for a file name)."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", model).strip("_") or "model"
    return REPORTS_DIR / f"replay-{safe}-{now:%Y%m%dT%H%M%SZ}.jsonl"


def default_client(settings: Settings) -> JevClient | None:
    """The real Jev client, or None without a key. The kill switch (``TAYYARI_JEV__ENABLED``) guards the live app, so a
    replay, which never touches it, ignores the switch."""
    if settings.jev_api_key is None:
        return None
    return HttpJevClient(
        settings=settings.jev.model_copy(update={"enabled": True}), api_key=settings.jev_api_key
    )


# ───────────────────────── the command ─────────────────────────


def _print_plan(summary: PlanSummary, corpus: Path, model: str, shuffle: int, *, dry_run: bool) -> None:
    head = "Replay plan (dry run: nothing is sent)" if dry_run else "Replay plan"
    price = jev_price_per_mtok(model)
    lines = [
        head,
        f"  corpus:     {corpus} ({summary.rows:,} rows, {summary.replayable_rows:,} can be asked)",
        f"  questions:  {summary.questions:,} distinct, over {summary.states:,} state{'' if summary.states == 1 else 's'}",
        f"  Jev calls:  {summary.calls:,}"
        + (f" + {summary.shuffle_calls:,} shuffled ({shuffle} per call with choices)" if shuffle else ""),
        f"  input:      about {summary.input_tokens:,} tokens (request bytes / {BYTES_PER_TOKEN})",
        f"  model:      {model} at ${price:g} per million input tokens: about {usd(summary.cost_micro_usd)}",
    ]
    if model.strip().lower() not in JEV_PRICES:
        lines.append(f"              ({model} has no known price: priced at the highest known Jev rate)")
    print("\n".join(lines))


def run(
    *,
    corpus: Path,
    model: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    shuffle: int = 0,
    limit: int | None = None,
    out: Path | None = None,
    dry_run: bool = False,
    seed: int = 0,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    settings: Settings | None = None,
    client_factory: ClientFactory | None = None,
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] | None = None,
) -> int:
    """Replay ``corpus`` against ``model``. Exit code 0 when at least one decision was answered, 1 on a corpus,
    key or run problem, 2 on a bad argument."""
    if not model.strip():
        print("replay: --model must not be blank", file=sys.stderr)
        return EXIT_USAGE
    if concurrency < 1 or shuffle < 0 or (limit is not None and limit < 1) or timeout_s <= 0:
        print(
            "replay: --concurrency and --limit must be at least 1, --shuffle at least 0, --timeout positive",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        rows = load_corpus(corpus, limit)
    except CorpusError as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print(f"replay: {corpus} has no rows", file=sys.stderr)
        return 1
    calls, by_row = plan_calls(rows)
    _print_plan(summarise_plan(rows, calls, model, shuffle), corpus, model, shuffle, dry_run=dry_run)
    if dry_run:
        return 0
    if not calls:
        print("replay: no row has a question that can be asked", file=sys.stderr)
        return 1

    base = settings if settings is not None else get_settings()
    try:
        client = (client_factory or default_client)(base)
    except Exception as exc:
        print(f"replay: could not build the Jev client: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if client is None:
        print(
            "replay: no Jev key is configured (set JEV_API_KEY in the environment or .env), or use --dry-run",
            file=sys.stderr,
        )
        return 1

    moment = (now or (lambda: datetime.now(UTC)))()
    target = out if out is not None else default_out(model, moment)
    replayer = Replayer(client, model=model, timeout_s=timeout_s, sleep=sleep)
    try:
        asyncio.run(replay_calls(replayer, calls, concurrency=concurrency, shuffle=shuffle, seed=seed))
    finally:  # also on Ctrl-C: keep what was answered
        lines = result_lines(rows, by_row, shuffle=shuffle, requested_model=model)
        if lines:
            write_lines(target, lines)

    answered = sum(1 for line in lines if line["new"] is not None)
    failed = len(lines) - answered
    shuffle_note = f" (+ {shuffle} shuffled runs of the choice questions)" if shuffle else ""
    print(
        f"Replayed {len(lines):,} decisions with {model} in {replayer.calls:,} Jev calls{shuffle_note}: "
        f"{answered:,} answered, {failed:,} failed; cost {usd(replayer.cost_micro_usd)}."
    )
    served = sorted(replayer.served - {model})
    if served:
        print(f"  the API served {', '.join(served)} for {model}")
    if replayer.fatal is not None:
        print(f"  stopped early: {replayer.fatal}", file=sys.stderr)
    print(f"Wrote {target}")
    if answered:
        print(f"Next: python -m tayyari.cli eval report --replay {target} --labels evals/labels/*.jsonl")
    return 0 if answered else 1


__all__ = [
    "CorpusError",
    "CorpusRow",
    "PlanSummary",
    "PlannedCall",
    "Replayer",
    "Unit",
    "UnitResult",
    "default_client",
    "default_out",
    "load_corpus",
    "plan_calls",
    "question_from_wire",
    "replay_calls",
    "result_lines",
    "run",
    "shuffle_question",
    "summarise_plan",
    "usd",
    "write_lines",
]
