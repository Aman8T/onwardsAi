"""Auto-apply storage, run against every backend (the fake, SQLite, and Postgres when configured), so `FakeApplyRepo` cannot
drift from `SqlApplyRepo`: owner scoping, the stage-guarded update, the worker's lease, screenshots kept apart from the row,
history order, playbook counters and status, run metrics and the saved profile."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from tayyari.storage.ports import (
    ApplyRepo,
    StoredApplication,
    StoredApplicationEvent,
    StoredPlaybook,
    StoredRunMetric,
)
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
