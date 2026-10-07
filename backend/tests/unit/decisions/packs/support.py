"""Builders for the pack tests: a `JevDecisionService` over a real `DecisionRunner` with the real ``defaults.toml``,
driven by `FakeJev`, `FakeClaudeClient`, `FakeRepositories` and a `FakeClock`.

    h = build_pack_harness(make_settings, answers=first_read_answers())
    result = await h.service.first_read(FirstReadInput("resume text", None, date(2026, 10, 7)), CTX)
    assert h.jev.calls[0].question_ids == [...]
    await h.service.drain(1)
    rows = h.rows               # the decision_log rows written so far
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from tayyari.core.settings import JevSettings, Settings
from tayyari.decisions.config import DecisionConfigStore, load_defaults
from tayyari.decisions.packs.first_read import (
    CAREER_FAMILY_ID,
    CAREER_STAGE_ID,
    DEPTH_ID,
    FAMILIES,
    FLAG_IDS,
    RESUME_LIKE_ID,
    STAGES,
)
from tayyari.decisions.packs.service_impl import JevDecisionService
from tayyari.decisions.recorder import DecisionRecorder
from tayyari.decisions.runner import DecisionRunner
from tayyari.decisions.service import DecisionContext, FirstReadInput, TriageInput
from tayyari.llm.breaker import CircuitBreaker
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.clock import FakeClock
from tests.fakes.jev import AnswerScript, FakeJev, choice_answer, noul_answer, score_answer
from tests.fakes.repos import FakeRepositories
from tests.unit.decisions.support import CTX, SlowJev, SpendTrip

TODAY = date(2026, 10, 7)
FAMILY_LABELS = [o.label for o in FAMILIES]
STAGE_LABELS = [o.label for o in STAGES]
CLAUDE_YES = {"answer": True, "confidence": "high"}
CLAUDE_NO = {"answer": False, "confidence": "high"}

SettingsFactory = Callable[..., Settings]


@dataclass
class PackHarness:
    service: JevDecisionService
    runner: DecisionRunner
    jev: FakeJev
    claude: FakeClaudeClient | None
    repos: FakeRepositories
    clock: FakeClock
    store: DecisionConfigStore
    settings: Settings
    breaker: CircuitBreaker
    slow: SlowJev | None = None

    async def drain(self) -> None:
        await self.service.drain(2.0)

    @property
    def rows(self) -> list[Any]:
        return list(self.repos.decisions.rows.values())

    @property
    def calls(self) -> list[Any]:
        return list(self.repos.decisions.calls.values())

    @property
    def fallback_calls(self) -> list[Any]:
        return [] if self.claude is None else self.claude.calls_for("decision_fallback")


def build_pack_harness(
    make_settings: SettingsFactory,
    answers: AnswerScript | None = None,
    *,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
    jev_settings: Mapping[str, Any] | None = None,
    jev_enabled: bool = True,
    with_claude: bool = True,
    spend: SpendTrip | None = None,
    slow_jev_s: float | None = None,
    shadow_wait_s: float = 1.5,
    breaker_failures: int = 5,
    fake_jev: FakeJev | None = None,
) -> PackHarness:
    settings = make_settings(jev=JevSettings(enabled=jev_enabled, **(jev_settings or {})))
    clock = FakeClock()
    repos = FakeRepositories(clock)
    recorder = DecisionRecorder(repos.decisions, clock, flush_interval_s=3600.0)
    breaker = CircuitBreaker(failures=breaker_failures, window_s=30.0, open_for_s=60.0, clock=clock)
    inner = fake_jev if fake_jev is not None else FakeJev(answers, clock=clock)
    slow = SlowJev(inner, slow_jev_s) if slow_jev_s is not None else None
    jev: Any = slow if slow is not None else inner
    claude = FakeClaudeClient(clock=clock) if with_claude else None
    store = DecisionConfigStore(load_defaults(), overrides, jev_enabled=jev_enabled, send_pii=False)
    runner = DecisionRunner(
        jev,
        claude,
        recorder,
        store,
        breaker,
        spend,
        clock,
        "claude-haiku-4-5",
        shadow_wait_s=shadow_wait_s,
    )
    service = JevDecisionService(runner=runner, settings=settings, clock=clock)
    return PackHarness(service, runner, inner, claude, repos, clock, store, settings, breaker, slow)


# ───────────────────────── scripted answers ─────────────────────────


def first_read_answers(**changes: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """A confident first read of a design resume: family and stage, depth 3, three yes answers, a clear resume.

    ``changes`` replace entries by short name (``depth=score_answer(4)``, ``resume_like=noul_answer(0.03)``).
    """
    script: dict[str, Mapping[str, Any]] = {
        CAREER_FAMILY_ID: choice_answer("design_ux", options=FAMILY_LABELS, p=0.92),
        CAREER_STAGE_ID: choice_answer("early_career", options=STAGE_LABELS, p=0.9),
        DEPTH_ID: score_answer(3, n_levels=6, p=0.85),
        RESUME_LIKE_ID: noul_answer(0.97),
    }
    for flag_id in FLAG_IDS:
        script[flag_id] = noul_answer(0.5)
    script["first_read.leads_or_mentors"] = noul_answer(0.95)
    script["first_read.measured_outcomes"] = noul_answer(0.9)
    script["first_read.public_proof"] = noul_answer(0.88)
    for name, value in changes.items():
        script[f"first_read.{name}"] = value
    return script


def triage_answers(
    quality: str = "readable",
    *,
    quality_p: float = 0.95,
    about_person: float = 0.9,
    english: float = 0.95,
    page_type: str = "blog_or_articles",
    page_type_p: float = 0.9,
) -> dict[str, Mapping[str, Any]]:
    qualities = ["readable", "garbled_or_code_dominated", "login_or_block_wall", "empty_or_placeholder"]
    from tayyari.decisions.packs.triage import PAGE_TYPE_OPTIONS

    return {
        "triage.text_quality": choice_answer(quality, options=qualities, p=quality_p),
        "triage.about_person_work": noul_answer(about_person),
        "triage.mainly_english": noul_answer(english),
        "triage.page_type": choice_answer(page_type, options=list(PAGE_TYPE_OPTIONS), p=page_type_p),
    }


def relation_answer(relation: str, p: float = 0.95) -> dict[str, Any]:
    return choice_answer(relation, options=["supports", "contradicts", "says_nothing"], p=p)


def resume_input(text: str = "Jane Doe. Product designer. Led the redesign of checkout.") -> FirstReadInput:
    return FirstReadInput(resume_text=text, notes_text=None, today=TODAY)


def page_input(
    text: str,
    *,
    kind: str = "link",
    host: str | None = "example.com",
    title: str | None = "A page",
    source_id: str = "link-1",
) -> TriageInput:
    return TriageInput(source_id=source_id, kind=kind, host=host, title=title, text=text)


def long_text(n: int = 1200) -> str:
    """Plain prose of at least ``n`` characters (clear of the 400-character "too short" rule)."""
    sentence = "I design checkout flows and run usability studies with real customers. "
    return (sentence * (n // len(sentence) + 1))[:n]


__all__ = [
    "CLAUDE_NO",
    "CLAUDE_YES",
    "CTX",
    "FAMILY_LABELS",
    "STAGE_LABELS",
    "TODAY",
    "DecisionContext",
    "PackHarness",
    "build_pack_harness",
    "first_read_answers",
    "long_text",
    "page_input",
    "relation_answer",
    "resume_input",
    "triage_answers",
]
