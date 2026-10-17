"""`BillingService`: the one object the billing routes talk to.

    entitlements_view   what the pricing page needs: the visitor's balance, the currency to show first, the prices
    checkout            create the order at the provider for the visitor's currency and store ours
    verify_razorpay     the browser hand-off after Checkout.js: signature checked, order settled, balance returned
    webhook             a provider's webhook: signature checked first, then settled (or marked failed)

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
from typing import Protocol

from tayyari.contract.billing import (
    CheckoutResponse,
    Currency,
    EntitlementsResponse,
    PriceBook,
    Product,
    Provider,
    VerifyResponse,
)
from tayyari.core.clock import Clock
from tayyari.core.errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.storage.ports import BillingRepo, SettleStatus, StoredOrder, StoredUser

from .entitlements import ZERO, EntitlementService, EntitlementSummary
from .gateway import InvalidSignature, PaymentEvent, PaymentProviderError
from .products import PRODUCTS, default_currency, on_sale, price_book, product_def
from .razorpay import RazorpayOrder
from .stripe_gw import StripeSession

log = get_logger(__name__)

NOT_OFFERED = "That plan is not on sale yet."
UNAVAILABLE = "Payments are unavailable right now. Please try again in a moment."
SIGNATURE_REFUSED = "That payment could not be verified."
BRAND = "Tayyari"


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
    }


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
    ) -> None:
        self._settings = settings
        self._repo = repo
        self.entitlements = entitlements
        self._clock = clock
        self._razorpay = razorpay
        self._stripe = stripe

    # ───────────────────────── what the page shows ─────────────────────────

    def prices(self) -> PriceBook:
        """The products on sale, by currency."""
        return price_book(self._settings)

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
            }
        )

    # ───────────────────────── checkout ─────────────────────────

    async def checkout(self, user: StoredUser, product: Product, currency: Currency) -> CheckoutResponse:
        """Create the order at the provider for ``currency`` and store ours. 409 when the plan is not on sale there."""
        price = on_sale(self._settings, product, currency)
        if price is None:
            raise Conflict(NOT_OFFERED, code="not_offered", detail=f"{product} {currency}")
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
        return VerifyResponse.model_validate(
            {"ok": status in ("granted", "already_paid"), **_summary_fields(summary)}
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


__all__ = ["BillingService", "PaymentEvent", "RazorpayPort", "StripePort"]
