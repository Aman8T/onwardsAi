"""Auto-apply storage, run against every backend (the fake, SQLite, and Postgres when configured), so `FakeApplyRepo` cannot
drift from `SqlApplyRepo`: owner scoping, the stage-guarded update, the worker's lease (and that ``submitting`` is never
claimed again), the sweep of expired leases, one application per posting per person, screenshots and confirmations kept
apart from the row, the purge of filled values, history order, playbook counters and status, run metrics, the saved
profile and the answer bank."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from tayyari.storage.ports import (
    NEVER_STORED_ANSWER_KEYS,
    ApplyRepo,
    StoredAnswer,
    StoredApplication,
    StoredApplicationEvent,
    StoredPlaybook,
    StoredRunMetric,
    account_owner_key,
)
from tayyari.storage.repos import build_repositories
from tayyari.storage.repos.apply import SqlApplyRepo
from tests.fakes.apply import FakeApplyRepo
from tests.unit.storage.conftest import T0, open_db

OWNER = "a:" + "1" * 40
OTHER = "a:" + "2" * 40


@pytest_asyncio.fixture
async def repo(backend: str, tmp_path: Path) -> AsyncIterator[ApplyRepo]:
    if backend == "fake":
        yield FakeApplyRepo()
        return
    async with open_db(backend, tmp_path) as db:
        yield SqlApplyRepo(db)


@dataclass(frozen=True)
class People:
    """A repository with two signed-in accounts (applications and the answer bank reference ``users``)."""

    repo: ApplyRepo
    ana: str
    ben: str


@pytest_asyncio.fixture
async def people(backend: str, tmp_path: Path) -> AsyncIterator[People]:
    if backend == "fake":
        yield People(FakeApplyRepo(), "user-ana", "user-ben")
        return
    async with open_db(backend, tmp_path) as db:
        users = build_repositories(db).users
        ana = (await users.sign_in_email(email="ana@example.com", now=T0)).id
        ben = (await users.sign_in_email(email="ben@example.com", now=T0)).id
        yield People(SqlApplyRepo(db), ana, ben)


def owned(
    app_id: str,
    user_id: str,
    *,
    stage: str = "queued",
    minutes: int = 0,
    posting_key: str | None = None,
    **fields: Any,
) -> StoredApplication:
    """A signed-in person's application (the shape the new start writes)."""
    at = T0 + timedelta(minutes=minutes)
    values: dict[str, Any] = {
        "id": app_id,
        "owner_key": account_owner_key(user_id),
        "user_id": user_id,
        "match_id": f"match-{app_id}",
        "source": "match",
        "job_url": f"https://jobs.lever.co/acme/{app_id}",
        "domain": "jobs.lever.co",
        "ats": "lever",
        "stage": stage,
        "created_at": at,
        "updated_at": at,
        "company": "Acme",
        "posting_key": posting_key,
        "resume_choice": "tailored",
    }
    values.update(fields)
    return StoredApplication(**values)


def answer(user_id: str, key: str, value: str | None = "30 days", **fields: Any) -> StoredAnswer:
    values: dict[str, Any] = {
        "user_id": user_id,
        "question_key": key,
        "question": "What is your notice period?",
        "value": value,
        "source": "form",
        "ask_each_time": False,
        "answered_at": T0,
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(fields)
    return StoredAnswer(**values)


def app(
    app_id: str, *, owner: str = OWNER, stage: str = "queued", minutes: int = 0, domain: str = "jobs.lever.co"
) -> StoredApplication:
    at = T0 + timedelta(minutes=minutes)
    return StoredApplication(
        id=app_id,
        owner_key=owner,
        source="link",
        job_url=f"https://{domain}/acme/{app_id}",
        domain=domain,
        ats="lever",
        stage=stage,
        created_at=at,
        updated_at=at,
        company="Acme",
        title="Engineer",
        job={"description": "Build things"},
    )


async def test_an_application_is_read_back_by_its_owner_only(repo: ApplyRepo) -> None:
    await repo.create_application(app("ap1"))
    mine = await repo.get_application("ap1", OWNER)
    assert mine is not None and mine.company == "Acme" and mine.job == {"description": "Build things"}
    assert mine.has_screenshot is False and mine.attempts == 0
    assert await repo.get_application("ap1", OTHER) is None
    assert await repo.get_application("ap1", None) is not None  # the worker's unscoped read
    assert await repo.get_application("nope", OWNER) is None


async def test_the_list_is_newest_first_and_scoped(repo: ApplyRepo) -> None:
    await repo.create_application(app("ap1", minutes=0))
    await repo.create_application(app("ap2", minutes=5))
    await repo.create_application(app("ap3", owner=OTHER, minutes=9))
    assert [a.id for a in await repo.list_applications(OWNER)] == ["ap2", "ap1"]
    assert await repo.count_created_since(OWNER, T0 + timedelta(minutes=1)) == 1


async def test_update_checks_the_stage_and_the_columns(repo: ApplyRepo) -> None:
    await repo.create_application(app("ap1"))
    now = T0 + timedelta(minutes=3)
    moved = await repo.update_application(
        "ap1", {"stage": "filling", "attempts": 1}, now=now, expected_stage="queued"
    )
    assert moved is not None and moved.stage == "filling" and moved.attempts == 1 and moved.updated_at == now
    assert await repo.update_application("ap1", {"stage": "failed"}, now=now, expected_stage="queued") is None
    still = await repo.get_application("ap1", OWNER)
    assert still is not None and still.stage == "filling"
    with pytest.raises(ValueError):
        await repo.update_application("ap1", {"owner_key": OTHER}, now=now)
    review = {"digest": "d" * 64, "fields": [{"key": "email", "value": "a@b.c"}]}
    saved = await repo.update_application(
        "ap1", {"review": review, "review_digest": "d" * 64, "needs": [{"key": "q1"}]}, now=now
    )
    assert saved is not None and saved.review == review and saved.needs == [{"key": "q1"}]


async def test_claim_takes_queued_and_approved_rows_once(repo: ApplyRepo) -> None:
    await repo.create_application(app("old", minutes=0))
    await repo.create_application(app("new", minutes=1))
    await repo.create_application(app("waiting", stage="ready_to_submit", minutes=2))
    await repo.create_application(app("done", stage="applied", minutes=3))
    now = T0 + timedelta(minutes=10)
    lease = now + timedelta(minutes=10)
    first = await repo.claim_next(now=now, lease_owner="w1", lease_until=lease)
    assert first is not None and first.id == "old" and first.lease_owner == "w1"
    second = await repo.claim_next(now=now, lease_owner="w2", lease_until=lease)
    assert second is not None and second.id == "new"
    assert (
        await repo.claim_next(now=now, lease_owner="w3", lease_until=lease) is None
    )  # not approved, applied
    await repo.update_application("waiting", {"approved_at": now, "approved_digest": "x" * 64}, now=now)
    approved = await repo.claim_next(now=now + timedelta(seconds=1), lease_owner="w3", lease_until=lease)
    assert approved is not None and approved.id == "waiting"
    # an expired lease is free again; a released one at once
    await repo.release("new", "someone-else")
    assert await repo.claim_next(now=now, lease_owner="w4", lease_until=lease) is None
    await repo.release("new", "w2")
    again = await repo.claim_next(now=now, lease_owner="w4", lease_until=lease)
    assert again is not None and again.id == "new"
    later = await repo.claim_next(
        now=lease + timedelta(seconds=1), lease_owner="w5", lease_until=lease + timedelta(hours=1)
    )
    assert later is not None and later.id == "old"


async def test_claim_skips_excluded_domains(repo: ApplyRepo) -> None:
    await repo.create_application(app("a", domain="jobs.lever.co"))
    await repo.create_application(app("b", domain="job-boards.greenhouse.io", minutes=1))
    now = T0 + timedelta(minutes=5)
    got = await repo.claim_next(
        now=now, lease_owner="w", lease_until=now + timedelta(minutes=5), exclude_domains=["jobs.lever.co"]
    )
    assert got is not None and got.id == "b"


async def test_screenshots_live_apart_and_are_purged(repo: ApplyRepo) -> None:
    await repo.create_application(app("ap1"))
    await repo.put_screenshot("ap1", b"\xff\xd8jpeg")
    assert await repo.get_screenshot("ap1", OWNER) == b"\xff\xd8jpeg"
    assert await repo.get_screenshot("ap1", OTHER) is None
    listed = await repo.list_applications(OWNER)
    assert listed[0].has_screenshot is True
    assert await repo.purge_screenshots(T0 - timedelta(days=1)) == 0
    assert await repo.purge_screenshots(T0 + timedelta(days=1)) == 1
    assert await repo.get_screenshot("ap1", OWNER) is None


async def test_events_come_back_in_order_and_go_with_the_application(repo: ApplyRepo) -> None:
    await repo.create_application(app("ap1"))
    for n, (frm, to) in enumerate([(None, "queued"), ("queued", "filling"), ("filling", "ready_to_submit")]):
        await repo.add_event(
            StoredApplicationEvent(
                id=f"ev{n}",
                application_id="ap1",
                at=T0 + timedelta(seconds=n),
                kind="stage",
                source="engine",
                from_stage=frm,
                to_stage=to,
                detail={"n": n},
            )
        )
    events = await repo.list_events("ap1")
    assert [e.to_stage for e in events] == ["queued", "filling", "ready_to_submit"] and events[0].detail == {
        "n": 0
    }
    assert await repo.delete_application("ap1", OTHER) is False
    assert await repo.delete_application("ap1", OWNER) is True
    assert await repo.list_events("ap1") == [] and await repo.get_application("ap1", None) is None


async def test_playbooks_count_results_and_change_status(repo: ApplyRepo) -> None:
    pb = StoredPlaybook(
        id="pb1",
        ats="site:careers.acme.com",
        domain="careers.acme.com",
        status="draft",
        origin="recorded",
        version=1,
        spec={"steps": []},
        successes=0,
        failures=0,
        created_at=T0,
        updated_at=T0,
    )
    await repo.save_playbook(pb)
    after = await repo.record_playbook_result("pb1", success=True, now=T0 + timedelta(minutes=1))
    assert after is not None and after.successes == 1 and after.failures == 0
    after = await repo.record_playbook_result("pb1", success=False, now=T0 + timedelta(minutes=2))
    assert after is not None and after.failures == 1
    promoted = await repo.set_playbook_status("pb1", "active", now=T0 + timedelta(minutes=3))
    assert promoted is not None and promoted.status == "active" and promoted.promoted_at is not None
    assert [p.id for p in await repo.list_playbooks(domain="careers.acme.com", statuses=["active"])] == [
        "pb1"
    ]
    assert await repo.list_playbooks(ats="lever") == []
    await repo.save_playbook(pb)  # saving the same id again replaces it: still one row
    assert len(await repo.list_playbooks()) == 1
    assert await repo.record_playbook_result("nope", success=True, now=T0) is None


async def test_run_metrics_are_counted_per_domain(repo: ApplyRepo) -> None:
    for n in range(3):
        await repo.add_run_metric(
            StoredRunMetric(
                id=f"m{n}",
                created_at=T0 + timedelta(minutes=n),
                run_kind="fill",
                ats="lever",
                domain="jobs.lever.co",
                mode="playbook" if n else "ai",
                outcome="success",
                steps=5,
                cost_micro_usd=10 * n,
            )
        )
    assert await repo.count_domain_runs_since("jobs.lever.co", T0 + timedelta(minutes=1)) == 2
    assert await repo.last_domain_run_at("jobs.lever.co") == T0 + timedelta(minutes=2)
    assert await repo.last_domain_run_at("example.com") is None
    rows = await repo.list_run_metrics(T0)
    assert [m.id for m in rows] == ["m2", "m1", "m0"] and rows[0].cost_micro_usd == 20


async def test_the_profile_keeps_details_answers_and_resume_apart(repo: ApplyRepo) -> None:
    assert await repo.get_profile(OWNER) is None
    made = await repo.put_details(OWNER, user_id=None, details={"firstName": "Riya"}, now=T0)
    assert made.details == {"firstName": "Riya"} and made.answers == {} and made.resume_bytes is None
    with_cv = await repo.put_resume(
        OWNER, user_id=None, name="cv.pdf", media_type="application/pdf", data=b"%PDF", now=T0
    )
    assert (
        with_cv.resume_name == "cv.pdf"
        and with_cv.resume_bytes == b"%PDF"
        and with_cv.details == {"firstName": "Riya"}
    )
    merged = await repo.merge_answers(OWNER, user_id=None, answers={"q:1": {"answer": "Yes"}}, now=T0)
    merged = await repo.merge_answers(OWNER, user_id=None, answers={"q:2": {"answer": "No"}}, now=T0)
    assert set(merged.answers) == {"q:1", "q:2"}
    gone = await repo.put_resume(OWNER, user_id=None, name=None, media_type=None, data=None, now=T0)
    assert gone.resume_bytes is None and gone.resume_name is None and set(gone.answers) == {"q:1", "q:2"}
    found = await repo.get_profile(OWNER)
    assert found is not None and found.details == {"firstName": "Riya"}


# ───────────────────────────── the A1 build ─────────────────────────────


async def test_claim_takes_queued_and_approved_rows_once_and_never_submitting(people: People) -> None:
    repo = people.repo
    await repo.create_application(owned("fill", people.ana, minutes=0))
    await repo.create_application(owned("send", people.ana, stage="approved", minutes=1))
    await repo.create_application(owned("clicking", people.ana, stage="submitting", minutes=2))
    await repo.create_application(owned("asks", people.ana, stage="waiting_for_you", minutes=3))
    await repo.create_application(owned("unclear", people.ana, stage="check_this_one", minutes=4))
    now = T0 + timedelta(minutes=10)
    lease = now + timedelta(minutes=10)
    first = await repo.claim_next(now=now, lease_owner="w1", lease_until=lease)
    second = await repo.claim_next(now=now, lease_owner="w2", lease_until=lease)
    assert first is not None and first.id == "fill" and first.lease_owner == "w1"
    assert second is not None and second.id == "send"
    assert await repo.claim_next(now=now, lease_owner="w3", lease_until=lease) is None


async def test_a_submitting_row_with_an_expired_lease_is_never_claimed(people: People) -> None:
    repo = people.repo
    lease = T0 + timedelta(minutes=5)
    await repo.create_application(
        owned(
            "clicked",
            people.ana,
            stage="submitting",
            lease_owner="dead-worker",
            lease_until=lease,
            submitting_at=T0 + timedelta(minutes=1),
            approved_at=T0,
            approved_digest="d" * 64,
        )
    )
    much_later = lease + timedelta(hours=6)
    taken = await repo.claim_next(
        now=much_later, lease_owner="w", lease_until=much_later + timedelta(minutes=10)
    )
    assert taken is None
    still = await repo.get_application("clicked", None)
    assert still is not None and still.stage == "submitting" and still.lease_owner == "dead-worker"
    assert still.submitting_at == T0 + timedelta(minutes=1)


async def test_list_expired_returns_stale_submitting_and_filling_rows(people: People) -> None:
    repo = people.repo
    now = T0 + timedelta(hours=1)
    await repo.create_application(
        owned("stale-send", people.ana, stage="submitting", minutes=2, lease_owner="w", lease_until=T0)
    )
    await repo.create_application(
        owned("stale-fill", people.ana, stage="filling", minutes=1, lease_owner="w", lease_until=T0)
    )
    await repo.create_application(owned("orphan-fill", people.ana, stage="filling", minutes=3))
    await repo.create_application(
        owned(
            "live-fill",
            people.ana,
            stage="filling",
            lease_owner="w",
            lease_until=now + timedelta(minutes=5),
        )
    )
    await repo.create_application(
        owned("queued", people.ana, stage="queued", lease_owner="w", lease_until=T0)
    )
    expired = await repo.list_expired(["submitting", "filling"], now=now)
    assert [a.id for a in expired] == [
        "stale-fill",
        "stale-send",
        "orphan-fill",
    ]  # least recently updated first
    assert [a.id for a in await repo.list_expired(["submitting"], now=now)] == ["stale-send"]
    assert len(await repo.list_expired(["submitting", "filling"], now=now, limit=1)) == 1
    assert await repo.list_expired([], now=now) == []


async def test_one_application_per_posting_per_person(people: People) -> None:
    repo = people.repo
    await repo.create_application(owned("first", people.ana, posting_key="greenhouse:acme:123"))
    with pytest.raises((IntegrityError, ValueError)):
        await repo.create_application(owned("again", people.ana, posting_key="greenhouse:acme:123"))
    assert await repo.get_application("again", None) is None
    # another person may apply to the same posting, and rows without a key may repeat
    await repo.create_application(owned("ben", people.ben, posting_key="greenhouse:acme:123"))
    await repo.create_application(owned("legacy-1", people.ana))
    await repo.create_application(owned("legacy-2", people.ana))
    found = await repo.find_by_posting(people.ana, "greenhouse:acme:123")
    assert found is not None and found.id == "first" and found.resume_choice == "tailored"
    assert (await repo.find_by_posting(people.ben, "greenhouse:acme:123")) is not None
    assert await repo.find_by_posting(people.ana, "lever:acme:999") is None
    # changing a row to a key the person already uses is refused too
    with pytest.raises((IntegrityError, ValueError)):
        await repo.update_application("legacy-1", {"posting_key": "greenhouse:acme:123"}, now=T0)
    moved = await repo.update_application("legacy-1", {"posting_key": "lever:acme:7"}, now=T0)
    assert moved is not None and moved.posting_key == "lever:acme:7"


async def test_the_new_columns_round_trip_through_create_and_update(people: People) -> None:
    repo = people.repo
    await repo.create_application(
        owned("ap1", people.ana, posting_key="lever:acme:1", listing_id="listing-9", credit_ref="apply:ap1")
    )
    got = await repo.get_application("ap1", account_owner_key(people.ana))
    assert got is not None and got.listing_id == "listing-9" and got.credit_ref == "apply:ap1"
    assert got.credit_state is None and got.has_confirmation is False and got.progress is None
    now = T0 + timedelta(minutes=2)
    progress = {"sections": [{"label": "You", "filled": 3, "total": 4}], "filled": 3, "total": 4}
    changed = await repo.update_application(
        "ap1",
        {
            "stage": "submitting",
            "submitting_at": now,
            "credit_state": "held",
            "stop_reason": "questions",
            "progress": progress,
            "resume_choice": "saved",
        },
        now=now,
        expected_stage="queued",
    )
    assert changed is not None and changed.submitting_at == now and changed.credit_state == "held"
    assert (
        changed.progress == progress
        and changed.stop_reason == "questions"
        and changed.resume_choice == "saved"
    )
    with pytest.raises(ValueError):
        await repo.update_application("ap1", {"user_id": people.ben}, now=now)
    with pytest.raises(ValueError):
        await repo.update_application("ap1", {"confirmation_screenshot": b"x"}, now=now)


async def test_list_waiting_is_the_persons_waiting_rows_oldest_first(people: People) -> None:
    repo = people.repo
    await repo.create_application(owned("w2", people.ana, stage="waiting_for_you", minutes=5))
    await repo.create_application(owned("w1", people.ana, stage="waiting_for_you", minutes=1))
    await repo.create_application(owned("q", people.ana, stage="queued", minutes=2))
    await repo.create_application(owned("signin", people.ana, stage="needs_signin", minutes=3))
    await repo.create_application(owned("bens", people.ben, stage="waiting_for_you", minutes=0))
    assert [a.id for a in await repo.list_waiting(people.ana)] == ["w1", "w2"]
    both = await repo.list_waiting(people.ana, ("waiting_for_you", "needs_signin"))
    assert [a.id for a in both] == ["w1", "signin", "w2"]
    assert await repo.list_waiting(people.ana, ()) == []


async def test_the_confirmation_screenshot_stays_outside_the_row(people: People) -> None:
    repo = people.repo
    mine = account_owner_key(people.ana)
    await repo.create_application(owned("ap1", people.ana, stage="sent"))
    await repo.put_screenshot("ap1", b"review-jpeg")
    await repo.put_confirmation("ap1", b"confirmation-jpeg")
    assert await repo.get_confirmation("ap1", mine) == b"confirmation-jpeg"
    assert await repo.get_confirmation("ap1", account_owner_key(people.ben)) is None
    assert await repo.get_confirmation("ap1", None) == b"confirmation-jpeg"
    assert await repo.get_screenshot("ap1", mine) == b"review-jpeg"  # the review screenshot is kept apart
    listed = (await repo.list_applications(mine))[0]
    assert listed.has_confirmation is True and listed.has_screenshot is True
    assert not any(isinstance(v, bytes) for v in vars_of(listed))
    await repo.put_confirmation("ap1", None)
    after = await repo.get_application("ap1", mine)
    assert after is not None and after.has_confirmation is False and after.has_screenshot is True
    await repo.put_confirmation("nope", b"x")  # an unknown id is a no-op
    assert await repo.get_confirmation("nope", None) is None


def vars_of(item: StoredApplication) -> list[object]:
    return [getattr(item, name) for name in item.__dataclass_fields__]


async def test_purge_filled_clears_values_but_keeps_stage_digest_and_events(people: People) -> None:
    repo = people.repo
    old = -60 * 24 * 40  # forty days before T0, in minutes
    typed_job = {"description": "Build things", "typed": {"phone": "+91 98765 43210"}}
    review = {"digest": "d" * 64, "fields": [{"key": "phone", "value": "+91 98765 43210"}]}
    for app_id, stage in [("sent", "sent"), ("cancelled", "cancelled"), ("unclear", "check_this_one")]:
        await repo.create_application(
            owned(
                app_id,
                people.ana,
                stage=stage,
                minutes=old,
                job=typed_job,
                review=review,
                review_digest="d" * 64,
                approved_digest="d" * 64,
                submitted_at=T0 + timedelta(minutes=old),
                needs=[{"key": "q1", "questionKey": "noticePeriod"}],
            )
        )
        await repo.put_screenshot(app_id, b"review")
        await repo.put_confirmation(app_id, b"confirmation")
    await repo.create_application(owned("recent", people.ana, stage="sent", review=review, job=typed_job))
    await repo.add_event(
        StoredApplicationEvent(
            id="ev1", application_id="sent", at=T0, kind="stage", source="engine", to_stage="sent"
        )
    )

    assert await repo.purge_filled(T0 - timedelta(days=30)) == 2
    sent = await repo.get_application("sent", None)
    assert sent is not None
    assert sent.stage == "sent" and sent.review_digest == "d" * 64 and sent.approved_digest == "d" * 64
    assert sent.submitted_at == T0 + timedelta(minutes=old) and sent.updated_at == T0 + timedelta(minutes=old)
    assert sent.review is None and sent.needs is None and sent.job == {"description": "Build things"}
    assert sent.has_screenshot is False and sent.has_confirmation is False
    assert (
        await repo.get_screenshot("sent", None) is None and await repo.get_confirmation("sent", None) is None
    )
    assert [e.id for e in await repo.list_events("sent")] == ["ev1"]
    unclear = await repo.get_application("unclear", None)  # the person still has to resolve it
    assert unclear is not None and unclear.review == review and unclear.has_confirmation is True
    recent = await repo.get_application("recent", None)
    assert recent is not None and recent.review == review and recent.job == typed_job
    assert await repo.purge_filled(T0 - timedelta(days=30)) == 0  # nothing left to clear


async def test_the_answer_bank_upserts_confirms_and_deletes(people: People) -> None:
    repo = people.repo
    assert await repo.list_answers(people.ana) == []
    first = await repo.put_answer(
        answer(people.ana, "noticePeriod", answered_on="Acme", application_id="ap1")
    )
    assert first.value == "30 days" and first.answered_on == "Acme" and first.confirmed_at is None
    later = T0 + timedelta(days=3)
    replaced = await repo.put_answer(
        answer(
            people.ana,
            "noticePeriod",
            "60 days",
            source="you",
            answered_at=later,
            created_at=later,
            updated_at=later,
        )
    )
    assert replaced.value == "60 days" and replaced.source == "you" and replaced.answered_at == later
    assert replaced.created_at == T0  # the first save's time is kept
    await repo.put_answer(
        answer(people.ana, "workAuthorization:IN", "Yes", question="Authorised to work in India?")
    )
    await repo.put_answer(
        answer(people.ana, "bond", None, ask_each_time=True, question="Will you sign a bond?")
    )
    await repo.put_answer(answer(people.ben, "noticePeriod", "Immediate"))
    assert [a.question_key for a in await repo.list_answers(people.ana)] == [
        "bond",
        "noticePeriod",
        "workAuthorization:IN",
    ]
    some = await repo.get_answers(people.ana, ["noticePeriod", "bond", "workAuthorization:US"])
    assert set(some) == {"noticePeriod", "bond"}
    assert some["bond"].value is None and some["bond"].ask_each_time is True
    assert await repo.get_answers(people.ana, []) == {}

    checked = await repo.confirm_answer(people.ana, "noticePeriod", now=T0 + timedelta(days=95))
    assert (
        checked is not None and checked.confirmed_at == T0 + timedelta(days=95) and checked.value == "60 days"
    )
    assert await repo.confirm_answer(people.ana, "nope", now=T0) is None

    assert await repo.delete_answer(people.ana, "noticePeriod") is True
    assert await repo.delete_answer(people.ana, "noticePeriod") is False
    assert "noticePeriod" not in await repo.get_answers(people.ana, ["noticePeriod"])
    bens = await repo.get_answers(people.ben, ["noticePeriod"])
    assert bens["noticePeriod"].value == "Immediate"  # one person's delete never touches another's


@pytest.mark.parametrize("key", ["pan", "aadhaar", "bankAccount", "religion", "fatherName", "pan:IN"])
async def test_put_answer_refuses_a_never_stored_key(people: People, key: str) -> None:
    with pytest.raises(ValueError):
        await people.repo.put_answer(answer(people.ana, key, "ABCDE1234F"))
    assert await people.repo.list_answers(people.ana) == []


def test_the_never_stored_keys_mirror_the_contract() -> None:
    from tayyari.contract import apply as contract

    keys = getattr(contract, "NEVER_STORED_KEYS", None)
    if keys is None:
        pytest.skip("contract.apply.NEVER_STORED_KEYS is not defined yet (package P1)")
    assert frozenset(keys) == NEVER_STORED_ANSWER_KEYS


async def test_the_profile_keeps_the_new_settings(people: People) -> None:
    repo = people.repo
    mine = account_owner_key(people.ana)
    on = await repo.set_self_id(people.ana, True, now=T0)
    assert on.owner_key == mine and on.user_id == people.ana and on.self_id_on_forms is True
    assert on.first_check_at is None and on.consent_at is None
    checked = await repo.mark_first_check(people.ana, now=T0 + timedelta(minutes=1))
    agreed = await repo.set_consent(people.ana, now=T0 + timedelta(minutes=2))
    assert checked.first_check_at == T0 + timedelta(minutes=1) and checked.self_id_on_forms is True
    assert agreed.consent_at == T0 + timedelta(minutes=2) and agreed.updated_at == T0 + timedelta(minutes=2)
    with_cv = await repo.put_resume(
        mine,
        user_id=people.ana,
        name="cv.pdf",
        media_type="application/pdf",
        data=b"%PDF",
        now=T0,
        sha256="a" * 64,
    )
    assert with_cv.resume_sha256 == "a" * 64 and with_cv.consent_at == T0 + timedelta(minutes=2)
    off = await repo.set_self_id(people.ana, False, now=T0 + timedelta(minutes=3))
    assert off.self_id_on_forms is False and off.resume_sha256 == "a" * 64
    gone = await repo.put_resume(mine, user_id=people.ana, name=None, media_type=None, data=None, now=T0)
    assert gone.resume_sha256 is None and gone.resume_bytes is None
    fresh = await repo.set_consent(people.ben, now=T0)
    assert fresh.self_id_on_forms is False and fresh.details == {} and fresh.answers == {}
