/**
 * What the plan screen and the bill print, worked out from the offer and the counts (never typed): the six counted
 * features in the order the boards list them, their Just Looking limits in words, the pips, the three-month sum and
 * the dates of the early price. Pure functions, so the dev gallery and the parity scripts can use them without a server.
 */
import type { Feature, FeatureUse, PlanOffer } from '../../shared/billing';
import type { Pip } from '../components/materials';
import { istDay, istTime } from './format';

/** The six counted features, in the order of the boards. */
export const FEATURES: readonly Feature[] = ['matches', 'searches', 'practice', 'coach', 'analyses', 'applications'];

/** What each is called while it is counted (the plan screen's Just Looking stage). */
export const COUNTED_LABEL: Record<Feature, string> = {
  matches: 'Posting matches',
  searches: 'Searches with AI',
  practice: 'Interview practice sessions',
  coach: 'Coach and Ask questions',
  analyses: 'New analyses',
  applications: 'Apply for me, applications (once)',
};

/** What each is called on Day One, where nothing is counted (the Day One stage and the bill). */
export const DAY_ONE_LABEL: Record<Feature, string> = {
  matches: 'Posting matches',
  searches: 'Searches with AI',
  practice: 'Interview practice, voice included',
  coach: 'Coach and Ask questions',
  analyses: 'New analyses',
  applications: 'Apply for me',
};

/** The Just Looking limit in words: "3 a month", "3 sessions a month", "5 applications, once". */
export function limitText(feature: Feature, limit: number): string {
  if (feature === 'applications') return `${limit} applications, once`;
  if (feature === 'practice') return `${limit} sessions a month`;
  return `${limit} a month`;
}

/** Most pips drawn for one count; a bigger limit is a number alone. */
export const MAX_PIPS = 5;

/** One circle per use of the limit, filled while it is left; null when the limit is too big to draw. */
export function pipsOf(use: FeatureUse): Pip[] | null {
  if (use.limit < 1 || use.limit > MAX_PIPS) return null;
  const left = Math.max(0, Math.min(use.limit, use.limit - use.used));
  return Array.from({ length: use.limit }, (_, i) => (i < left ? 'on' : 'no'));
}

/** "1 of 3 left". */
export function leftText(use: FeatureUse): string {
  return `${Math.max(0, use.limit - use.used)} of ${use.limit} left`;
}

export interface ThreeMonths {
  lowkey: number;
  lockedIn: number;
  dayOne: number;
  /** Bar widths as a share of the biggest sum, 0..100. */
  wLowkey: number;
  wLockedIn: number;
  wDayOne: number;
}

/** Three months of each plan, side by side: three Lowkey, three Locked In, Day One once. Plain arithmetic. */
export function threeMonths(offer: PlanOffer, dayOne: number): ThreeMonths {
  const lowkey = 3 * offer.lowkey;
  const lockedIn = 3 * offer.lockedIn;
  const top = Math.max(lowkey, lockedIn, dayOne, 1);
  const share = (n: number) => Math.round((n / top) * 1000) / 10;
  return { lowkey, lockedIn, dayOne, wLowkey: share(lowkey), wLockedIn: share(lockedIn), wDayOne: share(dayOne) };
}

export interface EarlyWindow {
  /** "15 Dec" (India). */
  endDay: string;
  /** "23:59" (India). */
  endTime: string;
  /** The day after the end, when the price reads as the after-price. */
  afterDay: string;
  /** Whole days left at the early price, counted up (the last day is "1 day left"). */
  daysLeft: number;
}

const DAY_MS = 86_400_000;

/** The dates of the early price from `offer.dayOneEndsAt`; the day after the end is a minute past it. */
export function earlyWindow(offer: PlanOffer, now: number): EarlyWindow {
  const end = Date.parse(offer.dayOneEndsAt);
  const after = new Date(end + 60_000).toISOString();
  return {
    endDay: istDay(offer.dayOneEndsAt),
    endTime: istTime(offer.dayOneEndsAt),
    afterDay: istDay(after),
    daysLeft: Number.isFinite(end) ? Math.max(0, Math.ceil((end - now) / DAY_MS)) : 0,
  };
}
