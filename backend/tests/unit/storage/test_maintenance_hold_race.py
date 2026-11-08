"""The maintenance hold sweep against a row reopened while it runs: the claim is a compare-and-set, so a failed row that
is queued again (with the hold its new run counts on) between the sweep's read and its ledger call keeps that hold, and
a reopen that lands during the ledger call gets the hold taken again."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tayyari.core.settings import Settings
from tayyari.storage import maintenance
from tayyari.storage.db import Database
from tayyari.storage.ports import StoredApplication, account_owner_key
from tayyari.storage.repos import Repositories, build_repositories

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
DAY = timedelta(days=1)


async def seed(repos: Repositories, user_id: str, app_id: str, stage: str) -> None:
    ref = f"apply:{app_id}"
    await repos.billing.hold(user_id, ref, now=NOW - 40 * DAY, free_allowance=0, cost=1)
    await repos.apply.create_application(
        StoredApplication(
            id=app_id, owner_key=account_owner_key(user_id), user_id=user_id, source="match",
            job_url=f"https://jobs.lever.co/acme/{app_id}", domain="jobs.lever.co", ats="lever", stage=stage,
            created_at=NOW - 40 * DAY, updated_at=NOW - 40 * DAY, credit_ref=ref, credit_state="held",
        )
    )  # fmt: skip


async def reopen(repos: Repositories, app_id: str) -> None:
    """What a fresh start on a failed row does: back to queued, its hold kept as ``held``."""
    await repos.apply.update_application(
        app_id, {"stage": "queued", "credit_state": "held"}, now=NOW, expected_stage="failed"
    )


async def test_a_row_requeued_between_the_select_and_the_release_keeps_its_hold(
    any_real_db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    repos = build_repositories(any_real_db)
    ana = (await repos.users.sign_in_email(email="ana@example.com", now=NOW - 50 * DAY)).id
    await repos.billing.grant(ana, credits=2, pass_days=0, ref="o1", now=NOW - 50 * DAY)
    await seed(repos, ana, "ap1", "failed")
    await seed(repos, ana, "ap2", "failed")  # an untouched neighbour is still released

    real_select = maintenance._open_holds

    async def select_then_reopen(db: Database) -> Sequence[Any]:
        rows = await real_select(db)
        await reopen(repos, "ap1")  # the person starts it again right after the sweep read it
        return rows

    monkeypatch.setattr(maintenance, "_open_holds", select_then_reopen)
    assert await maintenance._reconcile_holds(any_real_db, repos, NOW, settings.billing) == 1

    reopened = await repos.apply.get_application("ap1", None)
    assert reopened is not None and (reopened.stage, reopened.credit_state) == ("queued", "held")
    assert await repos.billing.hold_state(ana, "apply:ap1") == "held"  # never released under the new run
    other = await repos.apply.get_application("ap2", None)
    assert other is not None and other.credit_state == "released"
    assert await repos.billing.hold_state(ana, "apply:ap2") == "released"


async def test_a_reopen_during_the_ledger_call_gets_its_hold_taken_again(
    any_real_db: Database, settings: Settings
) -> None:
    repos = build_repositories(any_real_db)
    ana = (await repos.users.sign_in_email(email="ana@example.com", now=NOW - 50 * DAY)).id
    await repos.billing.grant(ana, credits=2, pass_days=0, ref="o1", now=NOW - 50 * DAY)
    await seed(repos, ana, "ap1", "failed")
    billing = repos.billing

    class ReopenOnRelease:
        """The ledger, with the person's fresh start landing while the release runs."""

        def __getattr__(self, name: str) -> Any:
            return getattr(billing, name)

        async def release(self, user_id: str, ref: str, *, now: datetime) -> bool:
            app = await repos.apply.get_application("ap1", None)
            assert app is not None and app.credit_state == "releasing"  # claimed before the ledger was called
            await repos.apply.update_application(
                "ap1", {"stage": "queued", "credit_state": "held"}, now=now, expected_stage="failed"
            )
            return await billing.release(user_id, ref, now=now)

    racing = replace(repos, billing=ReopenOnRelease())
    assert await maintenance._reconcile_holds(any_real_db, racing, NOW, settings.billing) == 0
    app = await repos.apply.get_application("ap1", None)
    assert app is not None and (app.stage, app.credit_state) == ("queued", "held")
    assert await repos.billing.hold_state(ana, "apply:ap1") == "held"  # taken again for the new run


async def test_a_claim_left_by_a_crashed_run_is_finished_by_the_next(
    any_real_db: Database, settings: Settings
) -> None:
    repos = build_repositories(any_real_db)
    ana = (await repos.users.sign_in_email(email="ana@example.com", now=NOW - 50 * DAY)).id
    await repos.billing.grant(ana, credits=1, pass_days=0, ref="o1", now=NOW - 50 * DAY)
    await seed(repos, ana, "ap1", "cancelled")
    await repos.apply.update_application("ap1", {"credit_state": "releasing"}, now=NOW)
    assert await maintenance._reconcile_holds(any_real_db, repos, NOW, settings.billing) == 1
    app = await repos.apply.get_application("ap1", None)
    assert app is not None and app.credit_state == "released"
    assert await repos.billing.hold_state(ana, "apply:ap1") == "released"


async def test_a_retry_that_writes_no_credit_state_during_the_ledger_call_still_gets_its_hold(
    any_real_db: Database, settings: Settings
) -> None:
    """`ApplyService.retry` read the row as ``held`` before the claim, so it moves it to queued without touching
    ``credit_state``: the row still says ``releasing`` when the write-back finds it moved."""
    repos = build_repositories(any_real_db)
    ana = (await repos.users.sign_in_email(email="ana@example.com", now=NOW - 50 * DAY)).id
    await repos.billing.grant(ana, credits=2, pass_days=0, ref="o1", now=NOW - 50 * DAY)
    await seed(repos, ana, "ap1", "failed")
    billing = repos.billing

    class RetryOnRelease:
        def __getattr__(self, name: str) -> Any:
            return getattr(billing, name)

        async def release(self, user_id: str, ref: str, *, now: datetime) -> bool:
            await repos.apply.update_application("ap1", {"stage": "queued"}, now=now, expected_stage="failed")
            return await billing.release(user_id, ref, now=now)

    racing = replace(repos, billing=RetryOnRelease())
    await maintenance._reconcile_holds(any_real_db, racing, NOW, settings.billing)
    app = await repos.apply.get_application("ap1", None)
    assert app is not None and (app.stage, app.credit_state) == ("queued", "held")
    assert await repos.billing.hold_state(ana, "apply:ap1") == "held"


async def test_a_ledger_call_that_fails_puts_the_claim_back(
    any_real_db: Database, settings: Settings
) -> None:
    repos = build_repositories(any_real_db)
    ana = (await repos.users.sign_in_email(email="ana@example.com", now=NOW - 50 * DAY)).id
    await repos.billing.grant(ana, credits=1, pass_days=0, ref="o1", now=NOW - 50 * DAY)
    await seed(repos, ana, "ap1", "cancelled")
    billing = repos.billing

    class Down:
        def __getattr__(self, name: str) -> Any:
            return getattr(billing, name)

        async def release(self, user_id: str, ref: str, *, now: datetime) -> bool:
            raise ConnectionError("ledger down")

    assert (
        await maintenance._reconcile_holds(any_real_db, replace(repos, billing=Down()), NOW, settings.billing)
        == 0
    )
    app = await repos.apply.get_application("ap1", None)
    assert app is not None and app.credit_state == "held"  # never left as an out-of-contract claim
    assert await maintenance._reconcile_holds(any_real_db, repos, NOW, settings.billing) == 1
