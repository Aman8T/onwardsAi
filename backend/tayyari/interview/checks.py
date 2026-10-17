"""A work sample's checks: Claude Haiku quotes the person's work, then names a verdict; code checks the quotes.

The schema puts each check's quotes before its verdict. Then code, for every check of the template, in the template's order:

    quotes      a quote counts only when it is a passage of the work, the test note or one of the answers (case, spacing,
                quote marks and dashes ignored). A check with no valid quote is Couldn't defend, with no quote: it was not
                shown, and the person can see exactly which words were looked for.
    verdict     the model's word on a check that has a valid quote; a verdict it did not give properly is Not assessed.

`assistant_use` is code alone: how many things the person asked the assistant, and whether the code it gave was found in the
work (not at all, as it was, or changed). It is a fact about the work, never a grade.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Final

from tayyari.contract.interview import AssistantTurn, CheckResult, Verdict, WorkTask
from tayyari.core.logging import get_logger
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError, SpendCeilingReached
from tayyari.llm.types import CallContext, TextBlock
from tayyari.prompts.interview import CHECKS_SYSTEM, build_checks_prompt

from .text import clean_quote, fold, locate, passage

log = get_logger(__name__)

CHECKS_MAX_TOKENS: Final = 1500
MAX_QUOTES: Final = 3
QUOTE_MIN_WORDS: Final = 1
QUOTE_MAX_WORDS: Final = 60
CODE_LINE_MIN_CHARS: Final = 15

CHECKS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["checks"],
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["check", "quotes", "verdict"],
                "properties": {
                    "check": {"type": "string"},
                    "quotes": {"type": "array", "items": {"type": "string"}},
                    "verdict": {"type": "string", "enum": ["defended", "shaky", "couldnt"]},
                },
            },
        }
    },
}
"""The quotes come before the verdict that rests on them."""

_VERDICTS: Final[dict[str, Verdict]] = {"defended": "defended", "shaky": "shaky", "couldnt": "couldnt"}
_FENCE: Final = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)


def check_checks(
    raw: Mapping[str, Any] | None,
    *,
    checks: Sequence[str],
    work: str,
    test_note: str,
    answers: Sequence[str],
) -> list[CheckResult]:
    """Each check of the template with its verdict and the words of the person it rests on (see the module docstring)."""
    texts = [work, test_note, *answers]
    folded = [fold(t) for t in texts]
    items: dict[str, Mapping[str, Any]] = {}
    listed = raw.get("checks") if isinstance(raw, Mapping) else None
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, Mapping):
            items.setdefault(fold(clean_quote(item.get("check"))), item)

    out: list[CheckResult] = []
    for check in checks:
        item = items.get(fold(check))
        found: list[str] = []
        quotes = item.get("quotes") if item is not None else None
        for value in (quotes if isinstance(quotes, list) else [])[:MAX_QUOTES]:
            cleaned = clean_quote(value)
            if not cleaned or not passage(
                cleaned, folded, min_words=QUOTE_MIN_WORDS, max_words=QUOTE_MAX_WORDS
            ):
                continue
            needle = fold(cleaned)
            source = next(text for text, f in zip(texts, folded, strict=True) if needle in f)
            found.append(locate(source, cleaned))
        if item is None or not found:
            out.append(CheckResult(check=check, verdict="couldnt"))
            continue
        verdict_raw = item.get("verdict")
        verdict = _VERDICTS.get(verdict_raw) if isinstance(verdict_raw, str) else None
        out.append(
            CheckResult(
                check=check,
                verdict=verdict or "not_assessed",
                quote=max(found, key=lambda q: len(q.split())) if verdict else None,
            )
        )
    return out


def unassessed(checks: Sequence[str]) -> list[CheckResult]:
    """Every check Not assessed: no work was handed in, so nothing was looked at."""
    return [CheckResult(check=check, verdict="not_assessed") for check in checks]


async def run_checks(
    *,
    claude: ClaudeClient,
    model: str,
    task: WorkTask,
    checks: Sequence[str],
    work: str,
    test_note: str,
    answers: Sequence[tuple[str, str]],
    ctx: CallContext,
) -> list[CheckResult]:
    """The checks of a work sample, judged. A model that fails leaves every check without a quote: Couldn't defend."""
    prompt = build_checks_prompt(
        task=task.task,
        material_name=task.materialName,
        checks=checks,
        work=work,
        test_note=test_note,
        answers=answers,
    )
    raw: Mapping[str, Any] | None = None
    try:
        result = await claude.structured(
            model=model,
            system=[TextBlock(CHECKS_SYSTEM)],
            user=[TextBlock(prompt)],
            schema=CHECKS_SCHEMA,
            max_tokens=CHECKS_MAX_TOKENS,
            effort=None,
            ctx=replace(ctx, purpose="interview.checks"),
        )
        raw = result.data
    except ClaudeError as exc:
        log.warning("interview_checks_failed", kind=exc.kind)
    except SpendCeilingReached:
        log.warning("interview_checks_skipped", reason="spend ceiling")
    if raw is None:
        return unassessed(checks)
    return check_checks(raw, checks=checks, work=work, test_note=test_note, answers=[a for _, a in answers])


def code_lines(reply: str) -> list[str]:
    """The lines of the code blocks of an assistant's reply that are long enough to recognise (15 characters or more)."""
    lines: list[str] = []
    for block in _FENCE.findall(reply):
        lines.extend(line.strip() for line in block.splitlines() if len(line.strip()) >= CODE_LINE_MIN_CHARS)
    return lines


def assistant_use(turns: Sequence[AssistantTurn], work: str | None) -> str | None:
    """What was seen of the assistant's use, as facts: how many things were asked, and whether its code is in the work.
    None when the assistant was not asked anything."""
    if not turns:
        return None
    asked = f"Asked the assistant {len(turns)} thing{'' if len(turns) == 1 else 's'}"
    lines = [line for turn in turns for line in code_lines(turn.assistant)]
    if not lines:
        return asked
    in_work = fold(work or "")
    used = sum(1 for line in lines if fold(line) in in_work)
    if used == 0:
        return f"{asked} · didn't use its code"
    if used == len(lines):
        return f"{asked} · used its version as is"
    return f"{asked} · changed its version before using it"


__all__ = [
    "CHECKS_SCHEMA",
    "assistant_use",
    "check_checks",
    "code_lines",
    "run_checks",
    "unassessed",
]
