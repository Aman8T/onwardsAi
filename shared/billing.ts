/**
 * Payments and entitlements: the contract between the billing routes (`/api/billing/*`) and the web app.
 *
 * Mirrored by backend/tayyari/contract/billing.py; tests/unit/contract/test_billing_parity.py parses this file
 * (together with shared/auth.ts, which owns `Currency`) and fails when the two drift. Keep to what that parser
 * reads: interfaces, literal unions, `x?:` optionals, `T[]`, inline objects. No `Record`, `null`, tuples or
 * `typeof` in types.
 *
 * Money is an integer in the currency's minor unit (paise, cents). A price is absent when the product is not on
 * sale in that currency: its price is unset on the server, or the payment provider for that currency is not
 * configured. The page shows such a product as "Coming soon".
 */
import type { Currency } from './auth';

export type { Currency };

/** `pack3`: three posting matches, no expiry. `pass30`: unlimited postings for 30 days from purchase. */
export type Product = 'pack3' | 'pass30';

/** INR is paid through Razorpay (Checkout.js in a modal), USD through Stripe (a redirect). */
export type Provider = 'razorpay' | 'stripe';

/** Longest IANA time zone name the client may send. */
export const TIMEZONE_MAX_CHARS = 64;

/** What each product costs in one currency; a product that is not on sale has no key. */
export interface ProductPrices {
  pack3?: number;
  pass30?: number;
}

export interface PriceBook {
  INR: ProductPrices;
  USD: ProductPrices;
}

/**
 * `GET /api/billing/entitlements`. An anonymous visitor gets zero credits and no free match, and the same prices as
 * everyone. `currency` is the one to show first: the account's saved choice, else INR for an Indian locale or time
 * zone, else USD.
 */
export interface EntitlementsResponse {
  credits: number;
  /** ISO timestamp until which every posting is included, when a pass is active. */
  passUntil?: string;
  freeMatchAvailable: boolean;
  currency: Currency;
  prices: PriceBook;
}

/** `POST /api/billing/checkout`. `currency` falls back to the visitor's default when left out. */
export interface CheckoutRequest {
  product: Product;
  currency?: Currency;
  /** IANA name from `Intl.DateTimeFormat().resolvedOptions().timeZone`; only used to guess a currency. */
  timezone?: string;
}

/**
 * What the web app needs to take the payment. Razorpay: open Checkout.js with `keyId`, `orderId`, `amount` and
 * `currency`. Stripe: send the browser to `url`.
 */
export interface CheckoutResponse {
  provider: Provider;
  orderId?: string;
  amount?: number;
  currency?: Currency;
  keyId?: string;
  name?: string;
  description?: string;
  prefillEmail?: string;
  url?: string;
}

/** `POST /api/billing/verify`: the three values Checkout.js passes to its success handler (Razorpay's own names). */
export interface VerifyRequest {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

/** The answer to a verified hand-off: the entitlements as they are now. */
export interface VerifyResponse {
  ok: boolean;
  credits: number;
  passUntil?: string;
  freeMatchAvailable: boolean;
}
