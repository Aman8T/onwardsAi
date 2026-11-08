"""The learning loop's numbers: per ATS, how many runs replayed a playbook with no model versus used the field-list loop
(a model chose which saved value goes where), how each run ended (`contract.apply.RunOutcome`), what the runs cost
(model calls and Browser Use sessions) and why they did not succeed. Computed in code from ``run_metrics`` rows.

Each A1 outcome has its own count on `contract.apply.MetricsRow`: ``success``, ``questions`` (a review that waits for the
person's answers), ``needs_signin`` (a login wall or a CAPTCHA), ``closed``, ``failed`` (before any click),
``check_this_one`` (no clear outcome after the click), ``interrupted`` (a fill that stopped half way) and ``deferred``
(put back for capacity). Every outcome other than a success shows in ``failureReasons``: ``failed`` and ``questions`` by
their reason, the others as ``<outcome>:<reason>`` (``check_this_one:no_confirmation``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Final

from tayyari.contract.apply import ApplyMetrics, FailureCount, MetricsRow, PlaybookSummary
from tayyari.storage.ports import ApplyRepo, StoredRunMetric

MODEL_MODES: Final = frozenset({"loop"})
"""Run modes that used a model to fill the form (counted in ``loopRuns`` and ``loopShare``)."""
BARE_REASON_OUTCOMES: Final = frozenset({"failed", "questions"})
"""Outcomes whose failure reason shows on its own in ``failureReasons`` (the others are prefixed with the outcome)."""


def failure_key(run: StoredRunMetric) -> str | None:
    """How a run that did not succeed shows in ``failureReasons``; None for a success or a run with no reason."""
    if run.outcome == "success" or not run.failure_reason:
        return None
    if run.outcome in BARE_REASON_OUTCOMES:
        return run.failure_reason
    return f"{run.outcome}:{run.failure_reason}"


def _count(runs: list[StoredRunMetric], outcome: str) -> int:
    return sum(1 for r in runs if r.outcome == outcome)


def _row(ats: str, runs: list[StoredRunMetric]) -> MetricsRow:
    n = len(runs)
    loop = sum(1 for r in runs if r.mode in MODEL_MODES)
    ok = _count(runs, "success")
    return MetricsRow(
        ats=ats, runs=n, playbookRuns=sum(1 for r in runs if r.mode == "playbook"), loopRuns=loop, successes=ok,
        questions=_count(runs, "questions"), needsSignin=_count(runs, "needs_signin"), closed=_count(runs, "closed"),
        failures=_count(runs, "failed"), checkThisOne=_count(runs, "check_this_one"),
        interrupted=_count(runs, "interrupted"), deferred=_count(runs, "deferred"),
        loopShare=round(loop / n, 4) if n else 0.0, successRate=round(ok / n, 4) if n else 0.0,
        avgSteps=round(sum(r.steps for r in runs) / n, 2) if n else 0.0, costMicroUsd=sum(r.cost_micro_usd for r in runs),
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


__all__ = ["BARE_REASON_OUTCOMES", "MODEL_MODES", "compute_metrics", "failure_key"]
