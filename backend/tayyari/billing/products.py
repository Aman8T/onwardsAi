"""What can be bought, what it costs, and which currency a visitor is shown first.

Two one-time products, no subscription and no auto-renew (a renewal is a new purchase):

    pack3   three posting matches, no expiry
    pass30  unlimited postings for 30 days from purchase

Prices are settings in minor units (`core.settings.BillingSettings`); 0 means unset. A product is *on sale* in a
currency only when its price is set and the payment provider for that currency (INR: Razorpay, USD: Stripe) is
configured. Everything that offers or sells a product asks `on_sale`, so the web app, the checkout route and the
entitlements endpoint can never disagree.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, get_args

from tayyari.contract.billing import Currency, PriceBook, Product, ProductPrices, Provider
from tayyari.core.settings import BillingSettings, Settings

PASS_DAYS: Final = 30
"""How long `pass30` lasts from the moment it is bought (or from the end of the pass already running)."""


@dataclass(frozen=True, slots=True)
class ProductDef:
    id: Product
    credits: int
    """Posting credits it adds (0 for a pass)."""
    pass_days: int
    """Days it extends the pass by (0 for a pack)."""
    title: str
    """Shown on the payment page and the receipt."""


PRODUCTS: Final[Mapping[Product, ProductDef]] = {
    "pack3": ProductDef("pack3", credits=3, pass_days=0, title="Tayyari: three postings"),
    "pass30": ProductDef("pass30", credits=0, pass_days=PASS_DAYS, title="Tayyari: 30-day pass"),
}
CURRENCIES: Final[tuple[Currency, ...]] = get_args(Currency)
PROVIDER_FOR: Final[Mapping[Currency, Provider]] = {"INR": "razorpay", "USD": "stripe"}


def product_def(name: str) -> ProductDef | None:
    """The product called ``name`` (as stored on an order), or None for a name this build does not know."""
    return next((spec for key, spec in PRODUCTS.items() if key == name), None)


# ───────────────────────── prices ─────────────────────────


def price_minor(billing: BillingSettings, product: Product, currency: Currency) -> int:
    """The configured price in minor units; 0 when it is not set."""
    value: int = getattr(billing, f"{product}_{currency.lower()}")
    return value


def provider_ready(settings: Settings, currency: Currency) -> bool:
    """True when the provider that takes ``currency`` has every secret it needs."""
    return settings.razorpay_enabled if PROVIDER_FOR[currency] == "razorpay" else settings.stripe_enabled


def on_sale(settings: Settings, product: Product, currency: Currency) -> int | None:
    """The price in minor units when ``product`` can be bought in ``currency`` now, else None."""
    price = price_minor(settings.billing, product, currency)
    return price if price > 0 and provider_ready(settings, currency) else None


def price_book(settings: Settings) -> PriceBook:
    """Every product that is on sale, by currency (a product that is not on sale has no key)."""

    def prices(currency: Currency) -> ProductPrices:
        return ProductPrices(
            pack3=on_sale(settings, "pack3", currency), pass30=on_sale(settings, "pass30", currency)
        )

    return PriceBook(INR=prices("INR"), USD=prices("USD"))


# ───────────────────────── currency ─────────────────────────

_INDIAN_TIMEZONES: Final = ("asia/kolkata", "asia/calcutta")
_INDIAN_LANGUAGES: Final = frozenset({"hi"})
"""Hindi is spoken almost only in India. Other Indian languages are shared with neighbouring countries (Bengali,
Tamil, Punjabi, Urdu), so they count only with the India region (``bn-IN``)."""


def _quality(params: str) -> float:
    """The ``q=`` weight of an ``Accept-Language`` item (1 when absent or unreadable)."""
    for part in params.split(";"):
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "q":
            try:
                return float(value)
            except ValueError:
                return 1.0
    return 1.0


def is_indian_locale(accept_language: str | None) -> bool:
    """True when an ``Accept-Language`` value lists an Indian locale: any ``-IN`` region (``en-IN``, ``ta-IN``) or Hindi.

    Quality weights are ignored except that ``q=0`` ("not acceptable") removes the tag.
    """
    for item in (accept_language or "").split(","):
        tag, _, params = item.partition(";")
        if _quality(params) <= 0:
            continue
        parts = tag.strip().replace("_", "-").lower().split("-")
        if parts[0] in _INDIAN_LANGUAGES or "in" in parts[1:]:
            return True
    return False


def is_indian_timezone(timezone: str | None) -> bool:
    """True for an IANA zone name that starts with ``Asia/Kolkata`` or its old name ``Asia/Calcutta``."""
    return (timezone or "").strip().lower().startswith(_INDIAN_TIMEZONES)


def saved_currency(value: str | None) -> Currency | None:
    """``value`` as a `Currency` when it is one we sell in, else None."""
    return "INR" if value == "INR" else ("USD" if value == "USD" else None)


def default_currency(*, saved: str | None, accept_language: str | None, timezone: str | None) -> Currency:
    """The currency to show first: the account's saved choice, else INR for an Indian locale or time zone, else USD."""
    chosen = saved_currency(saved)
    if chosen is not None:
        return chosen
    return "INR" if is_indian_timezone(timezone) or is_indian_locale(accept_language) else "USD"


__all__ = [
    "CURRENCIES",
    "PASS_DAYS",
    "PRODUCTS",
    "PROVIDER_FOR",
    "ProductDef",
    "default_currency",
    "is_indian_locale",
    "is_indian_timezone",
    "on_sale",
    "price_book",
    "price_minor",
    "product_def",
    "provider_ready",
    "saved_currency",
]
