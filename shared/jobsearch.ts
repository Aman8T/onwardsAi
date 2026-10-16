/**
 * Job search with AI: the contract between `POST /api/jobsearch` and the web app.
 *
 * How a search runs (built from two baseline runs by larger models, 2026-10-16, which found that roughly two in three
 * job links a web search returns are already closed, and that an employer's own board is the only reliable sign a role
 * is open today):
 *
 *   1. plan    code reads the profile: the role, the next roles, the years, the city. No name and no resume text leave
 *              the server in a search.
 *   2. search  three Claude Haiku 5.5 scouts each run a few web searches restricted to employers' applicant tracking
 *              sites (Greenhouse, Lever). Code takes the boards from the result links; a scout's own words are not used.
 *   3. boards  code reads each employer's public board (the same keyless APIs Discover reads), so every role shown is
 *              listed today. Tayyari's own employers are read too.
 *   4. narrow  code keeps the roles in the person's city or remote in India, of their kind, at their level.
 *   5. read    Haiku reads the shortlist against the person's evidence. Every quote it returns is checked in code
 *              against the posting and the profile; a quote that is not there is dropped.
 *   6. result  code works out the fit word from what survived the checks. Never a score, never a percentage.
 *
 * Mirrored by backend/tayyari/contract/jobsearch.py; tests/unit/contract/test_jobsearch_parity.py reads this file
 * together with shared/discover.ts (for `Cite`, `Place`, `QuickFit`, `Salary` and `DiscoverWorkMode`) and fails when
 * the two drift. Same subset as discover.ts: interfaces, literal unions, `x?:` optionals, `T[]`, inline objects.
 */

import type { Cite, DiscoverWorkMode, Place, QuickFit, Salary } from './discover';

/** Where a role came from: a board the scouts found on the web, or one of Tayyari's own employers. */
export type FoundVia = 'web' | 'tayyari';

/**
 * The fit in words, worked out in code from the requirements the reader quoted and the checks they passed:
 * "Strong fit", "Good fit", "A stretch".
 */
export type AiFitWord = 'strong' | 'good' | 'stretch';

/** The level against the person's years, from the posting's own words: below, at, a step up, or not stated. */
export type LevelWord = 'below' | 'at' | 'step_up' | 'unclear';

/** The stages of a search, in order. */
export type TrailStage = 'plan' | 'search' | 'boards' | 'narrow' | 'read' | 'result';

/** One line of "how we narrowed it down". The text is written in code from counts, never by a model. */
export interface TrailStep {
  stage: TrailStage;
  text: string;
  /** What the stage ended with, when it is a count. */
  count?: number;
  /** The searches a scout ran (on `search` steps only). */
  queries?: string[];
}

/** A requirement in the posting's own words, and the line of the person's evidence that meets it. */
export interface AskMet {
  /** Word for word from the posting (checked in code). */
  ask: string;
  /** Word for word from the profile's evidence (checked in code). */
  cite: Cite;
  /** The evidence meets part of the requirement. */
  partly: boolean;
}

export interface AiJob {
  id: string;
  title: string;
  employer: string;
  place: Place;
  workMode: DiscoverWorkMode;
  /** ISO time the employer gives, when it gives one. */
  postedAt?: string;
  /** ISO time the employer's board listed it during this search. */
  checkedAt: string;
  applyUrl: string;
  /** The host the apply link goes to, e.g. "job-boards.greenhouse.io". */
  applyHost: string;
  foundVia: FoundVia;
  /** The search that led to the employer (web only). */
  foundBy?: string;
  fit: AiFitWord;
  level: LevelWord;
  /** The posting's own words about years or level, when it states them. */
  levelQuote?: string;
  /** Years the posting asks for, read in code, when it says. */
  yearsAsked?: number;
  /** Requirements the person's evidence meets, strongest first (at most three). */
  met: AskMet[];
  /** Requirements in the posting's own words that the profile does not show (at most three). */
  gaps: string[];
  /** The code quick fit, as Discover shows it. */
  quickFit: QuickFit;
  /** Only when the employer states it. */
  salary?: Salary;
}

/** One stage of the funnel: what was left after it. */
export interface FunnelStep {
  label: string;
  count: number;
}

/**
 * `POST /api/jobsearch` streams one JSON object per line: `step` lines as the search moves, a `job` line for each role
 * that survives, then `done` (or `error`). Jobs arrive best first within each read batch; the web app keeps them ordered
 * by fit word.
 */
export type JobSearchEvent =
  | { type: 'step'; step: TrailStep }
  | { type: 'job'; job: AiJob }
  | { type: 'done'; jobs: number; funnel: FunnelStep[]; note?: string }
  | { type: 'error'; message: string };

/** `POST /api/jobsearch` body. `city` is the city Discover is showing; the profile's city when it is left out. */
export interface JobSearchRequest {
  profileId: string;
  city?: string;
}
