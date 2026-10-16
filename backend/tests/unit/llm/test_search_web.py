"""`AnthropicClaudeClient.search_web` against hand-written SSE fixtures: only the basic search tool is declared, domain
rules and the place are passed through, every search comes back with its own results (or its error), a ``pause_turn`` is
continued, and every request is one usage row that pays for its searches."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.llm.anthropic_client import WEB_SEARCH_BASIC_TYPE, search_tool
from tayyari.llm.errors import ClaudeRefusal
from tayyari.llm.models import WEB_SEARCH_MICRO_USD
from tayyari.llm.types import CallContext, SearchCall, SearchHit, TextBlock, WebSearchRun
from tests.unit.llm.test_anthropic_client import Rig, make_rig, ok, rig  # noqa: F401  (fixtures)

HAIKU_55 = "claude-haiku-5-5"
SCOUT = CallContext(purpose="jobsearch.scout", profile_id="prof-1", ip_hash="ab12")
DOMAINS = ("job-boards.greenhouse.io", "jobs.lever.co")
PLACE = {"city": "Bengaluru", "country": "IN", "timezone": "Asia/Kolkata"}


async def search(rig: Rig, **overrides: Any) -> WebSearchRun:  # noqa: F811
    values: dict[str, Any] = {
        "model": HAIKU_55,
        "system": [TextBlock("Search three times.")],
        "user": [TextBlock("<brief>Frontend Engineer</brief>")],
        "max_searches": 3,
        "allowed_domains": DOMAINS,
        "user_location": PLACE,
        "max_tokens": 1200,
        "effort": "low",
        "ctx": SCOUT,
    }
    values.update(overrides)
    return await rig.client.search_web(**values)


async def test_only_the_basic_search_tool_is_declared_with_the_domains_and_the_place(rig: Rig) -> None:  # noqa: F811
    rig.api.queue(ok("search_only"))
    await search(rig)
    (request,) = rig.api.requests
    assert request["tools"] == [
        {
            "type": WEB_SEARCH_BASIC_TYPE,
            "name": "web_search",
            "max_uses": 3,
            "allowed_domains": list(DOMAINS),
            "user_location": {"type": "approximate", **PLACE},
        }
    ]
    assert request["model"] == HAIKU_55 and request["thinking"] == {"type": "disabled"}
    assert (
        request["output_config"] == {"effort": "low"}
        and request["system"][0]["text"] == "Search three times."
    )
    assert "tool_choice" not in request


async def test_every_search_comes_back_with_its_own_results_or_its_error(rig: Rig) -> None:  # noqa: F811
    rig.api.queue(ok("search_only", "req_s1"))
    run = await search(rig)
    assert run.searches == (
        SearchCall(
            query="Frontend Engineer Bengaluru",
            hits=(
                SearchHit(
                    "https://job-boards.greenhouse.io/headoutcareers/jobs/4707747006",
                    "Job Application for Frontend Engineer at Headout",
                    "3 days ago",
                ),
                SearchHit("https://jobs.lever.co/neuron7/1f8eb6df", "Neuron7 - Frontend Engineer"),
            ),
        ),
        SearchCall(query="SDE 2 frontend Bangalore", error="too_many_requests"),
    )
    assert run.text == "DONE" and run.stop_reason == "end_turn"
    assert run.usage.web_searches == 2 and run.usage.request_id == "req_s1"
    assert run.usage.cost_micro_usd >= 2 * WEB_SEARCH_MICRO_USD
    (row,) = rig.rows
    assert (row.purpose, row.status, row.web_searches, row.profile_id) == (
        "jobsearch.scout",
        "ok",
        2,
        "prof-1",
    )
    assert row.cost_micro_usd == run.usage.cost_micro_usd


async def test_a_paused_turn_is_continued_and_its_searches_are_kept(rig: Rig) -> None:  # noqa: F811
    rig.api.queue(ok("search_pause", "req_p1"), ok("search_only", "req_p2"))
    run = await search(rig)
    assert [call.query for call in run.searches] == [
        "UI Engineer remote India",
        "Frontend Engineer Bengaluru",
        "SDE 2 frontend Bangalore",
    ]
    first, second = rig.api.requests
    assistant = second["messages"][1]
    assert assistant["role"] == "assistant" and assistant["content"][0]["id"] == "srvtoolu_01Paused"
    assert second["tools"] == first["tools"]
    assert run.usage.web_searches == 3 and [r.stop_reason for r in rig.rows] == ["pause_turn", "end_turn"]
    assert run.usage.cost_micro_usd == sum(r.cost_micro_usd for r in rig.rows)


def test_allowed_and_blocked_domains_together_are_a_programming_error() -> None:
    with pytest.raises(ValueError, match="not both"):
        search_tool(2, ("a.com",), ("b.com",), None)
    assert search_tool(2, (), ("linkedin.com",), None)["blocked_domains"] == ["linkedin.com"]
    assert (
        "user_location" not in search_tool(0, (), (), None) and search_tool(0, (), (), None)["max_uses"] == 1
    )


async def test_a_refusal_is_typed_and_recorded(rig: Rig) -> None:  # noqa: F811
    rig.api.queue(ok("structured_refusal"))
    with pytest.raises(ClaudeRefusal):
        await search(rig)
    assert [(r.status, r.error_kind) for r in rig.rows] == [("error", "refusal")]
