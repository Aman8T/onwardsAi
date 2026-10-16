/**
 * Discover's address. The place and the screen live in the query string so a view can be linked to, reloaded and
 * stepped back through:
 *
 *   /discover?city=Bengaluru&remote=0                          the roles of the last search, in this city, without remote
 *   /discover?tab=applications                                 the person's own list (kept in this browser)
 *   /discover?tab=prepare&run=<runId>&job=<jobId>              the prep page of one role of a search
 *   /discover?tab=compare&ids=<jobId>,<jobId>,<jobId>          two or three roles side by side
 *
 * Absent parameters mean the defaults: the city the resume names, remote roles included, the roles tab. The place
 * (`city`, `remote`) also lives in localStorage (`area.ts`); the server never stores it.
 *
 * Pure functions only (the store imports this file), so nothing here may touch the store or the DOM.
 */

export const DISCOVER_PATH = '/discover';

export type DiscoverTab = 'roles' | 'applications' | 'prepare' | 'compare';

export interface DiscoverQuery {
  /** The city the person looks in; absent = the one their resume names. */
  city?: string;
  /** Include remote roles open to India (the default). */
  remote: boolean;
  tab: DiscoverTab;
  /** `tab=prepare`: the search the role came from. */
  run?: string;
  /** `tab=prepare`: the role. */
  job?: string;
  /** `tab=compare`: the roles side by side (two or three). */
  ids?: string[];
}

export const DEFAULT_QUERY: DiscoverQuery = { remote: true, tab: 'roles' };

/** The most roles that can be compared at once. */
export const COMPARE_MAX = 3;

const TABS: readonly DiscoverTab[] = ['roles', 'applications', 'prepare', 'compare'];
const NAME_MAX = 60;
const ID = /^[A-Za-z0-9_-]{1,64}$/;

/** A place name from the address: trimmed, short, no control characters; anything else is ignored. */
function placeName(value: string | null): string | undefined {
  if (value === null) return undefined;
  const v = value.trim();
  return v !== '' && v.length <= NAME_MAX && !/[\u0000-\u001f\u007f]/.test(v) ? v : undefined;
}

const idOf = (value: string | null): string | undefined => (value !== null && ID.test(value) ? value : undefined);

export function isDiscoverPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, '') === DISCOVER_PATH;
}

/** Make the query consistent: a tab keeps only the fields it uses, and a tab without them is the roles tab. */
function settle(query: DiscoverQuery): DiscoverQuery {
  const next: DiscoverQuery = { ...query };
  if (next.tab !== 'prepare') {
    delete next.run;
    delete next.job;
  }
  if (next.tab !== 'compare') delete next.ids;
  if (next.tab === 'prepare' && (!next.run || !next.job)) next.tab = 'roles';
  if (next.tab === 'compare') {
    const ids = [...new Set(next.ids ?? [])].slice(0, COMPARE_MAX);
    if (ids.length > 0) next.ids = ids;
    else next.tab = 'roles';
  }
  if (next.tab === 'roles' || next.tab === 'applications') {
    delete next.run;
    delete next.job;
    delete next.ids;
  }
  return next;
}

/** The query an address names, or null when it is not the Discover address. */
export function parseDiscoverUrl(pathname: string, search: string): DiscoverQuery | null {
  if (!isDiscoverPath(pathname)) return null;
  const p = new URLSearchParams(search);
  const tab = TABS.find((t) => t === p.get('tab')) ?? 'roles';
  const q: DiscoverQuery = { remote: p.get('remote') !== '0', tab };
  const city = placeName(p.get('city'));
  if (city) q.city = city;
  const run = idOf(p.get('run'));
  const job = idOf(p.get('job'));
  if (run) q.run = run;
  if (job) q.job = job;
  const ids = (p.get('ids') ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => ID.test(s));
  if (ids.length > 0) q.ids = ids;
  return settle(q);
}

/** `{ ...query, ...patch }` without undefined keys, so a patch can clear a field by setting it to undefined. */
export function mergeQuery(query: DiscoverQuery, patch: Partial<DiscoverQuery>): DiscoverQuery {
  const next = { ...query, ...patch } as Record<string, unknown>;
  for (const key of Object.keys(next)) if (next[key] === undefined) delete next[key];
  return settle(next as unknown as DiscoverQuery);
}

/** The pathname and search of a query; defaults are left out. */
export function discoverPath(query: DiscoverQuery): string {
  const q = settle(query);
  const p = new URLSearchParams();
  if (q.city) p.set('city', q.city);
  if (!q.remote) p.set('remote', '0');
  if (q.tab !== DEFAULT_QUERY.tab) p.set('tab', q.tab);
  if (q.run) p.set('run', q.run);
  if (q.job) p.set('job', q.job);
  if (q.ids && q.ids.length > 0) p.set('ids', q.ids.join(','));
  const s = p.toString().replace(/%2C/g, ',');
  return s ? `${DISCOVER_PATH}?${s}` : DISCOVER_PATH;
}
