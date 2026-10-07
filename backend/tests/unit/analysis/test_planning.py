"""Planning: the source list, the goal, the reader cards and the sample replay."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.analysis import planning
from tayyari.analysis.planning import (
    AGENT,
    WEB_KINDS,
    WHO,
    agent_for,
    initial_states,
    plan_sources_for,
    resolve_goal,
    who_for,
    with_replay,
)
from tayyari.contract import SOURCE_KINDS, AnalyzeRequest
from tayyari.ingest import PlannedSource, SourceDoc
from tayyari.sample import SAMPLE_GOAL, SAMPLE_INGEST_LOGS
from tests.fakes.clock import FakeClock


def test_goal_is_tidied_and_blank_means_none() -> None:
    assert resolve_goal(AnalyzeRequest(goal="  Move   into\nAI  ")) == "Move into AI"
    assert resolve_goal(AnalyzeRequest(goal="   ")) is None
    assert resolve_goal(AnalyzeRequest()) is None


def test_the_sample_persona_has_a_default_goal_but_a_given_goal_wins() -> None:
    assert resolve_goal(AnalyzeRequest(sample=True)) == SAMPLE_GOAL
    assert resolve_goal(AnalyzeRequest(sample=True, goal="Lead a team")) == "Lead a team"


def test_every_source_kind_has_an_agent_and_a_log_name() -> None:
    assert set(AGENT) == set(SOURCE_KINDS) == set(WHO)
    assert agent_for("resume") == "Resume reader"
    assert agent_for("portfolio") == agent_for("link") == "Link reader"
    assert agent_for("unheard-of") == "Link reader"
    assert who_for("portfolio") == who_for("link") == "web"
    assert who_for("github") == "github"
    assert who_for("unheard-of") == "web"


def test_triage_applies_to_web_kinds_only() -> None:
    assert WEB_KINDS == {"link", "portfolio", "lovable", "linkedin"}
    assert not WEB_KINDS & {"resume", "notes", "github"}


def test_initial_states_are_queued_cards_in_reading_order() -> None:
    async def ingest(log: Any) -> SourceDoc:
        raise AssertionError("not called")

    planned = [
        PlannedSource("resume", "resume", "Resume", "Resume · PDF", "cv.pdf", None, ingest),
        PlannedSource("link-1", "link", "Kaggle", "Web page", "kaggle.com/x", "https://kaggle.com/x", ingest),
        PlannedSource("mystery", "mystery", "?", "?", "?", None, ingest),
    ]
    states = initial_states(planned)
    assert list(states) == ["resume", "link-1", "mystery"]
    resume = states["resume"]
    assert (resume.status, resume.line, resume.progress) == ("queued", "Queued", 0)
    assert (resume.agent, resume.kindLabel, resume.src, resume.kind) == (
        "Resume reader",
        "Resume · PDF",
        "cv.pdf",
        "resume",
    )
    assert states["mystery"].kind == "link"  # an unknown kind reads as a generic web page


def test_real_requests_plan_resume_then_links_then_notes() -> None:
    req = AnalyzeRequest(
        resume_bytes=b"%PDF-1.4",
        resume_name="cv.pdf",
        links=("https://github.com/someone", "https://someone.dev/work", "https://github.com/someone"),
        notes="I like frontend work.",
    )
    planned = plan_sources_for(req, github_token="", clock=FakeClock(), scale=0.0)
    assert [(p.id, p.kind) for p in planned] == [
        ("resume", "resume"),
        ("github", "github"),
        ("link-1", "link"),
        ("notes", "notes"),
    ]
    assert planned[0].kind_label == "Resume · PDF"


def test_nothing_to_plan_is_an_empty_list() -> None:
    assert plan_sources_for(AnalyzeRequest(), github_token="", clock=FakeClock(), scale=0.0) == []


def test_the_github_token_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_plan(inp: Any, *, github_token: str | None = None) -> list[PlannedSource]:
        seen["inp"], seen["token"] = inp, github_token
        return []

    monkeypatch.setattr(planning, "plan_sources", fake_plan)
    req = AnalyzeRequest(links=("https://github.com/someone",))
    plan_sources_for(req, github_token="ghp_secret", clock=FakeClock(), scale=0.0)
    assert seen == {"inp": req, "token": "ghp_secret"}
    plan_sources_for(req, github_token="", clock=FakeClock(), scale=0.0)
    assert seen["token"] == ""


async def test_sample_requests_plan_the_four_fixture_sources() -> None:
    planned = plan_sources_for(AnalyzeRequest(sample=True), github_token="", clock=FakeClock(), scale=0.0)
    assert [p.id for p in planned] == ["resume", "github", "lovable-1", "portfolio-1"]
    docs = [await p.ingest(lambda _: None) for p in planned]
    assert all(doc.ok and doc.text for doc in docs)
    assert [d.id for d in docs] == [p.id for p in planned]


async def test_the_sample_replay_logs_canned_lines_and_waits_on_the_clock() -> None:
    clock = FakeClock()
    lines: list[str] = []

    async def inner(log: Any) -> SourceDoc:
        lines.append("inner ran")
        return SourceDoc(id="github", kind="github", label="GitHub")

    original = PlannedSource(
        "github", "github", "GitHub", "GitHub profile", "github.com/x", "https://github.com/x", inner
    )
    replay = with_replay(original, 2, clock=clock, scale=1.0, rng=lambda: 0.5)
    assert (replay.id, replay.kind, replay.label, replay.src, replay.url) == (
        original.id,
        original.kind,
        original.label,
        original.src,
        original.url,
    )
    doc = await replay.ingest(lines.append)
    canned = SAMPLE_INGEST_LOGS["github"]
    assert lines == [*canned, "inner ran"]  # the canned lines come first, then the real fetch
    assert doc.id == "github"
    assert clock.sleeps == pytest.approx([0.18 + 2 * 0.26, *([0.3 + 0.5 * 0.4] * len(canned))])


async def test_the_replay_is_instant_when_pacing_is_off() -> None:
    clock = FakeClock()

    async def inner(log: Any) -> SourceDoc:
        return SourceDoc(id="resume", kind="resume", label="Resume")

    original = PlannedSource("resume", "resume", "Resume", "Resume", "cv", None, inner)
    seen: list[str] = []
    await with_replay(original, 0, clock=clock, scale=0.0).ingest(seen.append)
    assert seen == SAMPLE_INGEST_LOGS["resume"]
    assert clock.sleeps == []


async def test_a_source_without_canned_lines_just_runs() -> None:
    clock = FakeClock()

    async def inner(log: Any) -> SourceDoc:
        return SourceDoc(id="link-9", kind="link", label="Web")

    original = PlannedSource("link-9", "link", "Web", "Web page", "x", None, inner)
    seen: list[str] = []
    doc = await with_replay(original, 1, clock=clock, scale=1.0).ingest(seen.append)
    assert (seen, doc.id) == ([], "link-9")
    assert clock.sleeps == pytest.approx([0.18 + 0.26])
