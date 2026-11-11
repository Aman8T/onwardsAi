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

import type { Fact, LintReport, MatchFactKind, ResumeVariant } from './match';

/* ───────────────────────── enums ───────────────────────── */

/**
 * `upload`: the resume as the person wrote it. `path`: arranged by Leafout for one of the next roles. `posting`: the
 * resume tailored for a posting in Match, saved into the list by the person (`POST /api/resumes/from-match`).
 */
export type ResumeKind = 'upload' | 'path' | 'posting';
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
/**
 * Where a line of a second file went. `same`: the block exists, this file is another source of it. `new`: a new block.
 * `near`: the same thing worded differently, both wordings are kept. `disagree`: the same entry or line with a conflicting
 * figure, date, title or employer, each file keeps its own words.
 */
export type MergeSection = 'same' | 'new' | 'near' | 'disagree';
export type MergeDiffer = 'figure' | 'date' | 'title' | 'employer';
/** `analysis`: the file the profile was analysed from. `upload`: a file added later in `/resume`. */
export type ResumeFileOrigin = 'analysis' | 'upload';

/* ───────────────────────── constants ───────────────────────── */

export const RESUME_VERSION = 1;
/** The most operations one PATCH may carry. */
export const MAX_RESUME_OPS = 40;
/** The most resume files one profile keeps (the one it was analysed from included). */
export const MAX_RESUME_FILES = 8;
/** The longest line a person may type into a resume. */
export const MAX_LINE_CHARS = 320;
/** The most wordings from other resumes offered for one line. */
export const MAX_WORDINGS_PER_LINE = 3;

/* ───────────────────────── list ───────────────────────── */

export interface ResumeSummary {
  id: string;
  kind: ResumeKind;
  /** The file's label for an upload, the role's title for a path. */
  title: string;
  /** `Role.id` of the graph, for a path resume. */
  roleId?: string;
  /** The posting match it was saved from, for a posting resume. */
  matchId?: string;
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

/* ───────────────────────── the grammar check ───────────────────────── */

/**
 * One line whose grammar, spelling or punctuation can be fixed without changing what it says (`POST /api/resumes/{id}/grammar`).
 * Not stored: the person accepts it by sending the `edit` op `{ op: 'edit', bulletId, text: after }` through the
 * ordinary PATCH, so Finalise still checks the line.
 */
export interface GrammarProposal {
  bulletId: string;
  before: string;
  after: string;
  why: string;
}

export interface GrammarResponse {
  proposals: GrammarProposal[];
  /** False when the check could not run (no model key, a model failure, a timeout): `proposals` is then empty. */
  ran: boolean;
}

/* ───────────────────────── a wording from another resume ───────────────────────── */

/**
 * A finalised wording of the same block(s) that another resume of the profile uses (`GET /api/resumes/{id}/wordings`).
 * The person takes it with the `edit` op `{ op: 'edit', bulletId: lineId, text }`.
 */
export interface LineWording {
  lineId: string;
  text: string;
  fromResumeId: string;
  fromTitle: string;
}

export interface WordingsResponse {
  /** At most `MAX_WORDINGS_PER_LINE` per line, de-duplicated, never the line's own current text. */
  wordings: LineWording[];
}

/* ───────────────────────── saving a match's resume ───────────────────────── */

/** `POST /api/resumes/from-match`: the match's tailored resume becomes a `posting` resume of its profile. */
export interface FromMatchRequest {
  matchId: string;
}

/* ───────────────────────── the blocks ───────────────────────── */

export interface BlockEntry {
  factId: string;
  kind: BlockEntryKind;
  title: string;
  org?: string;
  dates?: string;
}

/** Another wording of a block, from a file that said the same thing differently (a `near` line). */
export interface BlockWording {
  fileId: string;
  filename: string;
  text: string;
  /** The resume made from that file. */
  resumeId?: string;
}

/** A file whose line is the same entry or bullet as the block but conflicts with it. It keeps its own words. */
export interface BlockDisagreement {
  fileId: string;
  filename: string;
  text: string;
  differs: MergeDiffer[];
  resumeId?: string;
}

export interface ResumeBlock {
  fact: Fact;
  /** The job, project or degree the block belongs to. */
  entryFactId?: string;
  /** How many of the person's resumes use it. */
  usedIn: number;
  /** The block's other wordings, each tied to its file (absent when there are none). */
  wordings?: BlockWording[];
  /** What other files say that conflicts with the block (absent when nothing does). */
  disagreements?: BlockDisagreement[];
}

export interface BlocksResponse {
  profileId: string;
  entries: BlockEntry[];
  blocks: ResumeBlock[];
  /** How many resumes the person has (the denominator of `usedIn`). */
  resumes: number;
}

/* ───────────────────────── a second file ───────────────────────── */

/** One line of a file and where it went. */
export interface MergeItem {
  /** The block of the person's set: an existing one for `same`, `near` and `disagree`, a new one for `new`. */
  blockId: string;
  kind: MatchFactKind;
  /** The new file's own words. */
  text: string;
  /** `near` and `disagree`: the words the block already had. */
  existing?: string;
  /** `disagree`: what differs. */
  differs?: MergeDiffer[];
}

export interface MergeCounts {
  same: number;
  new: number;
  near: number;
  disagree: number;
}

/** Every fact of a file in exactly one list. */
export interface MergeReport {
  fileId: string;
  filename: string;
  same: MergeItem[];
  new: MergeItem[];
  near: MergeItem[];
  disagree: MergeItem[];
}

/** `POST /api/resumes/upload?profile=<id>` (multipart, one file in the field `resume`): the file's own resume and how it merged. */
export interface UploadResponse {
  resume: ResumeDetail;
  merge: MergeReport;
}

export interface ResumeFile {
  id: string;
  filename: string;
  mime: string;
  size: number;
  origin: ResumeFileOrigin;
  uploadedAt: string;
  /** The resume made from this file. Its original is `GET /api/resumes/{resumeId}/original`. */
  resumeId?: string;
  /** How its lines merged (absent for the file the profile was analysed from). */
  counts?: MergeCounts;
}

/** `GET /api/resumes/files?profile=<id>` */
export interface ResumeFilesResponse {
  profileId: string;
  /** Oldest first: the file of the analysis, then each upload. */
  files: ResumeFile[];
}
