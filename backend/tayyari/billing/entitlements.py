"""What an account may use: the one place that decides whether a posting match or an auto-apply application is paid.

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

Auto-apply pays in two steps, on the same append-only ledger (no table of its own). The ``ref`` is always
``apply:<application id>``, so no column is needed and every call is idempotent:

    hold(user_id, ref)      at the start. One of the account's lifetime free applications (``apply_free``, delta 0,
                            while fewer than ``free_applications`` are in use), else ``apply_credit_cost`` credits
                            (``apply_hold``), else `PaymentRequired` (402). While no price is set
                            (``apply_credit_cost`` is None, decision 2) the ``apply_hold`` row takes nothing, so hold,
                            capture and release all work and nothing is sold. Asking again for a ``ref`` that holds
                            takes nothing more.
    capture(user_id, ref)   charge the hold once the application is sent (an ``apply_capture`` row, delta 0). After
                            that it is never given back by ``release``.
    release(user_id, ref)   give the hold back: the credit returns, a free application counts as unused again.

Who calls what. The start route holds, after the start checks pass and before the row is inserted, and releases if
the insert fails. The engine captures right after the move to ``sent``, and releases on ``failed`` and ``closed``.
`apply.service` releases on ``cancelled`` and when the person resolves a ``check_this_one`` as not sent (resolved as
sent, it captures); until then ``check_this_one`` keeps the hold. The maintenance sweep reconciles a capture or release
that failed, which is why both are idempotent. Unlimited accounts skip the hold, so their capture and release return
False (nothing is held). Apply never imports billing: it sees this class as `apply.seams.ApplyCredits`.

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
"""The ``reason`` of a 402: no pass, the free posting of the month is used, no credits left. For an application: the
free applications are used and the credits do not cover ``apply_credit_cost``."""

APPLY_REF_PREFIX = "apply:"
"""Every auto-apply hold is keyed ``apply:<application id>``."""

NO_APPLY_CREDITS_MESSAGE = "You have used your free applications. Choose a plan to continue."
"""The sentence a refused application hold shows (the default one speaks of the month's free posting)."""


def apply_ref(application_id: str) -> str:
    """The ledger ``ref`` of the credit hold for one application."""
    return f"{APPLY_REF_PREFIX}{application_id}"


def _check_apply_ref(ref: str) -> None:
    """A hold is only ever keyed ``apply:<application id>``: a bare id would be a hold the maintenance sweep never finds,
    and a match id could share a ledger ref with a consume."""
    if not ref.startswith(APPLY_REF_PREFIX) or len(ref) == len(APPLY_REF_PREFIX):
        raise ValueError("an application hold's ref is apply:<application id>")


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

    # ───────────────────────── auto-apply credit hold ─────────────────────────

    async def hold(self, user_id: str, ref: str) -> None:
        """Hold the credit for one application (``ref`` is ``apply:<application id>``), or raise `PaymentRequired`.
        A free application first, else ``apply_credit_cost`` credits (nothing while the price is unset). See the
        module docstring for who calls this and when.

        Returns nothing, as `apply.seams.ApplyCredits` asks: the caller only needs to know the hold was taken. What
        paid for it (``free_apply``, ``credit`` or ``hold``) is on the ledger and in the log."""
        _check_apply_ref(ref)
        outcome = await self._repo.hold(
            user_id,
            ref,
            now=self._clock.now(),
            free_allowance=self._settings.free_applications,
            cost=self._settings.apply_credit_cost,
        )
        if outcome is None:
            log.info("apply_hold_refused")
            raise PaymentRequired(NO_CREDITS, user_message=NO_APPLY_CREDITS_MESSAGE)
        if outcome.replayed:
            log.info("apply_hold_repeated", source=outcome.source)
        else:
            log.info("apply_hold_taken", source=outcome.source, credits_left=outcome.credits)

    async def capture(self, user_id: str, ref: str) -> bool:
        """Charge the hold for ``ref`` once the application is sent. True when it is charged (by this call or an
        earlier one); False when nothing is held (never taken, released, or an unlimited account)."""
        _check_apply_ref(ref)
        charged = await self._repo.capture(user_id, ref, now=self._clock.now())
        log.info("apply_hold_captured" if charged else "apply_hold_capture_nothing_held")
        return charged

    async def release(self, user_id: str, ref: str) -> bool:
        """Give the hold for ``ref`` back. True when something was given back; False when nothing is held (never
        taken, already released, an unlimited account) or the hold was captured (a charge is never given back)."""
        _check_apply_ref(ref)
        given_back = await self._repo.release(user_id, ref, now=self._clock.now())
        log.info("apply_hold_released" if given_back else "apply_hold_release_nothing_held")
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
        """The balance the account page shows. Free applications left is not in it yet: the billing contract
        (``shared/billing.ts``) has no field for it, and it waits on the price (decision 2)."""
        now = self._clock.now()
        balance = await self._repo.entitlements(user_id, month_start=month_start(now))
        running = balance.pass_until is not None and balance.pass_until > now
        return EntitlementSummary(
            credits=balance.credits,
            pass_until=balance.pass_until if running else None,
            free_match_available=balance.free_used < self._settings.free_matches_per_month,
        )


__all__ = [
    "APPLY_REF_PREFIX",
    "NO_APPLY_CREDITS_MESSAGE",
    "NO_CREDITS",
    "ZERO",
    "ConsumeResult",
    "EntitlementService",
    "EntitlementSummary",
    "apply_ref",
    "month_start",
]
