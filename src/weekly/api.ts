/**
 * The client for The Leafout Weekly: `/api/weekly/*` (types in `shared/weekly.ts`).
 *
 * Failures come back as JSON `{ "error": "<sentence>" }`; the sentence is shown as written, and a body without one
 * (a proxy's page, a bare status) becomes a generic line. A 401 means nobody is signed in. A request that never
 * reached the server is a `ApiError` with status 0.
 */
import type { WeeklyEdition, WeeklyEditionList, WeeklySubscription, WeeklySubscriptionUpdate } from '../../shared/weekly';
import { ApiError } from '../lib/api';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const OFFLINE = "We couldn't reach Leafout. Check your connection and try again.";
const TROUBLE = 'Leafout ran into trouble. Please try again in a moment.';

/** A machine code such as `weekly_disabled`, as opposed to a sentence. */
const looksLikeCode = (s: string) => /^[a-z][a-z0-9_]*$/.test(s);

async function failure(res: Response): Promise<ApiError> {
  let message = res.status >= 500 ? TROUBLE : `Request failed (${res.status})`;
  try {
    const body: unknown = await res.json();
    const err = typeof body === 'object' && body !== null ? (body as { error?: unknown }).error : undefined;
    if (typeof err === 'string' && err.trim() && !looksLikeCode(err)) message = err;
  } catch {
    /* not JSON */
  }
  return new ApiError(message, res.status);
}

async function send(url: string, init?: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new ApiError(OFFLINE, 0);
  }
  if (!res.ok) throw await failure(res);
  return res;
}

export async function getSubscription(signal?: AbortSignal): Promise<WeeklySubscription> {
  return (await send('/api/weekly/subscription', { signal })).json();
}

export async function saveSubscription(update: WeeklySubscriptionUpdate, signal?: AbortSignal): Promise<WeeklySubscription> {
  return (
    await send('/api/weekly/subscription', {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify(update),
      signal,
    })
  ).json();
}

export async function listEditions(signal?: AbortSignal): Promise<WeeklyEditionList> {
  return (await send('/api/weekly/editions', { signal })).json();
}

/** One edition of the signed-in reader; the server marks it read. */
export async function getEdition(id: string, signal?: AbortSignal): Promise<WeeklyEdition> {
  return (await send(`/api/weekly/editions/${encodeURIComponent(id)}`, { signal })).json();
}

/** Build this week's edition now (or get it if it was built already). */
export async function previewEdition(signal?: AbortSignal): Promise<WeeklyEdition> {
  return (await send('/api/weekly/editions/preview', { method: 'POST', signal })).json();
}
