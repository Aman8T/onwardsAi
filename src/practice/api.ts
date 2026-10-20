/**
 * The client for practice the interview: `/api/interview/*` (types in `shared/interview.ts`).
 *
 * Failures come back as JSON `{ "error": "<sentence>" }` with 400 (not understood, or a plan that cannot be made), 404 (the
 * interview or the role is not there any more), 409 (the interview moved on in another tab, or has ended), 429 (a daily
 * window is used) or 503 (practice is switched off, or paused for today). The server's sentence is shown as it was
 * written; it is replaced only when the body held none (a proxy's page, a bare status) or held a machine code instead.
 * A request that never reached the server (offline, a dropped connection) is a `PracticeApiError` with status 0, so a
 * screen can tell "the connection dropped" from "the server said no" with `isOffline`.
 *
 * Answers are read defensively (`read.ts`): an item of the wrong shape is dropped, never trusted, and an answer that is
 * not what was asked for is a 502 "Tayyari ran into trouble". Only text ever goes to the server: never audio.
 */
import type {
  AgainRequest,
  AnswerRequest,
  AssistantReply,
  AssistantRequest,
  Debrief,
  FactRequest,
  InterviewPlan,
  InterviewSession,
  PlanRequest,
  PracticeHistory,
  SessionRequest,
  SessionView,
  StartRequest,
  WorkSubmit,
} from '../../shared/interview';
import { ApiError } from '../lib/api';
import { isObj, readDebrief, readHistory, readPlan, readReply, readSession, readSessionView } from './read';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const TROUBLE = 'Leafout ran into trouble. Please try again in a moment.';
export const OFFLINE = "We couldn't reach Leafout. Check your connection and try again.";

export class PracticeApiError extends ApiError {
  /** Seconds the server asks the person to wait (a 429's `Retry-After`), when it gave a number. */
  readonly retryAfter?: number;
  constructor(message: string, status: number, retryAfter?: number) {
    super(message, status);
    this.name = 'PracticeApiError';
    this.retryAfter = retryAfter;
  }
}

/** A machine code such as `interview_disabled`, as opposed to a sentence. */
const looksLikeCode = (s: string) => /^[a-z][a-z0-9_]*$/.test(s);

const FALLBACK: Record<number, string> = {
  400: "That didn't go through. Please go back and try again.",
  404: "This practice interview isn't here any more. Practice interviews are kept for 30 days.",
  409: 'This interview moved on in another tab. Reload to carry on.',
  429: 'Too many requests right now. Please wait a moment and try again.',
  503: 'Practice is switched off right now.',
};

/** The error for a non-2xx response, with the server's sentence as given. */
export async function failure(res: Response): Promise<PracticeApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const sentence = isObj(body) && typeof body.error === 'string' ? body.error.trim() : '';
  const message = sentence && !looksLikeCode(sentence) ? sentence : (FALLBACK[res.status] ?? TROUBLE);
  const header = res.headers.get('retry-after')?.trim();
  const seconds = header ? Number(header) : NaN;
  return new PracticeApiError(message, res.status, Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined);
}

const isAbort = (err: unknown) => err instanceof DOMException && err.name === 'AbortError';

/** True when the request never got an answer: offline, a dropped connection, or a proxy that gave up (502, 504). */
export function isOffline(err: unknown): boolean {
  return err instanceof PracticeApiError && (err.status === 0 || err.status === 502 || err.status === 504);
}

type Method = 'GET' | 'POST' | 'DELETE';

async function call<T>(method: Method, path: string, body: unknown, read: (raw: unknown) => T | null, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      ...(body !== undefined ? { headers: JSON_HEADERS, body: JSON.stringify(body) } : {}),
      signal,
    });
  } catch (err) {
    if (isAbort(err) || signal?.aborted) throw err;
    throw new PracticeApiError(OFFLINE, 0);
  }
  if (!res.ok) throw await failure(res);
  let raw: unknown;
  try {
    raw = await res.json();
  } catch {
    // a page that is not JSON (a proxy, or a server that does not have this route) is not an answer
    throw new PracticeApiError(TROUBLE, 502);
  }
  const answer = read(raw);
  if (!answer) throw new PracticeApiError(TROUBLE, 502);
  return answer;
}

const BASE = '/api/interview';
const session = (id: string) => `${BASE}/sessions/${encodeURIComponent(id)}`;
const withProfile = (path: string, profileId: string) => `${path}?profileId=${encodeURIComponent(profileId)}`;

/** `POST /api/interview/plan`: the plan for a kind, with no model call (free to look at and to change). */
export function fetchPlan(req: PlanRequest, signal?: AbortSignal): Promise<InterviewPlan> {
  return call('POST', `${BASE}/plan`, req, readPlan, signal);
}

/** `POST /api/interview/sessions`: start. A few seconds: the questions are phrased once, here. */
export function startSession(req: StartRequest): Promise<InterviewSession> {
  return call('POST', `${BASE}/sessions`, req, readSession);
}

/** `GET /api/interview/sessions/{id}`: the session, with its debrief once it is done. Also how a reload carries on. */
export function fetchSession(id: string, profileId: string, signal?: AbortSignal): Promise<SessionView> {
  return call('GET', withProfile(session(id), profileId), undefined, readSessionView, signal);
}

/** `POST …/answer`: an answer (spoken, turned into text by the browser, or typed) or a button (`dont_know`, `skip`, `repeat`). */
export function sendAnswer(id: string, req: AnswerRequest): Promise<InterviewSession> {
  return call('POST', `${session(id)}/answer`, req, readSession);
}

/** `POST …/work`: hand in a work sample; the interviewer then asks about it. */
export function submitWork(id: string, req: WorkSubmit): Promise<InterviewSession> {
  return call('POST', `${session(id)}/work`, req, readSession);
}

/** `POST …/assistant`: one message to a work sample's optional assistant. */
export function askAssistant(id: string, req: AssistantRequest): Promise<AssistantReply> {
  return call('POST', `${session(id)}/assistant`, req, readReply);
}

/** `POST …/end`: end the interview and get its debrief (a finished interview returns the debrief it has). */
export function endSession(id: string, profileId: string): Promise<Debrief> {
  const body: SessionRequest = { profileId };
  return call('POST', `${session(id)}/end`, body, readDebrief);
}

/** `GET /api/interview/sessions`: the person's practice, newest first. */
export function fetchHistory(profileId: string, signal?: AbortSignal): Promise<PracticeHistory> {
  return call('GET', withProfile(`${BASE}/sessions`, profileId), undefined, readHistory, signal);
}

/** `POST …/again`: a new interview of the same kind with the one question again (always free). */
export function askAgain(id: string, req: AgainRequest): Promise<InterviewSession> {
  return call('POST', `${session(id)}/again`, req, readSession);
}

/** `POST …/facts`: keep (or take back) what the person said as a fact marked "From you". */
export function setFact(id: string, req: FactRequest): Promise<Debrief> {
  return call('POST', `${session(id)}/facts`, req, readDebrief);
}

/** `DELETE …`: delete a practice interview now. One that is already gone counts as deleted. */
export async function deleteSession(id: string, profileId: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch(withProfile(session(id), profileId), { method: 'DELETE' });
  } catch {
    throw new PracticeApiError(OFFLINE, 0);
  }
  if (res.ok || res.status === 404) return;
  throw await failure(res);
}

/** The calls the room and the setup screen make, so a test or the dev page can hand them scripted ones. */
export interface InterviewApi {
  fetchPlan: typeof fetchPlan;
  startSession: typeof startSession;
  fetchSession: typeof fetchSession;
  sendAnswer: typeof sendAnswer;
  submitWork: typeof submitWork;
  askAssistant: typeof askAssistant;
  endSession: typeof endSession;
  fetchHistory: typeof fetchHistory;
}

export const realApi: InterviewApi = { fetchPlan, startSession, fetchSession, sendAnswer, submitWork, askAssistant, endSession, fetchHistory };

/** The sentence a person reads for a failed call: the server's own when it sent one. */
export function sentenceOf(err: unknown): string {
  if (err instanceof ApiError) return err.message || TROUBLE;
  return err instanceof TypeError ? OFFLINE : TROUBLE;
}
