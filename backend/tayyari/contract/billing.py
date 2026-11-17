"""Pydantic mirror of shared/billing.ts: prices, entitlements, checkout and the payment hand-off.

Same rules as `tayyari.contract.auth`: camelCase field names that are the JSON keys (the two ``razorpay_*``
names are Razorpay's own, which Checkout.js hands to the page and the page hands to us), a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and
`tests/unit/contract/test_billing_parity.py` fails when the two drift.

Money is an integer in the currency's minor unit (paise, cents). A price is *absent* when the product is not on
sale in that currency: its price is unset, or the payment provider for that currency is not configured.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/billing.ts)

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from tayyari.contract.auth import Currency
from tayyari.contract.models import _Model

Product = Literal["pack3", "pass30", "dayone"]
"""``dayone``: the Day One plan, one payment, everything with no plan limits (roadmap B2). ``pack3`` and ``pass30`` are the
earlier products: still understood for an order that names one, never on sale (no price is set)."""
Provider = Literal["razorpay", "stripe"]
PlanId = Literal["just_looking", "day_one"]
"""The plan an account is on. Lowkey and Locked In are not sold yet, so no account is on them."""
ClosedPlan = Literal["lowkey", "locked_in"]
"""The two monthly plans the pricing page shows with a price and "Opening soon"."""
Feature = Literal["analyses", "matches", "searches", "practice", "coach", "applications"]
"""What Just Looking counts: each per calendar month (UTC), except ``applications``, counted once."""

TIMEZONE_MAX_CHARS: Final = 64
"""The longest IANA time zone name a client may send (``America/Argentina/ComodRivadavia`` is 32)."""


class ProductPrices(_Model):
    """What each product costs in one currency; a product that is not on sale has no key."""

    pack3: int | None = None
    pass30: int | None = None
    dayone: int | None = None


class PriceBook(_Model):
    """The prices on offer, by currency."""

    INR: ProductPrices
    USD: ProductPrices


class FeatureUse(_Model):
    """One counted feature: the Just Looking limit and how many this account has used in the current period."""

    limit: int
    used: int


class PlanUsage(_Model):
    analyses: FeatureUse
    matches: FeatureUse
    searches: FeatureUse
    practice: FeatureUse
    coach: FeatureUse
    applications: FeatureUse


class PlanOffer(_Model):
    """What the pricing page prints; every amount is INR paise from `core.settings.PlansSettings`.

    ``dayOne`` is what Day One costs right now, present only while it can be bought here (Razorpay configured).
    ``early`` says which side of ``dayOneEndsAt`` the server is on."""

    lowkey: int
    lockedIn: int
    dayOne: int | None = None
    dayOneEarly: int
    dayOneAfter: int
    dayOneEndsAt: str
    early: bool
    refundDays: int
    notify: list[ClosedPlan]


class Receipt(_Model):
    """A paid order, as the bill and "Your plan" print it. ``number`` is made from the day it was paid and the order id."""

    number: str
    product: Product
    amount: int
    currency: Currency
    paidAt: str
    paymentId: str | None = None


class OrdersResponse(_Model):
    """``GET /api/billing/orders``: this account's paid orders, newest first."""

    orders: list[Receipt]


class NotifyRequest(_Model):
    """``PUT /api/billing/notify``: ask to be told (or not) when a closed plan opens."""

    plan: ClosedPlan
    on: bool


class NotifyResponse(_Model):
    notify: list[ClosedPlan]


class LimitReached(_Model):
    """The body of a 402 when a Just Looking count has run out (`core.errors.PaymentRequired.to_body`)."""

    error: str
    reason: str
    feature: Feature | None = None
    limit: int | None = None
    resetsAt: str | None = None


class EntitlementsResponse(_Model):
    """``GET /api/billing/entitlements``: what the visitor may do and what is on sale.

    An anonymous visitor gets zero credits and no free match (they have no account to spend them on) and the same
    prices as everyone. ``currency`` is the one the page should show first (see `billing.products.default_currency`).
    """

    credits: int
    passUntil: str | None = None
    freeMatchAvailable: bool
    unlimited: bool | None = None
    """True for an account on the server's unlimited list (``TAYYARI_GUARD__UNLIMITED_EMAILS``): no limits, nothing charged.
    Left out of the JSON for everyone else (the field is ``unlimited?`` in the TypeScript contract)."""
    currency: Currency
    prices: PriceBook
    plan: PlanId
    """``just_looking`` for a visitor and for every account that has not bought Day One."""
    usage: PlanUsage
    """The Just Looking limits and what this account has used; a visitor has used nothing."""
    resetsAt: str
    """When the monthly counts start again (00:00 UTC on the first of next month)."""
    offer: PlanOffer


class CheckoutRequest(_Model):
    """``POST /api/billing/checkout``. ``currency`` falls back to the visitor's default when left out."""

    product: Product
    currency: Currency | None = None
    timezone: str | None = Field(default=None, max_length=TIMEZONE_MAX_CHARS)


class CheckoutResponse(_Model):
    """What the web app needs to take the payment.

    ``provider == "razorpay"``: open Checkout.js with ``keyId``, ``orderId``, ``amount`` and ``currency``
    (``name``, ``description`` and ``prefillEmail`` are the words and the address it shows).
    ``provider == "stripe"``: send the browser to ``url``.
    """

    provider: Provider
    orderId: str | None = None
    amount: int | None = None
    currency: Currency | None = None
    keyId: str | None = None
    name: str | None = None
    description: str | None = None
    prefillEmail: str | None = None
    url: str | None = None


class VerifyRequest(_Model):
    """``POST /api/billing/verify``: the three values Checkout.js passes to its success handler."""

    razorpay_order_id: str = Field(min_length=1, max_length=128)
    razorpay_payment_id: str = Field(min_length=1, max_length=128)
    razorpay_signature: str = Field(min_length=1, max_length=256)


class VerifyResponse(_Model):
    """The answer to a verified hand-off: the entitlements as they are now."""

    ok: bool
    credits: int
    passUntil: str | None = None
    freeMatchAvailable: bool
    plan: PlanId
    receipt: Receipt | None = None
    """The paid order, once it is settled (``ok``)."""


__all__ = [
    "TIMEZONE_MAX_CHARS",
    "CheckoutRequest",
    "CheckoutResponse",
    "ClosedPlan",
    "Currency",
    "EntitlementsResponse",
    "Feature",
    "FeatureUse",
    "LimitReached",
    "NotifyRequest",
    "NotifyResponse",
    "OrdersResponse",
    "PlanId",
    "PlanOffer",
    "PlanUsage",
    "PriceBook",
    "Product",
    "ProductPrices",
    "Provider",
    "Receipt",
    "VerifyRequest",
    "VerifyResponse",
]
