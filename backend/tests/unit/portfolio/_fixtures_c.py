"""Shared fakes for the service and API tests (unit C): a scripted "good Haiku" and "good Sonnet", and a rig.

`scripted_facts` (unit A) reads a fixture's source texts the way a careful extraction model would, so the real
verification runs on it. `good_copy` plays a careful copywriter: for every slot asked it returns the deterministic
fallback wording, cited to the facts it came from, which the real grounding check accepts; `overrides` swaps the text
of chosen slots to plant a problem. Both are scripted by schema (the facts call has a ``facts`` property, the copy
call a ``slots`` property), because every portfolio call shares the purpose ``portfolio``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tayyari.contract.models import Profile
from tayyari.contract.requests import RequestContext
from tayyari.contract.stored import StoredContext
from tayyari.core.settings import Settings
from tayyari.portfolio.fallback import fallback_slots
from tayyari.portfolio.service import PortfolioService
from tayyari.sample.provider import SampleProvider
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.fakes.clock import FakeClock
from tests.fakes.decisions import FakeDecisionService
from tests.fakes.repos import FakeRepositories
from tests.unit.portfolio._fixtures_a import (
    facts_for,
    graph_for,
    load_fixture,
    person_for,
    scripted_facts,
)

REAL_STEPS = ["facts", "design", "copy", "check"]


def is_facts_call(call: StructuredCall) -> bool:
    return "facts" in call.schema.get("properties", {})


def is_copy_call(call: StructuredCall) -> bool:
    return "slots" in call.schema.get("properties", {})


def asked_slots(call: StructuredCall) -> list[str]:
    """The slot ids a copy call asks for (the enum of the answer schema)."""
    ids: list[str] = call.schema["properties"]["slots"]["items"]["properties"]["id"]["enum"]
    return ids


def is_reask(call: StructuredCall) -> bool:
    return "rejected by the checker" in call.user_text


def good_copy(
    name: str, overrides: Mapping[str, str] | None = None
) -> Callable[[StructuredCall], dict[str, Any]]:
    """A careful Sonnet for fixture ``name`` (see the module docstring)."""
    fx = load_fixture(name)
    facts = list(facts_for(name))
    person, graph = person_for(fx.profile), graph_for(fx.profile)
    citeable = [f.id for f in facts if f.kind not in ("link", "contact")]

    def answer(call: StructuredCall) -> dict[str, Any]:
        ids = asked_slots(call)
        base = {s.id: s for s in fallback_slots(facts, person, graph, ids)}
        rows: list[dict[str, Any]] = []
        for sid in ids:
            fb = base.get(sid)
            text = fb.text if fb is not None else ""
            cited = list(fb.factIds) if fb is not None and fb.factIds else citeable[:1]
            if overrides is not None and sid in overrides:
                text = overrides[sid]
            rows.append({"id": sid, "text": text, "factIds": cited})
        return {"slots": rows}

    return answer


def script_facts(claude: FakeClaudeClient, ctx: StoredContext) -> None:
    claude.script_structured("portfolio", lambda call: scripted_facts(ctx), when=is_facts_call, repeat=True)


def script_copy(
    claude: FakeClaudeClient, script: Callable[[StructuredCall], Any], *, repeat: bool = True
) -> None:
    claude.script_structured("portfolio", script, when=is_copy_call, repeat=repeat)


def request_context(clock: FakeClock | None = None, ip_hash: str | None = "h1") -> RequestContext:
    return RequestContext(
        request_id="req-1",
        ip_hash=ip_hash,
        client_ip=None,
        started_at=(clock or FakeClock()).now(),
    )


@dataclass
class Rig:
    service: PortfolioService
    claude: FakeClaudeClient
    decisions: FakeDecisionService
    repos: FakeRepositories
    clock: FakeClock
    profile_id: str
    profile: Profile
    ctx: StoredContext
    rc: RequestContext


async def make_rig(
    settings: Settings,
    name: str = "riya",
    *,
    profile_id: str = "0123456789abcdef0123456789abcdef",
    claude: FakeClaudeClient | None = None,
    decisions: FakeDecisionService | None = None,
    script: bool = True,
    store: bool = True,
) -> Rig:
    """A `PortfolioService` over fakes, with the fixture ``name`` stored as a profile (``store=False`` leaves the
    repository empty) and, when ``script`` is set, a good Haiku and a good Sonnet scripted."""
    fx = load_fixture(name)
    clock = FakeClock()
    repos = FakeRepositories(clock)
    if store:
        await repos.profiles.save(
            profile_id, fx.profile.to_dict(), fx.ctx, goal=None, owner_hash=None, is_sample=False
        )
    fake_claude = claude if claude is not None else FakeClaudeClient()
    if script:
        script_facts(fake_claude, fx.ctx)
        script_copy(fake_claude, good_copy(name))
    fake_decisions = decisions if decisions is not None else FakeDecisionService()
    service = PortfolioService(
        settings=settings,
        claude=fake_claude,
        decisions=fake_decisions,
        portfolios=repos.portfolios,
        profiles=repos.profiles,
        sample=SampleProvider(),
        clock=clock,
    )
    return Rig(
        service=service,
        claude=fake_claude,
        decisions=fake_decisions,
        repos=repos,
        clock=clock,
        profile_id=profile_id,
        profile=fx.profile,
        ctx=fx.ctx,
        rc=request_context(clock),
    )
