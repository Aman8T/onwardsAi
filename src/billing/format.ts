/**
 * Words and numbers for money and entitlements. Pure functions, so the pricing page, the account menu and the chip
 * all say the same thing the same way.
 */
import type { MeEntitlements } from '../../shared/auth';
import type { Currency, Product } from '../../shared/billing';
import { plural } from '../lib/ui';

/** `49900` paise -> "₹499"; `900` cents -> "$9"; `1999` -> "$19.99". Amounts are minor units, always integers. */
export function formatPrice(amountMinor: number, currency: Currency): string {
  const whole = amountMinor % 100 === 0;
  return new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  }).format(amountMinor / 100);
}

/** "12 Nov" for an ISO timestamp (the day in the person's own time zone). */
export function shortDate(iso: string): string {
  const at = Date.parse(iso);
  return Number.isFinite(at) ? new Date(at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : '';
}

/**
 * True for an account on the server's unlimited list (`TAYYARI_GUARD__UNLIMITED_EMAILS`): no limits, nothing charged.
 * The one place the flag is read, so no screen has to guess; a missing flag (an older server, a visitor) means false.
 */
export function isUnlimited(e: { unlimited?: boolean } | null | undefined): boolean {
  return e?.unlimited === true;
}

/**
 * What the next posting will use, in the words of the chip: "Unlimited" for an account with no limits, else a pass
 * first, then the month's free posting and the credits ("1 free this month · 2 postings left"), or "No postings left".
 */
export function entitlementText(e: MeEntitlements, now: number = Date.now()): string {
  if (isUnlimited(e)) return 'Unlimited';
  const until = e.passUntil ? Date.parse(e.passUntil) : NaN;
  if (e.passUntil && Number.isFinite(until) && until > now) return `Pass until ${shortDate(e.passUntil)}`;
  const parts: string[] = [];
  if (e.freeMatchAvailable) parts.push('1 free this month');
  if (e.credits > 0) parts.push(`${plural(e.credits, 'posting')} left`);
  return parts.length > 0 ? parts.join(' · ') : 'No postings left';
}

/** What the person has after a purchase, as one sentence. */
export function purchaseSummary(product: Product, e: MeEntitlements): string {
  if (product === 'pass30' && e.passUntil) return `Your pass runs until ${shortDate(e.passUntil)}.`;
  if (product === 'pack3') return `${plural(e.credits, 'posting')} left on your account.`;
  return 'It is on your account now.';
}

/** Postings a pack holds (`PRODUCTS.pack3` on the server). */
const PACK_POSTINGS = 3;

/** What one posting costs inside a pack, rounded to a whole major unit ("₹50"), as minor units. */
export function perPostingMinor(packMinor: number): number {
  return Math.max(1, Math.round(packMinor / PACK_POSTINGS / 100)) * 100;
}

/**
 * The fewest postings in 30 days for which the pass costs less than buying packs, or undefined when it never does
 * within a reasonable number (or a price is missing). Computed from the live prices, never typed.
 */
export function passBreakEven(packMinor: number | undefined, passMinor: number | undefined): number | undefined {
  if (!packMinor || !passMinor || packMinor <= 0 || passMinor <= 0) return undefined;
  for (let n = 1; n <= 60; n++) {
    if (Math.ceil(n / PACK_POSTINGS) * packMinor > passMinor) return n > PACK_POSTINGS ? n : undefined;
  }
  return undefined;
}
