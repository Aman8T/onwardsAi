"""The learning loop's numbers: playbook vs AI per ATS, success rate and failure reasons, computed from run rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tayyari.apply.metrics import compute_metrics
from tayyari.storage.ports import StoredRunMetric
from tests.fakes.apply import FakeApplyRepo

T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def run(
    n: int, ats: str, mode: str, outcome: str, reason: str | None = None, cost: int = 0
) -> StoredRunMetric:
    return StoredRunMetric(id=f"r{n}", created_at=T0 - timedelta(hours=n), run_kind="fill", ats=ats, domain=f"{ats}.example",
                           mode=mode, outcome=outcome, failure_reason=reason, steps=4, cost_micro_usd=cost)  # fmt: skip


async def test_ai_share_and_success_rate_per_ats() -> None:
    repo = FakeApplyRepo()
    for item in [run(1, "lever", "playbook", "success"), run(2, "lever", "playbook", "success"),
                 run(3, "unknown", "ai", "success", cost=40000), run(4, "unknown", "ai", "failed", "agent_gave_up", 10000),
                 run(5, "unknown", "playbook", "blocked", "captcha"), run(24 * 40, "lever", "ai", "success")]:  # fmt: skip
        await repo.add_run_metric(item)
    m = await compute_metrics(repo, T0, days=30)
    rows = {r.ats: r for r in m.byAts}
    assert (rows["lever"].runs, rows["lever"].aiShare, rows["lever"].successRate) == (
        2,
        0.0,
        1.0,
    )  # the 40-day-old run is out
    assert (rows["unknown"].aiRuns, rows["unknown"].playbookRuns, rows["unknown"].costMicroUsd) == (
        2,
        1,
        50000,
    )
    assert rows["unknown"].aiShare == round(2 / 3, 4) and rows["unknown"].blocked == 1
    assert m.total.runs == 5 and {f.reason: f.count for f in m.failureReasons} == {
        "agent_gave_up": 1,
        "captcha": 1,
    }
