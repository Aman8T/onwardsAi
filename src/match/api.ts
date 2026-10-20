/**
 * The match API client: `POST /api/match` (NDJSON), `GET /api/match/{id}`, `GET /api/matches`,
 * `POST /api/match/{id}/answers`, `POST|GET|PATCH /api/match/{id}/resume`, `POST|GET /api/match/{id}/cover` and the
 * export URLs. It lives beside the screens (not in `src/lib/api.ts`) so the payments wave, which edits that file,
 * never has to merge with it; the stream reader and `ApiError` are reused from there.
 *
 * Failures keep the HTTP status, because the screens decide what to do from it:
 *   401 sign in · 402 nothing left to spend (calm pricing panel) · 409 resume decisions still open ·
 *   422 the text held no requirements · 429 slow down.
 * A 402 body is `{error: "payment_required", reason}` (a code, not a sentence), every other error body is
 * `{error: "<sentence>"}`. An `error` event inside a stream becomes the same error through its `code`.
 */
import type { Coverage, CoverNote, DecisionChoice, GateAnswer, MatchResult, Posting, Question, Radar, ResumeVariant } from '../../shared/match';
import { ApiError, readNdjson } from '../lib/api';
import type {
  AnswerInput,
  MatchDetail,
  MatchErrorCode,
  MatchEvent,
  MatchFact,
  MatchSummary,
  ResumeEvent,
  SubmitInput,
} from './types';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const PAYMENT_MESSAGE = "You've used your free posting this month.";
const RATE_MESSAGE = 'Too many postings right now. Please wait a moment and try again.';
const TROUBLE_MESSAGE = 'Leafout is having trouble right now. Please try again in a moment.';

const STATUS_OF_CODE: Record<MatchErrorCode, number> = {
  unauthorized: 401,
  payment_required: 402,
  export_blocked: 409,
  no_requirements: 422,
  rate_limited: 429,
};

export class MatchApiError extends ApiError {
  /** `reason` of a 402 (`no_credits`…), when the server gave one. */
  readonly reason?: string;
  readonly body?: unknown;
  constructor(message: string, status: number, reason?: string, body?: unknown) {
    super(message, status);
    this.name = 'MatchApiError';
    this.reason = reason;
    this.body = body;
  }
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
/** A machine code such as `payment_required`, as opposed to a sentence. */
const looksLikeCode = (s: string) => /^[a-z][a-z0-9_]*$/.test(s);

function messageFor(status: number, sentence?: string): string {
  if (status === 402) return PAYMENT_MESSAGE;
  if (sentence && !looksLikeCode(sentence)) return sentence;
  if (status === 429) return RATE_MESSAGE;
  if (status >= 500) return TROUBLE_MESSAGE;
  return `Request failed (${status})`;
}

/** The error for a non-2xx response, with its parsed body kept for the caller. */
export async function failure(res: Response): Promise<MatchApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const o = isObj(body) ? body : {};
  const sentence = typeof o.error === 'string' ? o.error : undefined;
  const reason = typeof o.reason === 'string' ? o.reason : undefined;
  return new MatchApiError(messageFor(res.status, sentence), res.status, reason, body);
}

/** The error an `error` event stands for. */
export function eventError(message: string, code?: MatchErrorCode): MatchApiError {
  const status = code ? STATUS_OF_CODE[code] : 500;
  return new MatchApiError(messageFor(status, message), status);
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await failure(res);
  if (!(res.headers.get('content-type') ?? '').includes('json')) throw new MatchApiError(TROUBLE_MESSAGE, 502);
  return res.json() as Promise<T>;
}

/** A JSON answer, or null when the route has nothing stored (404/405) or is not served. */
async function readJsonOrNull<T>(res: Response): Promise<T | null> {
  if (res.status === 404 || res.status === 405) return null;
  if (res.ok && !(res.headers.get('content-type') ?? '').includes('json')) return null;
  return readJson<T>(res);
}

const matchUrl = (id: string, tail = '') => `/api/match/${encodeURIComponent(id)}${tail}`;
export const resumeFileUrl = (id: string, ext: 'pdf' | 'docx') => matchUrl(id, `/resume.${ext}`);
export const coverFileUrl = (id: string) => matchUrl(id, '/cover.pdf');

/* ───────────────────────── events ───────────────────────── */

const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/** One wire line into a typed event; null for lines this client does not know (forward compatible). */
export function readMatchEvent(raw: unknown): MatchEvent | null {
  if (!isObj(raw) || typeof raw.type !== 'string') return null;
  switch (raw.type) {
    case 'posting':
      return isObj(raw.posting) ? { type: 'posting', posting: raw.posting as unknown as Posting, facts: arr<MatchFact>(raw.facts) } : null;
    case 'gates':
      // M1's own event calls the list `answers`
      return { type: 'gates', gates: arr<GateAnswer>(raw.gates ?? raw.answers), blocked: raw.blocked === true };
    case 'coverage':
      // ... and the batch `rows`
      return { type: 'coverage', coverage: arr<Coverage>(raw.coverage ?? raw.rows) };
    case 'radar':
      return isObj(raw.radar) ? { type: 'radar', radar: raw.radar as unknown as Radar } : null;
    case 'questions':
      return { type: 'questions', questions: arr<Question>(raw.questions) };
    case 'done':
      return typeof raw.matchId === 'string'
        ? {
            type: 'done',
            matchId: raw.matchId,
            result: isObj(raw.result) ? (raw.result as unknown as MatchResult) : undefined,
            createdAt: typeof raw.createdAt === 'string' ? raw.createdAt : undefined,
          }
        : null;
    case 'error':
      return {
        type: 'error',
        message: typeof raw.message === 'string' ? raw.message : TROUBLE_MESSAGE,
        code: typeof raw.code === 'string' && raw.code in STATUS_OF_CODE ? (raw.code as MatchErrorCode) : undefined,
      };
    default:
      return null;
  }
}

export function readResumeEvent(raw: unknown): ResumeEvent | null {
  if (!isObj(raw) || typeof raw.type !== 'string') return null;
  if (raw.type === 'variant' && isObj(raw.variant)) return { type: 'variant', variant: raw.variant as unknown as ResumeVariant };
  if (raw.type === 'done') return { type: 'done', variant: isObj(raw.variant) ? (raw.variant as unknown as ResumeVariant) : undefined };
  if (raw.type === 'error') {
    return {
      type: 'error',
      message: typeof raw.message === 'string' ? raw.message : TROUBLE_MESSAGE,
      code: typeof raw.code === 'string' && raw.code in STATUS_OF_CODE ? (raw.code as MatchErrorCode) : undefined,
    };
  }
  return null;
}

/* ───────────────────────── match ───────────────────────── */

/** Start a match and stream it: posting → gates → coverage (batches) → radar → questions → done (or error). */
export async function streamMatch(input: SubmitInput, onEvent: (e: MatchEvent) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch('/api/match', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(input), signal });
  if (!res.ok) throw await failure(res);
  await readNdjson<unknown>(res, (raw) => {
    const e = readMatchEvent(raw);
    if (e) onEvent(e);
  });
}

/** `{id, createdAt?, posting, facts, result}`; a server that flattens the result into the body is read too. */
export function readDetail(raw: unknown, id: string): MatchDetail {
  const o = isObj(raw) ? raw : {};
  const result = isObj(o.result) ? o.result : 'band' in o ? o : null;
  if (!result || !isObj(o.posting)) throw new MatchApiError(TROUBLE_MESSAGE, 502);
  return {
    id: typeof o.id === 'string' ? o.id : id,
    createdAt: typeof o.createdAt === 'string' ? o.createdAt : undefined,
    posting: o.posting as unknown as Posting,
    facts: arr<MatchFact>(o.facts),
    result: result as unknown as MatchResult,
  };
}

export async function getMatch(id: string, signal?: AbortSignal): Promise<MatchDetail> {
  const res = await fetch(matchUrl(id), { signal });
  return readDetail(await readJson<unknown>(res), id);
}

export async function listMatches(signal?: AbortSignal): Promise<MatchSummary[]> {
  const res = await fetch('/api/matches', { signal });
  const raw = await readJson<unknown>(res);
  const list = Array.isArray(raw) ? raw : isObj(raw) ? arr<unknown>(raw.matches) : [];
  return list.filter(isObj).map((m) => {
    const posting = isObj(m.posting) ? m.posting : {};
    return {
      id: String(m.id ?? ''),
      title: String(m.title ?? posting.title ?? 'Posting'),
      company: typeof (m.company ?? posting.company) === 'string' ? String(m.company ?? posting.company) : undefined,
      band: (m.band ?? (isObj(m.result) ? m.result.band : 'good')) as MatchSummary['band'],
      createdAt: String(m.createdAt ?? ''),
    };
  }).filter((m) => m.id);
}

/** Save the person's answers; the server re-checks the rows they touch and bumps the result's version. Never consumes. */
export async function postAnswers(id: string, answers: AnswerInput[], signal?: AbortSignal): Promise<void> {
  const res = await fetch(matchUrl(id, '/answers'), { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ answers }), signal });
  if (!res.ok) throw await failure(res);
}

/* ───────────────────────── resume and cover ───────────────────────── */

const variantOf = (raw: unknown): ResumeVariant => {
  const v = isObj(raw) && isObj(raw.variant) ? raw.variant : raw;
  if (!isObj(v) || !isObj(v.header)) throw new MatchApiError(TROUBLE_MESSAGE, 502);
  return v as unknown as ResumeVariant;
};

const coverOf = (raw: unknown): CoverNote => {
  const c = isObj(raw) && isObj(raw.cover) ? raw.cover : isObj(raw) && isObj(raw.note) ? raw.note : raw;
  if (!isObj(c) || !Array.isArray(c.paragraphs)) throw new MatchApiError(TROUBLE_MESSAGE, 502);
  return c as unknown as CoverNote;
};

/** The stored resume for a match, or null when none has been built yet. */
export async function getResume(id: string, signal?: AbortSignal): Promise<ResumeVariant | null> {
  const raw = await readJsonOrNull<unknown>(await fetch(matchUrl(id, '/resume'), { signal }));
  return raw === null ? null : variantOf(raw);
}

/** Build the resume for this match and stream it: `variant` events, then `done`. Never consumes. */
export async function streamResume(id: string, onEvent: (e: ResumeEvent) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch(matchUrl(id, '/resume'), { method: 'POST', headers: JSON_HEADERS, body: '{}', signal });
  if (!res.ok) throw await failure(res);
  await readNdjson<unknown>(res, (raw) => {
    const e = readResumeEvent(raw);
    if (e) onEvent(e);
  });
}

/** Apply decisions (keep/soften/drop, a figure, a term); the server re-verifies and returns the variant. */
export async function patchResume(id: string, decisions: DecisionChoice[], signal?: AbortSignal): Promise<ResumeVariant> {
  const res = await fetch(matchUrl(id, '/resume'), { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ decisions }), signal });
  return variantOf(await readJson<unknown>(res));
}

export async function getCover(id: string, signal?: AbortSignal): Promise<CoverNote | null> {
  const raw = await readJsonOrNull<unknown>(await fetch(matchUrl(id, '/cover'), { signal }));
  return raw === null ? null : coverOf(raw);
}

export async function postCover(id: string, signal?: AbortSignal): Promise<CoverNote> {
  const res = await fetch(matchUrl(id, '/cover'), { method: 'POST', headers: JSON_HEADERS, body: '{}', signal });
  return coverOf(await readJson<unknown>(res));
}

/* ───────────────────────── downloads ───────────────────────── */

export type DownloadResult = { ok: true } | { ok: false; status: number; message: string };

/**
 * Fetch an export and hand it to the browser as a file. The links on the page are plain `<a href download>`, so a
 * middle-click or a long-press still works; a normal click goes through here so a 409 (decisions still open) can be
 * shown on the page instead of becoming a failed download.
 */
export async function downloadFile(url: string, filename: string): Promise<DownloadResult> {
  let res: Response;
  try {
    res = await fetch(url);
  } catch {
    return { ok: false, status: 0, message: TROUBLE_MESSAGE };
  }
  if (!res.ok) {
    const e = await failure(res);
    return { ok: false, status: e.status, message: e.message };
  }
  const blob = await res.blob();
  const named = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(res.headers.get('content-disposition') ?? '')?.[1];
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = filename;
  if (named) {
    try {
      a.download = decodeURIComponent(named);
    } catch {
      /* a malformed name in the header: the default one stands */
    }
  }
  a.rel = 'noopener';
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 10_000);
  return { ok: true };
}
