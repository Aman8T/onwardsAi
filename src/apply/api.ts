/** Auto-apply's API (shared/apply.ts). Same-origin JSON; errors carry the server's own sentence. */
import type {
  AnswerNeedsRequest,
  ApplicantDetails,
  ApplicantProfile,
  ApplicationDetail,
  ApplicationList,
  ApplicationStage,
  ApplyMetrics,
  ApplyStatus,
  EmailClassification,
  EmailClassifyRequest,
} from '../../shared/apply';

export class ApplyError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: 'same-origin', ...init });
  if (!res.ok) {
    let message = `Something went wrong (${res.status}).`;
    try {
      const body = (await res.json()) as { error?: string; detail?: string };
      message = body.error ?? body.detail ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApplyError(message, res.status);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

export const applyApi = {
  status: () => call<ApplyStatus>('/api/apply/status'),
  profile: () => call<ApplicantProfile>('/api/apply/profile'),
  saveDetails: (details: ApplicantDetails) => call<ApplicantProfile>('/api/apply/profile', json('PUT', { details })),
  uploadResume: (file: File) =>
    call<ApplicantProfile>('/api/apply/profile/resume', {
      method: 'PUT',
      headers: { 'content-type': file.type || 'application/pdf', 'x-file-name': file.name },
      body: file,
    }),
  removeResume: () => call<ApplicantProfile>('/api/apply/profile/resume', { method: 'DELETE' }),
  list: () => call<ApplicationList>('/api/apply/applications'),
  get: (id: string) => call<ApplicationDetail>(`/api/apply/applications/${encodeURIComponent(id)}`),
  create: (url: string, matchId?: string) =>
    call<ApplicationDetail>('/api/apply/applications', json('POST', matchId ? { url, matchId } : { url })),
  answer: (id: string, body: AnswerNeedsRequest) =>
    call<ApplicationDetail>(`/api/apply/applications/${encodeURIComponent(id)}/answers`, json('POST', body)),
  approve: (id: string, digest: string) =>
    call<ApplicationDetail>(`/api/apply/applications/${encodeURIComponent(id)}/approve`, json('POST', { digest })),
  stage: (id: string, stage: ApplicationStage, note?: string) =>
    call<ApplicationDetail>(`/api/apply/applications/${encodeURIComponent(id)}/stage`, json('POST', note ? { stage, note } : { stage })),
  retry: (id: string) => call<ApplicationDetail>(`/api/apply/applications/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
  remove: (id: string) => call<void>(`/api/apply/applications/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  email: (body: EmailClassifyRequest) => call<EmailClassification>('/api/apply/email', json('POST', body)),
  metrics: () => call<ApplyMetrics>('/api/apply/metrics'),
};

export const STAGE_LABEL: Record<ApplicationStage, string> = {
  queued: 'Queued',
  filling: 'Filling the form',
  needs_user: 'Needs you',
  ready_to_submit: 'Ready for your review',
  applied: 'Applied',
  viewed: 'Viewed',
  interviewing: 'Interviewing',
  offer: 'Offer',
  rejected: 'Not this time',
  failed: 'Could not fill',
  waiting_for_you: 'Waiting for you',
  needs_signin: 'Needs you on the site',
  approved: 'Approved',
  submitting: 'Sending',
  sent: 'Sent',
  check_this_one: 'Check this one',
  closed: 'Posting closed',
  cancelled: 'Cancelled',
};

export const TRACKING_STAGES: ApplicationStage[] = ['applied', 'viewed', 'interviewing', 'offer', 'rejected'];

/** Still moving on the worker's side: worth polling. */
export function busy(stage: ApplicationStage, approvedAt?: string): boolean {
  return stage === 'queued' || stage === 'filling' || (stage === 'ready_to_submit' && Boolean(approvedAt));
}
