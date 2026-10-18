"""Shared helpers for the Prepare tests: the bundled persona (Riya), her AI Product Engineer plan, a service over fakes,
and scripted "good model" answers.

Two kinds of answer are scripted. The hand-written ones in `tests/data/prepare/riya.*.answer.json` are what a careful
model writes for Riya's AI Product Engineer role (the golden fixture is built from them). The responders
(`standing_answer`, `moves_answer`, `headline_answer`) read the call's own schema and prompt and answer validly for any
plan, so the route tests can run the real service over any profile without a fixture per role.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from tayyari.contract.models import Profile
from tayyari.contract.prepare import PreparePlan, PrepareRequest
from tayyari.contract.stored import StoredContext
from tayyari.core.paths import SAMPLE_DIR
from tayyari.llm.types import CallContext
from tayyari.prepare.assemble import assemble
from tayyari.prepare.service import PrepareService
from tayyari.sample.provider import SampleProvider
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.fakes.clock import FakeClock
from tests.fakes.prepare import FakePrepareRepo
from tests.fakes.repos import FakeRepositories

DATA = Path(__file__).resolve().parents[2] / "data" / "prepare"
MODEL = "claude-haiku-5-5"
ROLE = "ai-product-engineer"
PLAN_ID = "a" * 32
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
CTX = CallContext(purpose="prepare.standing", request_id="req-1", profile_id="sample-muw68cno-d358c0")


def js(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@cache
def riya() -> tuple[Profile, StoredContext]:
    profile = Profile.from_dict(json.loads((SAMPLE_DIR / "sample-profile.json").read_text(encoding="utf-8")))
    context = StoredContext.model_validate(
        json.loads((SAMPLE_DIR / "sample-context.json").read_text(encoding="utf-8"))
    )
    return profile, context


def plan_for(role_id: str = ROLE, requirement: str | None = None, *, plan_id: str = PLAN_ID) -> PreparePlan:
    profile, context = riya()
    return assemble(
        profile, context, role_id=role_id, requirement=requirement, plan_id=plan_id, created_at=NOW
    )


def request_for(
    role_id: str = ROLE, requirement: str | None = None, *, fresh: bool | None = None
) -> PrepareRequest:
    return PrepareRequest(profileId=riya()[0].id, roleId=role_id, requirement=requirement, fresh=fresh)


def make_service(
    fake: FakeClaudeClient,
    *,
    repo: FakePrepareRepo | None = None,
    clock: FakeClock | None = None,
    repos: FakeRepositories | None = None,
    timeout: float = 90.0,
) -> tuple[PrepareService, FakePrepareRepo, FakeClock]:
    """A `PrepareService` over the fakes: the scripted client, a fake plan store and a fake clock."""
    clock = clock or FakeClock(NOW)
    repos = repos or FakeRepositories(clock)
    store = repo or repos.prepare
    service = PrepareService(
        claude=fake,
        model=MODEL,
        repo=store,
        profiles=repos.profiles,
        sample=SampleProvider(),
        clock=clock,
        call_timeout_s=timeout,
    )
    return service, store, clock


# ───────────────────────── answers from the schema ─────────────────────────


def enum_of(schema: Any, name: str) -> list[str]:
    """The enum the schema sets on the property ``name`` (its items' enum for a list), searching every object."""
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            for key, sub in props.items():
                if key == name and isinstance(sub, dict):
                    target = sub.get("items") if sub.get("type") == "array" else sub
                    if isinstance(target, dict) and isinstance(target.get("enum"), list):
                        return [str(v) for v in target["enum"]]
                found = enum_of(sub, name)
                if found:
                    return found
        items = schema.get("items")
        if isinstance(items, dict):
            return enum_of(items, name)
    return []


def standing_answer(call: StructuredCall) -> dict[str, Any]:
    """A valid standing answer for any plan: one why per gap, citing the first evidence line, no number of its own."""
    ids = enum_of(call.schema, "evidenceIds")
    names = enum_of(call.schema, "name")
    return {
        "headline": "You already ship what this role is built on; the gaps share one shape, proof that it works.",
        "gaps": [
            {
                "name": name,
                "evidenceIds": ids[:1],
                "why": "Your cited line shows the nearest work; this requirement asks for more of it in the open.",
            }
            for name in names
        ],
    }


def moves_answer(call: StructuredCall) -> dict[str, Any]:
    """A valid moves answer for any plan: three moves of different sizes, each on the plan's own gaps."""
    ids = enum_of(call.schema, "evidenceIds")
    gaps = enum_of(call.schema, "works")
    have = [{"evidenceIds": ids[:1], "text": "What you already ship, as the cited line shows."}]

    def phase(title: str, hours: int) -> dict[str, Any]:
        return {
            "title": title,
            "hours": hours,
            "tasks": ["Write the first piece", "Run it against the app"],
            "deliverable": "Something another person can open",
        }

    return {
        "moves": [
            {
                "have": have,
                "title": "Ship the big piece",
                "body": "You build on what you have and close most of the gaps with one shipped thing.",
                "works": gaps[:3] or gaps,
                "phases": [phase("Lay it out", 8), phase("Build it", 12), phase("Write it up", 4)],
            },
            {
                "have": have,
                "title": "Ship the medium piece",
                "body": "You add the next most useful thing to an app you already have.",
                "works": gaps[:1],
                "phases": [phase("Start", 6), phase("Finish", 6)],
            },
            {
                "have": have,
                "title": "Ship the small piece",
                "body": "You make one small change you can point to by the end of the week.",
                "works": gaps[-1:],
                "phases": [phase("Do it", 3), phase("Show it", 2)],
            },
        ]
    }


_CURRENT = re.compile(r"<current>\n(?P<text>.*?)\n</current>", re.DOTALL)


def headline_answer(call: StructuredCall) -> dict[str, Any]:
    """A valid headline answer for any plan: the current headline, which every check allows, citing the first line."""
    ids = enum_of(call.schema, "evidenceIds")
    found = _CURRENT.search(call.user_text)
    current = found.group("text").strip() if found else "Engineer"
    return {"evidenceIds": ids[:1], "headline": current}


def script_good(fake: FakeClaudeClient) -> FakeClaudeClient:
    """Script every Prepare purpose with the schema-driven responders (repeatable)."""
    fake.script_structured("prepare.standing", standing_answer, repeat=True)
    fake.script_structured("prepare.moves", moves_answer, repeat=True)
    fake.script_structured("prepare.headline", headline_answer, repeat=True)
    return fake


def script_riya(fake: FakeClaudeClient) -> FakeClaudeClient:
    """Script the hand-written answers for Riya's AI Product Engineer role (repeatable)."""
    fake.script_structured("prepare.standing", js("riya.standing.answer.json"), repeat=True)
    fake.script_structured("prepare.moves", js("riya.moves.answer.json"), repeat=True)
    fake.script_structured("prepare.headline", js("riya.headline.answer.json"), repeat=True)
    return fake


Responder = Callable[[StructuredCall], dict[str, Any]]

__all__ = [
    "CTX",
    "DATA",
    "MODEL",
    "NOW",
    "PLAN_ID",
    "ROLE",
    "Responder",
    "enum_of",
    "headline_answer",
    "js",
    "make_service",
    "moves_answer",
    "plan_for",
    "request_for",
    "riya",
    "script_good",
    "script_riya",
    "standing_answer",
]
