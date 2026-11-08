"""The learning loop's numbers: playbook vs field-list loop runs per ATS, success rate, each A1 outcome on its own count
and the failure reasons, computed from run rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tayyari.apply.metrics import compute_metrics
from tayyari.contract.apply import MetricsRow
from tayyari.storage.ports import StoredRunMetric
from tests.fakes.apply import FakeApplyRepo

T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def run(
    n: int, ats: str, mode: str, outcome: str, reason: str | None = None, cost: int = 0
) -> StoredRunMetric:
    return StoredRunMetric(id=f"r{n}", created_at=T0 - timedelta(hours=n), run_kind="fill", ats=ats, domain=f"{ats}.example",
                           mode=mode, outcome=outcome, failure_reason=reason, steps=4, cost_micro_usd=cost)  # fmt: skip


async def test_loop_share_and_success_rate_per_ats() -> None:
    repo = FakeApplyRepo()
    for item in [run(1, "lever", "playbook", "success"), run(2, "lever", "playbook", "success"),
                 run(3, "unknown", "loop", "success", cost=40000), run(4, "unknown", "loop", "failed", "no_form", 10000),
                 run(5, "unknown", "playbook", "needs_signin", "captcha"), run(24 * 40, "lever", "loop", "success")]:  # fmt: skip
        await repo.add_run_metric(item)
    m = await compute_metrics(repo, T0, days=30)
    rows = {r.ats: r for r in m.byAts}
    assert (rows["lever"].runs, rows["lever"].loopShare, rows["lever"].successRate) == (
        2,
        0.0,
        1.0,
    )  # 40 days: out
    assert (rows["unknown"].loopRuns, rows["unknown"].playbookRuns, rows["unknown"].costMicroUsd) == (
        2,
        1,
        50000,
    )
    assert rows["unknown"].loopShare == round(2 / 3, 4) and rows["unknown"].needsSignin == 1
    assert m.total.runs == 5 and {f.reason: f.count for f in m.failureReasons} == {
        "no_form": 1,
        "needs_signin:captcha": 1,
    }


async def test_every_a1_outcome_has_its_own_count() -> None:
    repo = FakeApplyRepo()
    for item in [
        run(1, "greenhouse", "loop", "success", cost=25000),
        run(2, "greenhouse", "playbook", "needs_signin", "login_wall"),
        run(3, "greenhouse", "playbook", "check_this_one", "no_confirmation", cost=9000),
        run(4, "greenhouse", "playbook", "closed", "gone"),
        run(5, "greenhouse", "loop", "questions", "needs_answers"),
        run(6, "greenhouse", "playbook", "failed", "guard_probe", cost=1000),
        run(7, "greenhouse", "playbook", "interrupted", "TimeoutError", cost=3000),
        run(8, "greenhouse", "playbook", "deferred", "browser_use_busy"),
    ]:
        await repo.add_run_metric(item)
    m = await compute_metrics(repo, T0, days=30)
    (gh,) = m.byAts
    assert (gh.runs, gh.loopRuns, gh.playbookRuns, gh.loopShare) == (8, 2, 6, round(2 / 8, 4))
    assert (gh.successes, gh.questions, gh.needsSignin, gh.closed, gh.failures) == (1, 1, 1, 1, 1)
    assert (gh.checkThisOne, gh.interrupted, gh.deferred) == (1, 1, 1)
    assert gh.costMicroUsd == 38000  # model calls and Browser Use sessions, as the engine recorded them
    assert {f.reason: f.count for f in m.failureReasons} == {
        "needs_signin:login_wall": 1,
        "check_this_one:no_confirmation": 1,
        "closed:gone": 1,
        "needs_answers": 1,
        "guard_probe": 1,
        "interrupted:TimeoutError": 1,
        "deferred:browser_use_busy": 1,
    }


def test_the_row_has_no_legacy_ai_counts() -> None:
    assert not {"aiRuns", "aiShare", "needsUser", "blocked"} & set(MetricsRow.model_fields)
