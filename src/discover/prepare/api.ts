/**
 * The clients for a role's prep page and referral note: `POST /api/jobsearch/prepare` and `POST /api/jobsearch/referral`
 * (types in `shared/jobsearch.ts`). They live beside the screens, like the other Discover clients, so waves never merge
 * on one file; `ApiError` is reused from `src/lib/api.ts`.
 *
 * Failures come back as JSON `{ "error": "<sentence>" }` with 400 (not understood), 404 (the profile or the search is not
 * on this server any more), 429 (slow down) or 503 (the AI is paused for today, or switched off). The server's sentence is
 * shown as it was written; it is replaced only when the body held none (a proxy's page, a bare status) or held a machine
 * code instead.
 *
 * Answers are read defensively, like `../jobsearch/api.ts` and `../../match/api.ts`: an item of the wrong shape is
 * dropped, never trusted; a quote that is too long is dropped, not cut; lists are capped at the contract's limits; and a
 * link that is not a plain web address makes the whole referral note unusable. Nothing here adds anything the server did
 * not send, so every line on the page still comes from a checked quote or is marked as ours.
 */
import type { Cite } from '../../../shared/discover';
import type {
  PrepGap,
  PrepLead,
  PrepQuestion,
  ReferralLine,
  ReferralNote,
  ReferralPart,
  RolePrep,
  RoleRequest,
  StoryPart,
} from '../../../shared/jobsearch';
import { ApiError } from '../../lib/api';
import { safeHref } from '../util';
import { arr, isObj, isoTime, oneOf, readCite, text } from './read';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const TROUBLE = 'Tayyari ran into trouble. Please try again in a moment.';

export class RoleApiError extends ApiError {
  /** Seconds the server asks the person to wait (a 429's `Retry-After`), when it gave a number. */
  readonly retryAfter?: number;
  constructor(message: string, status: number, retryAfter?: number) {
    super(message, status);
    this.name = 'RoleApiError';
    this.retryAfter = retryAfter;
  }
}

/** A machine code such as `jobsearch_disabled`, as opposed to a sentence. */
const looksLikeCode = (s: string) => /^[a-z][a-z0-9_]*$/.test(s);

const FALLBACK: Record<number, string> = {
  400: "That role couldn't be opened. Please go back and try again.",
  404: "We couldn't find this role on this server. It may be from an older search. Search again, then open it.",
  429: 'Too many requests right now. Please wait a moment and try again.',
  503: 'This is not available right now. Please try again later.',
};

/** The error for a non-2xx response, with the server's sentence as given. */
export async function failure(res: Response): Promise<RoleApiError> {
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
  return new RoleApiError(message, res.status, Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined);
}

/* ───────────────────────── reading the prep page ───────────────────────── */

/** Contract-sized limits, enforced here too: the screen never shows more than the server promised. */
const MAX_LEAD = 12;
const MAX_GAPS = 8;
const MAX_QUESTIONS = 8;
const MAX_PARTS = 8;

function readLead(raw: unknown): PrepLead | null {
  if (!isObj(raw)) return null;
  const ask = text(raw.ask, 1000);
  const cite = readCite(raw.cite);
  if (!ask || !cite) return null;
  return { ask, cite, partly: raw.partly === true };
}

function readGap(raw: unknown): PrepGap | null {
  if (!isObj(raw)) return null;
  const gap = text(raw.gap, 1000);
  if (!gap) return null;
  const nearest = readCite(raw.nearest);
  // the step is advice; a gap without one is still a true statement about the posting, so it stays
  return { gap, ...(nearest ? { nearest } : {}), step: text(raw.step, 1000) ?? '' };
}

function readPart(raw: unknown): StoryPart | null {
  if (!isObj(raw)) return null;
  const label = text(raw.label, 80);
  if (!label) return null;
  const cite: Cite | undefined = readCite(raw.cite);
  const placeholder = text(raw.placeholder, 400);
  // a beat with neither evidence nor a box for the person has nothing to show
  if (!cite && !placeholder) return null;
  return { label, ...(cite ? { cite } : {}), ...(placeholder ? { placeholder } : {}) };
}

function readQuestion(raw: unknown): PrepQuestion | null {
  if (!isObj(raw)) return null;
  const question = text(raw.question, 600);
  const ask = text(raw.ask, 1000);
  if (!question || !ask) return null;
  const story = arr(raw.story).flatMap((p) => {
    const part = readPart(p);
    return part ? [part] : [];
  });
  return { question, ask, story: story.slice(0, MAX_PARTS) };
}

const keep = <T>(items: unknown[], read: (raw: unknown) => T | null, max: number): T[] =>
  items.flatMap((i) => {
    const v = read(i);
    return v ? [v] : [];
  }).slice(0, max);

/** The prep page, or null when the answer is not one. `fallbackJobId` stands in for an id the server left out. */
export function readRolePrep(raw: unknown, fallbackJobId = ''): RolePrep | null {
  if (!isObj(raw)) return null;
  // lists must be lists: an answer without them is not a prep page at all
  if (!Array.isArray(raw.lead) || !Array.isArray(raw.gaps) || !Array.isArray(raw.questions)) return null;
  return {
    jobId: text(raw.jobId, 200) ?? fallbackJobId,
    title: text(raw.title, 300) ?? '',
    employer: text(raw.employer, 200) ?? '',
    lead: keep(raw.lead, readLead, MAX_LEAD),
    gaps: keep(raw.gaps, readGap, MAX_GAPS),
    questions: keep(raw.questions, readQuestion, MAX_QUESTIONS),
    createdAt: isoTime(raw.createdAt) ?? new Date().toISOString(),
  };
}

/* ───────────────────────── reading the referral note ───────────────────────── */

const PARTS: readonly ReferralPart[] = ['company', 'you', 'role'];
const MAX_LINES = 3;
const MAX_SOURCES = 6;
/** The ask when the server's is missing: plain, and what the product's own fixture says. */
const DEFAULT_ASK = 'Would you be open to referring me?';

function readLine(raw: unknown): ReferralLine | null {
  if (!isObj(raw)) return null;
  const part = oneOf(PARTS, raw.part);
  const lineText = text(raw.text, 800);
  if (!part || !lineText) return null;
  const sources = arr(raw.sources).flatMap((s) => {
    const source = text(s, 80);
    return source ? [source] : [];
  });
  return { part, text: lineText, sources: sources.slice(0, MAX_SOURCES) };
}

/** The note, or null when it has no line or its link is not a plain web address. */
export function readReferralNote(raw: unknown, fallbackJobId = ''): ReferralNote | null {
  if (!isObj(raw)) return null;
  const link = text(raw.link, 2000);
  if (!link || !safeHref(link)) return null;
  const lines = keep(arr(raw.lines), readLine, MAX_LINES);
  if (lines.length === 0) return null;
  return {
    jobId: text(raw.jobId, 200) ?? fallbackJobId,
    lines,
    ask: text(raw.ask, 300) ?? DEFAULT_ASK,
    link,
    createdAt: isoTime(raw.createdAt) ?? new Date().toISOString(),
  };
}

/* ───────────────────────── the requests ───────────────────────── */

async function post<T>(path: string, req: RoleRequest, read: (raw: unknown, jobId: string) => T | null, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(req), signal });
  if (!res.ok) throw await failure(res);
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    // a page that is not JSON (a proxy, or a server that does not have this route) is not an answer
    throw new RoleApiError(TROUBLE, 502);
  }
  const answer = read(body, req.jobId);
  if (!answer) throw new RoleApiError(TROUBLE, 502);
  return answer;
}

/** `POST /api/jobsearch/prepare`: a few seconds the first time, cached after. */
export function postPrepare(req: RoleRequest, signal?: AbortSignal): Promise<RolePrep> {
  return post('/api/jobsearch/prepare', req, readRolePrep, signal);
}

/** `POST /api/jobsearch/referral`: `fresh: true` asks for a new draft (Rewrite). */
export function postReferral(req: RoleRequest, signal?: AbortSignal): Promise<ReferralNote> {
  return post('/api/jobsearch/referral', req, readReferralNote, signal);
}
