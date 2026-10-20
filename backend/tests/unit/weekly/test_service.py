"""The service: subscriptions, editions, and the scheduler pass with a fake clock. Monday 06:00 India time, two workers racing, a
failed send retried on the next tick, a pause, an unsubscribe, an account with no email, no mail provider, and the weekly cap."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tayyari.contract.weekly import WeeklySubscriptionUpdate
from tayyari.core.errors import BadRequest, NotFound
from tayyari.weekly.service import WeeklyService
from tests.fakes.auth import FakeMailer
from tests.unit.weekly.support import (
    MONDAY_MORNING,
    SUNDAY_NIGHT,
    WEEK_OF,
    Rig,
    make_listing,
    make_rig,
)

ALL = ["jobs", "progress", "news", "tip"]


def update(**over: Any) -> WeeklySubscriptionUpdate:
    values: dict[str, Any] = {"status": "active", "day": "mon", "sections": ALL}
    values.update(over)
    return WeeklySubscriptionUpdate.model_validate(values)


async def ready(tmp_path: Path, **kwargs: Any) -> Rig:
    rig = await make_rig(tmp_path, **kwargs)
    rig.listings(make_listing("1" * 32, employer="kestrelpay"))
    rig.script_good_week()
    return rig


def stored_editions(rig: Rig) -> list[Any]:
    return list(rig.repo.editions.values())


# ───────────────────────── the scheduler ─────────────────────────


async def test_monday_six_in_the_morning_india_time_builds_and_emails_the_paper(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    report = await rig.service.run_once()
    assert (report.days, report.composed, report.sent, report.failed) == (["mon"], 1, 1, 0)
    (row,) = stored_editions(rig)
    assert row.week_of.isoformat() == WEEK_OF and row.issue == 1 and row.emailed_at == rig.clock.now()
    (mail,) = rig.mailer.sent
    assert mail.to == "ana@example.com"
    assert mail.subject.startswith("The Leafout Weekly No. 1: ")
    assert mail.headers is not None
    assert mail.headers["List-Unsubscribe"].startswith(
        "<https://app.leafout.in/api/weekly/unsubscribe?token=tok-"
    )
    assert mail.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert f"https://app.leafout.in/weekly?e={row.id}" in mail.html and "tok-" in mail.text


async def test_nothing_is_sent_before_six_or_on_another_day(tmp_path: Path) -> None:
    early = await ready(tmp_path / "early", start=SUNDAY_NIGHT)  # Monday 01:30 India time
    await early.subscribe()
    report = await early.service.run_once()
    assert report.days == [] and early.mailer.sent == [] and stored_editions(early) == []

    tuesday = await ready(tmp_path / "tue", start=MONDAY_MORNING + timedelta(days=1))
    await tuesday.subscribe()
    assert (await tuesday.service.run_once()).days == [] and tuesday.mailer.sent == []


async def test_a_friday_reader_is_served_on_friday_only(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe(day="fri")
    assert (await rig.service.run_once()).composed == 0
    rig.clock.advance(4 * 86_400)  # Friday 06:30 India time
    report = await rig.service.run_once()
    assert (report.days, report.composed, report.sent) == (["fri"], 1, 1)
    assert stored_editions(rig)[0].week_of.isoformat() == "2026-10-23"


async def test_a_second_pass_the_same_morning_does_nothing_more(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    await rig.service.run_once()
    for _ in range(3):
        rig.clock.advance(15 * 60)
        report = await rig.service.run_once()
        assert (report.composed, report.sent) == (0, 0)
    assert len(rig.mailer.sent) == 1 and len(stored_editions(rig)) == 1


async def test_next_week_is_a_new_issue(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    await rig.service.run_once()
    rig.clock.advance(7 * 86_400)
    await rig.service.run_once()
    assert sorted(e.issue for e in stored_editions(rig)) == [1, 2]
    assert len(rig.mailer.sent) == 2


async def test_two_workers_at_once_make_one_edition_and_send_one_email(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    other = WeeklyService(
        settings=rig.settings,
        repo=rig.fakes.weekly,
        users=rig.fakes.users,
        composer=rig.composer,
        mailer=rig.mailer,
        clock=rig.clock,
        env="test",
        data_dir=tmp_path / "other",
    )
    first, second = await asyncio.gather(rig.service.run_once(), other.run_once())
    assert first.composed + second.composed == 1
    assert first.sent + second.sent == 1
    assert len(stored_editions(rig)) == 1 and len(rig.mailer.sent) == 1
    assert len(rig.claude.calls_for("weekly.judge")) == 1  # the model was asked once, not twice


async def test_the_unique_week_is_the_last_guard_when_a_claim_is_lost(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    first = await rig.service.build(rig.user.id)
    second = await rig.service.build(rig.user.id)
    assert first is not None and second is not None and first.id == second.id
    assert len(stored_editions(rig)) == 1


async def test_a_failed_send_is_retried_on_the_next_tick_and_marked_when_it_goes(tmp_path: Path) -> None:
    rig = await ready(tmp_path, mailer=FakeMailer(fail_with="the email provider answered 500"))
    await rig.subscribe()
    report = await rig.service.run_once()
    assert (report.composed, report.sent, report.failed) == (1, 0, 1)
    (row,) = stored_editions(rig)
    assert row.emailed_at is None

    # the same tick again: the send is still held by the first try, so nothing is attempted twice
    again = await rig.service.run_once()
    assert (again.sent, again.failed) == (0, 0)

    rig.mailer.fail_with = None
    rig.clock.advance(15 * 60)
    report = await rig.service.run_once()
    assert (report.composed, report.sent, report.failed) == (0, 1, 0)
    assert stored_editions(rig)[0].emailed_at is not None and len(rig.mailer.sent) == 1


async def test_after_three_failed_tries_the_week_is_given_up(tmp_path: Path) -> None:
    rig = await ready(tmp_path, mailer=FakeMailer(fail_with="down"))
    await rig.subscribe()
    failures = 0
    for _ in range(5):
        failures += (await rig.service.run_once()).failed
        rig.clock.advance(15 * 60)
    assert failures == 3
    assert rig.mailer.sent == [] and stored_editions(rig)[0].emailed_at is None


async def test_a_compose_that_fails_is_retried_and_stores_nothing_half_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    real = rig.composer.compose
    calls = {"n": 0}

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the graph could not be read")
        return await real(*args, **kwargs)

    monkeypatch.setattr(rig.composer, "compose", flaky)
    first = await rig.service.run_once()
    assert (first.composed, first.failed, first.sent) == (0, 1, 0) and stored_editions(rig) == []
    rig.clock.advance(15 * 60)
    second = await rig.service.run_once()
    assert (second.composed, second.sent) == (1, 1)


async def test_a_paused_reader_is_skipped_until_the_pause_is_over(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe(status="paused", paused_until=MONDAY_MORNING + timedelta(days=10))
    assert (await rig.service.run_once()).composed == 0 and rig.mailer.sent == []

    rig.clock.advance(11 * 86_400)  # a Friday: but the reader wants Mondays
    await rig.service.run_once()
    rig.clock.advance(3 * 86_400)  # Monday 2026-11-02, past the pause
    report = await rig.service.run_once()
    assert (report.composed, report.sent) == (1, 1)
    sub = await rig.repo.get_subscription(rig.user.id)
    assert sub is not None and sub.status == "active" and sub.paused_until is None


async def test_an_off_reader_is_skipped(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe(status="off")
    report = await rig.service.run_once()
    assert (report.composed, report.sent) == (0, 0) and rig.mailer.sent == []


async def test_unsubscribing_stops_the_next_paper(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    await rig.service.run_once()
    token = (await rig.repo.get_subscription(rig.user.id)).unsubscribe_token
    assert await rig.service.unsubscribe(token) is True
    rig.clock.advance(7 * 86_400)
    assert (await rig.service.run_once()).composed == 0
    assert len(rig.mailer.sent) == 1
    assert (await rig.service.subscription(rig.user.id)).status == "off"


async def test_an_account_with_no_email_gets_the_paper_in_the_app_only(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe(email=None)
    report = await rig.service.run_once()
    assert (report.composed, report.sent, report.failed) == (1, 0, 0)
    assert rig.mailer.sent == [] and len(stored_editions(rig)) == 1
    assert stored_editions(rig)[0].emailed_at is None
    listed = await rig.service.editions(rig.user.id)
    assert [e.read for e in listed.editions] == [False]


async def test_readers_are_emailed_in_batches_with_a_pause_between(tmp_path: Path) -> None:
    rig = await ready(tmp_path, batch_size=10, batch_pause_s=1.0, max_paid_editions_per_week=0)
    await rig.subscribe()
    for i in range(24):
        who = await rig.add_user(f"reader{i}@example.com")
        await rig.subscribe(who)
    report = await rig.service.run_once()
    assert (report.composed, report.sent) == (25, 25) and len(rig.mailer.sent) == 25
    assert len(rig.clock.sleeps) >= 3  # three batches (10, 10, 5), a pause after each
    assert all(s == 1.0 for s in rig.clock.sleeps)
    assert len({m.to for m in rig.mailer.sent}) == 25


async def test_without_a_mail_provider_nothing_is_sent_and_the_html_is_written_for_a_look(
    tmp_path: Path,
) -> None:
    rig = await ready(tmp_path, no_mailer=True)
    await rig.subscribe()
    report = await rig.service.run_once()
    assert (report.composed, report.sent, report.no_mailer) == (1, 0, 1)
    (row,) = stored_editions(rig)
    assert row.emailed_at is None
    html = rig.service.local_path(row.id).read_text(encoding="utf-8")
    assert "The Leafout Weekly" in html and f"?e={row.id}" in html


async def test_on_production_the_local_copy_is_not_written(tmp_path: Path) -> None:
    rig = await ready(tmp_path, no_mailer=True, env="prod")
    await rig.subscribe()
    await rig.service.run_once()
    (row,) = stored_editions(rig)
    assert not rig.service.local_path(row.id).exists()


async def test_the_weekly_cap_on_paid_editions_leaves_the_rest_without_the_model(tmp_path: Path) -> None:
    rig = await ready(tmp_path, max_paid_editions_per_week=1)
    await rig.subscribe()
    second = await rig.add_user("second@example.com")
    await rig.subscribe(second)
    await rig.service.run_once()
    editions = stored_editions(rig)
    assert len(editions) == 2
    with_news = [e for e in editions if e.payload["news"]]
    assert len(with_news) == 1  # one edition spent on the model, the other was built from the graph alone
    assert await rig.repo.paid_this_week(MONDAY_MORNING.date()) == 1
    assert len(rig.claude.calls_for("weekly.judge")) == 1


async def test_a_cap_of_zero_means_no_paid_edition_at_all(tmp_path: Path) -> None:
    rig = await ready(tmp_path, max_paid_editions_per_week=0)
    await rig.subscribe()
    await rig.service.run_once()
    assert rig.claude.calls == [] and rig.spend.held == []
    assert len(rig.mailer.sent) == 1


async def test_a_reader_whose_account_is_gone_is_skipped_quietly(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe(user_id="ghost-user")
    report = await rig.service.run_once()
    assert report.failed == 0 and stored_editions(rig) == []


# ───────────────────────── the subscription ─────────────────────────


async def test_a_reader_who_never_subscribed_is_off_with_their_account_email(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    sub = await rig.service.subscription(rig.user.id)
    assert (sub.status, sub.email, sub.day, sub.sections, sub.unread) == (
        "off",
        "ana@example.com",
        "mon",
        ALL,
        0,
    )


async def test_subscribing_defaults_the_email_to_the_account_and_mints_a_token(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    sub = await rig.service.update(rig.user.id, update(sections=["tip", "jobs"]))
    assert (sub.status, sub.email, sub.sections) == ("active", "ana@example.com", ["jobs", "tip"])
    row = await rig.repo.get_subscription(rig.user.id)
    assert row is not None and len(row.unsubscribe_token) >= 32


async def test_the_token_stays_while_running_and_is_fresh_after_off_then_on(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    await rig.service.update(rig.user.id, update())
    first = (await rig.repo.get_subscription(rig.user.id)).unsubscribe_token
    await rig.service.update(rig.user.id, update(day="fri"))
    assert (await rig.repo.get_subscription(rig.user.id)).unsubscribe_token == first
    await rig.service.update(rig.user.id, update(status="paused", pauseDays=14))
    assert (await rig.repo.get_subscription(rig.user.id)).unsubscribe_token == first
    await rig.service.update(rig.user.id, update(status="off"))
    assert (
        await rig.repo.get_subscription(rig.user.id)
    ).unsubscribe_token == first  # off keeps its old link working
    await rig.service.update(rig.user.id, update())
    fresh = (await rig.repo.get_subscription(rig.user.id)).unsubscribe_token
    assert fresh != first and await rig.repo.get_subscription_by_token(first) is None


async def test_a_pause_runs_for_the_days_asked(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    sub = await rig.service.update(rig.user.id, update(status="paused", pauseDays=30))
    assert (sub.status, sub.pausedUntil) == ("paused", "2026-11-18")
    default = await rig.service.update(rig.user.id, update(status="paused"))
    assert default.pausedUntil == "2026-11-18"


async def test_a_pause_that_is_over_reads_as_active(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    await rig.service.update(rig.user.id, update(status="paused", pauseDays=7))
    rig.clock.advance(8 * 86_400)
    sub = await rig.service.subscription(rig.user.id)
    assert sub.status == "active" and sub.pausedUntil is None


@pytest.mark.parametrize(
    "body",
    [
        {"status": "paused", "pauseDays": 3},
        {"status": "paused", "pauseDays": 91},
        {"status": "active", "pauseDays": 14},
        {"status": "active", "sections": []},
    ],
)
async def test_a_bad_update_is_refused(tmp_path: Path, body: dict[str, Any]) -> None:
    rig = await make_rig(tmp_path)
    with pytest.raises(BadRequest):
        await rig.service.update(rig.user.id, update(**body))
    assert await rig.repo.get_subscription(rig.user.id) is None


async def test_turning_it_off_needs_no_sections(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    await rig.service.update(rig.user.id, update(sections=["jobs"]))
    sub = await rig.service.update(rig.user.id, update(status="off", sections=[]))
    assert sub.status == "off" and sub.sections == ["jobs"]  # the earlier choice is kept for next time


async def test_an_account_with_no_email_subscribes_without_one(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    phone = await rig.fakes.users.sign_in_supabase(
        sub="11111111-1111-4111-8111-111111111111",
        email=None,
        email_verified=False,
        name=None,
        now=rig.clock.now(),
    )
    sub = await rig.service.update(phone.id, update())
    assert sub.email is None and sub.status == "active"


async def test_an_unsubscribe_token_that_is_wrong_changes_nothing(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    await rig.service.update(rig.user.id, update())
    assert await rig.service.unsubscribe("wrong") is False
    assert await rig.service.unsubscribe("") is False
    assert await rig.service.unsubscribe("x" * 500) is False
    assert (await rig.service.subscription(rig.user.id)).status == "active"


# ───────────────────────── editions ─────────────────────────


async def test_opening_an_edition_marks_it_read_and_only_its_reader_may(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    await rig.service.run_once()
    (row,) = stored_editions(rig)
    assert (await rig.service.subscription(rig.user.id)).unread == 1
    opened = await rig.service.edition(rig.user.id, row.id)
    assert opened.id == row.id and opened.readAt is not None and opened.emailedAt is not None
    assert (await rig.service.subscription(rig.user.id)).unread == 0
    stranger = await rig.add_user("stranger@example.com")
    with pytest.raises(NotFound):
        await rig.service.edition(stranger.id, row.id)
    with pytest.raises(NotFound):
        await rig.service.edition(rig.user.id, "0" * 32)


async def test_the_archive_lists_the_papers_newest_first(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    await rig.service.run_once()
    rig.clock.advance(7 * 86_400)
    await rig.service.run_once()
    listed = await rig.service.editions(rig.user.id)
    assert [e.issue for e in listed.editions] == [2, 1]
    assert [e.weekOf for e in listed.editions] == ["2026-10-26", "2026-10-19"]
    assert all(e.headline for e in listed.editions)


async def test_a_preview_builds_this_week_once_and_marks_it_read(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    assert await rig.service.current(rig.user.id) is None
    first = await rig.service.preview(rig.user.id)
    assert first.weekOf == WEEK_OF and first.readAt is not None and first.issue == 1
    assert await rig.service.current(rig.user.id) is not None
    again = await rig.service.preview(rig.user.id)
    assert again.id == first.id
    assert len(rig.claude.calls_for("weekly.judge")) == 1  # the second preview built nothing


async def test_a_preview_built_before_the_send_is_emailed_by_the_send(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe()
    built = await rig.service.preview(rig.user.id)
    report = await rig.service.run_once()
    assert (report.composed, report.sent) == (0, 1)
    assert len(rig.mailer.sent) == 1 and built.id in rig.mailer.sent[0].html


async def test_a_preview_for_a_friday_reader_belongs_to_their_friday(tmp_path: Path) -> None:
    rig = await ready(tmp_path)
    await rig.subscribe(day="fri")
    assert (await rig.service.preview(rig.user.id)).weekOf == "2026-10-23"
