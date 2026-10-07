"""``smoke``: real calls with the configured keys, one clear PASS / FAIL / SKIP line per check (costs a few cents).

    haiku structured   one Haiku call with a tiny schema through `AnthropicClaudeClient`. The call asks for
                       ``effort="low"`` on purpose: Haiku 4.5 rejects effort with a 400, so the client must not
                       send it. The proof is that the first request succeeds (``attempts == 1``) and the usage
                       ledger shows exactly one request row, with no rejected request and retry.
    sonnet cache       two `stream_chat` turns that share a cached system `TextBlock` of roughly 2,500 tokens of
                       static text. The second turn must read it back (``cache_read_tokens > 0``); write and
                       read tokens are reported.
    jev noul           one yes/no question through `HttpJevClient`; SKIP without a JEV_API_KEY.
    docx resume        a DOCX built in memory with python-docx, parsed by the resume ingester (no network).

Per-call tokens and the total cost come from a `MemoryUsageSink`, the same ledger rows the app writes to
``llm_calls``. The exit code is 1 when any check fails. ``--skip-claude`` and ``--skip-jev`` skip those checks.

The clients are built by factories that tests replace (`run(claude_factory=..., jev_factory=...)`), so the whole
run is exercised offline against `tests/fakes`.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from tayyari.cli.ingest_check import RESUME_LINES, make_docx
from tayyari.core.logging import configure_logging
from tayyari.core.settings import Settings, get_settings
from tayyari.ingest import ingest_resume
from tayyari.llm.anthropic_client import AnthropicClaudeClient
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError, JevError
from tayyari.llm.jev import JevClient, JevQuestion
from tayyari.llm.jev_http import HttpJevClient
from tayyari.llm.models import spec_for
from tayyari.llm.types import CallContext, MessageParam, TextBlock, TurnEnd
from tayyari.llm.usage import MemoryUsageSink, UsageSink

Status = Literal["PASS", "FAIL", "SKIP"]
ClaudeFactory = Callable[[Settings, UsageSink], ClaudeClient]
"""Builds the Claude client; it must report every request to the sink it is given."""
JevFactory = Callable[[Settings], JevClient | None]
"""Builds the Jev client, or returns None when Jev is not configured (the check is then skipped)."""

CTX = CallContext(purpose="smoke")
SKILLS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "confidence": {"type": "number"}},
                "required": ["name", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["skills"],
    "additionalProperties": False,
}
SKILLS_SYSTEM = "You extract skills from text. Reply only through the structured output."
SKILLS_TEXT = 'Text: "Built a React + TypeScript dashboard, wrote Vitest tests, deployed on Vercel."'
CHAT_RULE = "You are a concise career coach. Answer in one short sentence."
CHAT_TURNS = (
    "Name one skill a frontend engineer should learn to move into AI product roles.",
    "What did you just recommend? Repeat it in three words.",
)
JEV_STATE = "Built a React + TypeScript dashboard, wrote Vitest tests, deployed on Vercel."
JEV_QUESTION_ID = "smoke_language"
JEV_QUESTION = "Does the text name a programming language or a web framework?"
JEV_TIMEOUT_S = 10.0

_HANDBOOK_TOPICS: tuple[tuple[str, str], ...] = (
    ("resume bullets", "lead with the verb, then the scope, then one measured result"),
    ("portfolio projects", "pick two projects that show the target skill working end to end"),
    ("GitHub profiles", "pin the repositories that have a readme, tests and a recent commit"),
    ("interview preparation", "rehearse three stories with a situation, an action and a result"),
    ("salary conversations", "anchor on the market range for the role and the city, not on the last salary"),
    ("career switches", "name the transferable skills first and the missing ones second"),
    ("learning plans", "schedule small weekly deliverables instead of open-ended courses"),
    ("mentoring and leadership", "describe who was helped, what changed and how it was measured"),
    ("system design", "start from the user need, then data, then scale, and state the trade-offs"),
    ("testing habits", "show the test that would have caught a real bug"),
    ("accessibility", "mention keyboard use, contrast and screen reader checks that were actually done"),
    ("AI and machine learning", "separate using a model through an API from training or evaluating one"),
    ("product thinking", "connect a feature to the problem it solved and how that was verified"),
    ("communication", "write short, plain sentences and define a term the first time it appears"),
    ("open source", "link the merged pull request, not just the repository"),
    ("freelance work", "explain the client problem, the constraint and the outcome in numbers"),
    ("networking", "ask one specific question that is easy to answer in a message"),
    ("job search planning", "keep a weekly target of applications, conversations and finished projects"),
    ("remote work", "show written communication and independent delivery with examples"),
    ("promotion cases", "list the scope that grew and the people who can confirm it"),
    ("certifications", "treat them as support for a skill that a project already demonstrates"),
    ("career goals", "turn the goal into a role, a skill gap and a first step this week"),
)


def handbook_text() -> str:
    """About 10,000 characters (roughly 2,500 tokens) of static coaching rules.

    Deterministic: the same bytes every run, so the cached prefix is shared between runs inside the 5-minute TTL
    and between the two turns of one run. It carries no name, date or id.
    """
    rules = ["Coaching handbook. These rules apply to every answer in this conversation."]
    for number, (topic, advice) in enumerate(_HANDBOOK_TOPICS, start=1):
        rules.append(
            f"Rule {number} ({topic}): when the person asks about {topic}, {advice}. Keep the answer under "
            f"{2 + number % 3} sentences unless they ask for more detail, name one concrete next step, and never "
            "invent employers, dates, salaries or credentials that the profile does not contain. If the profile "
            "shows no evidence for the skill involved, say so plainly and suggest the smallest project that "
            "would create that evidence."
        )
    return "\n\n".join(rules)


@dataclass(frozen=True)
class Outcome:
    name: str
    status: Status
    detail: str = ""


@dataclass
class JevTotals:
    """What the Jev call cost (it is not a Claude request, so it is not in the Claude sink)."""

    calls: int = 0
    micro_usd: int = 0


def usd(micro: int) -> str:
    """Micro-USD as dollars: four decimals, six for amounts under a tenth of a cent."""
    dollars = micro / 1_000_000
    return f"${dollars:.4f}" if micro == 0 or micro >= 1_000 else f"${dollars:.6f}"


def describe(exc: BaseException) -> str:
    """One line for a failed check: a Claude error's user message and detail, else the exception."""
    if isinstance(exc, ClaudeError):
        extra = f" ({exc.detail})" if exc.detail else ""
        return f"{type(exc).__name__}: {exc.user_message}{extra}"
    if isinstance(exc, JevError):
        return f"JevError[{exc.kind}]: {exc}"
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:300]}" if text else type(exc).__name__


def _report(outcomes: list[Outcome], name: str, status: Status, detail: str = "") -> None:
    outcomes.append(Outcome(name, status, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""), flush=True)


async def _guarded(
    outcomes: list[Outcome], name: str, check: Callable[[], Awaitable[tuple[Status, str]]]
) -> None:
    """Run one check; whatever it raises becomes a FAIL line (a smoke run reports, it does not crash)."""
    try:
        status, detail = await check()
    except Exception as exc:
        status, detail = "FAIL", describe(exc)
    _report(outcomes, name, status, detail)


async def _aclose(client: object) -> None:
    """Close a client if it can be closed; a failure to close must not hide the report."""
    close = getattr(client, "aclose", None)
    if close is None:
        return
    with contextlib.suppress(Exception):
        result = close()
        if inspect.isawaitable(result):
            await result


# ───────────────────────── the checks ─────────────────────────


async def check_haiku_structured(
    claude: ClaudeClient, settings: Settings, sink: MemoryUsageSink
) -> tuple[Status, str]:
    model = settings.reader_model
    before = len(sink.records)
    started = time.monotonic()
    result = await claude.structured(
        model=model,
        system=[TextBlock(SKILLS_SYSTEM)],
        user=[TextBlock(SKILLS_TEXT)],
        schema=SKILLS_SCHEMA,
        max_tokens=2048,
        effort="low",  # deliberately: the client must drop it for Haiku
        ctx=CTX,
    )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    usage = result.usage
    rows = sink.records[before:]
    raw_skills = result.data.get("skills")
    names = (
        [str(s.get("name")) for s in raw_skills if isinstance(s, dict)]
        if isinstance(raw_skills, list)
        else []
    )

    problems: list[str] = []
    if result.attempts != 1:
        problems.append(f"needed {result.attempts} attempts (a rejected request was retried)")
    if len(rows) != 1 or any(r.status != "ok" for r in rows):
        problems.append(
            f"the usage ledger has {len(rows)} request row(s) [{', '.join(r.status for r in rows) or 'none'}], "
            "expected exactly one ok row"
        )
    if not names:
        problems.append("the answer has no skills")
    detail = (
        f"{model} attempts={result.attempts} in={usage.input_tokens} out={usage.output_tokens} "
        f"{elapsed_ms} ms {usd(usage.cost_micro_usd)}; skills: {', '.join(names) or '(none)'}"
    )
    if spec_for(model).supports_effort:
        detail += f"; note: {model} accepts effort, so this run cannot show that effort is withheld"
    return ("FAIL", f"{'; '.join(problems)} [{detail}]") if problems else ("PASS", detail)


async def _chat_turn(
    claude: ClaudeClient, settings: Settings, system: list[TextBlock], messages: list[MessageParam]
) -> TurnEnd:
    end: TurnEnd | None = None
    async for event in claude.stream_chat(
        model=settings.chat_model,
        system=system,
        messages=messages,
        tools="none",
        max_tokens=1024,
        effort="low",
        cache_tail=True,
        ctx=CTX,
    ):
        if isinstance(event, TurnEnd):
            end = event
    if end is None:
        raise RuntimeError("the chat stream ended without a final TurnEnd event")
    return end


async def check_sonnet_cache(claude: ClaudeClient, settings: Settings) -> tuple[Status, str]:
    handbook = handbook_text()
    system = [TextBlock(handbook, cache=True), TextBlock(CHAT_RULE)]
    first_messages: list[MessageParam] = [{"role": "user", "content": CHAT_TURNS[0]}]
    started = time.monotonic()
    first = await _chat_turn(claude, settings, system, first_messages)
    print(f"       turn 1: {' '.join(first.text.split())[:110]!r}", flush=True)
    second_messages: list[MessageParam] = [
        *first_messages,
        {"role": "assistant", "content": first.text},
        {"role": "user", "content": CHAT_TURNS[1]},
    ]
    second = await _chat_turn(claude, settings, system, second_messages)
    print(f"       turn 2: {' '.join(second.text.split())[:110]!r}", flush=True)
    elapsed_ms = round((time.monotonic() - started) * 1000)

    u1, u2 = first.usage, second.usage
    detail = (
        f"{settings.chat_model} static block ~{len(handbook) // 4:,} tokens (estimate); "
        f"turn 1 write={u1.cache_write_tokens:,} read={u1.cache_read_tokens:,} in={u1.input_tokens:,} out={u1.output_tokens:,}; "
        f"turn 2 write={u2.cache_write_tokens:,} read={u2.cache_read_tokens:,} in={u2.input_tokens:,} out={u2.output_tokens:,}; "
        f"{elapsed_ms} ms {usd(u1.cost_micro_usd + u2.cost_micro_usd)}"
    )
    problems: list[str] = []
    if not first.text.strip() or not second.text.strip():
        problems.append("a turn produced no text")
    if u2.cache_read_tokens <= 0:
        problems.append("turn 2 did not read the cache (cache_read_tokens is 0)")
    if u1.cache_read_tokens > 0:
        detail += "; note: turn 1 already hit the cache (an earlier run within the TTL wrote the same block)"
    return ("FAIL", f"{'; '.join(problems)} [{detail}]") if problems else ("PASS", detail)


async def check_jev(jev: JevClient, totals: JevTotals) -> tuple[Status, str]:
    started = time.monotonic()
    response = await jev.system_one(
        JEV_STATE,
        {JEV_QUESTION_ID: JevQuestion.noul(JEV_QUESTION)},
        timeout_s=JEV_TIMEOUT_S,
        ctx=CTX,
    )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    totals.calls += 1
    totals.micro_usd += response.cost_micro_usd
    answer = response.answers.get(JEV_QUESTION_ID)
    if answer is None or answer.kind != "noul" or answer.noul is None:
        return "FAIL", f"no usable noul answer for {JEV_QUESTION_ID!r}: {answer!r}"
    detail = (
        f"{response.model} p(yes)={answer.noul:.2f} confidence={answer.confidence:.2f} "
        f"in={response.input_tokens} out={response.output_tokens} {elapsed_ms} ms {usd(response.cost_micro_usd)}"
    )
    if answer.noul < 0.5:
        return "FAIL", f"Jev answered no to an obvious yes [{detail}]"
    return "PASS", detail


async def check_docx() -> tuple[Status, str]:
    lines: list[str] = []
    doc = await ingest_resume(make_docx(RESUME_LINES), "smoke-resume.docx", lines.append)
    problems: list[str] = []
    if not doc.ok or doc.error:
        problems.append(f"the DOCX was not read: {doc.error}")
    if "Aanya Verma" not in doc.text:
        problems.append("the name is missing from the extracted text")
    if doc.meta.get("format") != "DOCX":
        problems.append(f"format is {doc.meta.get('format')!r}, expected 'DOCX'")
    if not doc.quick_signals:
        problems.append("no skills were spotted")
    sections = ", ".join(str(s) for s in doc.meta.get("sections", []))
    detail = (
        f"{len(doc.text):,} chars, {doc.meta.get('words', 0)} words; sections: {sections or '(none)'}; "
        f"skills: {', '.join(doc.quick_signals) or '(none)'}"
    )
    return ("FAIL", f"{'; '.join(problems)} [{detail}]") if problems else ("PASS", detail)


# ───────────────────────── the run ─────────────────────────


def default_claude(settings: Settings, sink: UsageSink) -> ClaudeClient:
    """The real, unguarded Anthropic client (a smoke run is not subject to the spend ceiling)."""
    return AnthropicClaudeClient(settings=settings, usage_sink=sink)


def default_jev(settings: Settings) -> JevClient | None:
    """The real Jev client, or None when there is no key or Jev is switched off."""
    if settings.jev_api_key is None or not settings.jev.enabled:
        return None
    return HttpJevClient(settings=settings.jev, api_key=settings.jev_api_key)


def _print_ledger(sink: MemoryUsageSink, jev: JevTotals) -> None:
    print("\nUsage (the same rows the app writes to llm_calls)")
    if sink.records:
        print(
            f"  {'purpose':<8} {'model':<18} {'status':<9} {'in':>7} {'out':>6} {'c.write':>8} {'c.read':>8} {'cost':>9} {'ms':>6}"
        )
        for r in sink.records:
            print(
                f"  {r.purpose:<8} {r.model:<18} {r.status:<9} {r.input_tokens:>7,} {r.output_tokens:>6,} "
                f"{r.cache_write_tokens:>8,} {r.cache_read_tokens:>8,} {usd(r.cost_micro_usd):>9} {r.latency_ms:>6,}"
            )
    else:
        print("  (no Claude request was recorded)")
    claude_micro = sink.total_cost_micro_usd
    print(f"  Claude: {len(sink.records)} request(s), {usd(claude_micro)}")
    if jev.calls:
        print(f"  Jev:    {jev.calls} call(s), {usd(jev.micro_usd)}")
    print(f"  Total:  {usd(claude_micro + jev.micro_usd)}")


async def run_smoke(
    settings: Settings,
    *,
    skip_claude: bool = False,
    skip_jev: bool = False,
    claude_factory: ClaudeFactory | None = None,
    jev_factory: JevFactory | None = None,
) -> int:
    """Run every check, print the report and return 1 when any check failed."""
    sink = MemoryUsageSink()
    outcomes: list[Outcome] = []
    claude_names = ("haiku structured", "sonnet chat + cache")
    jev_totals = JevTotals()

    print(
        f"Tayyari smoke | reader={settings.reader_model} chat={settings.chat_model} jev={settings.jev.model} "
        f"| keys: anthropic={'yes' if settings.anthropic_api_key else 'no'} jev={'yes' if settings.jev_api_key else 'no'}\n"
    )

    # ── Claude ──
    claude: ClaudeClient | None = None
    if skip_claude:
        for name in claude_names:
            _report(outcomes, name, "SKIP", "--skip-claude")
    elif claude_factory is None and settings.anthropic_api_key is None:
        for name in claude_names:
            _report(
                outcomes, name, "FAIL", "ANTHROPIC_API_KEY is not set (put it in .env or the environment)"
            )
    else:
        try:
            claude = (claude_factory or default_claude)(settings, sink)
        except Exception as exc:
            for name in claude_names:
                _report(outcomes, name, "FAIL", f"could not build the Claude client: {describe(exc)}")
    if claude is not None:
        client = claude
        await _guarded(outcomes, claude_names[0], lambda: check_haiku_structured(client, settings, sink))
        await _guarded(outcomes, claude_names[1], lambda: check_sonnet_cache(client, settings))
        await _aclose(client)

    # ── Jev ──
    if skip_jev:
        _report(outcomes, "jev noul", "SKIP", "--skip-jev")
    else:
        jev: JevClient | None = None
        try:
            jev = (jev_factory or default_jev)(settings)
        except Exception as exc:
            _report(outcomes, "jev noul", "FAIL", f"could not build the Jev client: {describe(exc)}")
        else:
            if jev is None:
                _report(
                    outcomes,
                    "jev noul",
                    "SKIP",
                    "Jev is not configured (JEV_API_KEY missing, or TAYYARI_JEV__ENABLED=false)",
                )
        if jev is not None:
            jev_client = jev
            await _guarded(outcomes, "jev noul", lambda: check_jev(jev_client, jev_totals))
            await _aclose(jev_client)

    # ── no network ──
    await _guarded(outcomes, "docx resume parse", check_docx)

    _print_ledger(sink, jev_totals)
    passed = sum(o.status == "PASS" for o in outcomes)
    failed = sum(o.status == "FAIL" for o in outcomes)
    skipped = sum(o.status == "SKIP" for o in outcomes)
    print(
        f"\nResult: {passed} PASS, {failed} FAIL, {skipped} SKIP"
        + ("" if not failed else "  <-- smoke failed")
    )
    return 1 if failed else 0


def run(
    skip_claude: bool = False,
    skip_jev: bool = False,
    *,
    settings: Settings | None = None,
    claude_factory: ClaudeFactory | None = None,
    jev_factory: JevFactory | None = None,
) -> int:
    """Run the smoke checks (real calls unless factories are given). Exit code 1 when any check fails."""
    base = settings if settings is not None else get_settings()
    # The client logs every request at INFO; the report already shows them, so keep warnings and errors only.
    configure_logging(base.model_copy(update={"log_level": "WARNING"}))
    return asyncio.run(
        run_smoke(
            base,
            skip_claude=skip_claude,
            skip_jev=skip_jev,
            claude_factory=claude_factory,
            jev_factory=jev_factory,
        )
    )
