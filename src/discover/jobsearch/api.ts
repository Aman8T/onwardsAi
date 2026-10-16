/**
 * The client for "Search with AI": `POST /api/jobsearch`, an NDJSON stream typed in `shared/jobsearch.ts`. It lives
 * beside the screen (not in `src/lib/api.ts`), like the match and Discover clients, so waves never merge on one file;
 * `ApiError` and the stream reader are reused from there.
 *
 * Failures before the stream starts come back as JSON `{ "error": "<sentence>" }` and keep their HTTP status:
 *   400 the request was not understood · 401 sign in first · 404 the profile is not on this server ·
 *   429 slow down (with `Retry-After`) · 503 the AI search is paused for today, or switched off.
 * The server's sentence is shown as it was written. A sentence is only replaced when the body held none (a proxy's
 * page, a bare status) or held a machine code instead.
 *
 * Besides the stream: `GET /api/jobsearch/latest` (the last finished search, 404 when there is none) and
 * `GET /api/jobsearch/runs/{runId}/jobs/{jobId}/posting` (the posting text "See how you fit" sends to the match flow).
 *
 * Events are read defensively, like `match/api.ts`: a line this client does not know, or whose shape is wrong, is
 * dropped, never trusted. A job is dropped unless it has what the card must say (an id, a title, an employer, a plain
 * web address to apply at, a place, a fit word, where it was found, and when the employer's board listed it); fields
 * that only decorate it are left out when they are wrong. Lists are capped at the contract's own limits.
 */
import type { DiscoverWorkMode, Place, PlacePrecision, QuickFit } from '../../../shared/discover';
import type {
  AiFitWord,
  AiJob,
  AskCounts,
  AskMet,
  FoundVia,
  FunnelStep,
  JobPosting,
  JobSearchEvent,
  JobSearchRequest,
  JobSearchRun,
  LevelWord,
  TrailStage,
  TrailStep,
} from '../../../shared/jobsearch';
import { ApiError, readNdjson } from '../../lib/api';
import { hostOf, safeHref } from '../util';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const TROUBLE = 'The search ran into trouble. Please try again in a moment.';
export const ENDED_EARLY = 'The search stopped before it finished. Nothing was lost; you can search again.';
const DROPPED = 'The connection dropped before the search finished. You can search again.';

/** How the stream is started. The dev gallery swaps in a scripted one so the screen runs with no server. */
export type JobSearchStream = (req: JobSearchRequest, onEvent: (e: JobSearchEvent) => void, signal?: AbortSignal) => Promise<void>;

export class JobSearchApiError extends ApiError {
  /** Seconds the server asks the person to wait (a 429's `Retry-After`), when it gave a number. */
  readonly retryAfter?: number;
  constructor(message: string, status: number, retryAfter?: number) {
    super(message, status);
    this.name = 'JobSearchApiError';
    this.retryAfter = retryAfter;
  }
}

/** What the screen needs to say about a search that did not finish. */
export interface JobSearchFailure {
  message: string;
  status?: number;
  retryAfter?: number;
}

/** Whether asking again could help: not for a request the server refused outright (400, 404, 503) or a sign-in (401). */
export const canRetry = (f: JobSearchFailure): boolean => f.status === undefined || f.status === 429 || (f.status >= 500 && f.status !== 503);

export function failureOf(err: unknown): JobSearchFailure {
  if (err instanceof JobSearchApiError) return { message: err.message, status: err.status, retryAfter: err.retryAfter };
  return { message: DROPPED };
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
/** A machine code such as `jobsearch_disabled`, as opposed to a sentence. */
const looksLikeCode = (s: string) => /^[a-z][a-z0-9_]*$/.test(s);

const FALLBACK: Record<number, string> = {
  400: "That search couldn't be started. Please reload the page and try again.",
  401: 'Sign in to search with AI.',
  404: 'Profile not found',
  429: 'Too many searches right now. Please wait a moment and try again.',
  503: 'Search with AI is not available right now.',
};

/** The error for a non-2xx response, with the server's sentence as given. */
export async function failure(res: Response): Promise<JobSearchApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const sentence = isObj(body) && typeof body.error === 'string' ? body.error.trim() : '';
  const message = sentence && !looksLikeCode(sentence) ? sentence : (FALLBACK[res.status] ?? TROUBLE);
  // `Retry-After` may also be a date; only a number of seconds is used
  const header = res.headers.get('retry-after')?.trim();
  const seconds = header ? Number(header) : NaN;
  return new JobSearchApiError(message, res.status, Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined);
}

/* ───────────────────────── reading one line ───────────────────────── */

const TRAIL_STAGES: readonly TrailStage[] = ['plan', 'search', 'boards', 'narrow', 'read', 'result'];
const FIT_WORDS: readonly AiFitWord[] = ['strong', 'good', 'stretch'];
const LEVELS: readonly LevelWord[] = ['below', 'at', 'step_up', 'unclear'];
const VIA: readonly FoundVia[] = ['web', 'tayyari'];
const MODES: readonly DiscoverWorkMode[] = ['onsite', 'hybrid', 'remote', 'unknown'];
const PRECISIONS: readonly PlacePrecision[] = ['locality', 'city', 'country'];

/** Contract limits, enforced here as well: the screen never shows more than the server promised. */
const MAX_QUERIES = 8;
const MAX_MET = 3;
const MAX_GAPS = 3;
const MAX_FUNNEL = 12;

const oneOf = <T extends string>(allowed: readonly T[], v: unknown): T | undefined => (typeof v === 'string' && (allowed as readonly string[]).includes(v) ? (v as T) : undefined);
const finite = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
/** A non-empty string no longer than `max`; anything else is nothing. Quotes are kept whole: one that is too long is dropped, not cut. */
const text = (v: unknown, max: number): string | undefined => {
  if (typeof v !== 'string') return undefined;
  const t = v.trim();
  return t !== '' && t.length <= max ? t : undefined;
};
const isoTime = (v: unknown): string | undefined => {
  const t = text(v, 64);
  return t !== undefined && Number.isFinite(Date.parse(t)) ? t : undefined;
};

function readPlace(raw: unknown): Place | null {
  if (!isObj(raw)) return null;
  const label = text(raw.label, 200);
  if (!label) return null;
  const city = text(raw.city, 100);
  const locality = text(raw.locality, 100);
  const km = finite(raw.km);
  return {
    label,
    ...(city ? { city } : {}),
    ...(locality ? { locality } : {}),
    precision: oneOf(PRECISIONS, raw.precision) ?? 'city',
    ...(km !== undefined && km >= 0 ? { km } : {}),
  };
}

function readMet(raw: unknown): AskMet | null {
  if (!isObj(raw) || !isObj(raw.cite)) return null;
  const ask = text(raw.ask, 1000);
  const quote = text(raw.cite.text, 1000);
  if (!ask || !quote) return null;
  return { ask, cite: { source: text(raw.cite.source, 100) ?? '', text: quote }, partly: raw.partly === true };
}

/** The code quick fit is not shown on this screen; a job that arrives without one is given an empty, unclaimed one. */
function readQuickFit(raw: unknown): QuickFit {
  if (isObj(raw) && typeof raw.word === 'string') return raw as unknown as QuickFit;
  return { word: 'cant_tell', named: 0, matched: 0, notFound: [], reasons: [] };
}

const asksCount = (v: unknown): number => {
  const n = finite(v);
  return n !== undefined && n >= 0 ? Math.min(Math.round(n), 99) : 0;
};

/**
 * The pips: how every checked requirement came out. A job that arrives without them (an older server) is counted from
 * the quotes it does carry, so the card still draws something true.
 */
function readAsks(raw: unknown, met: readonly AskMet[], gaps: readonly string[]): AskCounts {
  if (isObj(raw) && ['met', 'partly', 'notShown'].every((k) => finite(raw[k]) !== undefined)) {
    return { met: asksCount(raw.met), partly: asksCount(raw.partly), notShown: asksCount(raw.notShown) };
  }
  const partly = met.filter((m) => m.partly).length;
  return { met: met.length - partly, partly, notShown: gaps.length };
}

/** One job, or null when it lacks what its card must say. */
export function readJob(raw: unknown): AiJob | null {
  if (!isObj(raw)) return null;
  const id = text(raw.id, 200);
  const title = text(raw.title, 300);
  const employer = text(raw.employer, 200);
  const applyUrl = text(raw.applyUrl, 2000);
  const checkedAt = isoTime(raw.checkedAt);
  const place = readPlace(raw.place);
  const fit = oneOf(FIT_WORDS, raw.fit);
  const foundVia = oneOf(VIA, raw.foundVia);
  if (!id || !title || !employer || !applyUrl || !checkedAt || !place || !fit || !foundVia) return null;
  // only a plain web address is ever linked, or posted to the match flow
  if (!safeHref(applyUrl)) return null;
  const applyHost = text(raw.applyHost, 200) ?? hostOf(applyUrl);
  if (!applyHost) return null;

  const postedAt = isoTime(raw.postedAt);
  const foundBy = foundVia === 'web' ? text(raw.foundBy, 400) : undefined;
  const levelQuote = text(raw.levelQuote, 600);
  const yearsAsked = finite(raw.yearsAsked);
  const salary = isObj(raw.salary) ? text(raw.salary.text, 200) : undefined;
  const met = arr(raw.met)
    .flatMap((m) => {
      const one = readMet(m);
      return one ? [one] : [];
    })
    .slice(0, MAX_MET);
  const gaps = arr(raw.gaps)
    .flatMap((g) => {
      const gap = text(g, 1000);
      return gap ? [gap] : [];
    })
    .slice(0, MAX_GAPS);

  return {
    id,
    title,
    employer,
    place,
    workMode: oneOf(MODES, raw.workMode) ?? 'unknown',
    ...(postedAt ? { postedAt } : {}),
    checkedAt,
    applyUrl,
    applyHost,
    foundVia,
    ...(foundBy ? { foundBy } : {}),
    fit,
    level: oneOf(LEVELS, raw.level) ?? 'unclear',
    ...(levelQuote ? { levelQuote } : {}),
    ...(yearsAsked !== undefined && yearsAsked >= 0 ? { yearsAsked } : {}),
    met,
    gaps,
    asks: readAsks(raw.asks, met, gaps),
    ...(raw.openLong === true ? { openLong: true } : {}),
    quickFit: readQuickFit(raw.quickFit),
    ...(salary ? { salary: { text: salary } } : {}),
  };
}

function readStep(raw: unknown): TrailStep | null {
  if (!isObj(raw)) return null;
  const stage = oneOf(TRAIL_STAGES, raw.stage);
  const t = text(raw.text, 600);
  if (!stage || !t) return null;
  const count = finite(raw.count);
  // the searches a scout ran belong to the search stage only
  const queries =
    stage === 'search'
      ? arr(raw.queries).flatMap((q) => {
          const query = text(q, 300);
          return query ? [query] : [];
        }).slice(0, MAX_QUERIES)
      : [];
  return { stage, text: t, ...(count !== undefined && count >= 0 ? { count } : {}), ...(queries.length > 0 ? { queries } : {}) };
}

function readFunnel(raw: unknown): FunnelStep[] {
  return arr(raw).flatMap((f) => {
    if (!isObj(f)) return [];
    const label = text(f.label, 120);
    const count = finite(f.count);
    return label && count !== undefined && count >= 0 ? [{ label, count: Math.round(count) }] : [];
  }).slice(0, MAX_FUNNEL);
}

/** One wire line into a typed event; null for lines this client does not know or cannot trust. */
export function readJobSearchEvent(raw: unknown): JobSearchEvent | null {
  if (!isObj(raw) || typeof raw.type !== 'string') return null;
  switch (raw.type) {
    case 'step': {
      const step = readStep(raw.step);
      return step ? { type: 'step', step } : null;
    }
    case 'job': {
      const job = readJob(raw.job);
      return job ? { type: 'job', job } : null;
    }
    case 'done': {
      const note = text(raw.note, 600);
      // a server that names no run leaves "" here: the roles still show, but nothing can be fetched for them
      return {
        type: 'done',
        jobs: Math.max(0, Math.round(finite(raw.jobs) ?? 0)),
        funnel: readFunnel(raw.funnel),
        ...(note ? { note } : {}),
        runId: text(raw.runId, 64) ?? '',
        createdAt: isoTime(raw.createdAt) ?? new Date().toISOString(),
      };
    }
    case 'error':
      return { type: 'error', message: text(raw.message, 600) ?? TROUBLE };
    default:
      return null;
  }
}

/* ───────────────────────── the stream ───────────────────────── */

/** Start a search and stream it: `step` lines, a `job` line for each role that survives, then `done` (or `error`). */
export const streamJobSearch: JobSearchStream = async (req, onEvent, signal) => {
  const res = await fetch('/api/jobsearch', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(req), signal });
  if (!res.ok) throw await failure(res);
  await readNdjson<unknown>(res, (raw) => {
    const e = readJobSearchEvent(raw);
    if (e) onEvent(e);
  });
};

/* ───────────────────────── the last results, and a posting ───────────────────────── */

/** The last finished search as the server stored it, read as defensively as a stream line; null when it is not one. */
export function readRun(raw: unknown): JobSearchRun | null {
  if (!isObj(raw)) return null;
  const runId = text(raw.runId, 64);
  const createdAt = isoTime(raw.createdAt);
  if (!runId || !createdAt) return null;
  const city = text(raw.city, 100);
  const note = text(raw.note, 600);
  return {
    runId,
    createdAt,
    ...(city ? { city } : {}),
    remote: raw.remote !== false,
    jobs: arr(raw.jobs).flatMap((j) => {
      const job = readJob(j);
      return job ? [job] : [];
    }),
    funnel: readFunnel(raw.funnel),
    trail: arr(raw.trail).flatMap((t) => {
      const step = readStep(t);
      return step ? [step] : [];
    }).slice(0, 60),
    ...(note ? { note } : {}),
  };
}

/** A posting's text, as the search read it; null when the answer is not one. */
export function readPosting(raw: unknown): JobPosting | null {
  if (!isObj(raw)) return null;
  const jobId = text(raw.jobId, 200);
  const title = text(raw.title, 300);
  const employer = text(raw.employer, 200);
  const applyUrl = text(raw.applyUrl, 2000);
  const postingText = typeof raw.postingText === 'string' ? raw.postingText : '';
  if (!jobId || !title || !employer || !applyUrl || postingText.trim() === '') return null;
  return { jobId, title, employer, applyUrl, postingText };
}

/** How the page asks for the last results; the dev gallery swaps in a scripted one. */
export type LatestLoader = (profileId: string, city: string | undefined, remote: boolean, signal: AbortSignal) => Promise<JobSearchRun | null>;

/** Said by `GET /api/jobsearch/latest` when the profile has not searched this place yet; any other 404 is the profile. */
const NO_SEARCH_YET = /no search yet/i;

/** `GET /api/jobsearch/latest`: the last finished search for this profile, city and remote choice; null when there is none. */
export const getLatestRun: LatestLoader = async (profileId, city, remote, signal) => {
  const q = new URLSearchParams({ profileId, remote: remote ? '1' : '0' });
  if (city) q.set('city', city);
  const res = await fetch(`/api/jobsearch/latest?${q}`, { signal });
  if (res.status === 404) {
    const err = await failure(res);
    if (NO_SEARCH_YET.test(err.message)) return null;
    throw err;
  }
  if (!res.ok) throw await failure(res);
  return readRun(await res.json().catch(() => null));
};

/** `GET /api/jobsearch/runs/{runId}/jobs/{jobId}/posting`: the text "See how you fit" sends to the match flow. */
export async function getPosting(runId: string, jobId: string, profileId: string, signal?: AbortSignal): Promise<JobPosting> {
  const res = await fetch(`/api/jobsearch/runs/${encodeURIComponent(runId)}/jobs/${encodeURIComponent(jobId)}/posting?${new URLSearchParams({ profileId })}`, { signal });
  if (!res.ok) throw await failure(res);
  const posting = readPosting(await res.json().catch(() => null));
  if (!posting) throw new JobSearchApiError(TROUBLE, 502);
  return posting;
}
