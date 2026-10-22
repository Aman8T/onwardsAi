/**
 * The client for the radar card: `/api/card*` (types in `shared/card.ts`).
 *
 *   get      GET  /api/card?profileId=     the owner's view (with `blocked` for the sample or an anonymous graph)
 *   put      PUT  /api/card                the settings; turning `shared` on makes the link, off clears it
 *   newLink  POST /api/card/new-link       a new slug; the old one stops at once
 *
 * Failures come back as JSON `{ "error": "<sentence>" }` and the sentence is shown as written; it is replaced only when
 * the body held none or held a machine code. A request that never reached the server is an `ApiError` with status 0.
 * The calls are an interface (`CardClient`) so the dev page can hand the sheet a scripted one.
 */
import type { CardNewLink, CardUpdate, CardView } from '../../shared/card';
import { ApiError } from '../lib/api';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const TROUBLE = 'Leafout ran into trouble. Please try again in a moment.';
export const OFFLINE = "We couldn't reach Leafout. Check your connection and try again.";

const FALLBACK: Record<number, string> = {
  400: "That didn't go through. Please check your choices and try again.",
  403: 'This graph belongs to another account.',
  404: 'This graph is not here any more.',
  429: "You've changed this a lot just now. Please wait a moment and try again.",
};

/** A machine code such as `card_disabled`, as opposed to a sentence. */
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

export interface CardClient {
  get(profileId: string, signal?: AbortSignal): Promise<CardView>;
  put(update: CardUpdate, signal?: AbortSignal): Promise<CardView>;
  newLink(req: CardNewLink, signal?: AbortSignal): Promise<CardView>;
}

const BASE = '/api/card';

export const realClient: CardClient = {
  get: (profileId, signal) => call('GET', `${BASE}?profileId=${encodeURIComponent(profileId)}`, undefined, signal),
  put: (update, signal) => call('PUT', BASE, update, signal),
  newLink: (req, signal) => call('POST', `${BASE}/new-link`, req, signal),
};
