"""Builders for the interview tests: Riya Kapoor (the sample profile), the Headout role the canvas draws, a scripted model, and
a service over the fakes.

The scripted model answers the way Haiku would, including the mistakes code must catch; a test that wants a mistake passes
its own answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tayyari.contract.discover import Place, QuickFit
from tayyari.contract.interview import PlanSlot, RoleSource, SlotSource
from tayyari.contract.jobsearch import AiJob, AskCounts, AskMet
from tayyari.contract.models import Profile
from tayyari.core.paths import SAMPLE_DIR
from tayyari.core.settings import InterviewSettings
from tayyari.interview.service import InterviewService
from tayyari.jobsearch.brief import Evidence, evidence_lines
from tayyari.llm.types import CallContext
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.clock import FakeClock
from tests.fakes.interview import FakeInterviewRepo

MODEL = "claude-haiku-5-5"
CTX = CallContext(purpose="interview.judge", request_id="req-1")
START = datetime(2026, 10, 16, 9, 0, 0, tzinfo=UTC)

SAMPLE: Profile = Profile.from_dict(
    json.loads((SAMPLE_DIR / "sample-profile.json").read_text(encoding="utf-8"))
)
OTHER: Profile = SAMPLE.model_copy(update={"id": "someone-else"})
EVIDENCE: dict[str, Evidence] = {line.id: line for line in evidence_lines(SAMPLE)}
RUN = RoleSource(runId="run-1", jobId="job-headout")

REACT_ASK = "Strong proficiency in React (must-have), along with JavaScript and TypeScript"
PERF_ASK = "Experience with Git and basic performance optimization"
CSS_GAP = "Solid understanding of HTML, CSS, responsive design, and cross-browser compatibility"
HEADOUT_POSTING = f"""Frontend Engineer
Headout · Bengaluru

What you'll need
- {REACT_ASK}
- {PERF_ASK}
- {CSS_GAP}
- 1-3 years of frontend development experience"""


def headout_job(**over: Any) -> AiJob:
    """The Headout role of the canvas: two asks the resume shows (React, performance) and one it does not."""
    values: dict[str, Any] = {
        "id": RUN.jobId,
        "title": "Frontend Engineer",
        "employer": "Headout",
        "place": Place(label="Bengaluru", city="Bengaluru", precision="city"),
        "workMode": "onsite",
        "checkedAt": START.isoformat(),
        "applyUrl": "https://job-boards.greenhouse.io/headout/jobs/1",
        "applyHost": "job-boards.greenhouse.io",
        "foundVia": "web",
        "fit": "good",
        "level": "at",
        "met": [
            AskMet(ask=REACT_ASK, cite=EVIDENCE["E3"].cite, partly=False),
            AskMet(ask=PERF_ASK, cite=EVIDENCE["E11"].cite, partly=False),
        ],
        "gaps": [CSS_GAP],
        "asks": AskCounts(met=2, partly=0, notShown=1),
        "quickFit": QuickFit(word="worth_a_look", named=3, matched=2, notFound=[], reasons=[]),
    }
    return AiJob(**{**values, **over})


class FakeRoles:
    """A `RoleLookup` over a dict ``{(profile_id, run_id, job_id): (job, posting_text)}``; every call is kept."""

    def __init__(self, roles: dict[tuple[str, str, str], tuple[AiJob, str]] | None = None) -> None:
        self.roles = (
            roles
            if roles is not None
            else {(SAMPLE.id, RUN.runId, RUN.jobId): (headout_job(), HEADOUT_POSTING)}
        )
        self.calls: list[tuple[str, str, str]] = []

    async def role(self, profile_id: str, run_id: str, job_id: str) -> tuple[AiJob, str] | None:
        self.calls.append((profile_id, run_id, job_id))
        return self.roles.get((profile_id, run_id, job_id))


@dataclass
class Rig:
    service: InterviewService
    claude: FakeClaudeClient
    repo: FakeInterviewRepo
    clock: FakeClock
    roles: FakeRoles
    settings: InterviewSettings = field(default_factory=InterviewSettings)


def make_rig(*, settings: InterviewSettings | None = None, roles: FakeRoles | None = None) -> Rig:
    claude = FakeClaudeClient()
    repo = FakeInterviewRepo()
    clock = FakeClock(START)
    lookup = roles if roles is not None else FakeRoles()
    cfg = settings if settings is not None else InterviewSettings()
    service = InterviewService(claude=claude, settings=cfg, sessions=repo, roles=lookup, clock=clock)
    return Rig(service, claude, repo, clock, lookup, cfg)


# ───────────────────────── scripted model answers ─────────────────────────


def lines(
    question: str, push: str, floor: str, covers: list[str] | None = None, *, slot: str
) -> dict[str, Any]:
    return {
        "id": slot,
        "covers": covers if covers is not None else ["Names what was done", "Says how it was checked"],
        "question": question,
        "push": push,
        "floor": floor,
    }


def sample_phrasing() -> dict[str, Any]:
    """What a good phrasing of the Headout plan (s1 ask, s2 ask, s3 gap, s4 claim, s5 story) looks like. Every line passes."""
    return {
        "slots": [
            lines(
                "Walk me through the Kitebird checkout rebuild. What did you change first, and why?",
                "When the cart update failed, what did the person see?",
                "Which part of the checkout was yours, in one sentence?",
                ["Names what changed first", "Says what the person built themselves"],
                slot="s1",
            ),
            lines(
                "Your resume says checkout LCP went from 4.1s to 1.9s at Kitebird. How did you find what was slow?",
                "Which single change moved it most, and how did you measure it?",
                "What is one thing you changed to make it faster?",
                ["Names what was slow", "Says which change was theirs", "Says how the gain was measured"],
                slot="s2",
            ),
            lines(
                "How do you make sure a component works on every screen size and browser your customers use?",
                "What was the last cross-browser bug you fixed?",
                "Which browsers do you test in?",
                ["Names the browsers and devices", "Describes a real bug and its fix"],
                slot="s3",
            ),
            lines(
                "You mentioned owning Kite UI with 42 components. What did owning it involve week to week?",
                "How did the three squads ask for changes, and who decided?",
                "What did you do yourself on Kite UI?",
                ["Says what owning it meant", "Names who decided"],
                slot="s4",
            ),
            lines(
                "Tell me about a time a design couldn't be built the way it was drawn. What did you do?",
                "What happened in the end?",
                "Who did you talk to first?",
                ["A specific handoff", "What the person changed", "How it ended"],
                slot="s5",
            ),
        ]
    }


def judged(
    answer: str,
    verdict: str = "defended",
    *,
    quote: str | None = None,
    why: str = "The answer names what was built and what happens when it fails.",
    on_topic: bool = True,
    story: dict[str, str] | None = None,
    new_fact: str = "",
) -> dict[str, Any]:
    """A judge answer for ``answer``: the quote is its first eight words unless one is given."""
    words = answer.split()
    return {
        "quotes": [quote if quote is not None else " ".join(words[:8])],
        "onTopic": on_topic,
        "story": {"situation": "", "action": "", "result": "", "reflection": "", **(story or {})},
        "verdict": verdict,
        "why": why,
        "newFact": new_fact,
    }


def slot_of(kind: str, topic: str, *sources: SlotSource, slot_id: str = "s1") -> PlanSlot:
    return PlanSlot(id=slot_id, kind=kind, topic=topic, sources=list(sources))  # type: ignore[arg-type]


def evidence_source(text: str, source: str = "Resume") -> SlotSource:
    return SlotSource(origin="evidence", text=text, source=source)


def posting_source(text: str) -> SlotSource:
    return SlotSource(origin="posting", text=text)


__all__ = [
    "CSS_GAP",
    "CTX",
    "EVIDENCE",
    "HEADOUT_POSTING",
    "MODEL",
    "OTHER",
    "PERF_ASK",
    "REACT_ASK",
    "RUN",
    "SAMPLE",
    "START",
    "FakeRoles",
    "Rig",
    "evidence_source",
    "headout_job",
    "judged",
    "lines",
    "make_rig",
    "posting_source",
    "sample_phrasing",
    "slot_of",
]
