"""What can be bought, what it costs, and which currency a visitor is shown first.

One-time products, no subscription and no auto-renew:

    dayone  the Day One plan (roadmap B2): a pass of `LIFETIME_DAYS`, so everything with no plan limits, for good
    pack3   three posting matches, no expiry (dormant: never priced, kept so an old order still settles)
    pass30  unlimited postings for 30 days from purchase (dormant, as pack3)

Prices are settings in minor units; 0 means unset. Day One's come from `core.settings.PlansSettings.dayone_price`, which
picks the early or the after price by the moment asked about; the dormant two from `core.settings.BillingSettings`. A product is *on sale* in a
currency only when its price is set, is at least the provider's minimum (`MIN_MINOR`) and the payment provider for
that currency (INR: Razorpay, USD: Stripe) is configured. Everything that offers or sells a product asks `on_sale`, so
the web app, the checkout route and the entitlements endpoint can never disagree.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, get_args

from tayyari.contract.billing import Currency, PriceBook, Product, ProductPrices, Provider
from tayyari.core.settings import Settings

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


LIFETIME_DAYS: Final = 36_500
"""How long Day One's pass runs: a hundred years, so for good."""
LIFETIME_MIN_DAYS: Final = 7_300
"""A pass that runs more than this many days past now is Day One's (no 30-day pass can stack this far)."""

PRODUCTS: Final[Mapping[Product, ProductDef]] = {
    "pack3": ProductDef("pack3", credits=3, pass_days=0, title="Leafout: three postings"),
    "pass30": ProductDef("pass30", credits=0, pass_days=PASS_DAYS, title="Leafout: 30-day pass"),
    "dayone": ProductDef("dayone", credits=0, pass_days=LIFETIME_DAYS, title="Leafout: Day One"),
}
CURRENCIES: Final[tuple[Currency, ...]] = get_args(Currency)
PROVIDER_FOR: Final[Mapping[Currency, Provider]] = {"INR": "razorpay", "USD": "stripe"}
MIN_MINOR: Final[Mapping[Currency, int]] = {"INR": 100, "USD": 50}
"""The smallest price each provider accepts, in minor units: Razorpay rejects an order under 100 paise (INR 1) and
Stripe a Checkout Session under 50 cents (USD 0.50). A price below it would only end as a 503 at checkout, so the
product is not put on sale."""


def product_def(name: str) -> ProductDef | None:
    """The product called ``name`` (as stored on an order), or None for a name this build does not know."""
    return next((spec for key, spec in PRODUCTS.items() if key == name), None)


def is_lifetime(pass_until: datetime | None, now: datetime) -> bool:
    """True when a pass runs more than `LIFETIME_MIN_DAYS` past ``now``: the account is on Day One."""
    return pass_until is not None and pass_until > now + timedelta(days=LIFETIME_MIN_DAYS)


# ───────────────────────── prices ─────────────────────────


def _now(now: datetime | None) -> datetime:
    return datetime.now(UTC) if now is None else now


def price_minor(
    settings: Settings, product: Product, currency: Currency, *, now: datetime | None = None
) -> int:
    """The configured price in minor units at ``now`` (default: this moment); 0 when it is not set. Day One asks
    `PlansSettings.dayone_price`, so the early price ends exactly where the settings say."""
    if product == "dayone":
        return settings.plans.dayone_price(_now(now), currency)
    value: int = getattr(settings.billing, f"{product}_{currency.lower()}")
    return value


def _configured(settings: Settings, product: Product, currency: Currency) -> tuple[int, ...]:
    """Every price set for ``product`` in ``currency``: Day One has two (early and after), the others one."""
    if product == "dayone":
        plans = settings.plans
        return (
            (plans.dayone_inr, plans.dayone_after_inr)
            if currency == "INR"
            else (plans.dayone_usd, plans.dayone_after_usd)
        )
    return (price_minor(settings, product, currency),)


def provider_ready(settings: Settings, currency: Currency) -> bool:
    """True when the provider that takes ``currency`` has every secret it needs."""
    return settings.razorpay_enabled if PROVIDER_FOR[currency] == "razorpay" else settings.stripe_enabled


def on_sale(
    settings: Settings, product: Product, currency: Currency, *, now: datetime | None = None
) -> int | None:
    """The price in minor units when ``product`` can be bought in ``currency`` at ``now`` (default: this moment), else
    None.

    Not on sale: no price set, a price under the provider's minimum (`MIN_MINOR`), or a provider without its secrets.
    """
    price = price_minor(settings, product, currency, now=now)
    return price if price >= MIN_MINOR[currency] and provider_ready(settings, currency) else None


@dataclass(frozen=True, slots=True)
class PriceBelowFloor:
    """A configured price the provider would refuse; `below_floor` lists them so startup can say so."""

    product: Product
    currency: Currency
    price_minor: int
    minimum_minor: int


def below_floor(settings: Settings) -> list[PriceBelowFloor]:
    """Every configured price (above 0) that is under the provider's minimum: those products are not on sale."""
    found: list[PriceBelowFloor] = []
    for product in PRODUCTS:
        for currency in CURRENCIES:
            for price in _configured(settings, product, currency):
                if 0 < price < MIN_MINOR[currency]:
                    found.append(PriceBelowFloor(product, currency, price, MIN_MINOR[currency]))
    return found


def price_book(settings: Settings, *, now: datetime | None = None) -> PriceBook:
    """Every product that is on sale at ``now``, by currency (a product that is not on sale has no key)."""
    moment = _now(now)

    def prices(currency: Currency) -> ProductPrices:
        return ProductPrices(
            pack3=on_sale(settings, "pack3", currency, now=moment),
            pass30=on_sale(settings, "pass30", currency, now=moment),
            dayone=on_sale(settings, "dayone", currency, now=moment),
        )

    return PriceBook(INR=prices("INR"), USD=prices("USD"))


def _group(whole: int, currency: Currency) -> str:
    """``1234567`` as "12,34,567" (INR, en-IN) or "1,234,567" (USD, en-US)."""
    digits = str(whole)
    if currency != "INR" or len(digits) <= 3:
        return f"{whole:,}"
    head, tail = digits[:-3], digits[-3:]
    pairs: list[str] = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *pairs, tail] if head else [*pairs, tail])


def format_price(amount_minor: int, currency: Currency) -> str:
    """``49900`` paise as "₹499", ``900`` cents as "$9", ``1999`` as "$19.99": the same words as ``src/billing/format.ts``.

    Amounts are minor units, always integers; a whole amount has no decimals, any other has two.
    """
    symbol = "₹" if currency == "INR" else "$"
    whole, cents = divmod(amount_minor, 100)
    grouped = _group(whole, currency)
    return f"{symbol}{grouped}" if cents == 0 else f"{symbol}{grouped}.{cents:02d}"


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
    "LIFETIME_DAYS",
    "LIFETIME_MIN_DAYS",
    "MIN_MINOR",
    "PASS_DAYS",
    "PRODUCTS",
    "PROVIDER_FOR",
    "PriceBelowFloor",
    "ProductDef",
    "below_floor",
    "default_currency",
    "format_price",
    "is_indian_locale",
    "is_indian_timezone",
    "is_lifetime",
    "on_sale",
    "price_book",
    "price_minor",
    "product_def",
    "provider_ready",
    "saved_currency",
]
