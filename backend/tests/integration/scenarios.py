"""Ready-made analysis scenarios: a DOCX resume + one link + pasted notes, run end to end against the real app.

Two variants share the texts and the scripted Claude answers:

    happy path   the link is a readable blog (Jev labels it "Blog", is unsure whether it shows personal work so
                 Claude decides that one question), and the synthesizer's graph holds one evidence quote that no
                 source supports (Jev says "says nothing", so it is dropped)
    walled link  the link is a login wall: Jev says so with high confidence and the page is short, so triage
                 skips the source and its Haiku reader is cancelled

Each `run_*` helper is an async context manager that starts the app, runs the analysis, lets the background
decision work finish and yields a `Composite` with everything a test may want to look at.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from tayyari.contract import AnalysisEvent, DoneEvent, Profile
from tayyari.core.settings import Settings
from tests.fakes.claude import StructuredCall
from tests.fakes.jev import FakeJev, choice_answer, noul_answer
from tests.integration.support import (
    DOCX_MIME,
    Env,
    FakeWeb,
    JevScript,
    RecordingClaude,
    analysis_events,
    fallback_answer,
    make_docx,
    readable_page,
    reader_output,
    running,
    source_id_of,
)

EMAIL = "test.person@example.com"
PHONE = "+91 98765 43210"

RESUME_PARAGRAPHS = [
    "Test Person",
    f"Frontend Engineer, Pune, India. {EMAIL}, {PHONE}",
    "Experience",
    "Acme Labs, Frontend Engineer, 2023 to 2026. Built the checkout in React and TypeScript, shipped weekly, "
    "mentored two interns and cut the largest bundle by forty percent.",
    "Skills",
    "React, TypeScript, Node.js, GraphQL, Figma, accessibility, Storybook, design systems.",
]
LINK_URL = "https://example.com/writing"
LINK_TEXT = (
    "Notes from a design-systems engineer. This post walks through how a product team moved onto design "
    "tokens in Storybook, added visual regression tests to catch drift, and kept every component accessible. "
    "The author shipped the checkout redesign at Acme Labs and writes about the trade-offs honestly, with "
    "numbers and screenshots from the real rollout. "
) * 2
WALL_TEXT = "Please sign in to continue."
NOTES = "I also run a design-systems newsletter and mentor two juniors every month."
BOGUS_CLAIM = "Led a Kubernetes migration of 42 services at Zorblax"
"""A quote whose names and numbers appear in no source: the code stage cannot settle it, so Jev is asked."""

SYNTH_SKILLS: list[tuple[str, int, str, list[tuple[str, str]]]] = [
    # (name, score, domain, [(source key, quote)]); the source key is "resume", "link" or "notes"
    (
        "React",
        88,
        "frontend",
        [("resume", "Built the checkout in React and TypeScript"), ("resume", BOGUS_CLAIM)],
    ),
    ("TypeScript", 75, "frontend", [("resume", "React and TypeScript, shipped weekly")]),
    ("Accessibility", 62, "frontend", [("resume", "accessibility, Storybook, design systems")]),
    ("Mentoring", 55, "practice", [("resume", "mentored two interns")]),
    ("Design systems", 48, "practice", [("notes", "design-systems newsletter")]),
    ("Testing", 46, "practice", [("link", "visual regression tests")]),
    ("LLM apps", 30, "ai", []),
]


def composite_synth(link_source: str = "link-1", notes_source: str = "notes") -> dict[str, Any]:
    """A valid synthesizer answer for the composite scenario: 3 domains, 7 skills (6 at 45+, all with evidence)
    and 3 roles. ``link_source`` and ``notes_source`` say which source id the link and notes quotes belong to."""
    source_ids = {"resume": "resume", "link": link_source, "notes": notes_source}
    skills = [
        {
            "id": f"skill-{i}",
            "name": name,
            "domainId": domain,
            "score": score,
            "evidence": [{"sourceId": source_ids[key], "text": text} for key, text in quotes],
            "unlocks": ["Senior Engineer"] if i % 2 == 0 else [],
        }
        for i, (name, score, domain, quotes) in enumerate(SYNTH_SKILLS, start=1)
    ]
    return {
        "person": {
            "name": "Test Person",
            "headline": "Frontend Engineer · 3 yrs · Pune",
            "currentRole": "Frontend Engineer",
            "yearsExperience": 3.0,
            "location": "Pune, India",
            "summary": "A frontend engineer.",
        },
        "mastery": {"score": 66, "insight": "Solid UI work; tests are thin."},
        "domains": [
            {"id": "frontend", "name": "Frontend", "short": "Frontend", "score": 80, "summary": "UI work"},
            {
                "id": "practice",
                "name": "Engineering Practice",
                "short": "Practice",
                "score": 50,
                "summary": "Tests",
            },
            {"id": "ai", "name": "AI and LLM Apps", "short": "AI", "score": 30, "summary": "Early"},
        ],
        "skills": skills,
        "roles": [
            {
                "id": "senior-engineer",
                "title": "Senior Engineer",
                "alignment": 72,
                "eta": "~6 months",
                "why": "Strong UI work, thin tests.",
                "requirements": [
                    {"name": "React", "skillId": "skill-1", "have": True, "current": 88, "target": 80},
                    {"name": "System design", "have": False, "current": 35, "target": 70},
                ],
            },
            {
                "id": "ai-engineer",
                "title": "AI Engineer",
                "alignment": 45,
                "eta": "~1 year",
                "why": "Needs evals.",
                "requirements": [
                    {"name": "LLM apps", "skillId": "skill-7", "have": False, "current": 30, "target": 70}
                ],
            },
            {
                "id": "design-engineer",
                "title": "Design Engineer",
                "alignment": 60,
                "eta": "~4 months",
                "why": "Motion and craft.",
                "requirements": [],
            },
        ],
        "sources": [],
    }


def script_basic_analysis(
    claude: RecordingClaude,
    *,
    reader: Callable[[StructuredCall], Any] | None = None,
    first_synth: Any = None,
) -> None:
    """Make any notes-only analysis succeed: a generic reader answer per source and the composite graph.

    ``reader`` replaces the reader script (the disconnect test uses it to hold a reader); ``first_synth`` is
    an answer (or script) for the first synthesis call only, ahead of the generic one."""

    def generic_reader(call: StructuredCall) -> dict[str, Any]:
        return reader_output(source_id_of(call))

    claude.script_structured("reader", reader or generic_reader, repeat=True)
    if first_synth is not None:
        claude.script_structured("synth", first_synth)
    claude.script_structured(
        "synth", lambda _call: composite_synth(link_source="notes", notes_source="notes"), repeat=True
    )


@dataclass
class Composite:
    """One finished composite analysis: the response, its events, the profile it ended with and the fakes."""

    env: Env
    response: httpx.Response
    events: list[AnalysisEvent]
    web: FakeWeb
    jev: FakeJev
    script: JevScript
    reader_cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    """Set when the Haiku reader of the link was cancelled mid-call (triage skipped the page)."""

    @property
    def done(self) -> DoneEvent:
        last = self.events[-1]
        assert isinstance(last, DoneEvent), last
        return last

    @property
    def profile(self) -> Profile:
        return self.done.profile


def _wall_triage(state: Any) -> dict[str, Any]:
    return {
        **readable_page(state),
        "triage.text_quality": choice_answer("login_or_block_wall", confidence=0.97),
    }


def _blog_triage(state: Any) -> dict[str, Any]:
    return {
        **readable_page(state),
        "triage.about_person_work": noul_answer(0.5),  # unsure: below its 0.7 bar, so Claude decides
        "triage.page_type": choice_answer("blog_or_articles", confidence=0.9),
    }


@asynccontextmanager
async def run_composite(
    settings: Settings, web: FakeWeb, *, walled: bool = False
) -> AsyncIterator[Composite]:
    """Start the app, run resume + link + notes through it and yield the result (the app stays up inside the
    ``async with``, so a test can query the database)."""
    triage_seen = asyncio.Event()
    cancelled = asyncio.Event()
    web.page(
        LINK_URL,
        WALL_TEXT if walled else LINK_TEXT,
        title="Sign in" if walled else "Writing · Test Person",
    )

    def evidence_verdict(claim: str) -> str:
        return "says_nothing" if "Zorblax" in claim else "supports"

    script = JevScript(
        triage=_wall_triage if walled else _blog_triage,
        evidence=evidence_verdict,
        on_triage=lambda _state: triage_seen.set(),
    )
    jev = FakeJev(script)
    claude = RecordingClaude()

    async def reader(call: StructuredCall) -> dict[str, Any]:
        source_id = source_id_of(call)
        if source_id == "link-1":
            # Let Jev's triage verdict arrive (and be applied) before this reader could finish.
            try:
                await asyncio.wait_for(triage_seen.wait(), 5)
                await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return reader_output(source_id)

    def synth(_call: StructuredCall) -> dict[str, Any]:
        return composite_synth()

    claude.script_structured("reader", reader, repeat=True)
    claude.script_structured("synth", synth, repeat=True)
    claude.script_structured("decision_fallback", fallback_answer, repeat=True)

    async with running(settings, claude=claude, jev=jev) as env:
        response = await env.analyze(
            resume=("cv.docx", make_docx(RESUME_PARAGRAPHS), DOCX_MIME),
            links=[LINK_URL],
            notes=NOTES,
            goal="Become a senior design engineer",
        )
        await env.settle()
        yield Composite(
            env=env,
            response=response,
            events=analysis_events(response),
            web=web,
            jev=jev,
            script=script,
            reader_cancelled=cancelled,
        )


__all__ = [
    "BOGUS_CLAIM",
    "EMAIL",
    "LINK_URL",
    "NOTES",
    "PHONE",
    "RESUME_PARAGRAPHS",
    "WALL_TEXT",
    "Composite",
    "composite_synth",
    "run_composite",
    "script_basic_analysis",
]
