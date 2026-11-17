"""The plans (roadmap B2): which plan an account is on, and Just Looking's monthly counts.

    plan_of(user_id)              ``day_one`` when the account's pass is a lifetime one (`products.is_lifetime`), else
                                  ``just_looking``
    use(user_id, feature, ref)    count one use of ``analyses``, ``searches``, ``practice`` or ``coach``. On Just Looking
                                  it is recorded while fewer than the month's limit are recorded, else `PaymentRequired`
                                  (``limit_reached``, 402). On Day One it is recorded with no limit (so we can see what
                                  Day One people use). Idempotent on ``ref``.
    release(user_id, feature, ref)
                                  give a use back (the run failed before it produced anything)
    usage(user_id | None)         all six counts: ``matches`` and ``applications`` from the credit ledger, the other
                                  four from ``plan_usage``; a visitor (None) has used nothing

Posting matches and auto-apply applications are not counted here: the ledger pays for them (`EntitlementService`), and
`usage` only reads what it holds. Accounts on the unlimited list and requests with no account are never counted: the
routes skip the call, as the match route does for ``consume``.

The month is the calendar month in UTC: counts start again at `next_month_start`. The 402 carries the feature, the limit
and that moment, and its ``user_message`` is the sentence a stream shows (`limit_sentence`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal, get_args

from tayyari.contract.billing import Feature, FeatureUse, PlanId, PlanUsage
from tayyari.core.clock import Clock
from tayyari.core.errors import PaymentRequired
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.storage.ports import BillingRepo, FeatureUseOutcome

from .products import is_lifetime

log = get_logger(__name__)

CountedFeature = Literal["analyses", "searches", "practice", "coach"]
"""The features `PlanService.use` counts in ``plan_usage``."""
COUNTED_FEATURES: Final[tuple[CountedFeature, ...]] = get_args(CountedFeature)
LIMIT_REACHED: Final = "limit_reached"
"""The ``reason`` of a 402 when a Just Looking count has run out."""

_WORDS: Final[dict[Feature, tuple[str, str]]] = {
    "analyses": ("new analysis", "new analyses"),
    "matches": ("posting match", "posting matches"),
    "searches": ("search with AI", "searches with AI"),
    "practice": ("practice interview", "practice interviews"),
    "coach": ("coach question", "coach questions"),
    "applications": ("free application", "free applications"),
}
"""One feature, in plain words: (one, many)."""


def month_start(now: datetime) -> datetime:
    """00:00 UTC on the first day of ``now``'s month: the monthly counts (and the free posting matches) renew here."""
    utc = now.astimezone(UTC)
    return datetime(utc.year, utc.month, 1, tzinfo=UTC)


def next_month_start(now: datetime) -> datetime:
    """00:00 UTC on the first day of the month after ``now``'s: when the monthly counts start again."""
    start = month_start(now)
    return (
        datetime(start.year + 1, 1, 1, tzinfo=UTC)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )


def short_date(moment: datetime) -> str:
    """``moment`` in UTC as "1 Dec"."""
    utc = moment.astimezone(UTC)
    return f"{utc.day} {utc.strftime('%b')}"


def limit_sentence(feature: Feature, limit: int, resets_at: datetime | None) -> str:
    """What a person reads when ``feature`` has run out: "You've used your 5 searches with AI this month. They come
    back on 1 Dec." ``resets_at`` None (applications, counted once) leaves out the month and the date."""
    one, many = _WORDS[feature]
    if limit <= 0:
        return f"{many[0].upper()}{many[1:]} are not part of Just Looking."
    words = one if limit == 1 else many
    if resets_at is None:
        return f"You've used your {limit} {words}."
    back = "It comes" if limit == 1 else "They come"
    return f"You've used your {limit} {words} this month. {back} back on {short_date(resets_at)}."


def limit_reached(feature: Feature, limit: int, resets_at: datetime | None) -> PaymentRequired:
    """The 402 for a Just Looking count that has run out."""
    return PaymentRequired(
        LIMIT_REACHED,
        feature=feature,
        limit=limit,
        resets_at=None if resets_at is None else resets_at.isoformat(),
        user_message=limit_sentence(feature, limit, resets_at),
    )


class PlanService:
    def __init__(self, repo: BillingRepo, settings: Settings, clock: Clock) -> None:
        self._repo = repo
        self._settings = settings
        self._clock = clock

    def limit_of(self, feature: Feature) -> int:
        """Just Looking's limit for ``feature`` (a month's, or for ``applications`` the account's lifetime)."""
        plans, billing = self._settings.plans, self._settings.billing
        limits: dict[Feature, int] = {
            "analyses": plans.free_analyses,
            "matches": billing.free_matches_per_month,
            "searches": plans.free_searches,
            "practice": plans.free_practice,
            "coach": plans.free_coach,
            "applications": billing.free_applications,
        }
        return limits[feature]

    def resets_at(self, now: datetime | None = None) -> datetime:
        """When the monthly counts start again, seen from ``now`` (default: the clock)."""
        return next_month_start(self._clock.now() if now is None else now)

    async def plan_of(self, user_id: str) -> PlanId:
        now = self._clock.now()
        balance = await self._repo.entitlements(user_id, month_start=month_start(now))
        return "day_one" if is_lifetime(balance.pass_until, now) else "just_looking"

    async def use(self, user_id: str, feature: CountedFeature, ref: str) -> FeatureUseOutcome:
        """Count one use of ``feature`` keyed by ``ref``, or raise `PaymentRequired` (``limit_reached``)."""
        now = self._clock.now()
        day_one = await self.plan_of(user_id) == "day_one"
        limit = self.limit_of(feature)
        outcome = await self._repo.use_feature(
            user_id, feature, ref, now=now, since=month_start(now), limit=None if day_one else limit
        )
        if outcome is None:
            log.info("plan_limit_reached", feature=feature, limit=limit)
            raise limit_reached(feature, limit, next_month_start(now))
        if not outcome.replayed:
            log.info("plan_feature_used", feature=feature, used=outcome.used, day_one=day_one)
        return outcome

    async def release(self, user_id: str, feature: CountedFeature, ref: str) -> bool:
        """Give back the use of ``feature`` keyed by ``ref``; False when there was none."""
        given_back = await self._repo.release_feature(user_id, feature, ref, now=self._clock.now())
        if given_back:
            log.info("plan_feature_released", feature=feature)
        return given_back

    async def usage(self, user_id: str | None) -> PlanUsage:
        """Every count against its Just Looking limit; a visitor (None) has used nothing."""
        used: dict[Feature, int] = dict.fromkeys(get_args(Feature), 0)
        if user_id is not None:
            now = self._clock.now()
            since = month_start(now)
            balance = await self._repo.entitlements(user_id, month_start=since)
            counts = await self._repo.feature_counts(user_id, since=since)
            used["matches"] = balance.free_used
            used["applications"] = balance.apply_free_used
            for feature in COUNTED_FEATURES:
                used[feature] = counts.get(feature, 0)

        def one(feature: Feature) -> FeatureUse:
            return FeatureUse(limit=self.limit_of(feature), used=used[feature])

        return PlanUsage(
            analyses=one("analyses"),
            matches=one("matches"),
            searches=one("searches"),
            practice=one("practice"),
            coach=one("coach"),
            applications=one("applications"),
        )


__all__ = [
    "COUNTED_FEATURES",
    "LIMIT_REACHED",
    "CountedFeature",
    "PlanService",
    "limit_reached",
    "limit_sentence",
    "month_start",
    "next_month_start",
    "short_date",
]
