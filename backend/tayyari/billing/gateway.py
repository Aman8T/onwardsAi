"""What the two payment providers have in common: the events their webhooks are reduced to, and the errors.

A gateway turns one provider's webhook into a `PaymentEvent` after checking its signature. The service never sees a
provider's payload, only ``paid``, ``failed`` or ``ignored`` for one provider order id, which is what makes the two
webhooks share one idempotent settling path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PaymentEventKind = Literal["paid", "failed", "ignored"]


class PaymentProviderError(RuntimeError):
    """The provider refused a request or did not answer. The message carries the name of the exception class (and,
    for a rejected key, a hint) only: never a key, an address, the provider's description or the response body."""


class InvalidSignature(ValueError):
    """A webhook or hand-off whose signature does not check out. Nothing in it is to be trusted or even parsed."""


@dataclass(frozen=True, slots=True)
class PaymentEvent:
    """One webhook, after its signature was verified.

    ``provider_order_id`` is the id the order was stored under (Razorpay ``order_...``, Stripe ``cs_...``).
    ``amount_minor`` and ``currency`` (upper case) are what the provider says was paid, when it says; the service
    refuses to grant when they differ from the order.
    """

    kind: PaymentEventKind
    provider_order_id: str | None
    payment_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    event: str = ""
    """The provider's own event name, for the log."""


__all__ = ["InvalidSignature", "PaymentEvent", "PaymentEventKind", "PaymentProviderError"]
