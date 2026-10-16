/**
 * Where the person looks, kept in this browser only:
 *
 *   tayyari.discover.area.<profileId>  →  { city?, remote? }
 *
 * `city` is the one they picked (absent = the city their resume names) and `remote` whether remote roles open to India
 * are included (absent = yes). The server never stores either; it receives them with each search and each look at the
 * last results. The address carries the same values, and wins when both are present.
 *
 * Older versions kept a locality and a second city here; those are dropped on read. Every storage access is wrapped:
 * with storage blocked the choice lives in the address for this page only.
 */
import type { DiscoverQuery } from './route';

export interface StoredArea {
  city?: string;
  /** false = remote roles are left out; absent = included. */
  remote?: boolean;
}

const NAME_MAX = 60;
export const areaKey = (profileId: string) => `tayyari.discover.area.${profileId}`;

const text = (v: unknown): string | undefined => (typeof v === 'string' && v.trim() !== '' && v.length <= NAME_MAX ? v.trim() : undefined);

/** Whatever was stored, as a clean area: known fields only. */
export function parseArea(raw: unknown): StoredArea {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return {};
  const o = raw as Record<string, unknown>;
  const area: StoredArea = {};
  const city = text(o.city);
  if (city) area.city = city;
  if (o.remote === false) area.remote = false;
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
    /* storage blocked or full: the address still carries the choice */
  }
}

/**
 * What this browser remembers that the address does not say: the stored city when the address names none, and
 * "without remote" when the address is silent (it only ever spells `remote=0`).
 */
export function storedPatch(query: DiscoverQuery, stored: StoredArea): Partial<DiscoverQuery> {
  const patch: Partial<DiscoverQuery> = {};
  if (!query.city && stored.city) patch.city = stored.city;
  if (query.remote && stored.remote === false) patch.remote = false;
  return patch;
}
