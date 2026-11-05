/**
 * Auto-apply: match -> apply -> track -> learn. The contract between `/api/apply/*` and the web app.
 *
 *   GET    /api/apply/status                         `ApplyStatus` (works with the feature off: `enabled: false`)
 *   GET    /api/apply/profile                        `ApplicantProfile` (the person's saved details for every form)
 *   PUT    /api/apply/profile                        `ApplicantProfileUpdate` -> `ApplicantProfile`
 *   PUT    /api/apply/profile/resume                 the resume file as the raw body (PDF or DOCX) -> `ApplicantProfile`
 *   DELETE /api/apply/profile/resume                 -> `ApplicantProfile`
 *   POST   /api/apply/applications                   `CreateApplicationRequest` -> `ApplicationDetail` (queued, or
 *                                                    needs_user with `manualUrl` for a site that forbids automation)
 *   GET    /api/apply/applications                   `ApplicationList`, newest first
 *   GET    /api/apply/applications/{id}              `ApplicationDetail`
 *   GET    /api/apply/applications/{id}/screenshot   image/jpeg: the filled form as it will be sent (404 until filled)
 *   POST   /api/apply/applications/{id}/answers      `AnswerNeedsRequest` -> `ApplicationDetail` (queued again)
 *   POST   /api/apply/applications/{id}/approve      `ApproveRequest` -> `ApplicationDetail` (409 unless the digest is the
 *                                                    review's: the person approves exactly what they saw)
 *   POST   /api/apply/applications/{id}/stage        `StageChangeRequest` -> `ApplicationDetail` (manual tracking)
 *   POST   /api/apply/applications/{id}/retry        -> `ApplicationDetail` (queued again after failed or needs_user)
 *   DELETE /api/apply/applications/{id}              204
 *   POST   /api/apply/email                          `EmailClassifyRequest` -> `EmailClassification`
 *   GET    /api/apply/metrics                        `ApplyMetrics` (admins only: playbook vs AI runs per ATS)
 *
 * Every route but `status` answers 404 while `AUTO_APPLY_ENABLED` is off. Nothing is submitted without approval.
 *
 * Mirrored by backend/tayyari/contract/apply.py; tests/unit/contract/test_apply_parity.py reads this file. Same subset as the
 * other contracts: interfaces, literal unions, `x?:` optionals (no `null`), `T[]`.
 */

export const APPLY_URL_MAX_CHARS = 2000;
export const APPLY_ANSWER_MAX_CHARS = 4000;
export const APPLY_NOTE_MAX_CHARS = 500;
export const APPLY_EMAIL_MAX_CHARS = 20000;
export const APPLY_DETAIL_MAX_CHARS = 300;
export const APPLY_RESUME_MAX_BYTES = 5242880;

/** Where an application stands. `ready_to_submit` with `approvedAt` set: approved, the worker is submitting it. */
export type ApplicationStage =
  | 'queued'
  | 'filling'
  | 'needs_user'
  | 'ready_to_submit'
  | 'applied'
  | 'viewed'
  | 'interviewing'
  | 'offer'
  | 'rejected'
  | 'failed';

/** The applicant tracking system behind a link. Workday and unknown go straight to the AI fallback. */
export type AtsKind = 'greenhouse' | 'lever' | 'ashby' | 'workday' | 'unknown';

export type ApplySource = 'match' | 'link';

export type StageSource = 'engine' | 'user' | 'email' | 'system';

export type FieldKind =
  | 'text'
  | 'email'
  | 'tel'
  | 'url'
  | 'number'
  | 'date'
  | 'textarea'
  | 'select'
  | 'radio'
  | 'checkbox'
  | 'file';

/** Where a filled value came from. `model` answers cite the person's facts (`factIds`). */
export type FieldSource = 'details' | 'resume' | 'answers' | 'model' | 'agent' | 'user' | 'default';

export type Confidence = 'high' | 'medium' | 'low';

/** How a run filled the form: a deterministic playbook, the AI browser agent, or not at all. */
export type RunMode = 'playbook' | 'ai' | 'manual';

export type RunOutcome = 'success' | 'needs_user' | 'failed' | 'blocked';

export type PlaybookStatus = 'active' | 'draft' | 'retired';

export type PlaybookOrigin = 'builtin' | 'recorded' | 'manual';

/** What an employer's email says about an application (`none`: nothing that moves a stage). */
export type EmailStage = 'applied' | 'viewed' | 'interviewing' | 'offer' | 'rejected' | 'none';

export interface ApplyStatus {
  enabled: boolean;
  signedIn: boolean;
  /** This visitor may use auto-apply without an account (no sign-in provider is configured). */
  anonymous: boolean;
  /** The AI fallback adapters this server has keys for, in the order they are tried. */
  agents: string[];
}

/** What the person saved once for every application. Typed by them; nothing here is inferred. */
export interface ApplicantDetails {
  firstName?: string;
  lastName?: string;
  email?: string;
  phone?: string;
  location?: string;
  currentCompany?: string;
  currentTitle?: string;
  linkedin?: string;
  github?: string;
  website?: string;
  workAuthorization?: string;
  needsSponsorship?: string;
  willingToRelocate?: string;
  noticePeriod?: string;
  salaryExpectation?: string;
  startDate?: string;
  howDidYouHear?: string;
  pronouns?: string;
  eeoGender?: string;
  eeoRace?: string;
  eeoVeteran?: string;
  eeoDisability?: string;
}

export interface ApplicantProfile {
  details: ApplicantDetails;
  hasResume: boolean;
  savedAnswers: number;
  resumeName?: string;
  updatedAt?: string;
}

export interface ApplicantProfileUpdate {
  details: ApplicantDetails;
}

/** One field of the form as it will be sent. */
export interface FilledField {
  key: string;
  label: string;
  kind: FieldKind;
  required: boolean;
  value?: string;
  source?: FieldSource;
  profileKey?: string;
  confidence?: Confidence;
  factIds?: string[];
}

/** A field the engine will not fill on its own: no saved answer, a custom question, or a low-confidence answer. */
export interface NeedsInput {
  key: string;
  label: string;
  kind: FieldKind;
  required: boolean;
  reason: string;
  options?: string[];
  suggestion?: string;
  factIds?: string[];
}

/** What will be sent, captured before anything was submitted. Approving signs `digest`. */
export interface ApplicationReview {
  digest: string;
  capturedAt: string;
  mode: RunMode;
  fields: FilledField[];
  skipped: FilledField[];
  warnings: string[];
  playbookId?: string;
  agent?: string;
  submitLabel?: string;
}

export interface ApplicationEvent {
  at: string;
  kind: string;
  source: StageSource;
  fromStage?: ApplicationStage;
  toStage?: ApplicationStage;
  note?: string;
}

export interface ApplicationSummary {
  id: string;
  stage: ApplicationStage;
  source: ApplySource;
  ats: AtsKind;
  domain: string;
  jobUrl: string;
  createdAt: string;
  updatedAt: string;
  needsCount: number;
  hasScreenshot: boolean;
  company?: string;
  title?: string;
  location?: string;
  matchId?: string;
  approvedAt?: string;
  stageReason?: string;
}

export interface ApplicationDetail {
  id: string;
  stage: ApplicationStage;
  source: ApplySource;
  ats: AtsKind;
  domain: string;
  jobUrl: string;
  createdAt: string;
  updatedAt: string;
  needsCount: number;
  hasScreenshot: boolean;
  needs: NeedsInput[];
  events: ApplicationEvent[];
  company?: string;
  title?: string;
  location?: string;
  matchId?: string;
  approvedAt?: string;
  stageReason?: string;
  applyUrl?: string;
  manualUrl?: string;
  description?: string;
  submittedAt?: string;
  review?: ApplicationReview;
}

export interface ApplicationList {
  applications: ApplicationSummary[];
}

export interface CreateApplicationRequest {
  url: string;
  matchId?: string;
  profileId?: string;
}

export interface NeedAnswer {
  key: string;
  value: string;
}

export interface AnswerNeedsRequest {
  answers: NeedAnswer[];
  /** Keep these answers for later applications (cached per person). */
  remember: boolean;
}

export interface ApproveRequest {
  digest: string;
}

export interface StageChangeRequest {
  stage: ApplicationStage;
  note?: string;
}

/** An email the person pasted. The body is classified and dropped: never stored or logged. */
export interface EmailClassifyRequest {
  subject: string;
  sender: string;
  body: string;
  applicationId?: string;
}

export interface EmailClassification {
  stage: EmailStage;
  confidence: Confidence;
  /** An application's stage was changed because of this email. */
  applied: boolean;
  /** The sentence of the email the classification rests on (checked to be in the email). */
  quote?: string;
  applicationId?: string;
  note?: string;
}

export interface MetricsRow {
  ats: string;
  runs: number;
  playbookRuns: number;
  aiRuns: number;
  successes: number;
  needsUser: number;
  failures: number;
  blocked: number;
  aiShare: number;
  successRate: number;
  avgSteps: number;
  costMicroUsd: number;
}

export interface FailureCount {
  reason: string;
  count: number;
}

export interface PlaybookSummary {
  id: string;
  ats: string;
  status: PlaybookStatus;
  origin: PlaybookOrigin;
  version: number;
  successes: number;
  failures: number;
  updatedAt: string;
  domain?: string;
}

export interface ApplyMetrics {
  since: string;
  total: MetricsRow;
  byAts: MetricsRow[];
  failureReasons: FailureCount[];
  playbooks: PlaybookSummary[];
}
