"""Payments and entitlements: what can be bought, who has paid, and what an account may still use.

    products      the two products (`pack3`, `pass30`), prices from settings, which currency to show first
    entitlements  `EntitlementService`: `consume` / `refund` / `grant` / `summary`, the single place that decides
                  whether a posting match is paid for (pass, then the month's free posting, then a credit)
    service       `BillingService`: checkout, the browser hand-off, webhooks; both providers settle one way
    razorpay      Razorpay through the ``razorpay`` SDK (orders, Checkout.js hand-off signature, webhook signature)
    stripe_gw     Stripe through the ``stripe`` package (Checkout Session, ``construct_event``)
    gateway       the shared `PaymentEvent` and errors

Sits between `contract` and `api`: it reads storage only through the Protocols in `storage.ports`. The container
builds one `EntitlementService` and one `BillingService` per process; `container.entitlements.consume(user_id, ref)`
is what the posting-match routes call.
"""

from tayyari.billing.entitlements import ConsumeResult, EntitlementService, EntitlementSummary
from tayyari.billing.service import BillingService

__all__ = ["BillingService", "ConsumeResult", "EntitlementService", "EntitlementSummary"]
