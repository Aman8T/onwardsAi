/**
 * Words and numbers for money, dates and the plan. Pure functions, so the plan screen, the bill, the account menu and
 * the chip all say the same thing the same way.
 */
import type { MeEntitlements } from '../../shared/auth';
import type { Currency, Feature } from '../../shared/billing';
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
 * The chip's words: "Day One" on Day One, "Unlimited" for an account with no limits, else "Just Looking · 2 matches
 * left" from the matches left this month ("Just Looking" when none are left or the server did not say).
 */
export function entitlementText(e: MeEntitlements): string {
  if (isUnlimited(e)) return 'Unlimited';
  if (e.plan === 'day_one') return 'Day One';
  const left = e.matchesLeft;
  return left !== undefined && left > 0 ? `Just Looking · ${plural(left, 'match', 'matches')} left` : 'Just Looking';
}

const DAY_MONTH: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'short' };

/** "15 Dec" in India (the early price ends at 23:59 IST, so the day must not move with the reader's zone). */
export function istDay(iso: string): string {
  const at = Date.parse(iso);
  return Number.isFinite(at) ? new Date(at).toLocaleDateString('en-GB', { ...DAY_MONTH, timeZone: 'Asia/Kolkata' }) : '';
}

/** "23:59" in India. */
export function istTime(iso: string): string {
  const at = Date.parse(iso);
  return Number.isFinite(at) ? new Date(at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }) : '';
}

/** "1 Dec" for the moment the monthly counts start again (00:00 UTC, so the day is read in UTC). */
export function utcDay(iso: string): string {
  const at = Date.parse(iso);
  return Number.isFinite(at) ? new Date(at).toLocaleDateString('en-GB', { ...DAY_MONTH, timeZone: 'UTC' }) : '';
}

/** "17 Nov 2026", the day in the person's own time zone. */
export function fullDate(iso: string): string {
  const at = Date.parse(iso);
  return Number.isFinite(at) ? new Date(at).toLocaleDateString('en-GB', { ...DAY_MONTH, year: 'numeric' }) : '';
}

/** What each count is, singular and plural, as it reads after "your 3 ...". */
const FEATURE_NOUN: Record<Feature, readonly [string, string]> = {
  analyses: ['analysis', 'analyses'],
  matches: ['posting match', 'posting matches'],
  searches: ['search with AI', 'searches with AI'],
  practice: ['practice session', 'practice sessions'],
  coach: ['coach or Ask question', 'coach and Ask questions'],
  applications: ['application', 'applications'],
};

/** The kicker's subject: the feature's name, where a count ran out. */
export const FEATURE_KICKER: Record<Feature, string> = {
  analyses: 'New analysis',
  matches: 'Posting match',
  searches: 'Search with AI',
  practice: 'Interview practice',
  coach: 'Coach and Ask',
  applications: 'Apply for me',
};

/** "You've used your 5 searches with AI this month." (without a number when the server did not say one). */
export function limitHeadline(feature: Feature, limit?: number): string {
  const [one, many] = FEATURE_NOUN[feature];
  const when = feature === 'applications' ? '' : ' this month';
  if (limit === undefined) return `You've used all your ${many}${when}.`;
  return `You've used your ${limit} ${limit === 1 ? one : many}${when}.`;
}

/** "They come back on 1 Dec." for a monthly count; empty for `applications` (counted once) or when no date is known. */
export function limitReturns(feature: Feature, limit: number | undefined, resetsAt?: string): string {
  if (feature === 'applications' || !resetsAt) return '';
  return `${limit === 1 ? 'It comes' : 'They come'} back on ${utcDay(resetsAt)}.`;
}

/** The sentence for a count that ran out (a 402 `limit_reached`), in the plain voice. */
export function limitSentence(feature: Feature, limit: number, resetsAt?: string): string {
  const back = limitReturns(feature, limit, resetsAt);
  return back ? `${limitHeadline(feature, limit)} ${back}` : limitHeadline(feature, limit);
}
