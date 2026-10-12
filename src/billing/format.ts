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
 * What the next posting will use, in the words of the chip: a pass first, then the month's free posting and the
 * credits ("1 free this month · 2 postings left"), or "No postings left".
 */
export function entitlementText(e: MeEntitlements, now: number = Date.now()): string {
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
