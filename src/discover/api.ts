/**
 * The Discover API client: `GET /api/discover`, `GET /api/discover/cities`, `GET /api/discover/listings/{id}`.
 * It lives beside the screens (not in `src/lib/api.ts`), like the match client, so the waves never merge on one
 * file; `ApiError` is reused from there.
 *
 * Answers are read defensively: a field the server leaves out becomes an empty list, never a crash, because the
 * screens must render whatever arrives. Failures keep the HTTP status and say what happened in a sentence:
 *   404 the profile is not on this server · 429 slow down · 503 Discover is switched off here · anything else, trouble.
 */
import type { DiscoverCitiesResponse, DiscoverCity, DiscoverResponse, ListingDetail } from '../../shared/discover';
import { ApiError } from '../lib/api';
import { apiParams } from './route';
import type { DiscoverQuery } from './route';

const TROUBLE = 'Discover is having trouble right now. Please try again in a moment.';

export class DiscoverApiError extends ApiError {
  readonly code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message, status);
    this.name = 'DiscoverApiError';
    this.code = code;
  }
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

function messageFor(status: number, code?: string, sentence?: string): string {
  if (status === 503 && code === 'discover_disabled') return "Discover isn't switched on for this server yet.";
  if (status === 404) return "We couldn't find your profile on this server. Analyse your resume again, then come back.";
  if (status === 429) return 'Too many requests right now. Please wait a moment and try again.';
  if (sentence && !/^[a-z][a-z0-9_]*$/.test(sentence)) return sentence;
  return TROUBLE;
}

async function failure(res: Response): Promise<DiscoverApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const o = isObj(body) ? body : {};
  const text = typeof o.error === 'string' ? o.error : undefined;
  const code = typeof o.error === 'string' && /^[a-z][a-z0-9_]*$/.test(o.error) ? o.error : typeof o.code === 'string' ? o.code : undefined;
  return new DiscoverApiError(messageFor(res.status, code, text), res.status, code);
}

async function readJson(res: Response): Promise<unknown> {
  if (!res.ok) throw await failure(res);
  if (!(res.headers.get('content-type') ?? '').includes('json')) throw new DiscoverApiError(TROUBLE, 502);
  return res.json();
}

const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/** The response with every list present, so the screens never meet `undefined.map`. */
export function readDiscover(raw: unknown): DiscoverResponse {
  if (!isObj(raw) || !isObj(raw.area)) throw new DiscoverApiError(TROUBLE, 502);
  const counts = isObj(raw.laneCounts) ? raw.laneCounts : {};
  const cov = isObj(raw.coverage) ? raw.coverage : {};
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  return {
    ...(raw as unknown as DiscoverResponse),
    total: num(raw.total),
    reachStops: arr(raw.reachStops),
    breadthStops: arr(raw.breadthStops),
    laneCounts: { near: num(counts.near), remote: num(counts.remote), city: num(counts.city) },
    groups: arr(raw.groups),
    listings: arr(raw.listings),
    companies: arr(raw.companies),
    signals: arr(raw.signals),
    coverage: { ...(cov as unknown as DiscoverResponse['coverage']), employers: num(cov.employers), listings: num(cov.listings) },
  };
}

/** The lens as the server sees it. The person's city and locality ride along as parameters and are not stored there. */
export async function getDiscover(profileId: string, query: DiscoverQuery, signal?: AbortSignal): Promise<DiscoverResponse> {
  const res = await fetch(`/api/discover?${apiParams(profileId, query)}`, { signal });
  return readDiscover(await readJson(res));
}

let cities: Promise<DiscoverCity[]> | null = null;

/** The cities and localities we know. The list does not change in a session, so it is asked for once. */
export function getCities(signal?: AbortSignal): Promise<DiscoverCity[]> {
  if (!cities) {
    cities = fetch('/api/discover/cities', { signal })
      .then(readJson)
      .then((raw) => arr<DiscoverCity>(isObj(raw) ? (raw as unknown as DiscoverCitiesResponse).cities : []))
      .catch((err) => {
        cities = null; // try again next time
        throw err;
      });
  }
  return cities;
}

/** One listing and the posting text a full match is started from; null when it is closed or unknown (404). */
export async function getListing(id: string, profileId: string, signal?: AbortSignal): Promise<ListingDetail | null> {
  const res = await fetch(`/api/discover/listings/${encodeURIComponent(id)}?profileId=${encodeURIComponent(profileId)}`, { signal });
  if (res.status === 404) return null;
  const raw = await readJson(res);
  if (!isObj(raw) || !isObj(raw.card) || typeof raw.postingText !== 'string') throw new DiscoverApiError(TROUBLE, 502);
  return raw as unknown as ListingDetail;
}
