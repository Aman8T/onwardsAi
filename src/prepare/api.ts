/**
 * The client for Prepare: `/api/prepare/*` (types in `shared/prepare.ts`).
 *
 *   stream        POST /api/prepare                      NDJSON of PrepareEvent (a stored plan streams at once)
 *   getPlan       GET  /api/prepare/{id}?profileId=      the stored plan
 *   putState      PUT  /api/prepare/{id}/state           compare-and-swap on `version`; 409 when it is stale
 *   getRepos      GET  /api/prepare/{id}/repos?profileId=
 *   postHeadline  POST /api/prepare/{id}/headline
 *
 * Failures come back as JSON `{ "error": "<sentence>" }`. The server's sentence is shown as written; it is replaced only
 * when the body held none or held a machine code. A request that never reached the server is an `ApiError` with status
 * 0. The calls are an interface (`PrepareClient`) so the dev page can hand the screens a scripted one.
 */
import type {
  PrepareEvent,
  PrepareHeadline,
  PrepareHeadlineRequest,
  PreparePlan,
  PrepareRepos,
  PrepareRequest,
  PrepareStateSaved,
  PrepareStateUpdate,
} from '../../shared/prepare';
import { ApiError, readNdjson } from '../lib/api';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const TROUBLE = 'Tayyari ran into trouble. Please try again in a moment.';
export const OFFLINE = "We couldn't reach Tayyari. Check your connection and try again.";

const FALLBACK: Record<number, string> = {
  400: "That didn't go through. Please go back and try again.",
  403: 'This plan belongs to another graph.',
  404: 'This plan is not here any more.',
  409: 'This plan changed in another tab.',
  429: "You've made a few plans today. Please wait a while and try again.",
  503: 'Prepare is switched off right now.',
};

/** A machine code such as `prepare_disabled`, as opposed to a sentence. */
const looksLikeCode = (s: string) => /^[a-z][a-z0-9_]*$/.test(s);

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;

/** The error for a non-2xx response, with the server's sentence as given. */
export async function failure(res: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const sentence = isObj(body) && typeof body.error === 'string' ? body.error.trim() : '';
  const message = sentence && !looksLikeCode(sentence) ? sentence : (FALLBACK[res.status] ?? TROUBLE);
  return new ApiError(message, res.status);
}

export const isAbort = (err: unknown): boolean => err instanceof DOMException && err.name === 'AbortError';

/** True when `err` is the server saying no with this status. */
export const isStatus = (err: unknown, status: number): boolean => err instanceof ApiError && err.status === status;

/** The sentence a person reads for a failed call: the server's own when it sent one. */
export function sentenceOf(err: unknown): string {
  if (err instanceof ApiError) return err.message || TROUBLE;
  return err instanceof TypeError ? OFFLINE : TROUBLE;
}

type Method = 'GET' | 'POST' | 'PUT';

async function call<T>(method: Method, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      ...(body !== undefined ? { headers: JSON_HEADERS, body: JSON.stringify(body) } : {}),
      signal,
    });
  } catch (err) {
    if (isAbort(err) || signal?.aborted) throw err;
    throw new ApiError(OFFLINE, 0);
  }
  if (!res.ok) throw await failure(res);
  try {
    return (await res.json()) as T;
  } catch {
    // a page that is not JSON (a proxy, or a server without this route) is not an answer
    throw new ApiError(TROUBLE, 502);
  }
}

export interface PrepareClient {
  stream(req: PrepareRequest, onEvent: (e: PrepareEvent) => void, signal?: AbortSignal): Promise<void>;
  getPlan(planId: string, profileId: string, signal?: AbortSignal): Promise<PreparePlan>;
  putState(planId: string, update: PrepareStateUpdate, signal?: AbortSignal): Promise<PrepareStateSaved>;
  getRepos(planId: string, profileId: string, signal?: AbortSignal): Promise<PrepareRepos>;
  postHeadline(planId: string, req: PrepareHeadlineRequest, signal?: AbortSignal): Promise<PrepareHeadline>;
}

const BASE = '/api/prepare';
const planPath = (id: string) => `${BASE}/${encodeURIComponent(id)}`;
const withProfile = (path: string, profileId: string) => `${path}?profileId=${encodeURIComponent(profileId)}`;

export const realClient: PrepareClient = {
  async stream(req, onEvent, signal) {
    let res: Response;
    try {
      res = await fetch(BASE, {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify(req),
        signal,
      });
    } catch (err) {
      if (isAbort(err) || signal?.aborted) throw err;
      throw new ApiError(OFFLINE, 0);
    }
    if (!res.ok) throw await failure(res);
    await readNdjson<PrepareEvent>(res, onEvent);
  },
  getPlan: (planId, profileId, signal) => call('GET', withProfile(planPath(planId), profileId), undefined, signal),
  putState: (planId, update, signal) => call('PUT', `${planPath(planId)}/state`, update, signal),
  getRepos: (planId, profileId, signal) => call('GET', withProfile(`${planPath(planId)}/repos`, profileId), undefined, signal),
  postHeadline: (planId, req, signal) => call('POST', `${planPath(planId)}/headline`, req, signal),
};
