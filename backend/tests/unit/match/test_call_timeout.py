"""How long a classification call may take before its stage settles for the honest default, and that the service
carries the configured value to every stage it starts."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from tayyari.match import calls
from tayyari.match.calls import CALL_TIMEOUT_S, ask_or_none, call_timeout
from tayyari.match.service import MatchService
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.unit.match.support import CTX, HAIKU, meera, script_pipeline

SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


async def hangs(_call: StructuredCall) -> dict[str, Any]:
    await asyncio.sleep(3600)
    return {}


async def ask(fake: FakeClaudeClient) -> dict[str, Any] | None:
    return await ask_or_none(
        fake,
        model=HAIKU,
        purpose="match.gates",
        system="s",
        user="u",
        schema=SCHEMA,
        max_tokens=100,
        call_ctx=CTX,
    )


async def test_a_late_call_gives_nothing_inside_the_configured_time_and_the_default_outside_it() -> None:
    fake = FakeClaudeClient()
    fake.script_structured("match.gates", hangs, repeat=True)
    started = time.monotonic()
    with call_timeout(0.05):
        assert await ask(fake) is None
    assert time.monotonic() - started < 5
    assert calls._TIMEOUT_S.get() == CALL_TIMEOUT_S  # the block restored the default


def test_the_timeout_nests_and_a_missing_or_nonsense_value_means_the_default() -> None:
    with call_timeout(5):
        assert calls._TIMEOUT_S.get() == 5
        with call_timeout(2):
            assert calls._TIMEOUT_S.get() == 2
        assert calls._TIMEOUT_S.get() == 5
    for unset in (None, 0, -3):
        with call_timeout(unset):
            assert calls._TIMEOUT_S.get() == CALL_TIMEOUT_S


async def test_a_service_with_a_short_timeout_settles_a_stuck_stage_for_unknown_instead_of_hanging() -> None:
    persona = meera()
    fake = script_pipeline(FakeClaudeClient(), persona)
    fake._queues.structured["match.coverage"] = []
    fake.script_structured("match.coverage", hangs, repeat=True)
    service = MatchService(claude=fake, model=HAIKU, call_timeout_s=0.05)
    started = time.monotonic()
    outcome = await service.analyse(
        profile=persona.profile,
        context=persona.context,
        posting_text=persona.posting_text,
        call_ctx=CTX,
        portfolio_facts=persona.portfolio_facts,
    )
    assert time.monotonic() - started < 10
    assert {row.status for row in outcome.result.coverage} == {"unknown"}
    assert outcome.result.confidence == "low"
    assert calls._TIMEOUT_S.get() == CALL_TIMEOUT_S
