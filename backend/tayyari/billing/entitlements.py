"""What an account may use: the one place that decides whether a posting match is paid for.

    consume(user_id, ref)   pay for one use. In this order: an active pass (``pass``), else the month's free allowance
                            (``free_monthly``: ``free_matches_per_month`` per UTC calendar month), else one credit
                            (``credit``), else `PaymentRequired` (402). Idempotent on ``ref``: asking again for a
                            ``ref`` already paid for returns the same answer and takes nothing more.
    refund(user_id, ref)    give back what ``consume`` took for ``ref`` (the posting could not be read, say).
    grant(user_id, product, order_id)
                            add a purchase: three credits, or 30 days of pass from the later of now and the end of
                            the pass already running. Idempotent on ``order_id``.
    summary(user_id)        ``credits``, ``pass_until`` (only while it is in the future) and whether this month's free
                            posting is still unused.

Re-checks after answers, and resume and cover generation for a match that already exists, never call ``consume``:
a posting is paid for once, when it is first read.

The arithmetic runs in the repository, inside one transaction that locks the account's balance row (see
`storage.repos.billing`), so two requests can never spend the same last credit. This class adds the clock, the month
boundary and the error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from tayyari.contract.billing import Product
from tayyari.core.clock import Clock
from tayyari.core.errors import PaymentRequired
from tayyari.core.logging import get_logger
from tayyari.core.settings import BillingSettings
from tayyari.storage.ports import BillingRepo, ConsumeSource

from .products import PRODUCTS

log = get_logger(__name__)

NO_CREDITS = "no_credits"
"""The ``reason`` of a 402: no pass, the free posting of the month is used, no credits left."""


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    """What paid for one use. ``credits`` and ``pass_until`` are the balance afterwards."""

    source: ConsumeSource
    credits: int
    pass_until: datetime | None
    replayed: bool = False
    """True when ``ref`` had already been paid for: nothing more was taken."""


@dataclass(frozen=True, slots=True)
class EntitlementSummary:
    credits: int
    pass_until: datetime | None
    """Only while the pass is still running; None otherwise."""
    free_match_available: bool


ZERO = EntitlementSummary(credits=0, pass_until=None, free_match_available=False)
"""What an anonymous visitor has: nothing, and no free posting (there is no account to spend it on)."""


def month_start(now: datetime) -> datetime:
    """00:00 UTC on the first day of ``now``'s month: the free allowance renews here."""
    utc = now.astimezone(UTC)
    return datetime(utc.year, utc.month, 1, tzinfo=UTC)


class EntitlementService:
    def __init__(self, repo: BillingRepo, settings: BillingSettings, clock: Clock) -> None:
        self._repo = repo
        self._settings = settings
        self._clock = clock

    async def consume(self, user_id: str, ref: str) -> ConsumeResult:
        """Pay for one use of ``ref`` (a match id), or raise `PaymentRequired`. See the module docstring."""
        now = self._clock.now()
        outcome = await self._repo.consume(
            user_id,
            ref,
            now=now,
            month_start=month_start(now),
            free_allowance=self._settings.free_matches_per_month,
        )
        if outcome is None:
            raise PaymentRequired(NO_CREDITS)
        if not outcome.replayed:
            log.info("entitlement_used", source=outcome.source, credits_left=outcome.credits)
        return ConsumeResult(outcome.source, outcome.credits, outcome.pass_until, outcome.replayed)

    async def refund(self, user_id: str, ref: str) -> bool:
        """Give back what ``consume`` took for ``ref``; False when there was nothing to give back."""
        given_back = await self._repo.release(user_id, ref, now=self._clock.now())
        if given_back:
            log.info("entitlement_refunded")
        return given_back

    async def grant(self, user_id: str, product: Product, order_id: str) -> bool:
        """Add what ``product`` includes, once per ``order_id``; False when that order was already granted."""
        spec = PRODUCTS[product]
        granted = await self._repo.grant(
            user_id,
            credits=spec.credits,
            pass_days=spec.pass_days,
            ref=order_id,
            now=self._clock.now(),
        )
        log.info("entitlement_granted" if granted else "entitlement_grant_repeated", product=product)
        return granted

    async def summary(self, user_id: str) -> EntitlementSummary:
        now = self._clock.now()
        balance = await self._repo.entitlements(user_id, month_start=month_start(now))
        running = balance.pass_until is not None and balance.pass_until > now
        return EntitlementSummary(
            credits=balance.credits,
            pass_until=balance.pass_until if running else None,
            free_match_available=balance.free_used < self._settings.free_matches_per_month,
        )


__all__ = ["NO_CREDITS", "ZERO", "ConsumeResult", "EntitlementService", "EntitlementSummary", "month_start"]
