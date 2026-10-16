/**
 * Discover's address. The lens lives in the query string so a view can be linked to, reloaded and stepped back
 * through:
 *
 *   /discover?city=Bengaluru&locality=HSR%20Layout&reach=10&breadth=next&lane=near&order=ranked&otherCity=Pune
 *   /discover?tab=applications      the person's own list (kept in this browser)
 *
 * Absent parameters mean the defaults. `reach` has no fixed default: with a locality it is 10 km, without one it is
 * the whole city (distances need both ends to be known to a locality). The area (`city`, `locality`, `otherCity`)
 * also lives in localStorage (`area.ts`); the server never stores it.
 *
 * Pure functions only (the store imports this file), so nothing here may touch the store or the DOM.
 */
import type { DiscoverBreadth, DiscoverLane, DiscoverOrder, DiscoverReach } from '../../shared/discover';

export const DISCOVER_PATH = '/discover';

export type DiscoverTab = 'roles' | 'applications';

export interface DiscoverQuery {
  /** The city the person is in or looks in; absent = the one their resume names. */
  city?: string;
  locality?: string;
  /** A second city they would move to (the "Worth moving for" lane). */
  otherCity?: string;
  /** Absent = 10 km with a locality, the whole city without one. */
  reach?: DiscoverReach;
  breadth: DiscoverBreadth;
  lane: DiscoverLane;
  order: DiscoverOrder;
  tab: DiscoverTab;
}

export const REACHES: readonly DiscoverReach[] = ['3', '5', '10', '25', 'city'];
export const BREADTHS: readonly DiscoverBreadth[] = ['role', 'next', 'stretch'];
export const LANES: readonly DiscoverLane[] = ['near', 'remote', 'city'];
export const ORDERS: readonly DiscoverOrder[] = ['ranked', 'posted'];

export const DEFAULT_QUERY: DiscoverQuery = { breadth: 'next', lane: 'near', order: 'ranked', tab: 'roles' };

const NAME_MAX = 60;

const oneOf = <T extends string>(allowed: readonly T[], value: string | null): T | undefined =>
  value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : undefined;

/** A place name from the address: trimmed, short, no control characters; anything else is ignored. */
function placeName(value: string | null): string | undefined {
  if (value === null) return undefined;
  const v = value.trim();
  return v !== '' && v.length <= NAME_MAX && !/[\u0000-\u001f\u007f]/.test(v) ? v : undefined;
}

export function isDiscoverPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, '') === DISCOVER_PATH;
}

/** The query an address names, or null when it is not the Discover address. */
export function parseDiscoverUrl(pathname: string, search: string): DiscoverQuery | null {
  if (!isDiscoverPath(pathname)) return null;
  const p = new URLSearchParams(search);
  const q: DiscoverQuery = {
    ...DEFAULT_QUERY,
    breadth: oneOf(BREADTHS, p.get('breadth')) ?? DEFAULT_QUERY.breadth,
    lane: oneOf(LANES, p.get('lane')) ?? DEFAULT_QUERY.lane,
    order: oneOf(ORDERS, p.get('order')) ?? DEFAULT_QUERY.order,
    tab: p.get('tab') === 'applications' ? 'applications' : 'roles',
  };
  const city = placeName(p.get('city'));
  const locality = placeName(p.get('locality'));
  const otherCity = placeName(p.get('otherCity'));
  const reach = oneOf(REACHES, p.get('reach'));
  if (city) q.city = city;
  if (locality && city) q.locality = locality;
  if (otherCity) q.otherCity = otherCity;
  if (reach) q.reach = reach;
  // the "Worth moving for" lane needs a city to look in
  if (q.lane === 'city' && !q.otherCity) q.lane = 'near';
  return q;
}

/** `{ ...query, ...patch }` without undefined keys, so a patch can clear a field by setting it to undefined. */
export function mergeQuery(query: DiscoverQuery, patch: Partial<DiscoverQuery>): DiscoverQuery {
  const next = { ...query, ...patch } as Record<string, unknown>;
  for (const key of Object.keys(next)) if (next[key] === undefined) delete next[key];
  const merged = next as unknown as DiscoverQuery;
  if (merged.lane === 'city' && !merged.otherCity) merged.lane = 'near';
  if (!merged.locality && merged.reach && merged.reach !== 'city') delete merged.reach;
  return merged;
}

/** The reach actually asked of the server. */
export function effectiveReach(query: DiscoverQuery): DiscoverReach {
  return query.reach ?? (query.locality ? '10' : 'city');
}

/** The pathname and search of a query; defaults are left out. */
export function discoverPath(query: DiscoverQuery): string {
  const p = new URLSearchParams();
  if (query.city) p.set('city', query.city);
  if (query.locality) p.set('locality', query.locality);
  if (query.otherCity) p.set('otherCity', query.otherCity);
  if (query.reach) p.set('reach', query.reach);
  if (query.breadth !== DEFAULT_QUERY.breadth) p.set('breadth', query.breadth);
  if (query.lane !== DEFAULT_QUERY.lane) p.set('lane', query.lane);
  if (query.order !== DEFAULT_QUERY.order) p.set('order', query.order);
  if (query.tab !== DEFAULT_QUERY.tab) p.set('tab', query.tab);
  const s = p.toString();
  return s ? `${DISCOVER_PATH}?${s}` : DISCOVER_PATH;
}

/** The parameters of `GET /api/discover` (the server's lens, not the screen's tab). */
export function apiParams(profileId: string, query: DiscoverQuery): URLSearchParams {
  const p = new URLSearchParams({ profileId });
  if (query.city) p.set('city', query.city);
  if (query.locality) p.set('locality', query.locality);
  if (query.otherCity) p.set('otherCity', query.otherCity);
  p.set('reach', effectiveReach(query));
  p.set('breadth', query.breadth);
  p.set('lane', query.lane);
  p.set('order', query.order);
  return p;
}
