"""The learning loop's numbers: per ATS, how many runs replayed a playbook with no model versus used one (the field-list
loop, or the legacy AI fallback), how many succeeded, needed the person, failed or were stopped at the door, what they cost
(model calls and Browser Use sessions) and why they did not succeed. Computed in code from ``run_metrics`` rows.

How the A1 outcomes fold into `contract.apply.MetricsRow`: ``needs_signin`` (a login wall or a CAPTCHA) counts as
``blocked``; ``check_this_one`` and ``closed`` are neither successes nor failures. Every outcome other than a success shows
in ``failureReasons``: ``failed`` by its reason, the others as ``<outcome>:<reason>`` (``check_this_one:no_confirmation``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Final

from tayyari.contract.apply import ApplyMetrics, FailureCount, MetricsRow, PlaybookSummary
from tayyari.storage.ports import ApplyRepo, StoredRunMetric

MODEL_MODES: Final = frozenset({"loop", "ai"})  # legacy: "ai" removed in Z
"""Run modes that used a model to fill the form (counted in ``aiRuns`` and ``aiShare``)."""
BLOCKED_OUTCOMES: Final = frozenset({"blocked", "needs_signin"})  # legacy: "blocked" removed in Z
"""Outcomes where the site stopped the run before the form (a login wall, a CAPTCHA)."""


def failure_key(run: StoredRunMetric) -> str | None:
    """How a run that did not succeed shows in ``failureReasons``; None for a success or a run with no reason."""
    if run.outcome == "success" or not run.failure_reason:
        return None
    if run.outcome in ("failed", "needs_user", "blocked"):
        return run.failure_reason
    return f"{run.outcome}:{run.failure_reason}"


def _row(ats: str, runs: list[StoredRunMetric]) -> MetricsRow:
    n = len(runs)
    ai = sum(1 for r in runs if r.mode in MODEL_MODES)
    ok = sum(1 for r in runs if r.outcome == "success")
    return MetricsRow(
        ats=ats, runs=n, playbookRuns=sum(1 for r in runs if r.mode == "playbook"), aiRuns=ai, successes=ok,
        needsUser=sum(1 for r in runs if r.outcome == "needs_user"), failures=sum(1 for r in runs if r.outcome == "failed"),
        blocked=sum(1 for r in runs if r.outcome in BLOCKED_OUTCOMES), aiShare=round(ai / n, 4) if n else 0.0,
        successRate=round(ok / n, 4) if n else 0.0, avgSteps=round(sum(r.steps for r in runs) / n, 2) if n else 0.0,
        costMicroUsd=sum(r.cost_micro_usd for r in runs),
    )  # fmt: skip


async def compute_metrics(repo: ApplyRepo, now: datetime, days: int = 30) -> ApplyMetrics:
    since = now - timedelta(days=days)
    runs = await repo.list_run_metrics(since)
    by_ats: dict[str, list[StoredRunMetric]] = defaultdict(list)
    for r in runs:
        by_ats[r.ats].append(r)
    reasons = Counter(key for r in runs if (key := failure_key(r)) is not None)
    playbooks = [
        PlaybookSummary(id=p.id, ats=p.ats, status=p.status, origin=p.origin, version=p.version,  # type: ignore[arg-type]
                        successes=p.successes, failures=p.failures, updatedAt=p.updated_at.isoformat(), domain=p.domain)
        for p in await repo.list_playbooks()
    ]  # fmt: skip
    return ApplyMetrics(
        since=since.isoformat(), total=_row("all", runs), byAts=[_row(a, rs) for a, rs in sorted(by_ats.items())],
        failureReasons=[FailureCount(reason=k, count=v) for k, v in reasons.most_common()], playbooks=playbooks,
    )  # fmt: skip


__all__ = ["BLOCKED_OUTCOMES", "MODEL_MODES", "compute_metrics", "failure_key"]
