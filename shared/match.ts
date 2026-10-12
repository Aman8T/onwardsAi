/**
 * Posting match: the contract between the match API and the web app.
 *
 * Mirrored by backend/tayyari/contract/match.py; backend/tests/unit/match/test_contract_parity.py parses this
 * file and fails when the two drift. Keep to what that parser reads: interfaces, literal unions, `x?:`
 * optionals, `T[]`, inline objects. No `Record`, `null`, tuples or `typeof` in types.
 *
 * Shape: a `Posting` is read on its own (never beside a person) into requirements and gates. Every
 * requirement is checked against the person's `Fact`s into a `Coverage` row; `MatchResult` carries the band,
 * counts and confidence that code computed from those rows. A `ResumeVariant` and a `CoverNote` are composed
 * from the same facts, and every sentence names the facts it rests on (`factIds`).
 *
 * `Fact` and `FactRef` here are not the portfolio's `Fact` and `SourceRef` (shared/portfolio.ts): import them
 * under another name where both are needed.
 */

/* ───────────────────────── enums ───────────────────────── */

export type FactSource = 'resume' | 'github' | 'lovable' | 'link' | 'user';
export type MatchFactKind = 'experience' | 'accomplishment' | 'skill' | 'education' | 'project' | 'cert' | 'other';
/** Strongest first: a public artifact, built in a project with an outcome, used in a bullet, only listed. */
export type EvidenceTier = 'artifact' | 'built' | 'used' | 'listed';
export type RequirementTier = 'must' | 'required' | 'preferred' | 'nice';
export type RequirementKind =
  | 'skill'
  | 'years'
  | 'degree'
  | 'cert'
  | 'tool'
  | 'domain'
  | 'location'
  | 'auth'
  | 'language'
  | 'other';
export type Provenance = 'stated' | 'structural' | 'inferred';
export type WorkMode = 'onsite' | 'hybrid' | 'remote' | 'unspecified';
export type GateVerdict = 'pass' | 'fail' | 'uncertain';
export type CoverageStatus = 'covered' | 'partial' | 'synonym_only' | 'missing_have_it' | 'missing_gap' | 'unknown';
export type MatchBand = 'strong' | 'good' | 'stretch' | 'weak';
export type Confidence = 'high' | 'medium' | 'low';
export type BulletVerdict = 'exact' | 'reframed' | 'stretch';
export type BulletStatus = 'verified' | 'needs_user' | 'dropped';
export type DecisionKind = 'stretch' | 'number' | 'confirm_term';
export type DecisionChoiceKey = 'keep' | 'soften' | 'drop' | 'fill' | 'use_posting_term' | 'keep_my_wording';
export type ChangeKind = 'surfaced' | 'reordered' | 'reworded' | 'left_out';
export type CoverKind = 'match' | 'gap' | 'logistics' | 'close';

/* ───────────────────────── constants ───────────────────────── */

/** Bumped when the shape of a stored `MatchResult` changes. */
export const MATCH_VERSION = 1;

/** How much a requirement counts in the band, by how strongly the posting asks for it. */
export const TIER_WEIGHTS = {
  must: 1,
  required: 0.8,
  preferred: 0.5,
  nice: 0.25,
} as const satisfies { [K in RequirementTier]: number };

export const MAX_REQUIREMENTS = 30;
export const MAX_GATES = 6;
export const MAX_QUESTIONS = 3;
/** Only a `fail` on a gate of at least this severity blocks. `uncertain` never blocks. */
export const GATE_BLOCK_SEVERITY = 7;

/** Lower bounds of the weighted coverage (0..1) for each band; below BAND_STRETCH is `weak`. */
export const BAND_STRONG = 0.8;
export const BAND_GOOD = 0.6;
export const BAND_STRETCH = 0.35;

export const COVER_WORDS_MIN = 250;
export const COVER_WORDS_MAX = 400;

/* ───────────────────────── facts ───────────────────────── */

/** Where a fact comes from: a source kind, a document id and (when known) the lines its quote sits on. */
export interface FactRef {
  source: FactSource;
  docId: string;
  /** `[first, last]`, 1-based. */
  lines?: number[];
  quote: string;
}

/** Something true about the person, with the words of its source. `quote` is a passage of the source. */
export interface Fact {
  id: string;
  text: string;
  kind: MatchFactKind;
  refs: FactRef[];
  evidenceTier?: EvidenceTier;
}

/* ───────────────────────── posting ───────────────────────── */

export interface Requirement {
  id: string;
  text: string;
  quote: string;
  tier: RequirementTier;
  kind: RequirementKind;
  provenance: Provenance;
  domainHint?: string;
  /** The named skills, tools or credentials of the requirement, each a passage of `quote`. */
  terms: string[];
}

export interface Gate {
  id: string;
  question: string;
  quote: string;
  /** 1..10 */
  severity: number;
}

export interface Posting {
  hash: string;
  title: string;
  company?: string;
  location?: string;
  workMode: WorkMode;
  rawText: string;
  requirements: Requirement[];
  gates: Gate[];
  /** Text in the posting that tries to instruct a model. Reported, never followed. */
  anomalies: string[];
  /** Web addresses written in the posting. Listed, never opened. */
  links: string[];
}

/* ───────────────────────── match ───────────────────────── */

export interface GateAnswer {
  gateId: string;
  factIds: string[];
  verdict: GateVerdict;
  note: string;
}

export interface Coverage {
  requirementId: string;
  factIds: string[];
  status: CoverageStatus;
  /** 0..4: 4 direct, specific, verifiable; 3 close with a minor gap; 2 claimed but not substantiated; 1 weak; 0 absent. */
  level: number;
  note: string;
  interviewRisk?: string;
  mitigation?: string;
}

export interface StatusCounts {
  covered: number;
  partial: number;
  synonym_only: number;
  missing_have_it: number;
  missing_gap: number;
  unknown: number;
}

export interface RadarAxis {
  domain: string;
  /** The person's mastery of the domain, 0..100. */
  you: number;
  /** What the role expects of the domain, 0..100 against the busiest domain. */
  role: number;
}

export interface Radar {
  axes: RadarAxis[];
}

export interface Question {
  id: string;
  requirementId?: string;
  gateId?: string;
  text: string;
  why: string;
}

export interface MatchResult {
  postingHash: string;
  band: MatchBand;
  counts: StatusCounts;
  confidence: Confidence;
  confidenceNote: string;
  gates: GateAnswer[];
  blocked: boolean;
  coverage: Coverage[];
  radar: Radar;
  questions: Question[];
  version: number;
}

/* ───────────────────────── resume variant ───────────────────────── */

export interface LintItem {
  id: string;
  ok: boolean;
  label: string;
  detail: string;
  location?: string;
}

export interface LintReport {
  items: LintItem[];
}

export interface Bullet {
  id: string;
  text: string;
  factIds: string[];
  verdict: BulletVerdict;
  /** `[x]` slots still waiting for a figure, e.g. `[x]%`. */
  placeholders: string[];
  status: BulletStatus;
}

export interface SkillItem {
  name: string;
  factIds: string[];
  evidenceTier?: EvidenceTier;
}

export interface SkillGroup {
  group: string;
  items: SkillItem[];
}

export interface ResumeHeader {
  name: string;
  headline?: string;
  /** Email and phone exactly as the resume writes them, shown as plain text. */
  contact: string[];
}

export interface ExperienceEntry {
  entryFactId: string;
  title: string;
  org?: string;
  /** ASCII range such as `Jan 2021 - Present`. */
  dates?: string;
  bullets: Bullet[];
}

export interface EducationEntry {
  factId: string;
  title: string;
  org?: string;
  dates?: string;
}

export interface Decision {
  bulletId: string;
  kind: DecisionKind;
  prompt: string;
  /** stretch: keep/soften/drop; number: fill/soften/drop; confirm_term: use_posting_term/keep_my_wording/drop. */
  options: DecisionChoiceKey[];
}

export interface Change {
  kind: ChangeKind;
  text: string;
  factIds: string[];
}

export interface ResumeVariant {
  header: ResumeHeader;
  summary: Bullet;
  skills: SkillGroup[];
  experience: ExperienceEntry[];
  projects: ExperienceEntry[];
  education: EducationEntry[];
  decisions: Decision[];
  changes: Change[];
  lint?: LintReport;
}

/** The person's answer to one `Decision` (the body item of the PATCH that settles a resume). */
export interface DecisionChoice {
  bulletId: string;
  choice: DecisionChoiceKey;
  /** The figure for `fill`. */
  value?: string;
}

/* ───────────────────────── cover note ───────────────────────── */

export interface CoverParagraph {
  text: string;
  requirementIds: string[];
  factIds: string[];
  kind: CoverKind;
}

export interface CoverNote {
  paragraphs: CoverParagraph[];
  wordCount: number;
  lint?: LintReport;
}
