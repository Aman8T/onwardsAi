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

Product = Literal["pack3", "pass30"]
Provider = Literal["razorpay", "stripe"]

TIMEZONE_MAX_CHARS: Final = 64
"""The longest IANA time zone name a client may send (``America/Argentina/ComodRivadavia`` is 32)."""


class ProductPrices(_Model):
    """What each product costs in one currency; a product that is not on sale has no key."""

    pack3: int | None = None
    pass30: int | None = None


class PriceBook(_Model):
    """The prices on offer, by currency."""

    INR: ProductPrices
    USD: ProductPrices


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


__all__ = [
    "TIMEZONE_MAX_CHARS",
    "CheckoutRequest",
    "CheckoutResponse",
    "Currency",
    "EntitlementsResponse",
    "PriceBook",
    "Product",
    "ProductPrices",
    "Provider",
    "VerifyRequest",
    "VerifyResponse",
]
