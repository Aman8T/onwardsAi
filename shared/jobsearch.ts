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
  /** How the read's checked requirements came out, all of them (the card's pips): never a score. */
  asks: AskCounts;
  /** First published over a year ago and still listed: worth asking if it is still hiring. */
  openLong?: boolean;
  /** The code quick fit, as Discover shows it. */
  quickFit: QuickFit;
  /** Only when the employer states it. */
  salary?: Salary;
}

/** The requirements a read checked and that survived the checks, by outcome. */
export interface AskCounts {
  met: number;
  partly: number;
  notShown: number;
}

/** One stage of the funnel: what was left after it. */
export interface FunnelStep {
  label: string;
  count: number;
}

/**
 * `POST /api/jobsearch` streams one JSON object per line: `step` lines as the search moves, a `job` line for each role
 * that survives, then `done` (or `error`). Roles from Tayyari's own employers arrive first (a few seconds in), the web
 * search's after; the web app keeps them ordered by fit word. `done` names the stored run, which
 * `GET /api/jobsearch/latest` returns until the next search.
 */
export type JobSearchEvent =
  | { type: 'step'; step: TrailStep }
  | { type: 'job'; job: AiJob }
  | { type: 'done'; jobs: number; funnel: FunnelStep[]; note?: string; runId: string; createdAt: string }
  | { type: 'error'; message: string };

/**
 * What "Not for me" taught the search. Kept in the browser and sent with each search: hidden roles never come back, an
 * employer the person waved off is skipped, and "too senior" leaves out roles a step up.
 */
export interface AvoidPrefs {
  jobIds: string[];
  /** Employer names as shown on the cards. */
  employers: string[];
  noStepUp: boolean;
}

/** `POST /api/jobsearch` body. `city` is the city Discover is showing; the profile's city when it is left out. */
export interface JobSearchRequest {
  profileId: string;
  city?: string;
  /** Include remote roles open to India (true when left out). */
  remote?: boolean;
  avoid?: AvoidPrefs;
}

/**
 * `GET /api/jobsearch/latest?profileId=…&city=…&remote=…`: the last finished search for this profile, city and remote
 * choice (404 when there is none). The search runs only when the person presses Search; this is how the page shows the
 * last results at once.
 */
export interface JobSearchRun {
  runId: string;
  createdAt: string;
  city?: string;
  remote: boolean;
  jobs: AiJob[];
  funnel: FunnelStep[];
  trail: TrailStep[];
  note?: string;
}

/**
 * `GET /api/jobsearch/runs/{runId}/jobs/{jobId}/posting?profileId=…`: the posting's text as the search read it, which the
 * web app sends to `POST /api/match` as `text` for "See how you fit".
 */
export interface JobPosting {
  jobId: string;
  title: string;
  employer: string;
  applyUrl: string;
  postingText: string;
}

/** `POST /api/jobsearch/prepare` and `POST /api/jobsearch/referral` body. `fresh` asks for a new draft (Rewrite). */
export interface RoleRequest {
  profileId: string;
  runId: string;
  jobId: string;
  fresh?: boolean;
}

/** A requirement the person's evidence meets, to lead with (from the search's checked read). */
export interface PrepLead {
  ask: string;
  cite: Cite;
  partly: boolean;
}

/** A requirement the profile does not show: the nearest evidence (checked), and one small step (a suggestion). */
export interface PrepGap {
  gap: string;
  nearest?: Cite;
  /** Written by the model as advice, never a claim about the person; the page labels it a suggestion. */
  step: string;
}

/** One beat of a story: a line of the person's evidence, or a placeholder for what only they know. */
export interface StoryPart {
  label: string;
  cite?: Cite;
  placeholder?: string;
}

/** A question the posting suggests, tied to the requirement it comes from, with a story built from evidence. */
export interface PrepQuestion {
  question: string;
  /** Word for word from the posting (checked in code). */
  ask: string;
  story: StoryPart[];
}

/** `POST /api/jobsearch/prepare` answer: the prep page for one role. */
export interface RolePrep {
  jobId: string;
  title: string;
  employer: string;
  lead: PrepLead[];
  gaps: PrepGap[];
  questions: PrepQuestion[];
  createdAt: string;
}

/** The three parts of a referral note: why the company, why the person fits, why the role fits them. */
export type ReferralPart = 'company' | 'you' | 'role';

/** One line of the note, and where its words come from ("the posting", "your resume"). */
export interface ReferralLine {
  part: ReferralPart;
  text: string;
  sources: string[];
}

/**
 * `POST /api/jobsearch/referral` answer: a short note asking a connection for a referral. Three lines, then `ask` and the
 * link. `[name]` in the first line is the connection's name, filled in by the person.
 */
export interface ReferralNote {
  jobId: string;
  lines: ReferralLine[];
  ask: string;
  link: string;
  createdAt: string;
}
