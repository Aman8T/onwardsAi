/**
 * "Your applications": the roles a person saved and what became of them, kept in this browser only.
 *
 *   tayyari.discover.tracker.<profileId>  →  { v: 1, items: TrackedRole[] }
 *
 * Nothing here is sent to the server. A saved role keeps a snapshot of what its row printed (title, employer, place,
 * the apply link and the quick-fit word at the time) so the list still reads when the employer takes the posting
 * down. The only request the list ever causes is `GET /api/discover/listings/{id}`, to find out whether a role is
 * still on the employer's board.
 *
 * A small store per profile, subscribable (`useTracker`), refreshed when another tab changes the key. Every storage
 * access is wrapped: with storage blocked the list lives for this page only.
 */
import { useMemo, useSyncExternalStore } from 'react';
import type { DiscoverWorkMode, ListingCard, QuickFitWord } from '../../shared/discover';
import { placeText, safeHref } from './model';

export type TrackStatus = 'saved' | 'applied' | 'interviewing' | 'offer' | 'closed';

export const STATUSES: readonly TrackStatus[] = ['saved', 'applied', 'interviewing', 'offer', 'closed'];
export const STATUS_LABEL: Record<TrackStatus, string> = {
  saved: 'Saved',
  applied: 'Applied',
  interviewing: 'Interviewing',
  offer: 'Offer',
  closed: 'Closed',
};

export interface TrackedRole {
  /** The listing id. */
  id: string;
  status: TrackStatus;
  savedAt: string;
  appliedAt?: string;
  closedAt?: string;
  updatedAt: string;
  /** The employer's board no longer lists it (the detail route said 404). */
  gone?: boolean;
  /** A full match started from this role, and the band it came to. */
  matchId?: string;
  band?: string;
  /* a snapshot of the row */
  title: string;
  employer: string;
  placeLabel: string;
  workMode: DiscoverWorkMode;
  km?: number;
  applyUrl: string;
  applyHost: string;
  sourceLabel: string;
  word: QuickFitWord;
  salaryText?: string;
}

export const trackKey = (profileId: string) => `tayyari.discover.tracker.${profileId}`;

const WORDS: readonly QuickFitWord[] = ['looks_close', 'worth_a_look', 'stretch', 'cant_tell'];
const MODES: readonly DiscoverWorkMode[] = ['onsite', 'hybrid', 'remote', 'unknown'];
const ID = /^[A-Za-z0-9_:.-]{1,128}$/;

const s = (v: unknown, max = 300): string | undefined => (typeof v === 'string' && v !== '' && v.length <= max ? v : undefined);
const iso = (v: unknown): string | undefined => (typeof v === 'string' && Number.isFinite(Date.parse(v)) ? v : undefined);

/** Whatever was stored, as a clean list: valid roles only, no repeated ids (the first wins). */
export function parseTracked(raw: unknown): TrackedRole[] {
  const list = typeof raw === 'object' && raw !== null && Array.isArray((raw as { items?: unknown }).items) ? (raw as { items: unknown[] }).items : [];
  const seen = new Set<string>();
  const out: TrackedRole[] = [];
  for (const entry of list) {
    if (typeof entry !== 'object' || entry === null) continue;
    const o = entry as Record<string, unknown>;
    const id = s(o.id, 128);
    const title = s(o.title);
    const employer = s(o.employer);
    const applyUrl = s(o.applyUrl, 2000);
    const applyHost = s(o.applyHost, 200);
    const sourceLabel = s(o.sourceLabel);
    const placeLabel = s(o.placeLabel);
    const savedAt = iso(o.savedAt);
    if (!id || !ID.test(id) || seen.has(id) || !title || !employer || !applyUrl || !applyHost || !sourceLabel || !placeLabel || !savedAt) continue;
    if (!safeHref(applyUrl)) continue;
    if (!STATUSES.includes(o.status as TrackStatus) || !WORDS.includes(o.word as QuickFitWord) || !MODES.includes(o.workMode as DiscoverWorkMode)) continue;
    seen.add(id);
    const item: TrackedRole = {
      id,
      status: o.status as TrackStatus,
      savedAt,
      updatedAt: iso(o.updatedAt) ?? savedAt,
      title,
      employer,
      placeLabel,
      workMode: o.workMode as DiscoverWorkMode,
      applyUrl,
      applyHost,
      sourceLabel,
      word: o.word as QuickFitWord,
    };
    const appliedAt = iso(o.appliedAt);
    const closedAt = iso(o.closedAt);
    const matchId = s(o.matchId, 64);
    const band = s(o.band, 40);
    const salaryText = s(o.salaryText);
    if (appliedAt) item.appliedAt = appliedAt;
    if (closedAt) item.closedAt = closedAt;
    if (o.gone === true) item.gone = true;
    if (matchId) item.matchId = matchId;
    if (band) item.band = band;
    if (typeof o.km === 'number' && Number.isFinite(o.km)) item.km = o.km;
    if (salaryText) item.salaryText = salaryText;
    out.push(item);
  }
  return out;
}

/* ───────────────────────── the store ───────────────────────── */

const EMPTY: readonly TrackedRole[] = [];
const lists = new Map<string, readonly TrackedRole[]>();
const listeners = new Set<() => void>();

function readFromStorage(profileId: string): readonly TrackedRole[] {
  try {
    return parseTracked(JSON.parse(localStorage.getItem(trackKey(profileId)) ?? 'null'));
  } catch {
    return EMPTY;
  }
}

function list(profileId: string): readonly TrackedRole[] {
  let l = lists.get(profileId);
  if (!l) {
    l = readFromStorage(profileId);
    lists.set(profileId, l);
  }
  return l;
}

function commit(profileId: string, next: readonly TrackedRole[]): void {
  lists.set(profileId, next);
  try {
    if (next.length > 0) localStorage.setItem(trackKey(profileId), JSON.stringify({ v: 1, items: next }));
    else localStorage.removeItem(trackKey(profileId));
  } catch {
    /* storage blocked or full: the list lives for this page only */
  }
  listeners.forEach((l) => l());
}

try {
  window.addEventListener('storage', (e) => {
    if (e.key && e.key.startsWith('tayyari.discover.tracker.')) {
      lists.delete(e.key.slice('tayyari.discover.tracker.'.length));
      listeners.forEach((l) => l());
    }
  });
} catch {
  /* not in a browser */
}

const update = (profileId: string, id: string, fn: (r: TrackedRole) => TrackedRole): void => {
  const cur = list(profileId);
  if (!cur.some((r) => r.id === id)) return;
  commit(profileId, cur.map((r) => (r.id === id ? fn(r) : r)));
};

const stamp = () => new Date().toISOString();

/** What a row printed, as the snapshot a saved role keeps. */
export function snapshotOf(card: ListingCard, areaCity?: string): Omit<TrackedRole, 'status' | 'savedAt' | 'updatedAt'> {
  const snap: Omit<TrackedRole, 'status' | 'savedAt' | 'updatedAt'> = {
    id: card.id,
    title: card.title,
    employer: card.employer,
    placeLabel: placeText(card.place, areaCity),
    workMode: card.workMode,
    applyUrl: card.applyUrl,
    applyHost: card.applyHost,
    sourceLabel: card.sourceLabel,
    word: card.fit.word,
  };
  if (card.place.km !== undefined) snap.km = card.place.km;
  if (card.salary) snap.salaryText = card.salary.text;
  return snap;
}

export interface Tracker {
  items: readonly TrackedRole[];
  get(id: string): TrackedRole | undefined;
  /** Save a listing (status "Saved"); a role already in the list is left as it is. */
  save(card: ListingCard, areaCity?: string): void;
  remove(id: string): void;
  setStatus(id: string, status: TrackStatus): void;
  attachMatch(id: string, matchId: string, band?: string): void;
  markGone(id: string, gone: boolean): void;
}

function actionsFor(profileId: string): Omit<Tracker, 'items' | 'get'> {
  return {
    save(card, areaCity) {
      const cur = list(profileId);
      if (cur.some((r) => r.id === card.id)) return;
      const now = stamp();
      commit(profileId, [{ ...snapshotOf(card, areaCity), status: 'saved', savedAt: now, updatedAt: now }, ...cur]);
    },
    remove(id) {
      commit(profileId, list(profileId).filter((r) => r.id !== id));
    },
    setStatus(id, status) {
      update(profileId, id, (r) => {
        if (r.status === status) return r;
        const now = stamp();
        return {
          ...r,
          status,
          updatedAt: now,
          ...(status === 'applied' && !r.appliedAt ? { appliedAt: now } : {}),
          ...(status === 'closed' ? { closedAt: now } : {}),
        };
      });
    },
    attachMatch(id, matchId, band) {
      update(profileId, id, (r) => (r.matchId === matchId && r.band === band ? r : { ...r, matchId, ...(band ? { band } : {}), updatedAt: stamp() }));
    },
    markGone(id, gone) {
      update(profileId, id, (r) => ((r.gone ?? false) === gone ? r : { ...r, gone: gone || undefined }));
    },
  };
}

/** Replace a profile's whole list; for the dev gallery only. */
export function seedTracker(profileId: string, items: readonly TrackedRole[]): void {
  commit(profileId, items);
}

/** Add a match to a saved role without a hook (the match screen's origin bar). No-op when the role is not saved. */
export function attachMatchTo(profileId: string, id: string, matchId: string, band?: string): void {
  actionsFor(profileId).attachMatch(id, matchId, band);
}

export function useTracker(profileId: string): Tracker {
  const items = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => list(profileId),
    () => EMPTY,
  );
  const actions = useMemo(() => actionsFor(profileId), [profileId]);
  return useMemo(() => ({ items, get: (id: string) => items.find((r) => r.id === id), ...actions }), [items, actions]);
}
