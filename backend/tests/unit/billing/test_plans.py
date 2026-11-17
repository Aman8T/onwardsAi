"""`PlanService` over the fake repositories (roadmap B2): Just Looking's limits, idempotency, give-backs, the month
boundary, Day One counted with no limit, `usage` for all six counts, the 402 body and the sentence a stream shows; and
`EntitlementService.hold` on each plan."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from tayyari.billing.entitlements import EntitlementService, apply_ref
from tayyari.billing.plans import (
    LIMIT_REACHED,
    PlanService,
    limit_sentence,
    month_start,
    next_month_start,
)
from tayyari.billing.products import LIFETIME_DAYS
from tayyari.core.errors import PaymentRequired
from tayyari.core.settings import Settings
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

USER = "ana"
DECEMBER = datetime(2026, 12, 1, tzinfo=UTC)


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(
        plans={"free_searches": 2, "free_analyses": 1, "free_practice": 3, "free_coach": 30},
        billing={"free_matches_per_month": 3, "free_applications": 2},
    )


@pytest.fixture
def plans(fakes: FakeRepositories, settings: Settings, clock: FakeClock) -> PlanService:
    return PlanService(fakes.billing, settings, clock)


async def make_day_one(fakes: FakeRepositories, clock: FakeClock, user: str = USER) -> None:
    await fakes.billing.grant(user, credits=0, pass_days=LIFETIME_DAYS, ref="order-dayone", now=clock.now())


# ───────────────────────── the month ─────────────────────────


@pytest.mark.parametrize(
    ("now", "start", "following"),
    [
        (datetime(2026, 11, 17, 9, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC), DECEMBER),
        (datetime(2026, 12, 31, 23, 59, tzinfo=UTC), DECEMBER, datetime(2027, 1, 1, tzinfo=UTC)),
        # 1 Dec 02:00 in India is still 30 Nov in UTC
        (datetime.fromisoformat("2026-12-01T02:00:00+05:30"), datetime(2026, 11, 1, tzinfo=UTC), DECEMBER),
    ],
)
def test_the_month_is_the_calendar_month_in_utc(now: datetime, start: datetime, following: datetime) -> None:
    assert month_start(now) == start
    assert next_month_start(now) == following


# ───────────────────────── Just Looking ─────────────────────────


async def test_just_looking_counts_up_to_the_limit_then_answers_402(plans: PlanService) -> None:
    await plans.use(USER, "searches", "run-1")
    await plans.use(USER, "searches", "run-2")
    with pytest.raises(PaymentRequired) as refused:
        await plans.use(USER, "searches", "run-3")
    err = refused.value
    assert err.status == 402 and err.reason == LIMIT_REACHED
    assert err.to_body() == {
        "error": "payment_required",
        "reason": "limit_reached",
        "feature": "searches",
        "limit": 2,
        "resetsAt": "2026-11-01T00:00:00+00:00",
    }
    assert err.user_message == "You've used your 2 searches with AI this month. They come back on 1 Nov."


async def test_a_use_is_idempotent_on_its_ref(plans: PlanService, fakes: FakeRepositories) -> None:
    first = await plans.use(USER, "searches", "run-1")
    again = await plans.use(USER, "searches", "run-1")
    assert (first.used, first.replayed) == (1, False)
    assert (again.used, again.replayed) == (1, True)
    assert len(fakes.billing.usage) == 1


async def test_a_use_given_back_can_be_used_again(plans: PlanService) -> None:
    await plans.use(USER, "analyses", "run-1")
    with pytest.raises(PaymentRequired):
        await plans.use(USER, "analyses", "run-2")
    assert await plans.release(USER, "analyses", "run-1") is True
    assert await plans.release(USER, "analyses", "run-1") is False
    await plans.use(USER, "analyses", "run-2")


async def test_the_limit_comes_back_with_the_month(plans: PlanService, clock: FakeClock) -> None:
    clock.advance((datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC) - clock.now()).total_seconds())
    await plans.use(USER, "analyses", "october")
    with pytest.raises(PaymentRequired):
        await plans.use(USER, "analyses", "october-again")
    clock.advance(1)
    november = await plans.use(USER, "analyses", "november")
    assert november.used == 1


async def test_a_limit_of_zero_refuses_the_first_use(
    make_settings: Callable[..., Settings], fakes: FakeRepositories, clock: FakeClock
) -> None:
    plans = PlanService(fakes.billing, make_settings(plans={"free_practice": 0}), clock)
    with pytest.raises(PaymentRequired) as refused:
        await plans.use(USER, "practice", "s1")
    assert refused.value.to_body()["limit"] == 0
    assert refused.value.user_message == "Practice interviews are not part of Just Looking."


# ───────────────────────── Day One ─────────────────────────


async def test_day_one_records_every_use_with_no_limit(
    plans: PlanService, fakes: FakeRepositories, clock: FakeClock
) -> None:
    await make_day_one(fakes, clock)
    assert await plans.plan_of(USER) == "day_one"
    for n in range(5):
        got = await plans.use(USER, "searches", f"run-{n}")
        assert got.used == n + 1
    assert (await plans.usage(USER)).searches.used == 5


async def test_a_thirty_day_pass_is_not_day_one(
    plans: PlanService, fakes: FakeRepositories, clock: FakeClock
) -> None:
    await fakes.billing.grant(USER, credits=0, pass_days=30, ref="order-pass", now=clock.now())
    assert await plans.plan_of(USER) == "just_looking"
    assert await plans.plan_of("nobody") == "just_looking"


# ───────────────────────── usage ─────────────────────────


async def test_usage_for_a_visitor_is_the_limits_with_nothing_used(plans: PlanService) -> None:
    got = (await plans.usage(None)).model_dump()
    assert got == {
        "analyses": {"limit": 1, "used": 0},
        "matches": {"limit": 3, "used": 0},
        "searches": {"limit": 2, "used": 0},
        "practice": {"limit": 3, "used": 0},
        "coach": {"limit": 30, "used": 0},
        "applications": {"limit": 2, "used": 0},
    }


async def test_usage_reads_all_six_counts(
    plans: PlanService, fakes: FakeRepositories, settings: Settings, clock: FakeClock
) -> None:
    ledger = EntitlementService(fakes.billing, settings.billing, clock)
    await ledger.consume(USER, "match-1")
    await ledger.consume(USER, "match-2")
    await ledger.hold(USER, apply_ref("a1"))
    await plans.use(USER, "analyses", "run-1")
    await plans.use(USER, "searches", "run-1")
    await plans.use(USER, "practice", "s1")
    for n in range(4):
        await plans.use(USER, "coach", f"q{n}")
    got = await plans.usage(USER)
    assert {name: use.used for name, use in got} == {
        "analyses": 1,
        "matches": 2,
        "searches": 1,
        "practice": 1,
        "coach": 4,
        "applications": 1,
    }
    assert {name: use.limit for name, use in got} == {
        "analyses": 1,
        "matches": 3,
        "searches": 2,
        "practice": 3,
        "coach": 30,
        "applications": 2,
    }


def test_resets_at_is_the_first_of_next_month(plans: PlanService) -> None:
    assert plans.resets_at() == datetime(2026, 11, 1, tzinfo=UTC)


# ───────────────────────── the sentence ─────────────────────────


@pytest.mark.parametrize(
    ("feature", "limit", "resets", "sentence"),
    [
        ("searches", 5, DECEMBER, "You've used your 5 searches with AI this month. They come back on 1 Dec."),
        ("analyses", 2, DECEMBER, "You've used your 2 new analyses this month. They come back on 1 Dec."),
        ("analyses", 1, DECEMBER, "You've used your 1 new analysis this month. It comes back on 1 Dec."),
        (
            "practice",
            3,
            DECEMBER,
            "You've used your 3 practice interviews this month. They come back on 1 Dec.",
        ),
        ("coach", 30, DECEMBER, "You've used your 30 coach questions this month. They come back on 1 Dec."),
        ("matches", 3, DECEMBER, "You've used your 3 posting matches this month. They come back on 1 Dec."),
        ("applications", 5, None, "You've used your 5 free applications."),
    ],
)
def test_the_sentence_a_stream_shows(
    feature: str, limit: int, resets: datetime | None, sentence: str
) -> None:
    assert limit_sentence(feature, limit, resets) == sentence  # type: ignore[arg-type]


# ───────────────────────── auto-apply on each plan ─────────────────────────


async def test_just_looking_is_refused_after_the_free_applications_while_no_price_is_set(
    fakes: FakeRepositories, settings: Settings, clock: FakeClock
) -> None:
    ledger = EntitlementService(fakes.billing, settings.billing, clock)
    await ledger.hold(USER, apply_ref("a1"))
    await ledger.hold(USER, apply_ref("a2"))
    with pytest.raises(PaymentRequired) as refused:
        await ledger.hold(USER, apply_ref("a3"))
    assert refused.value.to_body() == {
        "error": "payment_required",
        "reason": "limit_reached",
        "feature": "applications",
        "limit": 2,
    }
    assert refused.value.user_message == "You've used your 2 free applications."
    await ledger.hold(USER, apply_ref("a2"))  # a hold already taken is still answered


async def test_just_looking_pays_credits_after_the_free_applications_when_a_price_is_set(
    make_settings: Callable[..., Settings], fakes: FakeRepositories, clock: FakeClock
) -> None:
    settings = make_settings(billing={"free_applications": 1, "apply_credit_cost": 1})
    ledger = EntitlementService(fakes.billing, settings.billing, clock)
    await fakes.billing.grant(USER, credits=1, pass_days=0, ref="pack", now=clock.now())
    await ledger.hold(USER, apply_ref("a1"))  # free
    await ledger.hold(USER, apply_ref("a2"))  # one credit
    assert (await ledger.summary(USER)).credits == 0
    with pytest.raises(PaymentRequired) as refused:
        await ledger.hold(USER, apply_ref("a3"))
    assert refused.value.reason == LIMIT_REACHED


async def test_day_one_gets_a_hold_of_nothing_after_the_free_applications(
    make_settings: Callable[..., Settings], fakes: FakeRepositories, clock: FakeClock
) -> None:
    settings = make_settings(billing={"free_applications": 1, "apply_credit_cost": 3})
    ledger = EntitlementService(fakes.billing, settings.billing, clock)
    await make_day_one(fakes, clock)
    for n in range(4):
        await ledger.hold(USER, apply_ref(f"a{n}"))
    reasons = [row.reason for row in fakes.billing.ledger_of(USER) if row.reason.startswith("apply")]
    assert reasons == ["apply_free", "apply_hold", "apply_hold", "apply_hold"]
    assert fakes.billing.ledger_sum(USER) == 0  # nothing was charged


async def test_the_summary_names_the_plan_and_the_matches_left(
    fakes: FakeRepositories, settings: Settings, clock: FakeClock
) -> None:
    ledger = EntitlementService(fakes.billing, settings.billing, clock)
    await ledger.consume(USER, "m1")
    summary = await ledger.summary(USER)
    assert (summary.plan, summary.matches_left) == ("just_looking", 2)
    await make_day_one(fakes, clock)
    summary = await ledger.summary(USER)
    assert (summary.plan, summary.matches_left) == ("day_one", None)
