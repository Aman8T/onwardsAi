"""Progress, all in code: the plan's week and ticks, the closest role from the ETA text, the last practice, the nudge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tayyari.contract.models import Role
from tayyari.contract.weekly import WeeklyProgress
from tayyari.storage.ports import StoredClaimMark, StoredInterview, StoredPlan
from tayyari.weekly.progress import (
    build_progress,
    choose_nudge,
    closest_role,
    eta_days,
    last_practice,
    plan_progress,
    ready_to_redrill,
)

NOW = datetime(2026, 10, 19, 1, 0, tzinfo=UTC)


def role(title: str, eta: str) -> Role:
    return Role(
        id=title.lower().replace(" ", "-"), title=title, alignment=50, eta=eta, why="", requirements=[]
    )


def plan(
    *, created: datetime, done: list[str] | None = None, hours_per_week: int = 6, move_index: int = 0
) -> StoredPlan:
    phases = [
        {"title": "Learn", "hours": 6, "tasks": ["a", "b"], "deliverable": "x"},
        {"title": "Build", "hours": 6, "tasks": ["c"], "deliverable": "y"},
        {"title": "Ship", "hours": 12, "tasks": ["d", "e", "f"], "deliverable": "z"},
    ]
    return StoredPlan(
        id="a" * 32,
        profile_id="p" * 32,
        role_id="ai-product-engineer",
        requirement=None,
        content={
            "roleTitle": "AI Product Engineer",
            "moves": [{"id": "m1", "phases": phases}, {"id": "m2", "phases": []}],
        },
        state={"hoursPerWeek": hours_per_week, "moveIndex": move_index, "done": done or [], "links": []},
        version=1,
        created_at=created,
        updated_at=created,
    )


def session(days_ago: float) -> StoredInterview:
    when = NOW - timedelta(days=days_ago)
    return StoredInterview(
        id="s" * 32,
        profile_id="p" * 32,
        kind="role",
        status="done",
        created_at=when,
        updated_at=when,
        version=1,
        state={},
        debrief=None,
    )


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("~2 months", 60),
        ("6-9 months", 180),
        ("6–9 months", 180),
        ("1 year", 365),
        ("about 3 weeks", 21),
        ("a year", 365),
        ("two months", 60),
        ("10 days", 10),
        ("18+ months", 540),
        ("soon", None),
        ("", None),
        ("0 months", None),
    ],
)
def test_the_lower_end_of_an_eta_in_days(text: str, days: float | None) -> None:
    assert eta_days(text) == days


def test_the_closest_role_is_the_shortest_readable_eta() -> None:
    roles = [
        role("Senior Frontend Engineer", "~6 months"),
        role("Design Engineer", "~2 months"),
        role("Lead", "not sure"),
    ]
    near = closest_role(roles)
    assert near is not None and near.title == "Design Engineer"
    assert closest_role([role("Lead", "someday")]) is None
    assert closest_role([]) is None


def test_the_plan_week_counts_from_the_day_it_was_written() -> None:
    first = plan_progress(plan(created=NOW - timedelta(days=2)), NOW)
    assert first is not None and (first.planRole, first.planWeek, first.planWeeks) == (
        "AI Product Engineer",
        1,
        4,
    )
    second = plan_progress(plan(created=NOW - timedelta(days=8)), NOW)
    assert second is not None and second.planWeek == 2
    late = plan_progress(plan(created=NOW - timedelta(days=400)), NOW)
    assert late is not None and late.planWeek == 4  # never past the plan's last week


def test_ticks_are_the_steps_of_this_weeks_phases() -> None:
    # week 1: the Learn phase (a, b)
    week1 = plan_progress(plan(created=NOW - timedelta(days=1), done=["m1:0:0", "m1:1:0"]), NOW)
    assert week1 is not None and (week1.ticksThisWeek, week1.ticksPlanned) == (1, 2)
    # week 2: the Build phase (c)
    week2 = plan_progress(plan(created=NOW - timedelta(days=8), done=["m1:0:0", "m1:1:0"]), NOW)
    assert week2 is not None and (week2.ticksThisWeek, week2.ticksPlanned) == (1, 1)
    # week 3: the Ship phase spans weeks 3 to 4
    week3 = plan_progress(plan(created=NOW - timedelta(days=15)), NOW)
    assert week3 is not None and (week3.ticksThisWeek, week3.ticksPlanned) == (0, 3)


def test_the_hours_a_week_change_the_weeks() -> None:
    slow = plan_progress(plan(created=NOW, hours_per_week=3), NOW)
    assert slow is not None and slow.planWeeks == 8


def test_a_plan_with_no_move_to_follow_gives_nothing() -> None:
    no_moves = StoredPlan(
        id="a" * 32,
        profile_id="p" * 32,
        role_id="r",
        requirement=None,
        content={"roleTitle": "x", "moves": []},
        state={},
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    assert plan_progress(no_moves, NOW) is None


def test_the_move_the_person_picked_is_the_one_followed() -> None:
    picked = plan_progress(plan(created=NOW, move_index=1), NOW)
    assert picked is not None and picked.ticksPlanned == 0 and picked.planWeeks == 1


def test_the_last_practice_is_the_newest_within_thirty_days() -> None:
    assert last_practice([session(40), session(3), session(10)], NOW) == "2026-10-16"
    assert last_practice([session(40)], NOW) is None
    assert last_practice([], NOW) is None


@pytest.mark.parametrize(
    ("progress", "nudge"),
    [
        (
            WeeklyProgress(
                planRole="AI Product Engineer",
                planWeek=1,
                planWeeks=4,
                ticksThisWeek=1,
                ticksPlanned=3,
                lastPractice="2026-10-16",
            ),
            "Tick off 2 more steps of your AI Product Engineer plan this week.",
        ),
        (
            WeeklyProgress(
                planRole="AI Product Engineer",
                planWeek=1,
                planWeeks=4,
                ticksThisWeek=2,
                ticksPlanned=3,
                lastPractice="2026-10-16",
            ),
            "Tick off 1 more step of your AI Product Engineer plan this week.",
        ),
        (
            WeeklyProgress(
                planRole="AI Product Engineer",
                planWeek=1,
                planWeeks=4,
                ticksThisWeek=3,
                ticksPlanned=3,
                lastPractice="2026-10-16",
            ),
            "This week's steps of your AI Product Engineer plan are done. Run a practice interview for that role.",
        ),
        (
            WeeklyProgress(closestRole="Design Engineer", closestEta="~2 months"),
            "You have not practised an interview in the last 30 days. One short round keeps your answers ready.",
        ),
        (
            WeeklyProgress(closestRole="Design Engineer", lastPractice="2026-10-16"),
            "Open Prepare for Design Engineer and pick a first step.",
        ),
        (WeeklyProgress(lastPractice="2026-10-16"), None),
    ],
)
def test_the_nudge_is_the_first_rule_that_applies(progress: WeeklyProgress, nudge: str | None) -> None:
    assert choose_nudge(progress) == nudge


def test_progress_is_assembled_from_what_the_reader_has() -> None:
    built = build_progress(
        plan=plan(created=NOW - timedelta(days=1), done=["m1:0:0"]),
        roles=[role("Design Engineer", "~2 months")],
        sessions=[session(3)],
        now=NOW,
    )
    assert built is not None
    assert built.to_dict() == {
        "planRole": "AI Product Engineer",
        "planWeek": 1,
        "planWeeks": 4,
        "ticksThisWeek": 1,
        "ticksPlanned": 2,
        "closestRole": "Design Engineer",
        "closestEta": "~2 months",
        "lastPractice": "2026-10-16",
        "nudge": "Tick off 1 more step of your AI Product Engineer plan this week.",
    }


def test_a_reader_with_nothing_is_still_nudged_toward_a_first_practice() -> None:
    built = build_progress(plan=None, roles=[], sessions=[], now=NOW)
    assert built is not None
    assert built.to_dict() == {
        "nudge": "You have not practised an interview in the last 30 days. One short round keeps your answers ready."
    }


def test_a_recent_practice_alone_is_something_to_report() -> None:
    kwargs: dict[str, Any] = {"plan": None, "roles": [], "sessions": [session(1)], "now": NOW}
    built = build_progress(**kwargs)
    assert built is not None and built.to_dict() == {"lastPractice": "2026-10-18"}


# ───────────────────────── ready to re-drill (I2) ─────────────────────────


def mark(
    key: str, *, created: datetime, done: datetime | None = None, placed: bool = True
) -> StoredClaimMark:
    return StoredClaimMark(
        id=f"{key}-{created.isoformat()}",
        profile_id="p",
        claim_key=key,
        claim_text=key,
        source="Resume",
        session_id=f"s-{created.isoformat()}",
        slot_id="s1",
        session_kind="resume",
        slot_kind="claim",
        verdict="shaky",
        rung="push",
        question="q?",
        marked_on=created.date(),
        created_at=created,
        fix_text="Write down one real example.",
        fix_minutes=15,
        fix_kept=placed,
        fix_added_at=created if placed else None,
        fix_done_at=done,
    )


def test_a_claim_is_ready_to_redrill_when_its_fix_was_ticked_last_week_and_not_asked_since() -> None:
    old = NOW - timedelta(days=10)
    marks = [
        mark("a", created=old, done=NOW - timedelta(days=2)),  # ready
        mark("b", created=old, done=NOW - timedelta(days=3)),  # ready
        mark("c", created=old, done=NOW - timedelta(days=9)),  # ticked too long ago
        mark("d", created=old, done=NOW - timedelta(days=4)),
        mark("d", created=NOW - timedelta(days=1)),  # asked again since: not waiting any more
        mark("e", created=old),  # not ticked
    ]
    assert ready_to_redrill(marks, NOW) == 2
    built = build_progress(plan=None, roles=[], sessions=[session(1)], now=NOW, marks=marks)
    assert built is not None
    assert built.nudge == "You ticked 2 Fix steps last week. Those 2 claims are ready to re-drill."
    one = build_progress(plan=None, roles=[], sessions=[session(1)], now=NOW, marks=marks[:1])
    assert (
        one is not None and one.nudge == "You ticked a Fix step last week. That claim is ready to re-drill."
    )


def test_without_a_ticked_fix_the_nudge_is_as_before() -> None:
    assert choose_nudge(WeeklyProgress(), ready=0) == choose_nudge(WeeklyProgress())
