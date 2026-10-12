import type { ClaimRequest, MagicLinkRequest, MagicLinkResponse, MePatch, MeResponse } from '../../shared/auth';
import type { CheckoutRequest, CheckoutResponse, EntitlementsResponse, VerifyRequest, VerifyResponse } from '../../shared/billing';
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
  /** Why a 402 `payment_required` was refused (`no_credits`); absent for every other failure. */
  readonly reason?: string;
  constructor(message: string, status: number, reason?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.reason = reason;
  }
}

/** A 402's body is `{ error: 'payment_required', reason }`: a code, not a sentence, so say it in words. */
const PAYMENT_REQUIRED = "You've used this month's free posting. Choose a plan to continue.";

/** The server's `{ error }` text for a failed response, else "Request failed (status)". */
async function failure(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  let reason: string | undefined;
  try {
    const body = await res.json();
    if (body?.error === 'payment_required') {
      message = PAYMENT_REQUIRED;
      if (typeof body.reason === 'string') reason = body.reason;
    } else if (body?.error) message = body.error;
  } catch {
    /* not JSON */
  }
  return new ApiError(message, res.status, reason);
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

/* ───────────────────────── accounts ───────────────────────── */

/** Who is signed in (absent `user` = an anonymous visitor), their profiles, entitlements, and the sign-in methods on offer. */
export async function getMe(signal?: AbortSignal): Promise<MeResponse> {
  const res = await fetch('/api/me', { signal });
  if (!res.ok) throw await failure(res);
  return res.json();
}

/** Ask for a sign-in link by email. Always succeeds (202) for a well-formed address; a local server also returns `devLink`. */
export async function requestMagicLink(email: string, signal?: AbortSignal): Promise<MagicLinkResponse> {
  const req: MagicLinkRequest = { email };
  const res = await fetch('/api/auth/magic', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(req), signal });
  if (!res.ok) throw await failure(res);
  return res.json();
}

/** End the session on the server (the cookie is cleared by the response). */
export async function logout(): Promise<void> {
  const res = await fetch('/api/auth/logout', { method: 'POST' });
  if (!res.ok) throw await failure(res);
}

/** Attach an unowned profile (and its portfolio) to the signed-in account; resolves with the refreshed `/api/me`. */
export async function claimProfile(profileId: string, signal?: AbortSignal): Promise<MeResponse> {
  const req: ClaimRequest = { profileId };
  const res = await fetch('/api/me/claim', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(req), signal });
  if (!res.ok) throw await failure(res);
  return res.json();
}

export async function patchMe(patch: MePatch, signal?: AbortSignal): Promise<MeResponse> {
  const res = await fetch('/api/me', { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(patch), signal });
  if (!res.ok) throw await failure(res);
  return res.json();
}

/* ───────────────────────── payments ───────────────────────── */

/**
 * The balance (zeros for an anonymous visitor), the currency to show first and the prices that are on sale.
 * `timezone` is only used to guess a currency for a visitor who has not chosen one.
 */
export async function getBillingEntitlements(timezone?: string, signal?: AbortSignal): Promise<EntitlementsResponse> {
  const query = timezone ? `?timezone=${encodeURIComponent(timezone)}` : '';
  const res = await fetch(`/api/billing/entitlements${query}`, { signal });
  if (!res.ok) throw await failure(res);
  return res.json();
}

/** Create the order at the provider. 409 means the plan is not on sale; 401 means sign in first. */
export async function createCheckout(req: CheckoutRequest, signal?: AbortSignal): Promise<CheckoutResponse> {
  const res = await fetch('/api/billing/checkout', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(req), signal });
  if (!res.ok) throw await failure(res);
  return res.json();
}

/** Checkout.js's hand-off after a payment: the server checks the signature and settles the order. */
export async function verifyPayment(req: VerifyRequest, signal?: AbortSignal): Promise<VerifyResponse> {
  const res = await fetch('/api/billing/verify', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(req), signal });
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
