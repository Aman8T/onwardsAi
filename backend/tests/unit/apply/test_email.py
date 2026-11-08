"""Employers' emails moving stages (spec 3.2): the quote must be in the email, only a sent (or maybe sent) application is
ever moved, two candidates make it a question for the person, and the model call runs inside a spend reservation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from tayyari.apply.email import AMBIGUOUS_NOTE, NOT_SENT_NOTE, EmailTracker, keyword_stage, target_of
from tayyari.contract.apply import EmailClassifyRequest
from tayyari.llm.errors import SpendCeilingReached
from tayyari.llm.types import StructuredResult, Usage
from tayyari.storage.ports import StoredApplication
from tests.fakes.apply import FakeApplyRepo

T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
OWNER = "u:usr1"


class Clock:
    def now(self) -> datetime:
        return T0

    def monotonic(self) -> float:
        return 0.0


class Claude:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls = 0

    async def structured(self, **kw: Any) -> StructuredResult:
        assert kw["ctx"].purpose == "apply.email"
        self.calls += 1
        return StructuredResult(self.data, Usage(model=kw["model"]))


class Reserve:
    """A spend reservation that records what it held, or refuses like a spent daily ceiling."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.held: list[float] = []
        self.open = 0

    def __call__(self, est_usd: float) -> AbstractAsyncContextManager[None]:
        @asynccontextmanager
        async def hold() -> AsyncIterator[None]:
            if self.refuse:
                raise SpendCeilingReached("spent")
            self.held.append(est_usd)
            self.open += 1
            try:
                yield
            finally:
                self.open -= 1

        return hold()


def app(app_id: str, stage: str, company: str = "Acme", title: str | None = None) -> StoredApplication:
    return StoredApplication(
        id=app_id, owner_key=OWNER, user_id="usr1", source="match", job_url="https://x", domain="x", ats="lever",
        stage=stage, created_at=T0, updated_at=T0, company=company, title=title,
    )  # fmt: skip


async def repo_with(*apps: StoredApplication) -> FakeApplyRepo:
    repo = FakeApplyRepo()
    for item in apps:
        await repo.create_application(item)
    return repo


def email(body: str, subject: str = "Your application to Acme", **extra: Any) -> EmailClassifyRequest:
    return EmailClassifyRequest(subject=subject, sender="no-reply@hire.lever.co", body=body, **extra)


async def stage_of(repo: FakeApplyRepo, app_id: str) -> str:
    found = await repo.get_application(app_id, OWNER)
    assert found is not None
    return found.stage


def test_keyword_rules_quote_the_sentence() -> None:
    assert keyword_stage("Hi. Unfortunately we will not be moving forward. Best") == (
        "rejected",
        "Unfortunately we will not be moving forward.",
    )
    assert keyword_stage("We'd like to schedule an interview with you next week.")[0] == "interviewing"
    assert keyword_stage("Here are 10 new jobs for you")[0] == "none"


def test_a_received_email_settles_only_an_unclear_send() -> None:
    assert target_of("applied", "check_this_one") == "sent"
    assert target_of("applied", "sent") is None
    assert target_of("interviewing", "sent") == "interviewing"
    assert target_of("none", "sent") is None


async def test_check_this_one_becomes_sent_when_the_employer_confirms_it() -> None:
    repo = await repo_with(app("ap1", "check_this_one"))
    got = await EmailTracker(repo, Clock(), None, "m").handle(
        OWNER, email("We have received your application.")
    )
    assert got.stage == "applied" and got.applied and got.applicationId == "ap1"
    assert await stage_of(repo, "ap1") == "sent"
    (event,) = repo.events
    assert (event.source, event.from_stage, event.to_stage) == ("email", "check_this_one", "sent")


async def test_sent_moves_forward_to_interviewing() -> None:
    repo = await repo_with(app("ap1", "sent"))
    got = await EmailTracker(repo, Clock(), None, "m").handle(
        OWNER, email("We'd like to schedule an interview with you next week.")
    )
    assert got.stage == "interviewing" and got.applied
    assert await stage_of(repo, "ap1") == "interviewing"


async def test_a_rejection_moves_the_only_matching_application() -> None:
    repo = await repo_with(app("ap1", "interviewing"))
    got = await EmailTracker(repo, Clock(), None, "m").handle(
        OWNER, email("Thank you. Unfortunately, we have decided to pursue other candidates.")
    )
    assert got.stage == "rejected" and got.applied and got.applicationId == "ap1"
    (event,) = repo.events
    assert event.source == "email" and "Unfortunately" in (event.detail or {})["note"]


@pytest.mark.parametrize("stage", ["waiting_for_you", "approved", "queued", "failed", "needs_signin"])
async def test_an_email_never_moves_an_unsent_application(stage: str) -> None:
    repo = await repo_with(app("ap1", stage))
    tracker = EmailTracker(repo, Clock(), None, "m")
    by_company = await tracker.handle(OWNER, email("We'd like to schedule an interview with you."))
    assert not by_company.applied and by_company.applicationId is None
    named = await tracker.handle(
        OWNER, email("We'd like to schedule an interview with you.", applicationId="ap1")
    )
    assert not named.applied and named.note == NOT_SENT_NOTE
    assert await stage_of(repo, "ap1") == stage and repo.events == []


async def test_two_candidates_make_it_a_question_for_the_person() -> None:
    repo = await repo_with(
        app("ap1", "sent", title="Data Analyst"), app("ap2", "sent", title="Product Analyst")
    )
    tracker = EmailTracker(repo, Clock(), None, "m")
    got = await tracker.handle(OWNER, email("Unfortunately we will not move forward with your application."))
    assert not got.applied and got.applicationId is None and got.note == AMBIGUOUS_NOTE
    assert {await stage_of(repo, "ap1"), await stage_of(repo, "ap2")} == {"sent"}
    # the role named in the email tells them apart
    got = await tracker.handle(
        OWNER,
        email("Unfortunately we will not move forward.", subject="Your Product Analyst application at Acme"),
    )
    assert got.applied and got.applicationId == "ap2"
    assert await stage_of(repo, "ap1") == "sent" and await stage_of(repo, "ap2") == "rejected"


async def test_the_model_must_quote_the_email() -> None:
    repo = await repo_with(app("ap1", "sent"))
    invented = Claude(
        {
            "quote": "We would love to offer you the role.",
            "stage": "offer",
            "company": "Acme",
            "confidence": "high",
        }
    )
    got = await EmailTracker(repo, Clock(), invented, "m").handle(  # type: ignore[arg-type]
        OWNER, email("Thanks for applying, we are reviewing it.")
    )
    assert got.stage == "none" and not got.applied
    real = Claude(
        {"quote": "we are reviewing it", "stage": "viewed", "company": "Acme", "confidence": "high"}
    )
    got = await EmailTracker(repo, Clock(), real, "m").handle(  # type: ignore[arg-type]
        OWNER, email("Thanks for applying, we are reviewing it.")
    )
    assert got.stage == "viewed" and got.applied


async def test_the_model_call_runs_inside_the_spend_reservation() -> None:
    repo = await repo_with(app("ap1", "sent"))
    reserve = Reserve()
    claude = Claude(
        {"quote": "we are reviewing it", "stage": "viewed", "company": "Acme", "confidence": "high"}
    )
    tracker = EmailTracker(repo, Clock(), claude, "m", reserve=reserve, est_usd=0.005)  # type: ignore[arg-type]
    got = await tracker.handle(OWNER, email("Thanks for applying, we are reviewing it."))
    assert got.applied and reserve.held == [0.005] and reserve.open == 0 and claude.calls == 1


async def test_a_refused_reservation_makes_no_call_and_moves_nothing() -> None:
    repo = await repo_with(app("ap1", "sent"))
    claude = Claude({"quote": "x", "stage": "offer", "company": "Acme", "confidence": "high"})
    tracker = EmailTracker(repo, Clock(), claude, "m", reserve=Reserve(refuse=True), est_usd=0.005)  # type: ignore[arg-type]
    got = await tracker.handle(OWNER, email("We'd like to schedule an interview with you."))
    assert claude.calls == 0
    assert (
        got.stage == "interviewing" and got.confidence == "low" and not got.applied
    )  # keyword rules, not believed
    assert await stage_of(repo, "ap1") == "sent"


async def test_email_never_moves_backwards() -> None:
    repo = await repo_with(app("ap1", "interviewing"))
    got = await EmailTracker(repo, Clock(), None, "m").handle(
        OWNER, email("We have received your application.")
    )
    assert got.stage == "applied" and not got.applied
    assert await stage_of(repo, "ap1") == "interviewing"
