/**
 * The Leafout Weekly: a personal old-style newspaper, built every week from the reader's own graph (design canvas
 * https://claude.ai/artifact/BpwdFiwGf6tHjy7H1VqM12). The contract between `/api/weekly/*` and the web app.
 *
 *   GET  /api/weekly/subscription           -> WeeklySubscription (signed in; `status: 'off'` when never subscribed)
 *   PUT  /api/weekly/subscription           WeeklySubscriptionUpdate -> WeeklySubscription
 *   GET  /api/weekly/editions               -> WeeklyEditionList (newest first)
 *   GET  /api/weekly/editions/{id}          -> WeeklyEdition (owner only; marks it read)
 *   POST /api/weekly/editions/preview       -> WeeklyEdition (builds this week's edition now, or returns it if built)
 *   GET|POST /api/weekly/unsubscribe?token= one-click unsubscribe from the email (RFC 8058); answers a small HTML page
 *
 * Every line about the reader cites a fact (their resume or links), a listing on an employer's own board, or a named
 * article; numbers are computed in code. Mirrored by backend/tayyari/contract/weekly.py;
 * tests/unit/contract/test_weekly_parity.py reads this file. Same subset as the other contracts: interfaces, literal
 * unions, `x?:` optionals (no `null`), `T[]`.
 */

/** The day an edition is built and sent, from 06:00 India time. */
export type WeeklyDay = 'mon' | 'fri';
/** The parts an edition can carry; the reader turns them on and off. */
export type WeeklySection = 'jobs' | 'progress' | 'news' | 'tip';
/** `off`: never subscribed or unsubscribed; `paused`: no editions until `pausedUntil`. */
export type WeeklyStatus = 'active' | 'paused' | 'off';
/** The code-only quick fit of a listing against the reader's graph (discover/quickfit.py's words). */
export type WeeklyFit = 'looks_close' | 'worth_a_look' | 'stretch' | 'cant_tell';

export interface WeeklySubscription {
  status: WeeklyStatus;
  /** Where the email goes; absent for an account without an email (then editions are in-app only). */
  email?: string;
  day: WeeklyDay;
  sections: WeeklySection[];
  /** ISO date; present while paused. */
  pausedUntil?: string;
  /** Editions not yet opened in the app. */
  unread: number;
}

export interface WeeklySubscriptionUpdate {
  status: WeeklyStatus;
  day: WeeklyDay;
  sections: WeeklySection[];
  /** Pause for this many days (7..90); only with `status: 'paused'`. */
  pauseDays?: number;
}

/** Where a line came from: the reader's own source (Resume, GitHub, ...) and the words it rests on. */
export interface WeeklyCite {
  source: string;
  text: string;
}

export interface WeeklyMet {
  /** The posting's own words for the ask, verbatim. */
  ask: string;
  cite: WeeklyCite;
}

export interface WeeklyJob {
  listingId: string;
  employer: string;
  title: string;
  /** City or "Remote", from the listing. */
  place: string;
  /** The employer's own board. */
  applyUrl: string;
  fit: WeeklyFit;
  /** Asks the reader's evidence meets, each cited (the lead job only; empty for the others). */
  met: WeeklyMet[];
  /** Asks nothing the reader shared shows yet, in the posting's words. */
  notShown: string[];
  /** One plain line on the fit, written from the facts above (never a score). */
  note?: string;
}

export interface WeeklyProgress {
  /** The reader's most recent Prepare plan: its role and where they are in it. */
  planRole?: string;
  planWeek?: number;
  planWeeks?: number;
  ticksThisWeek?: number;
  ticksPlanned?: number;
  /** The role of their graph with the shortest ETA, and that ETA as the graph states it ("~2 months"). */
  closestRole?: string;
  closestEta?: string;
  /** ISO date of their latest practice interview, if any in the last 30 days. */
  lastPractice?: string;
  /** One suggestion for the week, chosen by code. */
  nudge?: string;
}

export interface WeeklyNews {
  headline: string;
  publication: string;
  /** ISO date of the article, when the page states one. */
  date?: string;
  url: string;
  /** Why it matters to this reader, citing one skill on their graph. */
  why: string;
  skill: string;
}

export interface WeeklyEdition {
  id: string;
  /** 1 for the reader's first edition, then counting up. */
  issue: number;
  /** ISO date of the Monday (or Friday) of that week. */
  weekOf: string;
  /** The lead headline, written by code from the jobs and progress. */
  headline: string;
  /** The reader's name and city, as on their graph, for the masthead. */
  reader: string;
  place?: string;
  jobs: WeeklyJob[];
  progress?: WeeklyProgress;
  news: WeeklyNews[];
  tip?: string;
  quote?: string;
  /** ISO timestamps. */
  createdAt: string;
  emailedAt?: string;
  readAt?: string;
}

export interface WeeklyEditionSummary {
  id: string;
  issue: number;
  weekOf: string;
  headline: string;
  read: boolean;
}

export interface WeeklyEditionList {
  editions: WeeklyEditionSummary[];
}
