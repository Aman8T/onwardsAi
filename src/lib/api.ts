import type {
  Portfolio,
  PortfolioEvent,
  PortfolioPatch,
  PortfolioRequest,
  PublicPortfolio,
} from '../../shared/portfolio';
import type { AnalysisEvent, ChatEvent, ChatRequest, Profile } from '../../shared/types';

/** A failed API call: the server's `{ error }` message (or a generic one) and the HTTP status. */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** The server's `{ error }` text for a failed response, else "Request failed (status)". */
async function failure(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (body?.error) message = body.error;
  } catch {
    /* not JSON */
  }
  return new ApiError(message, res.status);
}

/** Read a newline-delimited JSON response body as it streams in. */
export async function readNdjson<T>(res: Response, onEvent: (e: T) => void): Promise<void> {
  if (!res.ok || !res.body) throw await failure(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf('\n')) !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line) as T);
    }
  }
  const rest = buf.trim();
  if (rest) onEvent(JSON.parse(rest) as T);
}

export interface AnalyzeInput {
  file?: File | null;
  links: string[];
  goal?: string;
  notes?: string;
  /** Use the bundled sample persona (fixture sources, live AI). */
  sample?: boolean;
}

export async function analyze(input: AnalyzeInput, onEvent: (e: AnalysisEvent) => void, signal?: AbortSignal) {
  const form = new FormData();
  if (input.file) form.append('resume', input.file);
  form.append('links', JSON.stringify(input.links));
  if (input.goal) form.append('goal', input.goal);
  if (input.notes) form.append('notes', input.notes);
  if (input.sample) form.append('sample', '1');
  const res = await fetch('/api/analyze', { method: 'POST', body: form, signal });
  await readNdjson<AnalysisEvent>(res, onEvent);
}

export async function chat(req: ChatRequest, onEvent: (e: ChatEvent) => void, signal?: AbortSignal) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  });
  await readNdjson<ChatEvent>(res, onEvent);
}

/** Pre-computed sample profile, for demos when live analysis is unavailable. */
export async function sampleProfile(): Promise<Profile> {
  const res = await fetch('/api/sample-profile');
  if (!res.ok) throw new Error('No pre-computed sample profile on the server');
  return res.json();
}

export async function fetchProfile(id: string): Promise<Profile | null> {
  const res = await fetch(`/api/profile/${encodeURIComponent(id)}`);
  return res.ok ? res.json() : null;
}

/* ───────────────────────── portfolio ───────────────────────── */

const JSON_HEADERS = { 'Content-Type': 'application/json' };
const portfolioUrl = (profileId: string, tail = '') => `/api/portfolio/${encodeURIComponent(profileId)}${tail}`;

/** Build (or rebuild) the portfolio and stream its progress: progress → draft → done (or error). */
export async function generatePortfolio(
  profileId: string,
  regenerate: boolean,
  onEvent: (e: PortfolioEvent) => void,
  signal?: AbortSignal,
) {
  const req: PortfolioRequest = { profileId, regenerate };
  const res = await fetch('/api/portfolio', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(req), signal });
  await readNdjson<PortfolioEvent>(res, onEvent);
}

/** The owner's portfolio, or null when none has been generated yet (404). */
export async function getPortfolio(profileId: string, signal?: AbortSignal): Promise<Portfolio | null> {
  const res = await fetch(portfolioUrl(profileId), { signal });
  if (res.status === 404) return null;
  if (!res.ok) throw await failure(res);
  return res.json();
}

/** Apply an edit; the server validates it against the kit and returns the whole updated portfolio. */
export async function patchPortfolio(profileId: string, patch: PortfolioPatch, signal?: AbortSignal): Promise<Portfolio> {
  const res = await fetch(portfolioUrl(profileId), { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(patch), signal });
  if (!res.ok) throw await failure(res);
  return res.json();
}

/** Ask the model for fresh wording for one copy slot; returns the whole updated portfolio. */
export async function rewritePortfolioSlot(profileId: string, slotId: string, signal?: AbortSignal): Promise<Portfolio> {
  const res = await fetch(portfolioUrl(profileId, '/rewrite'), {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ slotId }),
    signal,
  });
  if (!res.ok) throw await failure(res);
  return res.json();
}

/** The owner's portfolio as a viewer would receive it (private blocks and details already filtered out). */
export async function getPublicPreview(profileId: string, signal?: AbortSignal): Promise<PublicPortfolio> {
  const res = await fetch(portfolioUrl(profileId, '/public-preview'), { signal });
  if (!res.ok) throw await failure(res);
  return res.json();
}

/** A published portfolio by its public id, or null when there is none (404: unknown or not published). */
export async function getPublicPortfolio(publicId: string, signal?: AbortSignal): Promise<PublicPortfolio | null> {
  const res = await fetch(`/api/p/${encodeURIComponent(publicId)}`, { signal });
  if (res.status === 404) return null;
  if (!res.ok) throw await failure(res);
  return res.json();
}

export interface Health {
  ok: boolean;
  models: { reader: string; synth: string; chat: string };
  hasSample: boolean;
}

export async function health(): Promise<Health | null> {
  try {
    const res = await fetch('/api/health');
    return res.ok ? res.json() : null;
  } catch {
    return null;
  }
}
