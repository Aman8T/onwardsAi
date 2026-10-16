/**
 * The cities Discover knows: `GET /api/discover/cities`. (The roles themselves come from `jobsearch/api.ts`.) It lives
 * beside the screens, like the match client, so waves never merge on one file; `ApiError` is reused from `lib/api`.
 *
 * The list does not change in a session, so it is asked for once. Failures keep the HTTP status and say what happened
 * in a sentence; the picker then says the list is not available and the screen still works.
 */
import type { DiscoverCitiesResponse, DiscoverCity } from '../../shared/discover';
import { ApiError } from '../lib/api';

const TROUBLE = 'The list of cities is not available right now.';

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Only well-formed cities survive; a name is short plain text. */
export function readCities(raw: unknown): DiscoverCity[] {
  const list = isObj(raw) ? (raw as Partial<DiscoverCitiesResponse>).cities : undefined;
  if (!Array.isArray(list)) return [];
  const out: DiscoverCity[] = [];
  for (const c of list as unknown[]) {
    if (!isObj(c) || typeof c.name !== 'string') continue;
    const name = c.name.trim();
    if (name === '' || name.length > 60) continue;
    out.push({ name, localities: Array.isArray(c.localities) ? c.localities.filter((l): l is string => typeof l === 'string') : [] });
  }
  return out;
}

let cities: Promise<DiscoverCity[]> | null = null;

export function getCities(signal?: AbortSignal): Promise<DiscoverCity[]> {
  if (!cities) {
    cities = fetch('/api/discover/cities', { signal })
      .then((res) => {
        if (!res.ok) throw new ApiError(TROUBLE, res.status);
        return res.json() as Promise<unknown>;
      })
      .then(readCities)
      .catch((err: unknown) => {
        cities = null; // try again next time
        throw err;
      });
  }
  return cities;
}
