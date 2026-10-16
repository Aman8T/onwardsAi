/**
 * The person's area, kept in this browser only:
 *
 *   tayyari.discover.area.<profileId>  →  { city?, locality?, otherCity?, localityDeclined? }
 *
 * The server never stores it; it receives the city and locality as query parameters on each request and uses them for
 * that request alone. The address carries the same values, and wins when both are present.
 *
 * Every storage access is wrapped: with storage blocked the area lives in the address for this page only.
 */
import type { DiscoverQuery } from './route';

export interface StoredArea {
  city?: string;
  locality?: string;
  /** A city the person would move to. */
  otherCity?: string;
  /** They chose "Not now" when asked for a locality; do not ask again. */
  localityDeclined?: boolean;
}

const NAME_MAX = 60;
export const areaKey = (profileId: string) => `tayyari.discover.area.${profileId}`;

const text = (v: unknown): string | undefined => (typeof v === 'string' && v.trim() !== '' && v.length <= NAME_MAX ? v.trim() : undefined);

/** Whatever was stored, as a clean area: known fields only. A locality means nothing without its city. */
export function parseArea(raw: unknown): StoredArea {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return {};
  const o = raw as Record<string, unknown>;
  const city = text(o.city);
  const locality = city ? text(o.locality) : undefined;
  const otherCity = text(o.otherCity);
  const area: StoredArea = {};
  if (city) area.city = city;
  if (locality) area.locality = locality;
  if (otherCity) area.otherCity = otherCity;
  if (o.localityDeclined === true) area.localityDeclined = true;
  return area;
}

export function readArea(profileId: string): StoredArea {
  try {
    return parseArea(JSON.parse(localStorage.getItem(areaKey(profileId)) ?? 'null'));
  } catch {
    return {};
  }
}

export function writeArea(profileId: string, area: StoredArea): void {
  try {
    const clean = parseArea(area);
    if (Object.keys(clean).length > 0) localStorage.setItem(areaKey(profileId), JSON.stringify(clean));
    else localStorage.removeItem(areaKey(profileId));
  } catch {
    /* storage blocked or full: the address still carries the area */
  }
}

/** The address' area where it has one, else the stored one (the address wins). */
export function withStoredArea(query: DiscoverQuery, stored: StoredArea): DiscoverQuery {
  if (query.city) return query.otherCity || !stored.otherCity ? query : { ...query, otherCity: stored.otherCity };
  const next: DiscoverQuery = { ...query };
  if (stored.city) next.city = stored.city;
  if (stored.city && stored.locality) next.locality = stored.locality;
  if (stored.otherCity && !next.otherCity) next.otherCity = stored.otherCity;
  return next;
}
