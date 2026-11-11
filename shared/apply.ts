/**
 * Auto-apply: match -> apply -> track -> learn. The contract between `/api/apply/*` and the web app.
 *
 *   GET    /api/apply/status                         `ApplyStatus` (works with the feature off: `enabled: false`)
 *   POST   /api/apply/consent                        -> `ApplyStatus` (the person agreed to what auto-apply sends)
 *   POST   /api/apply/check                          `ApplyCheckRequest` -> `ApplyCheck` (read-only, charges nothing)
 *   POST   /api/apply/applications                   `StartApplicationRequest` -> `ApplicationDetail` (queued, with a
 *                                                    credit hold; 409 with `existingApplicationId` or the reason, 402
 *                                                    when no credit is left)
 *   GET    /api/apply/applications                   `ApplicationList`, newest first
 *   GET    /api/apply/applications/{id}              `ApplicationDetail`
 *   GET    /api/apply/applications/{id}/resume-copy  application/pdf, the frozen copy of the default resume (404 without)
 *   GET    /api/apply/applications/{id}/screenshot   image/jpeg, `?kind=review|confirmation` (404 until there is one)
 *   POST   /api/apply/applications/{id}/answers      `AnswerRequest` -> `AnswerResult` (queued again, with every other
 *                                                    waiting application that asks the same question)
 *   POST   /api/apply/applications/{id}/approve      `ApproveRequest` -> `ApplicationDetail` (409 unless the digest is the
 *                                                    review's: the person approves exactly what they saw)
 *   POST   /api/apply/applications/{id}/cancel       -> `ApplicationDetail` (any stage before submitting)
 *   POST   /api/apply/applications/{id}/resolve      `ResolveRequest` -> `ApplicationDetail` (check_this_one only)
 *   POST   /api/apply/applications/{id}/applied-there -> `ApplicationDetail` (needs_signin -> sent, "I applied there")
 *   POST   /api/apply/applications/{id}/stage        `StageChangeRequest` -> `ApplicationDetail` (manual tracking)
 *   POST   /api/apply/applications/{id}/retry        -> `ApplicationDetail` (queued again after failed, needs_signin, closed)
 *   DELETE /api/apply/applications/{id}              204
 *   GET    /api/apply/details                        `YourDetails`
 *   PUT    /api/apply/details/answer                 `SaveAnswerRequest` -> `YourDetails`
 *   PUT    /api/apply/details/answers/{key}          `{askEachTime, value?}` -> `YourDetails`
 *   DELETE /api/apply/details/answers/{key}          -> `YourDetails`
 *   POST   /api/apply/details/still-true             `StillTrueRequest` -> `YourDetails`
 *   POST   /api/apply/details/first-check            `FirstCheckRequest` -> `YourDetails`
 *   PUT    /api/apply/details/self-id                `SelfIdRequest` -> `YourDetails`
 *   PUT    /api/apply/profile/resume                 the resume file as the raw body (PDF or DOCX) -> `YourDetails`
 *   DELETE /api/apply/profile/resume                 -> `YourDetails`
 *   POST   /api/apply/email                          `EmailClassifyRequest` -> `EmailClassification`
 *   GET    /api/apply/metrics                        `ApplyMetrics` (admins only: playbook vs field-list loop runs per ATS)
 *
 * Every route but `status` answers 404 while `AUTO_APPLY_ENABLED` is off, and 401 without a sign-in. Nothing is
 * submitted without approval.
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

/**
 * Where an application stands (spec 3). `waiting_for_you`: questions or the review wait for the person. `needs_signin`: a
 * login wall, a CAPTCHA or an emailed code (`stopReason` says which). `submitting`: the send was committed before the
 * click; only the engine leaves it. `check_this_one`: no clear outcome after the click, never retried.
 */
export type ApplicationStage =
  | 'queued'
  | 'filling'
  | 'waiting_for_you'
  | 'needs_signin'
  | 'approved'
  | 'submitting'
  | 'sent'
  | 'check_this_one'
  | 'failed'
  | 'closed'
  | 'cancelled'
  | 'viewed'
  | 'interviewing'
  | 'offer'
  | 'rejected';

/** The applicant tracking system behind a link. */
export type AtsKind = 'greenhouse' | 'lever' | 'ashby' | 'workday' | 'unknown';

/** A posting match (a pasted application link included), or a Discover listing (which runs the match first). */
export type ApplySource = 'match' | 'discover';

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
  | 'file'
  | 'combobox'
  | 'checkboxGroup';

/** Where a filled value came from. `model` answers cite the person's facts (`factIds`). */
export type FieldSource = 'details' | 'resume' | 'fact' | 'answers' | 'model' | 'user' | 'default';

export type Confidence = 'high' | 'medium' | 'low';

/** How a run filled the form: a playbook (no model), the field-list loop (Haiku, then Sonnet), or not at all. */
export type RunMode = 'playbook' | 'loop' | 'manual';

/** How one engine run ended. `interrupted`: stopped half way; `deferred`: put back for capacity (a 429, the spend ceiling). */
export type RunOutcome =
  | 'success'
  | 'questions'
  | 'needs_signin'
  | 'closed'
  | 'failed'
  | 'check_this_one'
  | 'interrupted'
  | 'deferred';

export type PlaybookStatus = 'active' | 'draft' | 'retired';

export type PlaybookOrigin = 'builtin' | 'recorded' | 'manual';

/** What an employer's email says about an application (`none`: nothing that moves a stage). */
export type EmailStage = 'applied' | 'viewed' | 'interviewing' | 'offer' | 'rejected' | 'none';

/** Why a run stopped short of a review or a send. `guard_probe`: the guard did not prove itself, nothing was filled. */
export type StopReason =
  | 'questions'
  | 'signin'
  | 'challenge'
  | 'form_changed'
  | 'verification_code'
  | 'fill_error'
  | 'guard_probe';

/** The match's tailored resume (exported to PDF) or the person's saved file. */
export type ResumeChoice = 'tailored' | 'saved';

/** Held at start, charged when sent, released when not. */
export type CreditState = 'held' | 'charged' | 'released';

/** Why "Apply for me" is not offered. `unknown_liveness`: never shown as closed; the employer's site is offered. */
export type ApplyBlockReason =
  | 'signed_out'
  | 'no_credit'
  | 'closed'
  | 'not_fillable'
  | 'own_account_ats'
  | 'blocked_by_requirement'
  | 'already_applied'
  | 'manual_only'
  | 'unknown_liveness';

/** Where a kept answer came from. */
export type AnswerSource = 'resume' | 'first_check' | 'form' | 'you' | 'default';

/** The Your details section an answer is shown under. */
export type AnswerGroup = 'you' | 'work' | 'education' | 'registrations' | 'where_how' | 'self_id';

/** The person's word on a `check_this_one` application. */
export type ResolveOutcome = 'sent' | 'not_sent';

/**
 * One step of a fill script. There is no upload step: the review leaves the file input empty, and the submit pass attaches
 * the resume itself after the `submitting` marker, once the guard is lifted for the form's upload hosts.
 */
export type ScriptOp = 'fill' | 'select' | 'check' | 'choose' | 'group';

export type ProgressState = 'done' | 'now' | 'todo';

export interface ApplyStatus {
  enabled: boolean;
  signedIn: boolean;
  /** The person did (or skipped) the 30-second first check. */
  firstCheckDone?: boolean;
  /** When the person agreed to what auto-apply sends and who processes it; absent until they did. */
  consentAt?: string;
  /** Credits charged when an application is sent; absent while the price is not set. */
  price?: number;
  /** Free applications this account has left. */
  freeLeft?: number;
}

/** One field of the form as it will be sent. */
export interface FilledField {
  key: string;
  label: string;
  kind: FieldKind;
  required: boolean;
  /** The field's stable selector (id or name, never a positional path). */
  selector: string;
  value?: string;
  source?: FieldSource;
  profileKey?: string;
  confidence?: Confidence;
  factIds?: string[];
  /** For a select, radio or combobox: the option's own value (`value` is its visible text). */
  optionValue?: string;
  options?: string[];
  /** The form's own section heading. */
  group?: string;
  /** Position in the form. */
  order?: number;
  /** A value the person cannot change in Review. */
  locked?: boolean;
  /** The answer bank key. */
  questionKey?: string;
  /** A signature, declaration or consent tick: typed by the person in Review, never kept. */
  attestation?: boolean;
}

/** Another waiting application that asks the same question. */
export interface AlsoWaiting {
  applicationId: string;
  company: string;
}

/** A field the engine will not fill on its own: no saved answer, a custom question, or a low-confidence answer. */
export interface NeedsInput {
  key: string;
  label: string;
  kind: FieldKind;
  required: boolean;
  reason: string;
  /** The answer bank key; one answer fills every waiting form that asks it. */
  questionKey: string;
  options?: string[];
  suggestion?: string;
  factIds?: string[];
  /** Answered for this form only: the save box starts unticked. */
  perOffer?: boolean;
  /** Typed by the person on the employer's page; a required one blocks approval. */
  onSite?: boolean;
  /** A signature, declaration or consent tick the person gives in Review for this form only (never kept). */
  attestation?: boolean;
  /** The employer whose form asks it. */
  requiredBy?: string;
  /** The unit shown beside the input (`%`, `lakh a year`). */
  unit?: string;
  alsoWaiting?: AlsoWaiting[];
}

/** The text of a fact a model answer cites, copied at fill time. */
export interface CitedFact {
  id: string;
  text: string;
}

/** The resume that goes with the application, attached only on approval. `sha256` is part of the digest. */
export interface ReviewResume {
  name: string;
  sha256: string;
  choice: ResumeChoice;
}

/**
 * One of the person's resumes (`/resume`) as Apply for me names it: the default (`ApplyCheck.defaultResume`) or the frozen
 * copy an application was prepared with (`ApplicationDetail.resumeCopy`). `sha256` (frozen copies only) is in the digest.
 */
export interface ResumeRef {
  resumeId: string;
  title: string;
  finalisedAt: string;
  fileName: string;
  sha256?: string;
}

/** One executable step of the review's fill script; the submit pass replays it with no model. */
export interface ScriptStep {
  selector: string;
  op: ScriptOp;
  frame: number;
  value?: string;
  optionValue?: string;
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
  submitLabel?: string;
  resume?: ReviewResume;
  facts?: CitedFact[];
  script?: ScriptStep[];
  /** The model that answered custom questions or ran the field-list loop, when one did. */
  model?: string;
}

export interface ApplicationEvent {
  at: string;
  kind: string;
  source: StageSource;
  fromStage?: ApplicationStage;
  toStage?: ApplicationStage;
  note?: string;
}

/** One section of the form while it is being filled. */
export interface ProgressSection {
  label: string;
  state: ProgressState;
  filled: number;
  total: number;
  /** Fields in this section that wait for the person. */
  waiting?: number;
  note?: string;
}

/** How far a fill has got, written by the worker while the row is `filling`. */
export interface ApplyProgress {
  sections: ProgressSection[];
  filled: number;
  total: number;
  /** The fields filled so far. */
  fields?: FilledField[];
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
  resumeChoice?: ResumeChoice;
  listingId?: string;
  creditState?: CreditState;
  stopReason?: StopReason;
  hasConfirmation?: boolean;
  progress?: ApplyProgress;
  /** When it went to the employer (the click that went through, or the person's word on it); absent before. */
  sentAt?: string;
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
  resumeChoice?: ResumeChoice;
  listingId?: string;
  creditState?: CreditState;
  stopReason?: StopReason;
  hasConfirmation?: boolean;
  progress?: ApplyProgress;
  /** When it went to the employer (as on `ApplicationSummary`). */
  sentAt?: string;
  /** The default resume as it stood when the values were prepared (`GET .../resume-copy` is its file); absent otherwise. */
  resumeCopy?: ResumeRef;
}

export interface ApplicationList {
  applications: ApplicationSummary[];
}

/** The cheap, uncharged pre-check behind "Apply for me". */
export interface ApplyCheckRequest {
  matchId?: string;
  listingId?: string;
  runId?: string;
  applyUrl?: string;
}

/** Whether "Apply for me" is offered for a posting, and why not. */
export interface ApplyCheck {
  offered: boolean;
  reason?: ApplyBlockReason;
  host?: string;
  ats?: AtsKind;
  note?: string;
  /** The earlier application to the same posting (`already_applied`). */
  applicationId?: string;
  /** The match's hard requirements that blocked (`blocked_by_requirement`). */
  gateReasons?: string[];
  /** The employer's own page for the posting, for "Open on <host>'s site" when it is not offered. */
  applyUrl?: string;
  /** The profile's default resume when it has a finalised version: what "my current resume" sends (given a match). */
  defaultResume?: ResumeRef;
}

/** Start an application. The posting match always runs first, so `matchId` is required. */
export interface StartApplicationRequest {
  matchId: string;
  resume: ResumeChoice;
  listingId?: string;
  runId?: string;
  applyUrl?: string;
}

export interface QuestionAnswer {
  questionKey: string;
  value: string;
  /** Keep it in the answer bank for later forms. */
  save: boolean;
}

export interface AnswerRequest {
  answers: QuestionAnswer[];
}

export interface AnswerResult {
  application: ApplicationDetail;
  /** Other waiting applications this answer filled and queued again. */
  requeued: number;
}

export interface ApproveRequest {
  digest: string;
}

export interface ResolveRequest {
  outcome: ResolveOutcome;
}

export interface StageChangeRequest {
  stage: ApplicationStage;
  note?: string;
}

/** Work authorisation for one country (ISO 3166-1 alpha-2); never inferred from another country. */
export interface WorkAuthorization {
  country: string;
  authorised: string;
  needsSponsorship?: string;
}

/** One answer in the answer bank, as Your details shows it. */
export interface SavedAnswer {
  questionKey: string;
  question: string;
  source: AnswerSource;
  askEachTime: boolean;
  answeredAt: string;
  /** Absent for an ask-each-time question with no answer yet, and for a private value. */
  value?: string;
  group?: AnswerGroup;
  /** The employer whose form asked it. */
  answeredOn?: string;
  confirmedAt?: string;
  /** Older than 90 days and not confirmed since: "Still true?". */
  stale?: boolean;
  country?: string;
  /** Saved but never shown back (current CTC). */
  private?: boolean;
}

/** A common question not asked yet. */
export interface BankQuestion {
  questionKey: string;
  question: string;
  group: AnswerGroup;
  kind: FieldKind;
  options?: string[];
}

/** Your details: every kept answer with its source and date, the questions not asked yet, and what is never stored. */
export interface YourDetails {
  answers: SavedAnswer[];
  notAskedYet: BankQuestion[];
  workAuthorization: WorkAuthorization[];
  selfIdOnForms: boolean;
  /** The labels of what we leave to the person. */
  neverStored: string[];
  hasResume: boolean;
  firstCheckDone: boolean;
  resumeName?: string;
  updatedAt?: string;
}

export interface SaveAnswerRequest {
  questionKey: string;
  askEachTime: boolean;
  value?: string;
}

/** The 30-second first check. `workAuthorization` is for India only; `edits` correct what was read from the resume. */
export interface FirstCheckRequest {
  skipped: boolean;
  noticePeriod?: string;
  workAuthorization?: WorkAuthorization;
  edits?: QuestionAnswer[];
}

export interface SelfIdRequest {
  selfIdOnForms: boolean;
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

/** "Still true?" on a saved answer. */
export interface StillTrueRequest {
  questionKey: string;
}

/** One ATS's engine runs (fills and submits) in the window, by how they ended (`RunOutcome`). */
export interface MetricsRow {
  ats: string;
  runs: number;
  playbookRuns: number;
  /** Runs the field-list loop filled (a model chose which saved value goes where). */
  loopRuns: number;
  successes: number;
  questions: number;
  needsSignin: number;
  closed: number;
  failures: number;
  checkThisOne: number;
  interrupted: number;
  deferred: number;
  loopShare: number;
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
