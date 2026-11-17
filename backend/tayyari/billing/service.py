"""`BillingService`: the one object the billing routes talk to.

    entitlements_view   what the pricing page needs: the visitor's balance, the currency to show first, the prices,
                        the plan, Just Looking's counts and the plans' offer (roadmap B2)
    checkout            create the order at the provider for the visitor's currency and store ours (Day One at
                        `PlansSettings.dayone_price` for this moment; 409 ``already_yours`` on a Day One account)
    verify_razorpay     the browser hand-off after Checkout.js: signature checked, order settled, balance, plan and
                        receipt returned
    orders              this account's paid orders as receipts
    notify              "Tell me when it opens" for a closed plan (stored; no email is sent by this build)
    webhook             a provider's webhook: signature checked first, then settled (or marked failed)

A receipt's number is ``LF-<yymmdd of the day paid, UTC>-<first six characters of the order id, upper case>``
(`receipt_number`): computed from the order, nothing new is stored.

Both providers end in `_settle`, which is idempotent on the provider's order id: the webhook, its retries and the
browser hand-off can arrive in any order and any number of times, and the order is paid (and granted) exactly once. For
Razorpay the hand-off is Razorpay's own signed proof under the key secret, so it settles the order at once; the webhook,
when its secret is set, covers the browser that never came back. Stripe's hosted page has no hand-off, so its webhook
is the only way a Stripe payment is settled.

Nothing here logs an email address, a key, a signature or a response body; the log context carries the account id only.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import UTC
from typing import Protocol

from tayyari.contract.billing import (
    CheckoutResponse,
    ClosedPlan,
    Currency,
    EntitlementsResponse,
    NotifyResponse,
    OrdersResponse,
    PlanOffer,
    PriceBook,
    Product,
    Provider,
    Receipt,
    VerifyResponse,
)
from tayyari.core.clock import Clock
from tayyari.core.errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.storage.ports import BillingRepo, SettleStatus, StoredOrder, StoredUser

from .entitlements import ZERO, EntitlementService, EntitlementSummary
from .gateway import InvalidSignature, PaymentEvent, PaymentProviderError
from .plans import PlanService
from .products import PRODUCTS, default_currency, on_sale, price_book, product_def, saved_currency
from .razorpay import RazorpayOrder
from .stripe_gw import StripeSession

log = get_logger(__name__)

NOT_OFFERED = "That plan is not on sale yet."
ALREADY_YOURS = "Day One is already yours."
CLOSED_PLANS: tuple[ClosedPlan, ...] = ("lowkey", "locked_in")
UNAVAILABLE = "Payments are unavailable right now. Please try again in a moment."
SIGNATURE_REFUSED = "That payment could not be verified."
BRAND = "Leafout"


class RazorpayPort(Protocol):
    @property
    def key_id(self) -> str: ...

    @property
    def webhooks_enabled(self) -> bool: ...

    async def create_order(
        self, *, amount_minor: int, currency: str, receipt: str, notes: Mapping[str, str]
    ) -> RazorpayOrder: ...

    def verify_checkout(self, order_id: str, payment_id: str, signature: str) -> bool: ...

    def parse_webhook(self, raw: bytes, signature: str | None) -> PaymentEvent: ...


class StripePort(Protocol):
    async def create_session(
        self,
        *,
        order_id: str,
        user_id: str,
        product: str,
        title: str,
        amount_minor: int,
        currency: str,
        success_url: str,
        cancel_url: str,
        customer_email: str | None,
    ) -> StripeSession: ...

    def parse_webhook(self, raw: bytes, signature: str | None) -> PaymentEvent: ...


def _summary_fields(summary: EntitlementSummary) -> dict[str, object]:
    return {
        "credits": summary.credits,
        "passUntil": None if summary.pass_until is None else summary.pass_until.isoformat(),
        "freeMatchAvailable": summary.free_match_available,
        "plan": summary.plan,
    }


def receipt_number(order: StoredOrder) -> str:
    """``LF-<yymmdd of the day paid, UTC>-<first six characters of the order id, upper case>``."""
    paid = (order.paid_at or order.created_at).astimezone(UTC)
    return f"LF-{paid.strftime('%y%m%d')}-{order.id[:6].upper()}"


def receipt_of(order: StoredOrder) -> Receipt | None:
    """The receipt of a paid order; None for an order that is not paid or names a product or currency we do not know."""
    spec = product_def(order.product)
    currency = saved_currency(order.currency)
    if order.status != "paid" or order.paid_at is None or spec is None or currency is None:
        return None
    paid = (order.raw or {}).get("paid")
    payment_id = paid.get("payment_id") if isinstance(paid, dict) else None
    return Receipt(
        number=receipt_number(order),
        product=spec.id,
        amount=order.amount_minor,
        currency=currency,
        paidAt=order.paid_at.isoformat(),
        paymentId=payment_id if isinstance(payment_id, str) and payment_id else None,
    )


class BillingService:
    def __init__(
        self,
        *,
        settings: Settings,
        repo: BillingRepo,
        entitlements: EntitlementService,
        clock: Clock,
        razorpay: RazorpayPort | None = None,
        stripe: StripePort | None = None,
        plans: PlanService | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self.entitlements = entitlements
        self._clock = clock
        self._razorpay = razorpay
        self._stripe = stripe
        self.plans = plans or PlanService(repo, settings, clock)

    # ───────────────────────── what the page shows ─────────────────────────

    def prices(self) -> PriceBook:
        """The products on sale now, by currency."""
        return price_book(self._settings, now=self._clock.now())

    async def offer(self, user: StoredUser | None) -> PlanOffer:
        """What the pricing page prints about the plans; ``notify`` is empty for a visitor."""
        now = self._clock.now()
        plans = self._settings.plans
        wanted = [] if user is None else await self._repo.interests(user.id)
        return PlanOffer(
            lowkey=plans.lowkey_inr,
            lockedIn=plans.locked_in_inr,
            dayOne=on_sale(self._settings, "dayone", "INR", now=now),
            dayOneEarly=plans.dayone_inr,
            dayOneAfter=plans.dayone_after_inr,
            dayOneEndsAt=plans.dayone_ends_at.isoformat(),
            early=plans.dayone_early(now),
            refundDays=plans.refund_days,
            notify=[plan for plan in CLOSED_PLANS if plan in wanted],
        )

    def currency_for(
        self, user: StoredUser | None, *, accept_language: str | None, timezone: str | None
    ) -> Currency:
        return default_currency(
            saved=None if user is None else user.currency, accept_language=accept_language, timezone=timezone
        )

    async def entitlements_view(
        self, user: StoredUser | None, *, accept_language: str | None = None, timezone: str | None = None
    ) -> EntitlementsResponse:
        summary = ZERO if user is None else await self.entitlements.summary(user.id)
        unlimited = user is not None and self._settings.guard.is_unlimited(user.email)
        return EntitlementsResponse.model_validate(
            {
                **_summary_fields(summary),
                "unlimited": True if unlimited else None,
                "currency": self.currency_for(user, accept_language=accept_language, timezone=timezone),
                "prices": self.prices().model_dump(exclude_none=True),
                "usage": (await self.plans.usage(None if user is None else user.id)).model_dump(),
                "resetsAt": self.plans.resets_at().isoformat(),
                "offer": (await self.offer(user)).model_dump(exclude_none=True),
            }
        )

    async def orders(self, user: StoredUser) -> OrdersResponse:
        """This account's paid orders as receipts, newest first."""
        found = (receipt_of(order) for order in await self._repo.paid_orders(user.id))
        return OrdersResponse(orders=[receipt for receipt in found if receipt is not None])

    async def notify(self, user: StoredUser, plan: ClosedPlan, *, on: bool) -> NotifyResponse:
        """Ask to be told (``on``) or stop asking when ``plan`` opens; answers with the closed plans asked about now."""
        await self._repo.set_interest(user.id, plan, on=on, now=self._clock.now())
        log.info("plan_interest_set", plan=plan, on=on)
        wanted = await self._repo.interests(user.id)
        return NotifyResponse(notify=[closed for closed in CLOSED_PLANS if closed in wanted])

    # ───────────────────────── checkout ─────────────────────────

    async def checkout(self, user: StoredUser, product: Product, currency: Currency) -> CheckoutResponse:
        """Create the order at the provider for ``currency`` and store ours. 409 when the plan is not on sale there."""
        price = on_sale(self._settings, product, currency, now=self._clock.now())
        if price is None:
            raise Conflict(NOT_OFFERED, code="not_offered", detail=f"{product} {currency}")
        if product == "dayone" and await self.plans.plan_of(user.id) == "day_one":
            raise Conflict(ALREADY_YOURS, code="already_yours")
        spec = PRODUCTS[product]
        order_id = secrets.token_hex(16)
        notes = {"order": order_id, "user": user.id, "product": product}
        try:
            if currency == "INR":
                razorpay = self._require(self._razorpay)
                order = await razorpay.create_order(
                    amount_minor=price, currency=currency, receipt=order_id, notes=notes
                )
                provider: Provider = "razorpay"
                provider_order_id = order.id
                response = CheckoutResponse(
                    provider="razorpay",
                    orderId=order.id,
                    amount=price,
                    currency=currency,
                    keyId=razorpay.key_id,
                    name=BRAND,
                    description=spec.title,
                    prefillEmail=user.email,
                )
            else:
                stripe = self._require(self._stripe)
                base = self._settings.auth.public_url
                session = await stripe.create_session(
                    order_id=order_id,
                    user_id=user.id,
                    product=product,
                    title=spec.title,
                    amount_minor=price,
                    currency=currency,
                    success_url=f"{base}/pricing?checkout=success",
                    cancel_url=f"{base}/pricing?checkout=cancelled",
                    customer_email=user.email,
                )
                provider = "stripe"
                provider_order_id = session.id
                response = CheckoutResponse(provider="stripe", url=session.url)
        except PaymentProviderError as err:
            log.error("checkout_provider_failed", currency=currency, reason=str(err))
            raise ServiceUnavailable(UNAVAILABLE, code="payment_provider_unavailable") from None
        await self._repo.add_order(
            StoredOrder(
                id=order_id,
                user_id=user.id,
                provider=provider,
                provider_order_id=provider_order_id,
                product=product,
                currency=currency,
                amount_minor=price,
                status="created",
                created_at=self._clock.now(),
                raw={"provider_order_id": provider_order_id},
            )
        )
        log.info("checkout_started", provider=provider, product=product, currency=currency)
        return response

    @staticmethod
    def _require[T](gateway: T | None) -> T:
        """The gateway for a provider that `on_sale` just said is configured."""
        if gateway is None:  # pragma: no cover - on_sale and the container agree on what is configured
            raise ServiceUnavailable(UNAVAILABLE, code="payments_not_configured")
        return gateway

    # ───────────────────────── paid ─────────────────────────

    async def verify_razorpay(
        self, user: StoredUser, *, order_id: str, payment_id: str, signature: str
    ) -> VerifyResponse:
        """Checkout.js's hand-off: the signature must be Razorpay's (made with the key secret) and the order must be
        this account's; then the order is settled and the new balance returned. The webhook covers the rest."""
        gateway = self._razorpay
        if gateway is None:
            raise Conflict(NOT_OFFERED, code="not_offered", detail="razorpay is not configured")
        if not gateway.verify_checkout(order_id, payment_id, signature):
            log.warning("handoff_signature_invalid", provider="razorpay")
            raise BadRequest(SIGNATURE_REFUSED, code="invalid_signature")
        order = await self._repo.get_order_by_provider_id(order_id)
        if order is None or order.user_id != user.id or order.provider != "razorpay":
            raise NotFound("Order not found")
        status = await self._settle("razorpay", order_id, payment_id=payment_id, event="checkout.handoff")
        summary = await self.entitlements.summary(user.id)
        ok = status in ("granted", "already_paid")
        settled = await self._repo.get_order_by_provider_id(order_id) if ok else None
        receipt = None if settled is None else receipt_of(settled)
        return VerifyResponse.model_validate(
            {
                "ok": ok,
                **_summary_fields(summary),
                "receipt": None if receipt is None else receipt.model_dump(exclude_none=True),
            }
        )

    async def webhook(self, provider: Provider, raw: bytes, signature: str | None) -> str:
        """Handle a provider's webhook; returns what happened (for the log and the tests).

        The signature is checked before anything else, including parsing. Raises 503 when the provider (or, for
        Razorpay, its webhook secret) is not configured here and 400 for a signature that does not check out; every
        other outcome is a normal return, so the provider stops retrying.
        """
        gateway: RazorpayPort | StripePort | None = self._razorpay if provider == "razorpay" else self._stripe
        if gateway is None:
            log.warning("webhook_for_unconfigured_provider", provider=provider)
            raise ServiceUnavailable("Payments are not set up here.", code="payments_not_configured")
        if provider == "razorpay" and self._razorpay is not None and not self._razorpay.webhooks_enabled:
            log.warning("webhook_without_secret", provider=provider)
            raise ServiceUnavailable("Webhooks are not set up here.", code="webhooks_not_configured")
        try:
            event = gateway.parse_webhook(raw, signature)
        except InvalidSignature:
            log.warning("webhook_signature_invalid", provider=provider)
            raise BadRequest(SIGNATURE_REFUSED, code="invalid_signature") from None
        if event.kind == "ignored" or event.provider_order_id is None:
            log.info("webhook_ignored", provider=provider, provider_event=event.event)
            return "ignored"
        if event.kind == "failed":
            changed = await self._repo.fail_order(event.provider_order_id)
            log.info("webhook_payment_failed", provider=provider, provider_event=event.event, changed=changed)
            return "failed"
        return await self._settle(
            provider,
            event.provider_order_id,
            payment_id=event.payment_id,
            amount_minor=event.amount_minor,
            currency=event.currency,
            event=event.event,
        )

    async def _settle(
        self,
        provider: Provider,
        provider_order_id: str,
        *,
        payment_id: str | None,
        event: str,
        amount_minor: int | None = None,
        currency: str | None = None,
    ) -> SettleStatus | str:
        order = await self._repo.get_order_by_provider_id(provider_order_id)
        if order is None:
            log.warning("payment_for_unknown_order", provider=provider, provider_event=event)
            return "unknown_order"
        spec = product_def(order.product)
        if order.provider != provider or spec is None:
            log.error("payment_order_inconsistent", provider=provider, order_provider=order.provider)
            return "mismatch"
        raw = {**(order.raw or {}), "paid": {"payment_id": payment_id, "event": event}}
        result = await self._repo.settle_order(
            provider_order_id,
            now=self._clock.now(),
            credits=spec.credits,
            pass_days=spec.pass_days,
            raw=raw,
            amount_minor=amount_minor,
            currency=currency,
        )
        fields = {"provider": provider, "product": order.product, "provider_event": event}
        if result.status == "granted":
            log.info("order_paid", **fields)
        elif result.status == "already_paid":
            log.info("order_paid_again", **fields)
        else:
            log.error("order_not_settled", status=result.status, **fields)
        return result.status


__all__ = [
    "ALREADY_YOURS",
    "CLOSED_PLANS",
    "BillingService",
    "PaymentEvent",
    "RazorpayPort",
    "StripePort",
    "receipt_number",
    "receipt_of",
]
