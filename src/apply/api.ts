/**
 * Auto-apply's API (shared/apply.ts, the routes listed at its top). Same-origin JSON; errors carry the server's own
 * sentence and, for a 409 on start, the application that already exists for the posting.
 *
 * Only the new contract's names are used here. Every call is signed-in only on the server; a 401 comes back as an
 * `ApplyError` with status 401 and the shell shows the sign-in panel.
 */
import type {
  AnswerRequest,
  AnswerResult,
  ApplicationDetail,
  ApplicationList,
  ApplicationStage,
  ApplyCheck,
  ApplyCheckRequest,
  ApplyMetrics,
  ApplyStatus,
  AtsKind,
  EmailClassification,
  EmailClassifyRequest,
  FirstCheckRequest,
  ResolveOutcome,
  SaveAnswerRequest,
  StartApplicationRequest,
  StopReason,
  YourDetails,
} from '../../shared/apply';

export class ApplyError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** The JSON error body, when there was one (`{error, existingApplicationId?, …}`). */
    readonly body: Record<string, unknown> = {},
  ) {
    super(message);
  }

  /** The earlier application to the same posting, on a 409 from start. */
  get existingApplicationId(): string | undefined {
    const id = this.body.existingApplicationId ?? this.body.applicationId;
    return typeof id === 'string' ? id : undefined;
  }
}

/** The sentence to show for any failure: the server's own when it sent one. */
export function errorText(err: unknown, fallback = 'Something went wrong. Try again.'): string {
  return err instanceof ApplyError ? err.message : fallback;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: 'same-origin', ...init });
  if (!res.ok) {
    let message = `Something went wrong (${res.status}).`;
    let body: Record<string, unknown> = {};
    try {
      const raw: unknown = await res.json();
      if (raw && typeof raw === 'object') body = raw as Record<string, unknown>;
      const text = body.error ?? body.detail;
      if (typeof text === 'string' && text) message = text;
    } catch {
      /* not JSON */
    }
    throw new ApplyError(message, res.status, body);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

const app = (id: string, tail = '') => `/api/apply/applications/${encodeURIComponent(id)}${tail}`;

export const applyApi = {
  status: () => call<ApplyStatus>('/api/apply/status'),
  /** The cheap pre-check behind "Apply for me": read-only, charges nothing. */
  check: (body: ApplyCheckRequest) => call<ApplyCheck>('/api/apply/check', json('POST', body)),
  /** Start: the row is created `queued` with a credit hold, never charged here. */
  start: (body: StartApplicationRequest) => call<ApplicationDetail>('/api/apply/applications', json('POST', body)),
  list: () => call<ApplicationList>('/api/apply/applications'),
  get: (id: string) => call<ApplicationDetail>(app(id)),
  /** A batch of answers; saved ones fill every other waiting form that asks the same question (`requeued`). */
  answer: (id: string, body: AnswerRequest) => call<AnswerResult>(app(id, '/answers'), json('POST', body)),
  /** The digest must be the review's: the person approves exactly what they saw. */
  approve: (id: string, digest: string) => call<ApplicationDetail>(app(id, '/approve'), json('POST', { digest })),
  /** Any state before submitting -> cancelled; the hold is released. */
  cancel: (id: string) => call<ApplicationDetail>(app(id, '/cancel'), { method: 'POST' }),
  /** Check this one: the person says whether it went through. */
  resolve: (id: string, outcome: ResolveOutcome) => call<ApplicationDetail>(app(id, '/resolve'), json('POST', { outcome })),
  /** "I applied there": needs_signin -> sent, the person's word (its own route, like approve and resolve). */
  appliedThere: (id: string) => call<ApplicationDetail>(app(id, '/applied-there'), { method: 'POST' }),
  /** Manual tracking after sent (viewed, interviewing, offer, rejected). */
  stage: (id: string, stage: ApplicationStage, note?: string) =>
    call<ApplicationDetail>(app(id, '/stage'), json('POST', note ? { stage, note } : { stage })),
  /** Queued again after failed (before the click), needs_signin or closed. Never offered after a click. */
  retry: (id: string) => call<ApplicationDetail>(app(id, '/retry'), { method: 'POST' }),
  remove: (id: string) => call<void>(app(id), { method: 'DELETE' }),

  details: () => call<YourDetails>('/api/apply/details'),
  saveAnswer: (body: SaveAnswerRequest) => call<YourDetails>('/api/apply/details/answer', json('PUT', body)),
  /** "Still true?": confirms a stale answer without changing it. */
  stillTrue: (questionKey: string) => call<YourDetails>('/api/apply/details/still-true', json('POST', { questionKey })),
  firstCheck: (body: FirstCheckRequest) => call<YourDetails>('/api/apply/details/first-check', json('POST', body)),
  setSelfId: (selfIdOnForms: boolean) => call<YourDetails>('/api/apply/details/self-id', json('PUT', { selfIdOnForms })),
  /** The consent screen's "I agree" (spec 5); the status then carries `consentAt`. */
  consent: () => call<ApplyStatus>('/api/apply/consent', { method: 'POST' }),
  /** The default resume, PDF or DOCX, as the raw body. Read `details()` again afterwards for its name. */
  uploadResume: (file: File) =>
    call<unknown>('/api/apply/profile/resume', {
      method: 'PUT',
      headers: { 'content-type': file.type || 'application/pdf', 'x-file-name': encodeURIComponent(file.name) },
      body: file,
    }).then(() => undefined),
  removeResume: () => call<unknown>('/api/apply/profile/resume', { method: 'DELETE' }).then(() => undefined),

  email: (body: EmailClassifyRequest) => call<EmailClassification>('/api/apply/email', json('POST', body)),
  /** Admins only; anyone else gets a 404. */
  metrics: () => call<ApplyMetrics>('/api/apply/metrics'),
};

/** The filled form (`review`) or the employer's confirmation page (`confirmation`); `v` busts the cache on a new fill. */
export function screenshotUrl(id: string, kind: 'review' | 'confirmation', v: string): string {
  return `${app(id, '/screenshot')}?kind=${kind}&v=${encodeURIComponent(v)}`;
}

/** Honest status words (spec 2): "Filling", "Waiting for you", "Sending". */
export const STAGE_LABEL: Record<ApplicationStage, string> = {
  queued: 'Waiting to fill',
  filling: 'Filling',
  waiting_for_you: 'Waiting for you',
  needs_signin: 'Needs you on the site',
  approved: 'Approved',
  submitting: 'Sending',
  sent: 'Sent',
  check_this_one: 'Check this one',
  failed: "Didn't go",
  closed: 'Posting closed',
  cancelled: 'Skipped',
  viewed: 'Heard back',
  interviewing: 'Interviewing',
  offer: 'Offer',
  rejected: 'Not this time',
  // legacy names, removed with the contract's in Z; nothing new is written with them
  needs_user: 'Waiting for you',
  ready_to_submit: 'Waiting for you',
  applied: 'Sent',
};

/**
 * How a stage's chip reads (apply.css `ap-tone-*`): `you` waits for the person (well and a strong hairline), `ok` is
 * sent or ready (the soft accent), `bad` is only Check this one and a failure (danger), `plain` is everything else.
 */
export type StageTone = 'you' | 'ok' | 'bad' | 'plain';

export function stageTone(stage: ApplicationStage, needsCount = 0): StageTone {
  switch (stage) {
    case 'waiting_for_you':
    case 'needs_user':
    case 'ready_to_submit':
      return needsCount > 0 ? 'you' : 'ok';
    case 'needs_signin':
      return 'you';
    case 'check_this_one':
    case 'failed':
      return 'bad';
    case 'sent':
    case 'applied':
    case 'viewed':
    case 'interviewing':
    case 'offer':
      return 'ok';
    default:
      return 'plain';
  }
}

export const ATS_LABEL: Record<AtsKind, string> = {
  greenhouse: 'Greenhouse',
  lever: 'Lever',
  ashby: 'Ashby',
  workday: 'Workday',
  unknown: '',
};

/** Why a run stopped, in the person's words. */
export const STOP_REASON_TEXT: Record<StopReason, string> = {
  questions: 'The form asks things we do not know yet',
  signin: 'The form asks you to sign in or create an account',
  challenge: 'The form shows a check meant for a person',
  form_changed: 'The form changed after you reviewed it',
  verification_code: 'The form wants a code the employer emailed you',
  fill_error: 'A field would not take its value',
  guard_probe: 'Our safety check did not pass, so nothing was filled',
};

/** Still moving on the worker's side: worth polling. */
export const BUSY_STAGES: readonly ApplicationStage[] = ['queued', 'filling', 'approved', 'submitting'];

export function busy(stage: ApplicationStage): boolean {
  return BUSY_STAGES.includes(stage);
}

/** What a person can mark after sending; the stage moves only forward from `sent`. */
export const TRACKING_STAGES: readonly ApplicationStage[] = ['viewed', 'interviewing', 'offer', 'rejected'];

/** Sent and every later stage: the "After sending" pipeline. */
export const SENT_STAGES: readonly ApplicationStage[] = ['sent', ...TRACKING_STAGES, 'applied'];

export function isSent(stage: ApplicationStage): boolean {
  return SENT_STAGES.includes(stage);
}
