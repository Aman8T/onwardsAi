/**
 * Resumes: the contract between the resume API and the web app (`/resume`).
 *
 * Mirrored by backend/tayyari/contract/resume.py; backend/tests/unit/resume/test_contract_parity.py parses this
 * file and fails when the two drift. Keep to what that parser reads: interfaces, literal unions, `x?:`
 * optionals, `T[]`. No `Record`, `null`, tuples or `typeof` in types.
 *
 * Shape: a person has one set of blocks (the cited `Fact`s of shared/match.ts: one per job, project, degree and
 * bullet) and several resumes. A resume is an arrangement of those blocks: which are in, in what order, in which
 * words. It is stored as a `ResumeVariant` (shared/match.ts), so every sentence names the facts it rests on.
 * A resume has a draft, edited freely, and at most one finalised version, the only one that can be downloaded.
 */

import type { Fact, LintReport, ResumeVariant } from './match';

/* ───────────────────────── enums ───────────────────────── */

/** `upload`: the resume as the person wrote it. `path`: arranged by Leafout for one of the next roles. */
export type ResumeKind = 'upload' | 'path';
/** `draft` while the draft differs from the finalised version or none exists yet, else `final`. */
export type ResumeState = 'draft' | 'final';
export type ResumeOpKind = 'move' | 'leave_out' | 'put_back' | 'edit' | 'reset';
export type MoveDir = 'up' | 'down';
/** How a line of a draft differs from the uploaded resume. */
export type MarkKind = 'moved' | 'edited' | 'added';
/** What a check found in a line the person typed: a figure, a scope verb, a posting term or a name no cited fact has. */
export type FindingKind = 'number' | 'scope' | 'term' | 'name';
/** `mine`: it is true, keep it as my own words (it becomes a fact said by the person). `undo`: back to the fact's wording. */
export type FindingChoice = 'mine' | 'undo';
export type BlockEntryKind = 'experience' | 'project' | 'education';

/* ───────────────────────── constants ───────────────────────── */

export const RESUME_VERSION = 1;
/** The most operations one PATCH may carry. */
export const MAX_RESUME_OPS = 40;
/** The longest line a person may type into a resume. */
export const MAX_LINE_CHARS = 320;

/* ───────────────────────── list ───────────────────────── */

export interface ResumeSummary {
  id: string;
  kind: ResumeKind;
  /** The file's label for an upload, the role's title for a path. */
  title: string;
  /** `Role.id` of the graph, for a path resume. */
  roleId?: string;
  state: ResumeState;
  isDefault: boolean;
  /** Lines of the draft that differ from the uploaded resume: moved, edited, added or left out. */
  changes: number;
  updatedAt: string;
  finalisedAt?: string;
}

/** One of the graph's next roles, with the resume made for it when there is one. */
export interface PathSlot {
  roleId: string;
  title: string;
  alignment: number;
  resumeId?: string;
}

export interface ResumeList {
  profileId: string;
  resumes: ResumeSummary[];
  paths: PathSlot[];
  /** How many blocks the person has. */
  blocks: number;
}

/* ───────────────────────── one resume ───────────────────────── */

export interface BulletMark {
  bulletId: string;
  kind: MarkKind;
}

export interface ResumeDetail {
  resume: ResumeSummary;
  /** The draft. */
  variant: ResumeVariant;
  marks: BulletMark[];
  /** The facts the draft's lines cite, so the page can show each line's source. */
  facts: Fact[];
  /** Bullet blocks the person has that this resume does not use. */
  leftOut: Fact[];
  /** A finalised version exists, so the files can be downloaded. */
  canDownload: boolean;
}

/** `move`, `leave_out`, `edit`, `reset` name a line (`bulletId`); `put_back` names a block (`factId`). */
export interface ResumeOp {
  op: ResumeOpKind;
  bulletId?: string;
  factId?: string;
  dir?: MoveDir;
  text?: string;
}

export interface ResumeOpsRequest {
  ops: ResumeOp[];
}

export interface PathRequest {
  roleId: string;
}

/* ───────────────────────── finalise ───────────────────────── */

export interface Finding {
  bulletId: string;
  kind: FindingKind;
  /** The line as the person typed it. */
  text: string;
  /** The part no cited fact supports, a passage of `text`. */
  what: string;
  options: FindingChoice[];
}

export interface FindingAnswer {
  bulletId: string;
  choice: FindingChoice;
}

export interface FinaliseRequest {
  makeDefault?: boolean;
  answers?: FindingAnswer[];
}

export interface FinaliseReport {
  /** True when the resume was finalised by this call; false while findings wait for the person. */
  done: boolean;
  findings: Finding[];
  /** What the export fixed by itself, in plain words (layout only, never wording). */
  fixed: string[];
  /** The read-back of the exported files, when `done`. */
  lint?: LintReport;
}

export interface FinaliseResponse {
  detail: ResumeDetail;
  report: FinaliseReport;
}

/* ───────────────────────── the blocks ───────────────────────── */

export interface BlockEntry {
  factId: string;
  kind: BlockEntryKind;
  title: string;
  org?: string;
  dates?: string;
}

export interface ResumeBlock {
  fact: Fact;
  /** The job, project or degree the block belongs to. */
  entryFactId?: string;
  /** How many of the person's resumes use it. */
  usedIn: number;
}

export interface BlocksResponse {
  profileId: string;
  entries: BlockEntry[];
  blocks: ResumeBlock[];
  /** How many resumes the person has (the denominator of `usedIn`). */
  resumes: number;
}
