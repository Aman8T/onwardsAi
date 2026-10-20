/**
 * The Leafout Weekly's address: `/weekly` is the reading room (the latest edition), `/weekly?e=<id>` opens that edition.
 * Pure functions only (the store imports this file), so nothing here may touch the store or the DOM.
 */
export const WEEKLY_PATH = '/weekly';

export interface WeeklyQuery {
  /** The edition to read; the latest when absent. */
  edition?: string;
}

export const DEFAULT_WEEKLY: WeeklyQuery = {};

const ID = /^[A-Za-z0-9_-]{1,64}$/;

export function isWeeklyPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, '') === WEEKLY_PATH;
}

/** The query an address names, or null when it is not the Weekly address. */
export function parseWeeklyUrl(pathname: string, search: string): WeeklyQuery | null {
  if (!isWeeklyPath(pathname)) return null;
  const e = new URLSearchParams(search).get('e');
  return e !== null && ID.test(e) ? { edition: e } : {};
}

/** `{ ...query, ...patch }` without undefined keys, so a patch can clear the edition by setting it to undefined. */
export function mergeWeekly(query: WeeklyQuery, patch: Partial<WeeklyQuery>): WeeklyQuery {
  const edition = 'edition' in patch ? patch.edition : query.edition;
  return edition && ID.test(edition) ? { edition } : {};
}

/** The pathname and search of a query; the latest edition is the bare path. */
export function weeklyPath(query: WeeklyQuery): string {
  return query.edition ? `${WEEKLY_PATH}?e=${encodeURIComponent(query.edition)}` : WEEKLY_PATH;
}
