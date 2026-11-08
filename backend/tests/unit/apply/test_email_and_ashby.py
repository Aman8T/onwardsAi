"""Employers' emails moving stages: the quote must be in the email, the application must be the only one the email names,
and the stage machine decides. (Ashby's preview is in test_ashby.py.)"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tayyari.apply.email import EmailTracker, keyword_stage
from tayyari.contract.apply import EmailClassifyRequest
from tayyari.llm.types import StructuredResult, Usage
from tayyari.storage.ports import StoredApplication
from tests.fakes.apply import FakeApplyRepo

T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
OWNER = "a:" + "5" * 40


class Clock:
    def now(self) -> datetime:
        return T0

    def monotonic(self) -> float:
        return 0.0


class Claude:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    async def structured(self, **kw: Any) -> StructuredResult:
        assert kw["ctx"].purpose == "apply.email"
        return StructuredResult(self.data, Usage(model=kw["model"]))


async def repo_with(stage: str = "applied", company: str = "Acme") -> FakeApplyRepo:
    repo = FakeApplyRepo()
    await repo.create_application(StoredApplication(id="ap1", owner_key=OWNER, source="link", job_url="https://x", domain="x",
                                                    ats="lever", stage=stage, created_at=T0, updated_at=T0, company=company))  # fmt: skip
    return repo


def email(body: str, subject: str = "Your application to Acme") -> EmailClassifyRequest:
    return EmailClassifyRequest(subject=subject, sender="no-reply@hire.lever.co", body=body)


def test_keyword_rules_quote_the_sentence() -> None:
    assert keyword_stage("Hi. Unfortunately we will not be moving forward. Best") == (
        "rejected",
        "Unfortunately we will not be moving forward.",
    )
    assert keyword_stage("We'd like to schedule an interview with you next week.")[0] == "interviewing"
    assert keyword_stage("Here are 10 new jobs for you")[0] == "none"


async def test_a_rejection_moves_the_only_matching_application() -> None:
    repo = await repo_with("interviewing")
    got = await EmailTracker(repo, Clock(), None, "m").handle(
        OWNER, email("Thank you. Unfortunately, we have decided to pursue other candidates.")
    )
    assert got.stage == "rejected" and got.applied and got.applicationId == "ap1"
    app = await repo.get_application("ap1", OWNER)
    assert app is not None and app.stage == "rejected"
    (event,) = repo.events
    assert event.source == "email" and "Unfortunately" in (event.detail or {})["note"]


async def test_the_model_must_quote_the_email() -> None:
    repo = await repo_with()
    invented = Claude(
        {
            "quote": "We would love to offer you the role.",
            "stage": "offer",
            "company": "Acme",
            "confidence": "high",
        }
    )
    got = await EmailTracker(repo, Clock(), invented, "m").handle(
        OWNER, email("Thanks for applying, we are reviewing it.")
    )  # type: ignore[arg-type]
    assert got.stage == "none" and not got.applied
    real = Claude(
        {"quote": "we are reviewing it", "stage": "viewed", "company": "Acme", "confidence": "high"}
    )
    got = await EmailTracker(repo, Clock(), real, "m").handle(
        OWNER, email("Thanks for applying, we are reviewing it.")
    )  # type: ignore[arg-type]
    assert got.stage == "viewed" and got.applied


async def test_email_never_moves_backwards_or_guesses_the_application() -> None:
    repo = await repo_with("interviewing")
    got = await EmailTracker(repo, Clock(), None, "m").handle(
        OWNER, email("We have received your application.")
    )
    assert got.stage == "applied" and not got.applied  # interviewing -> applied is backwards
    other = await repo_with("applied", company="Beta")
    got = await EmailTracker(other, Clock(), None, "m").handle(
        OWNER, email("Unfortunately we will not move forward.", subject="Hello")
    )
    assert not got.applied and got.applicationId is None
