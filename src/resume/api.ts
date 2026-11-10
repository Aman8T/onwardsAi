/**
 * The resume API client (`docs/resume.md`, shapes in `shared/resume.ts`):
 *
 *   GET   /api/resumes?profile=<id>          ResumeList (the first call creates the uploaded resume)
 *   GET   /api/resumes/blocks?profile=<id>   BlocksResponse
 *   POST  /api/resumes/path?profile=<id>     PathRequest → ResumeDetail (the one already made for the role, else made now)
 *   GET   /api/resumes/{id}                  ResumeDetail
 *   PATCH /api/resumes/{id}                  ResumeOpsRequest → ResumeDetail (all ops apply or none)
 *   POST  /api/resumes/{id}/finalise         FinaliseRequest → FinaliseResponse
 *   POST  /api/resumes/{id}/default          ResumeDetail (409 without a finalised version)
 *   GET   /api/resumes/{id}/resume.pdf|.docx the file of the finalised version (409 without one)
 *
 * Failures keep the HTTP status, because the screens decide what to do from it: 401 sign in · 404 not yours or gone ·
 * 409 not finalised yet · 422 an op that does not fit · 429 slow down. An error body is `{error: "<sentence>"}` like
 * the rest of the app; where the server sends a code instead of a sentence (`not_finalised`, `payment_required`), or
 * nothing readable, the sentence is chosen here from the status.
 */
import { ApiError } from '../lib/api';
import { downloadFile as fetchFile } from '../match/api';
import type { DownloadResult } from '../match/api';
import type {
  BlocksResponse,
  FileKind,
  FinaliseRequest,
  FinaliseResponse,
  PathRequest,
  ResumeDetail,
  ResumeList,
  ResumeOp,
  ResumeOpsRequest,
} from './types';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const NOT_FINALISED_MESSAGE = 'Finalise this resume first. Only a finalised resume is downloaded or made the default.';
const SIGN_IN_MESSAGE = 'Sign in to open your resumes.';
const NOT_FOUND_MESSAGE = "That resume isn't here. It may belong to another profile, or it was removed.";
const SPEND_MESSAGE = 'Making a resume for a role uses a model call, and there is nothing left to spend on one right now.';
const RATE_MESSAGE = 'Too many requests right now. Please wait a moment and try again.';
const TROUBLE_MESSAGE = 'Leafout is having trouble right now. Please try again in a moment.';

export class ResumeApiError extends ApiError {
  /** The machine code, when the server sent one (`not_finalised`…). */
  readonly code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message, status);
    this.name = 'ResumeApiError';
    this.code = code;
  }
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
/** A machine code such as `not_finalised`, as opposed to a sentence. */
const looksLikeCode = (s: string) => /^[a-z][a-z0-9_]*$/.test(s);

function messageFor(status: number, sentence: string | undefined, code: string | undefined): string {
  if (code === 'not_finalised') return NOT_FINALISED_MESSAGE;
  if (status === 402) return SPEND_MESSAGE;
  if (sentence && !looksLikeCode(sentence)) return sentence;
  if (status === 401) return SIGN_IN_MESSAGE;
  if (status === 404) return NOT_FOUND_MESSAGE;
  if (status === 409) return NOT_FINALISED_MESSAGE;
  if (status === 429) return RATE_MESSAGE;
  if (status >= 500) return TROUBLE_MESSAGE;
  return `Request failed (${status})`;
}

/** The error for a non-2xx response: `{error}` as a sentence, or as a code (`{error: "not_finalised"}` or `{code}`). */
export async function failure(res: Response): Promise<ResumeApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const o = isObj(body) ? body : {};
  const error = typeof o.error === 'string' ? o.error : undefined;
  const code = typeof o.code === 'string' ? o.code : error && looksLikeCode(error) ? error : undefined;
  return new ResumeApiError(messageFor(res.status, error, code), res.status, code);
}

async function readJson(res: Response): Promise<Record<string, unknown>> {
  if (!res.ok) throw await failure(res);
  if (!(res.headers.get('content-type') ?? '').includes('json')) throw new ResumeApiError(TROUBLE_MESSAGE, 502);
  const body: unknown = await res.json();
  if (!isObj(body)) throw new ResumeApiError(TROUBLE_MESSAGE, 502);
  return body;
}

/** A `ResumeDetail` the page can render: the summary and the draft are there, and every list is a list. */
function detailOf(raw: Record<string, unknown>): ResumeDetail {
  if (!isObj(raw.resume) || typeof raw.resume.id !== 'string' || !isObj(raw.variant) || !isObj(raw.variant.header)) {
    throw new ResumeApiError(TROUBLE_MESSAGE, 502);
  }
  const d = raw as unknown as ResumeDetail;
  return {
    ...d,
    marks: Array.isArray(d.marks) ? d.marks : [],
    facts: Array.isArray(d.facts) ? d.facts : [],
    leftOut: Array.isArray(d.leftOut) ? d.leftOut : [],
    canDownload: d.canDownload === true,
  };
}

const withProfile = (path: string, profileId: string) => `/api/resumes${path}?profile=${encodeURIComponent(profileId)}`;
const resumeUrl = (id: string, tail = '') => `/api/resumes/${encodeURIComponent(id)}${tail}`;

/** The address of a finalised resume's file. */
export const resumeFileUrl = (id: string, file: FileKind) => resumeUrl(id, `/resume.${file}`);

export async function listResumes(profileId: string, signal?: AbortSignal): Promise<ResumeList> {
  const raw = await readJson(await fetch(withProfile('', profileId), { signal }));
  const list = raw as unknown as ResumeList;
  return {
    profileId: typeof list.profileId === 'string' ? list.profileId : profileId,
    resumes: Array.isArray(list.resumes) ? list.resumes : [],
    paths: Array.isArray(list.paths) ? list.paths : [],
    blocks: typeof list.blocks === 'number' ? list.blocks : 0,
  };
}

export async function getBlocks(profileId: string, signal?: AbortSignal): Promise<BlocksResponse> {
  const raw = await readJson(await fetch(withProfile('/blocks', profileId), { signal }));
  const b = raw as unknown as BlocksResponse;
  return {
    profileId: typeof b.profileId === 'string' ? b.profileId : profileId,
    entries: Array.isArray(b.entries) ? b.entries : [],
    blocks: Array.isArray(b.blocks) ? b.blocks : [],
    resumes: typeof b.resumes === 'number' ? b.resumes : 0,
  };
}

/** The resume for one of the next roles: the one already made, else made now (a model call ranks the blocks). */
export async function makePath(profileId: string, roleId: string, signal?: AbortSignal): Promise<ResumeDetail> {
  const body: PathRequest = { roleId };
  const res = await fetch(withProfile('/path', profileId), { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body), signal });
  return detailOf(await readJson(res));
}

export async function getResume(id: string, signal?: AbortSignal): Promise<ResumeDetail> {
  return detailOf(await readJson(await fetch(resumeUrl(id), { signal })));
}

/** Apply operations to the draft; the answer is the whole resume as it now stands. */
export async function patchResume(id: string, ops: ResumeOp[], signal?: AbortSignal): Promise<ResumeDetail> {
  const body: ResumeOpsRequest = { ops };
  const res = await fetch(resumeUrl(id), { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(body), signal });
  return detailOf(await readJson(res));
}

/** Run the checks. `report.done` is false while findings wait for the person; send their answers with the next call. */
export async function finalise(id: string, req: FinaliseRequest, signal?: AbortSignal): Promise<FinaliseResponse> {
  const res = await fetch(resumeUrl(id, '/finalise'), { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(req), signal });
  const raw = await readJson(res);
  if (!isObj(raw.detail) || !isObj(raw.report)) throw new ResumeApiError(TROUBLE_MESSAGE, 502);
  const report = raw.report as unknown as FinaliseResponse['report'];
  return {
    detail: detailOf(raw.detail),
    report: {
      done: report.done === true,
      findings: Array.isArray(report.findings) ? report.findings : [],
      fixed: Array.isArray(report.fixed) ? report.fixed : [],
      ...(isObj(report.lint) && Array.isArray(report.lint.items) ? { lint: report.lint } : {}),
    },
  };
}

export async function makeDefault(id: string, signal?: AbortSignal): Promise<ResumeDetail> {
  const res = await fetch(resumeUrl(id, '/default'), { method: 'POST', headers: JSON_HEADERS, body: '{}', signal });
  return detailOf(await readJson(res));
}

/* ───────────────────────── downloads ───────────────────────── */

export type { DownloadResult };

/**
 * Fetch a finalised resume's file and hand it to the browser (the match screens' own helper does the work). The links
 * on the page are plain `<a href download>`, so a middle-click or a long-press still works; a normal click goes
 * through here so a 409 (no finalised version) is said on the page instead of becoming a failed download.
 */
export async function downloadResume(id: string, file: FileKind, filename: string): Promise<DownloadResult> {
  const result = await fetchFile(resumeFileUrl(id, file), filename);
  return !result.ok && result.status === 409 ? { ...result, message: NOT_FINALISED_MESSAGE } : result;
}
