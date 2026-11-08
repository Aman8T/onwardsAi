/**
 * What the entry points to auto-apply (a match, its origin bar, the header, Discover's cards) share: the auto-apply
 * status, one cached `applyApi.list()` indexed by match and by Discover listing, the read-only start check per
 * posting, and the person's saved resume name. Each is fetched once per signed-in account and shared by every
 * component that asks, the way `useMe` shares `/api/me`; a different account (or signing out) starts afresh.
 *
 * Also the small hand-off from a match to `/apply/new` (`rememberApplyIntent`): which resume was chosen, and the
 * application link typed for a pasted match. It lives in sessionStorage, so a reload of the resume screen keeps it,
 * and every storage access is wrapped: with storage blocked it lives for this page only.
 *
 * Nothing here charges or starts anything. Browser-only hooks: the parity render never calls them.
 */
import { useEffect, useSyncExternalStore } from 'react';
import type { ApplicationSummary, ApplyCheck, ApplyCheckRequest, ApplyStatus, ResumeChoice } from '../../shared/apply';
import { useMe } from '../auth/useMe';
import { applyApi } from './api';
import { cleanApplyUrl } from './ats';

/* ───────────────────────── the index ───────────────────────── */

/** Stages a new start reopens (`checks.REOPENABLE_STAGES`): such a row does not count as "already applied". */
export const REOPENABLE_STAGES: ReadonlySet<ApplicationSummary['stage']> = new Set<ApplicationSummary['stage']>(['cancelled', 'closed', 'failed']);

export const reopens = (a: Pick<ApplicationSummary, 'stage'>): boolean => REOPENABLE_STAGES.has(a.stage);

export interface ApplicationsIndex {
  byMatch: ReadonlyMap<string, ApplicationSummary>;
  byListing: ReadonlyMap<string, ApplicationSummary>;
}

/**
 * The application that stands for each match and each listing. The list is newest first, so the newest row wins,
 * except that a live row (anything but cancelled, closed or failed) wins over a reopenable one whatever its age.
 */
export function indexApplications(list: readonly ApplicationSummary[]): ApplicationsIndex {
  const byMatch = new Map<string, ApplicationSummary>();
  const byListing = new Map<string, ApplicationSummary>();
  const put = (map: Map<string, ApplicationSummary>, key: string | undefined, a: ApplicationSummary) => {
    if (!key) return;
    const kept = map.get(key);
    if (!kept || (reopens(kept) && !reopens(a))) map.set(key, a);
  };
  for (const a of list) {
    put(byMatch, a.matchId, a);
    put(byListing, a.listingId, a);
  }
  return { byMatch, byListing };
}

/* ───────────────────────── the shared cache ───────────────────────── */

type Load = 'idle' | 'loading' | 'ready' | 'error';

export interface CheckState {
  state: Load;
  check?: ApplyCheck;
}

export interface SavedResume {
  hasResume: boolean;
  name?: string;
}

interface Store {
  /** The account the cache was read for ('' for a visitor); null before anyone asked. */
  account: string | null;
  status: ApplyStatus | null;
  statusLoad: Load;
  list: readonly ApplicationSummary[];
  index: ApplicationsIndex;
  listLoad: Load;
  checks: ReadonlyMap<string, CheckState>;
  resume: SavedResume | null;
  resumeLoad: Load;
  /** Bumped by `requestApplyChoice`: a match screen opens its choice when its number changes. */
  choiceAsks: ReadonlyMap<string, number>;
}

const EMPTY_INDEX: ApplicationsIndex = { byMatch: new Map(), byListing: new Map() };

const fresh = (account: string | null): Store => ({
  account,
  status: null,
  statusLoad: 'idle',
  list: [],
  index: EMPTY_INDEX,
  listLoad: 'idle',
  checks: new Map(),
  resume: null,
  resumeLoad: 'idle',
  choiceAsks: new Map(),
});

let store: Store = fresh(null);
const listeners = new Set<() => void>();

function set(next: Partial<Store>): void {
  store = { ...store, ...next };
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

const SERVER: Store = fresh(null);

function useStore(): Store {
  return useSyncExternalStore(subscribe, () => store, () => SERVER);
}

/** Start over for another account (sign-in, sign-out): nothing read for one person shows for another. */
function forAccount(account: string): void {
  if (store.account !== account) set(fresh(account));
}

function loadStatus(account: string): void {
  forAccount(account);
  if (store.statusLoad === 'loading' || store.statusLoad === 'ready') return;
  set({ statusLoad: 'loading' });
  applyApi.status().then(
    (status) => store.account === account && set({ status, statusLoad: 'ready' }),
    () => store.account === account && set({ statusLoad: 'error' }),
  );
}

function loadList(account: string, again = false): void {
  if (store.account !== account) return;
  if (!again && (store.listLoad === 'loading' || store.listLoad === 'ready')) return;
  if (store.listLoad !== 'ready') set({ listLoad: 'loading' });
  applyApi.list().then(
    (res) => store.account === account && set({ list: res.applications, index: indexApplications(res.applications), listLoad: 'ready' }),
    () => store.account === account && set({ listLoad: store.listLoad === 'ready' ? 'ready' : 'error' }),
  );
}

/** The cache key of a check: the same posting asked twice is one request. */
export function checkKey(body: ApplyCheckRequest): string {
  return [body.matchId ?? '', body.listingId ?? '', body.runId ?? '', body.applyUrl ?? ''].join('|');
}

function loadCheck(account: string, body: ApplyCheckRequest): void {
  if (store.account !== account) return;
  const key = checkKey(body);
  const had = store.checks.get(key);
  if (had && had.state !== 'error' && had.state !== 'idle') return;
  const put = (state: CheckState) => {
    if (store.account !== account) return;
    const checks = new Map(store.checks);
    checks.set(key, state);
    set({ checks });
  };
  put({ state: 'loading' });
  applyApi.check(body).then(
    (check) => put({ state: 'ready', check }),
    () => put({ state: 'error' }),
  );
}

function loadResume(account: string): void {
  if (store.account !== account || store.resumeLoad === 'loading' || store.resumeLoad === 'ready') return;
  set({ resumeLoad: 'loading' });
  applyApi.details().then(
    (d) => store.account === account && set({ resume: { hasResume: d.hasResume, ...(d.resumeName ? { name: d.resumeName } : {}) }, resumeLoad: 'ready' }),
    () => store.account === account && set({ resumeLoad: 'error' }),
  );
}

/** The signed-in account's id, '' for a visitor, null until the server has said. */
function useAccount(): string | null {
  const { loaded, user } = useMe();
  return loaded ? (user?.id ?? '') : null;
}

/* ───────────────────────── hooks ───────────────────────── */

/** The auto-apply status for whoever is signed in, or null until it is known (and when it could not be read). */
export function useApplyStatus(): ApplyStatus | null {
  const account = useAccount();
  const s = useStore();
  useEffect(() => {
    if (account !== null) loadStatus(account);
  }, [account]);
  return account !== null && s.account === account ? s.status : null;
}

/** Auto-apply is switched on and the person is signed in: the only case any entry point offers it. */
export const applyOn = (status: ApplyStatus | null): boolean => !!status?.enabled && status.signedIn;

export interface ApplicationsView extends ApplicationsIndex {
  status: ApplyStatus | null;
  /** The list has been read (always false while auto-apply is off or nobody is signed in). */
  ready: boolean;
  applications: readonly ApplicationSummary[];
  /** Read the list again (after a start, a cancel). */
  refresh: () => void;
}

/** Every application of the signed-in person, read once and shared, with the lookups by match and by listing. */
export function useApplications(): ApplicationsView {
  const status = useApplyStatus();
  const s = useStore();
  const account = s.account;
  const on = applyOn(status) && account !== null && account !== '';
  useEffect(() => {
    if (on && account) loadList(account);
  }, [on, account]);
  const ready = on && s.listLoad === 'ready';
  return {
    status,
    ready,
    applications: ready ? s.list : [],
    byMatch: ready ? s.index.byMatch : EMPTY_INDEX.byMatch,
    byListing: ready ? s.index.byListing : EMPTY_INDEX.byListing,
    refresh: () => {
      if (on && account) loadList(account, true);
    },
  };
}

/**
 * The read-only start check for one posting (`POST /api/apply/check`, charges nothing), asked once per posting and
 * account. `body` null asks nothing (auto-apply off, signed out, or the answer is already known).
 */
export function useApplyCheck(body: ApplyCheckRequest | null): CheckState {
  const s = useStore();
  const account = s.account;
  const key = body ? checkKey(body) : null;
  useEffect(() => {
    // the key names the body, so a new object for the same posting asks nothing again
    if (body && account) loadCheck(account, body);
  }, [key, account]);
  if (!key || !account) return { state: 'idle' };
  return s.checks.get(key) ?? { state: 'loading' };
}

/** The person's saved resume (its file name), read when `wanted`; null until known. */
export function useSavedResume(wanted: boolean): SavedResume | null {
  const s = useStore();
  const account = s.account;
  useEffect(() => {
    if (wanted && account) loadResume(account);
  }, [wanted, account]);
  return wanted ? s.resume : null;
}

/** Ask the match screen for this match to open its "How should we apply?" choice (the origin bar's Apply for me). */
export function requestApplyChoice(matchId: string): void {
  const choiceAsks = new Map(store.choiceAsks);
  choiceAsks.set(matchId, (choiceAsks.get(matchId) ?? 0) + 1);
  set({ choiceAsks });
}

/** The match screen opened the choice: the ask is spent, so coming back to the match later does not reopen it. */
export function consumeApplyChoice(matchId: string): void {
  if (!store.choiceAsks.has(matchId)) return;
  const choiceAsks = new Map(store.choiceAsks);
  choiceAsks.delete(matchId);
  set({ choiceAsks });
}

/** How many times the choice was asked for on this match and not yet opened (0 when none). */
export function useApplyChoiceAsks(matchId: string | undefined): number {
  const s = useStore();
  return matchId ? (s.choiceAsks.get(matchId) ?? 0) : 0;
}

/* ───────────────────────── the hand-off to /apply/new ───────────────────────── */

export const INTENT_KEY = 'tayyari.apply.intent';

/** What the person chose on a match, kept until the start screen reads it. */
export interface ApplyIntent {
  matchId: string;
  resume: ResumeChoice;
  /** The application form's link, typed for a pasted match (the server checks it is the same posting). */
  applyUrl?: string;
  listingId?: string;
  runId?: string;
}

const ID = /^[A-Za-z0-9_-]{1,96}$/;
const LISTING = /^[A-Za-z0-9_.:-]{1,200}$/;

/** Whatever was stored, as a clean intent or null. */
export function parseIntent(raw: unknown): ApplyIntent | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const o = raw as Record<string, unknown>;
  if (typeof o.matchId !== 'string' || !ID.test(o.matchId)) return null;
  if (o.resume !== 'tailored' && o.resume !== 'saved') return null;
  const intent: ApplyIntent = { matchId: o.matchId, resume: o.resume };
  const link = typeof o.applyUrl === 'string' ? cleanApplyUrl(o.applyUrl) : null;
  if (link) intent.applyUrl = link;
  if (typeof o.listingId === 'string' && LISTING.test(o.listingId)) intent.listingId = o.listingId;
  if (typeof o.runId === 'string' && ID.test(o.runId)) intent.runId = o.runId;
  return intent;
}

let intentHere: ApplyIntent | null = null;

/** The person pressed Continue on a match's choice. */
export function rememberApplyIntent(intent: ApplyIntent): void {
  const clean = parseIntent(intent);
  intentHere = clean;
  try {
    if (clean) sessionStorage.setItem(INTENT_KEY, JSON.stringify(clean));
    else sessionStorage.removeItem(INTENT_KEY);
  } catch {
    /* storage blocked: the intent lives for this page only */
  }
}

/** The intent for this match, if the person chose on it in this tab. */
export function applyIntentFor(matchId: string): ApplyIntent | null {
  let stored: ApplyIntent | null = null;
  try {
    stored = parseIntent(JSON.parse(sessionStorage.getItem(INTENT_KEY) ?? 'null'));
  } catch {
    stored = null;
  }
  const intent = stored ?? intentHere;
  return intent && intent.matchId === matchId ? intent : null;
}

/** The application link typed on this match's choice (for the start screen's check and start), if any. */
export function applyLinkFor(matchId: string | undefined): string | undefined {
  return matchId ? applyIntentFor(matchId)?.applyUrl : undefined;
}

export function clearApplyIntent(): void {
  intentHere = null;
  try {
    sessionStorage.removeItem(INTENT_KEY);
  } catch {
    /* storage blocked */
  }
}
