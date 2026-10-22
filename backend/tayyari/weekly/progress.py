"""The reader's progress, worked out in code from what they already have: their newest Prepare plan, the roles of their graph and
their latest practice interview. No model, no score; every number is a count of ticks, weeks or days.

    plan        the newest plan of the reader's newest profile: its role, the week they are in (counted from the day the plan
                was written, at most the plan's last week), and how many of this week's steps they have ticked. A step belongs
                to a week the way the Prepare screen puts it (`src/prepare/plan.ts`, `phaseSpans`): a phase starts in the week
                its first hour falls in and ends in the week its last hour falls in, at the hours a week the person chose.
    closest     the role of the graph with the shortest ETA, from the ETA text the graph states ("~2 months", "6-9 months");
                the text is shown as it is, and the lower end of a range is the one compared. A role whose ETA reads as no
                time at all (or cannot be read) is not compared.
    practice    the date of the newest practice interview within 30 days.
    re-drill    (I2) the claims whose Fix step was ticked in the last 7 days and that no session has asked since: ready to
                re-drill. A count of claims, from the claim marks.
    nudge       one suggestion, chosen by the first rule that applies (see `choose_nudge`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from tayyari.contract.models import Role
from tayyari.contract.weekly import WeeklyProgress
from tayyari.storage.ports import StoredClaimMark, StoredInterview, StoredPlan

from .times import iso_date

PRACTICE_WINDOW: Final = timedelta(days=30)
TICK_WINDOW: Final = timedelta(days=7)
"""A Fix step ticked within this long counts for "ready to re-drill"."""

_NUMBER_WORDS: Final = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip
_UNIT_DAYS: Final = {
    "day": 1.0,
    "week": 7.0,
    "wk": 7.0,
    "month": 30.0,
    "mo": 30.0,
    "year": 365.0,
    "yr": 365.0,
}
_ETA = re.compile(
    r"(?P<low>\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"(?:\s*(?:-|–|—|to)\s*(?P<high>\d+(?:\.\d+)?))?"
    r"\s*\+?\s*(?P<unit>days?|weeks?|wks?|months?|mos?|years?|yrs?)\b",
    re.IGNORECASE,
)


def eta_days(text: str) -> float | None:
    """The lower end of an ETA text in days ("~2 months" is 60, "6-9 months" is 180), or None when it cannot be read."""
    found = _ETA.search(text or "")
    if found is None:
        return None
    raw = found.group("low").lower()
    number = _NUMBER_WORDS[raw] if raw in _NUMBER_WORDS else float(raw)
    unit = found.group("unit").lower().rstrip("s")
    days = number * _UNIT_DAYS[unit]
    return days if days > 0 else None


def closest_role(roles: Sequence[Role]) -> Role | None:
    """The role with the shortest readable ETA (the graph's order breaks a tie), or None."""
    best: tuple[float, Role] | None = None
    for role in roles:
        days = eta_days(role.eta)
        if days is not None and (best is None or days < best[0]):
            best = (days, role)
    return None if best is None else best[1]


def _phase_weeks(phases: Sequence[Any], hours_per_week: int) -> list[tuple[int, int]]:
    """``(first week, last week)`` of each phase, as the Prepare screen counts them."""
    spans: list[tuple[int, int]] = []
    spent = 0
    for phase in phases:
        hours = max(0, int(phase.get("hours", 0))) if isinstance(phase, dict) else 0
        start = spent // hours_per_week + 1
        spent += hours
        spans.append((start, max(start, math.ceil(spent / hours_per_week))))
    return spans


def plan_progress(plan: StoredPlan, now: datetime) -> WeeklyProgress | None:
    """The plan fields of the progress block, or None when the stored plan has no move to follow."""
    moves = plan.content.get("moves")
    if not isinstance(moves, list) or not moves:
        return None
    state = plan.state
    index = state.get("moveIndex")
    move = moves[index] if isinstance(index, int) and 0 <= index < len(moves) else moves[0]
    if not isinstance(move, dict) or not isinstance(move.get("phases"), list):
        return None
    phases: list[Any] = move["phases"]
    hours_raw = state.get("hoursPerWeek")
    hours_per_week = hours_raw if isinstance(hours_raw, int) and hours_raw >= 1 else 6
    total = sum(max(0, int(p.get("hours", 0))) for p in phases if isinstance(p, dict))
    weeks = max(1, math.ceil(total / hours_per_week))
    started = max(0, (now - plan.created_at).days)
    week = min(weeks, started // 7 + 1)
    done = {key for key in state.get("done", []) if isinstance(key, str)}
    planned = ticked = 0
    move_id = str(move.get("id", ""))
    for i, ((first, last), phase) in enumerate(
        zip(_phase_weeks(phases, hours_per_week), phases, strict=True)
    ):
        tasks = phase.get("tasks") if isinstance(phase, dict) else None
        if not isinstance(tasks, list) or not first <= week <= last:
            continue
        planned += len(tasks)
        ticked += sum(1 for j in range(len(tasks)) if f"{move_id}:{i}:{j}" in done)
    role = plan.content.get("roleTitle")
    return WeeklyProgress(
        planRole=role if isinstance(role, str) and role else None,
        planWeek=week,
        planWeeks=weeks,
        ticksThisWeek=ticked,
        ticksPlanned=planned,
    )


def last_practice(sessions: Sequence[StoredInterview], now: datetime) -> str | None:
    """The date of the newest practice interview within the last 30 days, or None."""
    recent = [s.created_at for s in sessions if now - s.created_at <= PRACTICE_WINDOW]
    return iso_date(max(recent)) if recent else None


def ready_to_redrill(marks: Sequence[StoredClaimMark], now: datetime) -> int:
    """How many claims had their Fix step ticked in the last 7 days and have not been asked since (no later mark)."""
    latest: dict[str, datetime] = {}
    for m in marks:
        latest[m.claim_key] = max(latest.get(m.claim_key, m.created_at), m.created_at)
    ready = {
        m.claim_key
        for m in marks
        if m.fix_done_at is not None
        and (m.fix_plan_id is not None or m.fix_kept)
        and now - m.fix_done_at <= TICK_WINDOW
        and latest[m.claim_key] <= m.fix_done_at
    }
    return len(ready)


def _steps(count: int) -> str:
    return "1 more step" if count == 1 else f"{count} more steps"


def choose_nudge(progress: WeeklyProgress, *, ready: int = 0) -> str | None:
    """One suggestion for the week from the first rule that applies:

    0. Fix steps ticked last week whose claims have not been asked since: re-drill them (I2);
    1. a plan with steps left this week: tick them;
    2. a plan whose steps this week are all ticked: practise that role out loud;
    3. no practice interview in 30 days: one short round;
    4. no plan but a closest role: open Prepare for it.
    """
    planned, ticked = progress.ticksPlanned or 0, progress.ticksThisWeek or 0
    if ready == 1:
        return "You ticked a Fix step last week. That claim is ready to re-drill."
    if ready > 1:
        return f"You ticked {ready} Fix steps last week. Those {ready} claims are ready to re-drill."
    if progress.planRole and planned > ticked:
        return f"Tick off {_steps(planned - ticked)} of your {progress.planRole} plan this week."
    if progress.planRole and planned > 0:
        return f"This week's steps of your {progress.planRole} plan are done. Run a practice interview for that role."
    if progress.lastPractice is None:
        return "You have not practised an interview in the last 30 days. One short round keeps your answers ready."
    if progress.closestRole and not progress.planRole:
        return f"Open Prepare for {progress.closestRole} and pick a first step."
    return None


def build_progress(
    *,
    plan: StoredPlan | None,
    roles: Sequence[Role],
    sessions: Sequence[StoredInterview],
    now: datetime,
    marks: Sequence[StoredClaimMark] = (),
) -> WeeklyProgress | None:
    """The progress block, or None when the reader has nothing to report and nothing to be nudged toward."""
    base = (plan_progress(plan, now) if plan is not None else None) or WeeklyProgress()
    near = closest_role(roles)
    practised = last_practice(sessions, now)
    merged = base.model_copy(
        update={
            "closestRole": near.title if near is not None else None,
            "closestEta": near.eta if near is not None else None,
            "lastPractice": practised,
        }
    )
    nudged = merged.model_copy(update={"nudge": choose_nudge(merged, ready=ready_to_redrill(marks, now))})
    return nudged if nudged.model_dump(exclude_none=True) else None


__all__ = [
    "PRACTICE_WINDOW",
    "TICK_WINDOW",
    "build_progress",
    "choose_nudge",
    "closest_role",
    "eta_days",
    "last_practice",
    "plan_progress",
    "ready_to_redrill",
]
