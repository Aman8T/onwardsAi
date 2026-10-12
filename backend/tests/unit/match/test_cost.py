"""The cost of a whole match on Haiku 5.5, estimated from the real prompts, schemas and scripted answers.

The estimate is deliberately pessimistic: characters are turned into tokens at 3 characters per token, the schema is
counted as input, and every output is counted at its full JSON length. The target (roadmap, M1) is under $0.05 for a
posting of about fourteen requirements.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tayyari.llm.models import RawTokens, claude_cost_micro, spec_for
from tests.fakes.claude import FakeClaudeClient
from tests.unit.match.support import CTX, HAIKU, Persona, js, meera, riya, script_pipeline

CHARS_PER_TOKEN = 3.0


class MeteredFake(FakeClaudeClient):
    def __init__(self) -> None:
        super().__init__()
        self.meter: list[tuple[str, int, int]] = []

    async def structured(self, **kwargs: Any):  # type: ignore[no-untyped-def, override]
        result = await super().structured(**kwargs)
        sent = sum(len(b.text) for b in kwargs["system"]) + sum(len(b.text) for b in kwargs["user"])
        sent += len(json.dumps(kwargs["schema"]))
        received = len(json.dumps(result.data))
        self.meter.append(
            (kwargs["ctx"].purpose, int(sent / CHARS_PER_TOKEN), int(received / CHARS_PER_TOKEN))
        )
        return result


async def whole_match(persona: Persona) -> MeteredFake:
    from tayyari.match.service import MatchService

    fake = script_pipeline(MeteredFake(), persona)
    fake.script_structured("match.cover", js(f"{persona.name}.cover.answer.json"))
    service = MatchService(claude=fake, model=HAIKU)
    outcome = await service.analyse(
        profile=persona.profile,
        context=persona.context,
        posting_text=persona.posting_text,
        call_ctx=CTX,
        portfolio_facts=persona.portfolio_facts,
    )
    await service.compose_resume(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    await service.compose_cover(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    assert isinstance(fake, MeteredFake)
    return fake


def dollars(fake: MeteredFake) -> float:
    spec = spec_for(HAIKU)
    micro = sum(claude_cost_micro(spec, RawTokens(input_tokens=i, output_tokens=o)) for _, i, o in fake.meter)
    return micro / 1_000_000


@pytest.mark.parametrize("persona", [meera(), riya()], ids=["meera", "riya"])
async def test_a_whole_match_costs_well_under_five_cents_in_at_most_nine_calls(persona: Persona) -> None:
    fake = await whole_match(persona)
    cost = dollars(fake)
    purposes = [p for p, _, _ in fake.meter]
    assert len(purposes) <= 9, purposes
    assert set(purposes) == {
        "match.posting",
        "match.gates",
        "match.coverage",
        "match.relevance",
        "match.phrase",
        "match.cover",
    }
    assert cost < 0.05, (cost, fake.meter)
    assert (
        cost < 0.02
    )  # in fact it is closer to a cent: the margin leaves room for longer postings and thicker resumes
    biggest = max(fake.meter, key=lambda row: row[1])
    assert biggest[1] < 12_000, biggest  # no single prompt is large enough to matter


def test_even_if_every_call_used_its_whole_output_budget_a_match_stays_under_five_cents() -> None:
    from tayyari.match.cover import COVER_MAX_TOKENS
    from tayyari.match.coverage import COVERAGE_MAX_TOKENS
    from tayyari.match.gate import GATES_MAX_TOKENS
    from tayyari.match.posting import POSTING_MAX_TOKENS
    from tayyari.match.variant import MAX_PHRASE_CALLS, PHRASE_MAX_TOKENS, RELEVANCE_MAX_TOKENS

    output = (
        POSTING_MAX_TOKENS
        + GATES_MAX_TOKENS
        + 2 * COVERAGE_MAX_TOKENS
        + RELEVANCE_MAX_TOKENS
        + MAX_PHRASE_CALLS * PHRASE_MAX_TOKENS
        # the cover note is asked for at most twice: the whole note again when it came out short, or one re-ask of
        # the refused paragraphs (REASK_MAX_TOKENS, smaller than a whole note); never a third call
        + 2 * COVER_MAX_TOKENS
    )
    spec = spec_for(HAIKU)
    worst = claude_cost_micro(spec, RawTokens(input_tokens=60_000, output_tokens=output)) / 1_000_000
    assert worst < 0.05, worst
