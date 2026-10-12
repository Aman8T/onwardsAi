"""The match's calls through the real Anthropic client (a mock transport): thinking is off for every `match.*` purpose."""

from __future__ import annotations

import pytest

from tayyari.llm.types import CallContext
from tests.unit.llm.test_anthropic_client import (  # noqa: F401  (make_rig is a fixture used below)
    Rig,
    make_rig,
    ok,
    run_structured,
)

HAIKU_55 = "claude-haiku-5-5"
HAIKU_45 = "claude-haiku-4-5"
PURPOSES = [
    "match.facts",
    "match.posting",
    "match.gates",
    "match.coverage",
    "match.relevance",
    "match.phrase",
    "match.cover",
]


@pytest.mark.parametrize("purpose", PURPOSES)
async def test_haiku_5_5_is_told_not_to_think_for_every_match_purpose(make_rig, purpose: str) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    # even with every thinking switch on, a match call is an extraction-type call
    rig: Rig = make_rig(reader_thinking=True, synth_thinking=True, chat_thinking=True)
    rig.api.queue(ok("structured_haiku_ok"))
    await run_structured(rig, model=HAIKU_55, ctx=CallContext(purpose=purpose), effort=None)  # type: ignore[arg-type]
    assert rig.api.requests[0].get("thinking") == {"type": "disabled"}
    assert "output_config" in rig.api.requests[0]  # the schema still goes out as structured output


@pytest.mark.parametrize("purpose", PURPOSES)
async def test_haiku_4_5_gets_no_thinking_parameter_for_a_match_purpose(make_rig, purpose: str) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    rig: Rig = make_rig(reader_thinking=True)
    rig.api.queue(ok("structured_haiku_ok"))
    await run_structured(rig, model=HAIKU_45, ctx=CallContext(purpose=purpose), effort=None)  # type: ignore[arg-type]
    assert "thinking" not in rig.api.requests[0]


async def test_every_call_is_recorded_with_its_match_purpose(make_rig) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    rig: Rig = make_rig()
    for purpose in PURPOSES:
        rig.api.queue(ok("structured_haiku_ok"))
        await run_structured(rig, model=HAIKU_55, ctx=CallContext(purpose=purpose), effort=None)  # type: ignore[arg-type]
    assert [row.purpose for row in rig.rows] == PURPOSES
    assert all(row.model == HAIKU_55 and row.cost_micro_usd > 0 for row in rig.rows)
