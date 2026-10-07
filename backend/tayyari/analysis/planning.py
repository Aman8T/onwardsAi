"""Planning an analysis: which sources will be read, who reads them, and what the goal is.

    plan_sources_for     ingest planning (resume, links, notes), with the sample replay in sample mode
    with_replay          sample mode: replay plausible fetch logs with short delays so the demo feels live
    initial_states       the first `readers` event: one queued `ReaderState` per planned source
    resolve_goal         the goal text, the sample persona's goal in sample mode

All delays go through `tayyari.core.clock.paced`, so ``pacing_scale=0`` (tests, fast demos) makes the replay
instant and a `FakeClock` sees every sleep.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any, cast

from tayyari.contract import AnalyzeRequest, ReaderState
from tayyari.core.clock import Clock, paced
from tayyari.core.text import tidy
from tayyari.ingest import PlannedSource, SourceDoc, plan_sources
from tayyari.sample import SAMPLE_GOAL, SAMPLE_INGEST_LOGS

from .normalise import source_kind_or_link

AGENT: dict[str, str] = {
    "resume": "Resume reader",
    "github": "GitHub analyst",
    "lovable": "Lovable reviewer",
    "linkedin": "LinkedIn reader",
    "portfolio": "Link reader",
    "link": "Link reader",
    "notes": "Notes reader",
}
"""The agent name shown on each reader card, by source kind."""

WHO: dict[str, str] = {
    "resume": "resume",
    "github": "github",
    "lovable": "lovable",
    "linkedin": "linkedin",
    "portfolio": "web",
    "link": "web",
    "notes": "notes",
}
"""Short name used as the ``who`` column of the live log, by source kind."""

WEB_KINDS: frozenset[str] = frozenset({"link", "portfolio", "lovable", "linkedin"})
"""Source kinds that come from a web page: the only ones that go through triage."""

REPLAY_START_S = 0.18
REPLAY_STEP_S = 0.26
REPLAY_LINE_MIN_S = 0.3
REPLAY_LINE_JITTER_S = 0.4


def agent_for(kind: str) -> str:
    return AGENT.get(kind, "Link reader")


def who_for(kind: str) -> str:
    return WHO.get(kind, "web")


def resolve_goal(req: AnalyzeRequest) -> str | None:
    """The person's goal, tidied; the sample persona's goal in sample mode when none was given; else None."""
    return tidy(req.goal) or (SAMPLE_GOAL if req.sample else "") or None


def with_replay(
    p: PlannedSource,
    i: int,
    *,
    clock: Clock,
    scale: float,
    rng: Callable[[], float] = random.random,
) -> PlannedSource:
    """Sample mode: the same source, but its fetch first replays its canned log lines with short delays."""
    inner = p.ingest

    async def ingest(log: Callable[[str], None]) -> SourceDoc:
        await paced(clock, REPLAY_START_S + i * REPLAY_STEP_S, scale)
        for line in SAMPLE_INGEST_LOGS.get(p.id, []):
            await paced(clock, REPLAY_LINE_MIN_S + rng() * REPLAY_LINE_JITTER_S, scale)
            log(line)
        return await inner(log)

    return PlannedSource(p.id, p.kind, p.label, p.kind_label, p.src, p.url, ingest)


def plan_sources_for(
    req: AnalyzeRequest,
    *,
    github_token: str | None,
    clock: Clock,
    scale: float,
    rng: Callable[[], float] = random.random,
) -> list[PlannedSource]:
    """The sources of one analysis in reading order (resume, usable links, notes).

    ``github_token`` is a plain string: pass ``""`` to force unauthenticated GitHub calls (None would make the
    ingester read ``$GITHUB_TOKEN`` itself).
    """
    # `AnalyzeRequest` has the attributes of the legacy `AnalyzeInput` the ingester is annotated with.
    inp: Any = cast("Any", req)
    planned = plan_sources(inp, github_token=github_token)
    if req.sample:
        return [with_replay(p, i, clock=clock, scale=scale, rng=rng) for i, p in enumerate(planned)]
    return planned


def initial_states(planned: list[PlannedSource]) -> dict[str, ReaderState]:
    """One queued reader card per planned source, keyed by source id (insertion order is reading order)."""
    return {
        p.id: ReaderState(
            sourceId=p.id,
            kind=source_kind_or_link(p.kind),
            src=p.src,
            kindLabel=p.kind_label,
            agent=agent_for(p.kind),
            status="queued",
            line="Queued",
            progress=0,
        )
        for p in planned
    }


__all__ = [
    "AGENT",
    "WEB_KINDS",
    "WHO",
    "agent_for",
    "initial_states",
    "plan_sources_for",
    "resolve_goal",
    "who_for",
    "with_replay",
]
