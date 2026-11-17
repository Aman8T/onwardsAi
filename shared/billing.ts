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

/**
 * `dayone`: the Day One plan, one payment, everything with no plan limits (roadmap B2). `pack3` (three posting matches)
 * and `pass30` (30 days of postings) are the earlier products: still understood for an order that names one, never
 * on sale (no price is set) and offered by no screen.
 */
export type Product = 'pack3' | 'pass30' | 'dayone';

/** The plan an account is on. Lowkey and Locked In are not sold yet, so no account is on them. */
export type PlanId = 'just_looking' | 'day_one';

/** The two monthly plans the pricing page shows with a price and "Opening soon". */
export type ClosedPlan = 'lowkey' | 'locked_in';

/** What Just Looking counts. Each is per calendar month (UTC), except `applications`, which is counted once. */
export type Feature = 'analyses' | 'matches' | 'searches' | 'practice' | 'coach' | 'applications';

/** One counted feature: the Just Looking limit and how many this account has used in the current period. */
export interface FeatureUse {
  limit: number;
  used: number;
}

export interface PlanUsage {
  analyses: FeatureUse;
  matches: FeatureUse;
  searches: FeatureUse;
  practice: FeatureUse;
  coach: FeatureUse;
  applications: FeatureUse;
}

/**
 * What the pricing page prints. Every number is a server setting (`TAYYARI_PLANS__*`); amounts are INR paise.
 * `dayOne` is what Day One costs right now and is present only while it can be bought here (Razorpay configured):
 * `dayOneEarly` until `dayOneEndsAt`, `dayOneAfter` from then on. `early` says which side of that moment the server is on.
 */
export interface PlanOffer {
  lowkey: number;
  lockedIn: number;
  dayOne?: number;
  dayOneEarly: number;
  dayOneAfter: number;
  /** ISO timestamp: the end of the early price. */
  dayOneEndsAt: string;
  early: boolean;
  /** Days after paying within which Day One is refunded in full, for any reason. */
  refundDays: number;
  /** The closed plans this account asked to be told about; empty for a visitor. */
  notify: ClosedPlan[];
}

/** A paid order, as the bill and "Your plan" print it. `number` is made from the day it was paid and the order's id. */
export interface Receipt {
  number: string;
  product: Product;
  amount: number;
  currency: Currency;
  /** ISO timestamp. */
  paidAt: string;
  /** The provider's payment id, when it was recorded. */
  paymentId?: string;
}

/** `GET /api/billing/orders`: this account's paid orders, newest first. */
export interface OrdersResponse {
  orders: Receipt[];
}

/** `PUT /api/billing/notify`: ask to be told (or not) when a closed plan opens. */
export interface NotifyRequest {
  plan: ClosedPlan;
  on: boolean;
}

export interface NotifyResponse {
  notify: ClosedPlan[];
}

/** INR is paid through Razorpay (Checkout.js in a modal), USD through Stripe (a redirect). */
export type Provider = 'razorpay' | 'stripe';

/** Longest IANA time zone name the client may send. */
export const TIMEZONE_MAX_CHARS = 64;

/** What each product costs in one currency; a product that is not on sale has no key. */
export interface ProductPrices {
  pack3?: number;
  pass30?: number;
  dayone?: number;
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
  /** True for an account on the server's unlimited list (`TAYYARI_GUARD__UNLIMITED_EMAILS`): no limits, nothing charged. */
  unlimited?: boolean;
  currency: Currency;
  prices: PriceBook;
  /** `just_looking` for a visitor and for every account that has not bought Day One. */
  plan: PlanId;
  /** The Just Looking limits and what this account has used; a visitor has used nothing. */
  usage: PlanUsage;
  /** ISO timestamp: when the monthly counts start again (00:00 UTC on the first of next month). */
  resetsAt: string;
  offer: PlanOffer;
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
  plan: PlanId;
  /** The paid order, once it is settled (`ok`). */
  receipt?: Receipt;
}

/**
 * The body of a 402 when a Just Looking count has run out:
 * `{ "error": "payment_required", "reason": "limit_reached", "feature", "limit", "resetsAt"? }`.
 * `resetsAt` is absent for `applications`, which is counted once. The older `reason: "no_credits"` body has no more keys.
 */
export interface LimitReached {
  error: string;
  reason: string;
  feature?: Feature;
  limit?: number;
  resetsAt?: string;
}
