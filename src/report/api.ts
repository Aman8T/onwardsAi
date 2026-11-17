/**
 * The client for "Where can I go next", the shareable report: `/api/report*` (types in `shared/report.ts`).
 *
 *   get      GET  /api/report?profileId=     the owner's view (with `blocked` for the sample or an anonymous graph)
 *   put      PUT  /api/report                the choices; `shared` on makes the link, off clears it
 *   newLink  POST /api/report/new-link       a new slug; the old one stops at once
 *
 * Failures come back as JSON `{ "error": "<sentence or code>" }`. A sentence is shown as written; a machine code
 * (`report_blocked`, `quote_not_offered`, `not_shared`) gets its own sentence here, and anything else the status's.
 * A request that never reached the server is an `ApiError` with status 0. The calls are an interface (`ReportClient`)
 * so the dev page can hand the sheet a scripted one.
 */
import type { ReportNewLink, ReportUpdate, ReportView } from '../../shared/report';
import { ApiError } from '../lib/api';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const TROUBLE = 'Leafout ran into trouble. Please try again in a moment.';
export const OFFLINE = "We couldn't reach Leafout. Check your connection and try again.";

/** The server's machine codes, each as a sentence that names what failed and the next step. */
const CODES: Record<string, string> = {
  report_blocked: "This graph can't be shared. Analyse your own resume, or sign in, and try again.",
  quote_not_offered: 'That line is no longer in your report. Close this and open it again to see the current lines.',
  not_shared: 'The link is off, so there is nothing to replace. Make a link first.',
};

const FALLBACK: Record<number, string> = {
  400: "That didn't go through. Please check your choices and try again.",
  401: 'Your sign-in has ended. Sign in again, then try once more.',
  403: 'This graph belongs to another account.',
  404: 'This graph is not here any more. Analyse your resume again to make a report.',
  429: "You've changed this a lot just now. Please wait a moment and try again.",
};

/** A machine code such as `report_blocked`, as opposed to a sentence. */
const looksLikeCode = (s: string) => /^[a-z][a-z0-9_]*$/.test(s);

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;

/** The error for a non-2xx response, with the server's sentence as given, or the sentence for its code. */
export async function failure(res: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const said = isObj(body) && typeof body.error === 'string' ? body.error.trim() : '';
  const message = !said ? (FALLBACK[res.status] ?? TROUBLE) : looksLikeCode(said) ? (CODES[said] ?? FALLBACK[res.status] ?? TROUBLE) : said;
  return new ApiError(message, res.status);
}

export const isAbort = (err: unknown): boolean => err instanceof DOMException && err.name === 'AbortError';

/** The sentence a person reads for a failed call: the server's own when it sent one. */
export function sentenceOf(err: unknown): string {
  if (err instanceof ApiError) return err.message || TROUBLE;
  return err instanceof TypeError ? OFFLINE : TROUBLE;
}

async function call<T>(method: 'GET' | 'POST' | 'PUT', path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
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

export interface ReportClient {
  get(profileId: string, signal?: AbortSignal): Promise<ReportView>;
  put(update: ReportUpdate, signal?: AbortSignal): Promise<ReportView>;
  newLink(req: ReportNewLink, signal?: AbortSignal): Promise<ReportView>;
}

const BASE = '/api/report';

export const realClient: ReportClient = {
  get: (profileId, signal) => call('GET', `${BASE}?profileId=${encodeURIComponent(profileId)}`, undefined, signal),
  put: (update, signal) => call('PUT', BASE, update, signal),
  newLink: (req, signal) => call('POST', `${BASE}/new-link`, req, signal),
};
