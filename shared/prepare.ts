/**
 * Prepare: "How do I prepare for this role?" answered with typed blocks instead of a wall of text (canvas
 * https://claude.ai/artifact/QgpVd4MRfnsqZRrPrLRe73). Four blocks: where you stand (the role's gaps, from the graph), the
 * one move (up to three, shown one by one), the plan (phases; weeks computed from the hours a week the person picks) and
 * the next actions (calendar, repo, headline, practice). "Read this as text" is written in the browser from the same
 * blocks, so it can never say more than they do.
 *
 * Code supplies every number: the gaps and their current/target come from `Profile.roles`; weeks come from
 * `PrepareState.hoursPerWeek`. The model writes the headline, one "why" per gap and the moves, each citing the evidence it
 * rests on (`PrepareEvidence`, the graph's own evidence lines), and code checks every citation, gap name and number.
 *
 *   POST /api/prepare                         PrepareRequest -> NDJSON stream of PrepareEvent (a stored plan streams at once)
 *   GET  /api/prepare/{planId}?profileId=     PreparePlan
 *   PUT  /api/prepare/{planId}/state          PrepareStateUpdate -> PrepareStateSaved (409 when `version` is stale)
 *   GET  /api/prepare/{planId}/repos?profileId=   PrepareRepos (computed from the stored GitHub source, no network)
 *   POST /api/prepare/{planId}/headline       PrepareHeadlineRequest -> PrepareHeadline
 *   GET  /api/prepare?profileId=              PreparePlanSummary[] (newest first; the "where it goes" picker of a Fix step)
 *
 * A plan also carries the Fix steps from practice placed in it (`practice`, I2). They are stored on the claim's mark, not in
 * `PrepareState`; they are ticked through `PATCH /api/interview/fixes/{id}`.
 *
 * Mirrored by backend/tayyari/contract/prepare.py; tests/unit/contract/test_prepare_parity.py reads this file. Same
 * subset as the other contracts: interfaces, literal unions, `x?:` optionals, `T[]`, one discriminated event union.
 * `FixTask` comes from interview.ts, so the parity test reads the two files together.
 */

import type { FixTask } from './interview';

export type PrepareScope = 'role' | 'requirement';

export interface PrepareRequest {
  profileId: string;
  /** `Role.id` on the person's graph. */
  roleId: string;
  /** A requirement name of that role: prepare for just that gap (scope `requirement`). */
  requirement?: string;
  /** Make a new plan even when one is stored for this role (and requirement). */
  fresh?: boolean;
}

/** One evidence line of the graph (`Skill.evidence`), numbered so the model can cite it. */
export interface PrepareEvidence {
  /** "e1", "e2"… stable within one plan. */
  id: string;
  /** The `StoredSource` id the line comes from. */
  sourceId: string;
  /** resume | github | lovable | linkedin | link | … (the source's kind). */
  sourceKind: string;
  /** How the chip names it: "your resume", "your GitHub". */
  sourceLabel: string;
  /** The skill the line evidences. */
  skill: string;
  text: string;
}

export interface PrepareGap {
  /** The requirement's name on the graph. */
  name: string;
  skillId?: string;
  current: number;
  target: number;
  /** The gap with the largest `target - current` (ties: the first). Exactly one gap has it. */
  biggest: boolean;
  /** One sentence, written by the model and checked against the evidence it cites. Absent until it arrives, or when it
   * failed the check. */
  why?: string;
  evidenceIds?: string[];
}

/** A requirement already met (`have`, or current at or above target): one quiet line, no block. */
export interface PrepareStrong {
  name: string;
  current: number;
  target: number;
}

export interface PrepareCited {
  text: string;
  evidenceIds: string[];
}

export interface PreparePhase {
  title: string;
  /** The model's estimate, 1 to 40. Weeks are computed from it and the hours a week, never written by the model. */
  hours: number;
  tasks: string[];
  /** What the person has at the end of the phase. */
  deliverable: string;
}

export interface PrepareMove {
  /** "m1", "m2", "m3", best first. */
  id: string;
  title: string;
  body: string;
  /** What the person already has that this move builds on, each citing evidence. */
  have: PrepareCited[];
  /** The gaps this move works on: each one of the plan's gap names. Never a predicted number. */
  works: string[];
  phases: PreparePhase[];
}

export interface PrepareLink {
  moveId: string;
  phaseIndex: number;
  url: string;
  addedAt: string;
}

/** What the person changed: kept with the plan, so it is still there next week. */
export interface PrepareState {
  /** 1 to 40; the screen offers 3, 6 and 10. */
  hoursPerWeek: number;
  moveIndex: number;
  /** Done tasks, as "<moveId>:<phaseIndex>:<taskIndex>". */
  done: string[];
  links: PrepareLink[];
}

export interface PreparePlan {
  id: string;
  profileId: string;
  roleId: string;
  roleTitle: string;
  scope: PrepareScope;
  requirement?: string;
  /** The role's place among the person's roles by alignment (1 = closest) and how many roles there are. No percentage. */
  rank: number;
  roleCount: number;
  /** `Role.eta` as the graph has it ("~2 months"). */
  eta: string;
  createdAt: string;
  /** One sentence over the gaps, written by the model and checked. */
  headline?: string;
  gaps: PrepareGap[];
  strong: PrepareStrong[];
  evidence: PrepareEvidence[];
  moves: PrepareMove[];
  state: PrepareState;
  version: number;
  /** Fix steps from practice placed in this plan (I2), oldest first; they belong to the plan's current week. */
  practice?: FixTask[];
}

/** `GET /api/prepare?profileId=`: one plan, for choosing where a Fix step goes. */
export interface PreparePlanSummary {
  id: string;
  roleId: string;
  roleTitle: string;
  requirement?: string;
  createdAt: string;
}

/** Stream events, in this order: `gaps` at once (code only), then `standing` and `moves` as each model call lands (either
 * order), then `done` with the stored plan; or `error` at any point. A stored plan sends `gaps` then `done` at once. */
export type PrepareEvent =
  | { type: 'gaps'; plan: PreparePlan }
  | { type: 'standing'; headline?: string; gaps: PrepareGap[] }
  | { type: 'moves'; moves: PrepareMove[] }
  | { type: 'done'; plan: PreparePlan }
  | { type: 'error'; message: string; code?: string };

export interface PrepareStateUpdate {
  profileId: string;
  /** The version the client last saw. */
  version: number;
  state: PrepareState;
}

export interface PrepareStateSaved {
  state: PrepareState;
  version: number;
}

export interface PrepareRepo {
  name: string;
  url?: string;
  language?: string;
  description?: string;
  tests: boolean;
  ci: boolean;
  readme: boolean;
  /** Short, code-made reasons ("has tests", "no README yet", "names RAG"). */
  reasons: string[];
  /** The one repo to start from (at most one is suggested). */
  suggested: boolean;
}

export interface PrepareRepos {
  /** False when the graph was built without a GitHub source. */
  github: boolean;
  repos: PrepareRepo[];
}

export interface PrepareHeadlineRequest {
  profileId: string;
}

export interface PrepareHeadline {
  /** The headline on the graph today (`Person.headline`). */
  current: string;
  /** A headline for this role, built only from cited evidence; absent when none passed the check. */
  proposed?: string;
  evidenceIds: string[];
  note?: string;
}
