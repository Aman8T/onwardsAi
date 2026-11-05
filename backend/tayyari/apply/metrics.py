"""The learning loop's numbers: per ATS, how many runs a playbook did versus the AI fallback, how many succeeded, needed the
person, failed or were blocked, what they cost, and why they failed. Computed in code from ``run_metrics`` rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from tayyari.contract.apply import ApplyMetrics, FailureCount, MetricsRow, PlaybookSummary
from tayyari.storage.ports import ApplyRepo, StoredRunMetric


def _row(ats: str, runs: list[StoredRunMetric]) -> MetricsRow:
    n = len(runs)
    ai = sum(1 for r in runs if r.mode == "ai")
    ok = sum(1 for r in runs if r.outcome == "success")
    return MetricsRow(
        ats=ats, runs=n, playbookRuns=sum(1 for r in runs if r.mode == "playbook"), aiRuns=ai, successes=ok,
        needsUser=sum(1 for r in runs if r.outcome == "needs_user"), failures=sum(1 for r in runs if r.outcome == "failed"),
        blocked=sum(1 for r in runs if r.outcome == "blocked"), aiShare=round(ai / n, 4) if n else 0.0,
        successRate=round(ok / n, 4) if n else 0.0, avgSteps=round(sum(r.steps for r in runs) / n, 2) if n else 0.0,
        costMicroUsd=sum(r.cost_micro_usd for r in runs),
    )  # fmt: skip


async def compute_metrics(repo: ApplyRepo, now: datetime, days: int = 30) -> ApplyMetrics:
    since = now - timedelta(days=days)
    runs = await repo.list_run_metrics(since)
    by_ats: dict[str, list[StoredRunMetric]] = defaultdict(list)
    for r in runs:
        by_ats[r.ats].append(r)
    reasons = Counter(r.failure_reason for r in runs if r.failure_reason)
    playbooks = [
        PlaybookSummary(id=p.id, ats=p.ats, status=p.status, origin=p.origin, version=p.version,  # type: ignore[arg-type]
                        successes=p.successes, failures=p.failures, updatedAt=p.updated_at.isoformat(), domain=p.domain)
        for p in await repo.list_playbooks()
    ]  # fmt: skip
    return ApplyMetrics(
        since=since.isoformat(), total=_row("all", runs), byAts=[_row(a, rs) for a, rs in sorted(by_ats.items())],
        failureReasons=[FailureCount(reason=k, count=v) for k, v in reasons.most_common()], playbooks=playbooks,
    )  # fmt: skip


__all__ = ["compute_metrics"]
