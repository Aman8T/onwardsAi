"""One edition composed over fakes: the sections the reader chose, the headline chosen by code, the reservation around the paid part,
and an edition that is still built when the model, the news or the spend ceiling fail."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tayyari.contract.weekly import WeeklyJob, WeeklyNews, WeeklyProgress, WeeklySection
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.weekly.compose import NO_PROFILE_HEADLINE, headline_for, payload_of
from tests.fakes.claude import StructuredCall
from tests.unit.jobsearch.support import evidence_in, item, postings_in
from tests.unit.weekly.support import (
    MONDAY_MORNING,
    POSTING_INJECTED,
    POSTING_REACT,
    WEEK_OF,
    Rig,
    make_listing,
    make_rig,
)

ALL: list[WeeklySection] = ["jobs", "progress", "news", "tip"]
WEEK = date(2026, 10, 19)


async def compose(
    rig: Rig, sections: list[WeeklySection] | None = None, *, allow_paid: bool = True, issue: int = 1
) -> Any:
    return await rig.composer.compose(
        rig.user,
        sections or ALL,
        edition_id="e" * 32,
        issue=issue,
        week_of=WEEK,
        now=MONDAY_MORNING,
        allow_paid=allow_paid,
    )


def job(fit: str = "looks_close") -> WeeklyJob:
    return WeeklyJob(
        listingId="1" * 32,
        employer="E",
        title="T",
        place="P",
        applyUrl="https://x.example/1",
        fit=fit,
        met=[],
        notShown=[],  # type: ignore[arg-type]
    )


def news() -> WeeklyNews:
    return WeeklyNews(headline="h", publication="p", url="https://x.example", why="w", skill="s")


async def full_week(tmp_path: Path) -> Rig:
    rig = await make_rig(tmp_path)
    rig.listings(
        make_listing("1" * 32, employer="kestrelpay"),
        make_listing(
            "2" * 32,
            employer="nilgirimills",
            text="Frontend Engineer\nRequirements\n- React and TypeScript\n- Kubernetes\n- Terraform\n",
        ),
    )
    rig.script_good_week()
    return rig


# ───────────────────────── a whole edition ─────────────────────────


async def test_a_whole_edition_has_every_part_and_cites_what_it_says(tmp_path: Path) -> None:
    rig = await full_week(tmp_path)
    result = await compose(rig)
    edition = result.edition
    assert result.paid is True
    assert (edition.id, edition.issue, edition.weekOf) == ("e" * 32, 1, WEEK_OF)
    assert (edition.reader, edition.place) == ("Riya Kapoor", "Bengaluru, India")
    assert edition.headline == "Two openings fit your graph this week"
    assert [j.employer for j in edition.jobs] == ["Kestrelpay", "Nilgirimills"]
    assert edition.jobs[0].met and edition.jobs[1].met == []  # the lead alone is read
    assert edition.progress is not None and edition.progress.closestRole == "Design Engineer"
    assert [n.skill for n in edition.news] == ["Design systems", "Accessibility (WCAG)"]
    assert edition.tip and edition.quote
    assert edition.createdAt == MONDAY_MORNING.isoformat()


async def test_the_paid_part_runs_once_inside_one_reservation(tmp_path: Path) -> None:
    rig = await full_week(tmp_path)
    await compose(rig)
    assert rig.spend.held == [0.06] and rig.spend.open == 0
    purposes = sorted(c.ctx.purpose for c in rig.claude.calls)
    assert purposes == ["weekly.judge", "weekly.news", "weekly.news", "weekly.news", "weekly.news.read"]


async def test_the_payload_has_everything_but_what_the_row_keeps(tmp_path: Path) -> None:
    rig = await full_week(tmp_path)
    edition = (await compose(rig)).edition
    payload = payload_of(edition)
    assert not {"id", "createdAt", "emailedAt", "readAt"} & set(payload)
    assert payload["headline"] == edition.headline and payload["issue"] == 1


# ───────────────────────── the reader's choice of parts ─────────────────────────


async def test_only_the_tip_when_only_the_tip_is_chosen(tmp_path: Path) -> None:
    rig = await full_week(tmp_path)
    edition = (await compose(rig, ["tip"])).edition
    assert edition.jobs == [] and edition.progress is None and edition.news == []
    assert edition.tip and edition.quote
    assert rig.claude.calls == [] and rig.spend.held == []  # nothing paid for, nothing reserved
    assert edition.headline == "Something to try this week"


async def test_no_tip_and_no_quote_when_the_tip_is_off(tmp_path: Path) -> None:
    rig = await full_week(tmp_path)
    edition = (await compose(rig, ["jobs"])).edition
    assert edition.tip is None and edition.quote is None
    assert edition.progress is None and edition.news == []
    assert [c.ctx.purpose for c in rig.claude.calls] == ["weekly.judge"]  # the lead's read only, no news


async def test_the_tip_and_quote_rotate_with_the_issue(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    first = (await compose(rig, ["tip"], issue=1)).edition
    second = (await compose(rig, ["tip"], issue=2)).edition
    assert first.tip != second.tip and first.quote != second.quote


# ───────────────────────── when the paid part cannot run ─────────────────────────


async def test_a_refused_reservation_builds_the_edition_without_the_model(tmp_path: Path) -> None:
    rig = await full_week(tmp_path)
    rig.spend.refuse = True
    result = await compose(rig)
    assert result.paid is False and rig.claude.calls == []
    assert len(result.edition.jobs) == 2 and result.edition.jobs[0].met == [] and result.edition.news == []
    assert result.edition.jobs[0].note == "You show 6 of the 6 skills it names."


async def test_allow_paid_false_makes_no_reservation_and_no_call(tmp_path: Path) -> None:
    rig = await full_week(tmp_path)
    result = await compose(rig, allow_paid=False)
    assert result.paid is False and rig.claude.calls == [] and rig.spend.held == []
    assert len(result.edition.jobs) == 2 and result.edition.progress is not None


async def test_a_news_failure_leaves_the_jobs_and_the_read(tmp_path: Path) -> None:
    rig = await full_week(tmp_path)
    rig.claude._queues.search.clear()
    rig.claude.script_search(ClaudeOverloaded(), repeat=True)
    result = await compose(rig)
    assert result.edition.news == [] and result.edition.jobs[0].met
    assert result.paid is True


async def test_a_failed_lead_read_leaves_the_quick_fit(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(make_listing("1" * 32))
    rig.claude.script_structured("weekly.judge", ClaudeOverloaded())
    rig.claude.script_search(ClaudeOverloaded(), repeat=True)
    result = await compose(rig, ["jobs", "news"])
    assert len(result.edition.jobs) == 1 and result.edition.jobs[0].met == []


async def test_a_lead_the_reader_flags_as_aimed_at_an_ai_is_dropped_and_the_next_stays(
    tmp_path: Path,
) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(
        make_listing("1" * 32, employer="kestrelpay"),
        make_listing(
            "2" * 32, employer="nilgirimills", text=POSTING_REACT.replace("Design Systems", "Platform")
        ),
    )

    def flags(call: StructuredCall) -> dict[str, Any]:
        (posting_id, _), *_ = postings_in(call)
        ev = evidence_in(call)["React"]
        flagged = item(
            posting_id,
            [("Strong experience with React and TypeScript in production", [ev], "met")],
            instruction_quote="Strong experience with React and TypeScript in production",
        )
        return {"postings": [flagged]}

    rig.claude.script_structured("weekly.judge", flags)
    edition = (await compose(rig, ["jobs"])).edition
    assert (
        len(edition.jobs) == 1 and edition.jobs[0].met == []
    )  # the flagged lead is gone; the other is not "read"


async def test_a_posting_aimed_at_an_ai_never_reaches_the_edition_or_the_model(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(make_listing("1" * 32, text=POSTING_INJECTED))
    edition = (await compose(rig, ["jobs"])).edition
    assert edition.jobs == [] and rig.claude.calls == []


# ───────────────────────── a reader with no graph ─────────────────────────


async def test_a_reader_with_no_profile_gets_a_short_paper(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path, with_profile=False)
    result = await compose(rig)
    edition = result.edition
    assert edition.headline == NO_PROFILE_HEADLINE and edition.reader == "Reader"
    assert edition.jobs == [] and edition.news == [] and edition.progress is None
    assert edition.tip and result.paid is False and rig.claude.calls == []


async def test_progress_reflects_a_plan_and_a_practice(tmp_path: Path) -> None:
    from datetime import timedelta

    from tayyari.storage.ports import StoredInterview, StoredPlan

    rig = await make_rig(tmp_path)
    plan = StoredPlan(
        id="a" * 32,
        profile_id=rig.profile.id,
        role_id="r",
        requirement=None,
        content={
            "roleTitle": "AI Product Engineer",
            "moves": [{"id": "m1", "phases": [{"hours": 6, "tasks": ["a", "b"]}]}],
        },
        state={"hoursPerWeek": 6, "moveIndex": 0, "done": ["m1:0:0"], "links": []},
        version=1,
        created_at=MONDAY_MORNING - timedelta(days=1),
        updated_at=MONDAY_MORNING,
    )
    await rig.fakes.prepare.create(plan)
    await rig.fakes.interview.create(
        StoredInterview(
            id="s" * 32,
            profile_id=rig.profile.id,
            kind="role",
            status="done",
            created_at=MONDAY_MORNING - timedelta(days=4),
            updated_at=MONDAY_MORNING,
            version=1,
            state={},
            debrief=None,
        )
    )
    progress = (await compose(rig, ["progress"])).edition.progress
    assert progress is not None
    assert (progress.planRole, progress.planWeek, progress.planWeeks) == ("AI Product Engineer", 1, 1)
    assert (progress.ticksThisWeek, progress.ticksPlanned) == (1, 2)
    assert progress.lastPractice == "2026-10-15"
    assert progress.nudge == "Tick off 1 more step of your AI Product Engineer plan this week."


# ───────────────────────── the headline ─────────────────────────


@pytest.mark.parametrize(
    ("jobs", "progress", "news_items", "sections", "headline"),
    [
        ([job(), job(), job()], None, [], ALL, "Three openings fit your graph this week"),
        ([job("worth_a_look"), job("stretch")], None, [], ALL, "One opening fits your graph this week"),
        ([job(), job("worth_a_look")], None, [], ALL, "Two openings fit your graph this week"),
        ([job("stretch")], None, [], ALL, "New on the boards this week, each one a stretch"),
        ([job("cant_tell")], None, [], ALL, "New on the boards this week, each one a stretch"),
        (
            [],
            WeeklyProgress(planRole="AI Product Engineer"),
            [],
            ALL,
            "A quiet week on the boards: here's your progress",
        ),
        ([], None, [news()], ALL, "A quiet week on the boards: news from your field"),
        ([], None, [], ALL, "A quiet week on the boards"),
        (
            [],
            WeeklyProgress(planRole="AI Product Engineer"),
            [],
            ["progress", "tip"],
            "Your week in progress",
        ),
        ([], None, [news()], ["news"], "News from your field this week"),
        ([], None, [], ["tip"], "Something to try this week"),
    ],
)
def test_the_headline_is_chosen_by_code_from_the_counts(
    jobs: list[WeeklyJob],
    progress: WeeklyProgress | None,
    news_items: list[WeeklyNews],
    sections: list[WeeklySection],
    headline: str,
) -> None:
    assert (
        headline_for(jobs=jobs, progress=progress, news=news_items, sections=sections, has_profile=True)
        == headline
    )
