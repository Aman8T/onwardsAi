/**
 * Your applications: the roles a person saved from Search with AI, and where each stands.
 *
 *   localStorage "tayyari.discover.applications.<profileId>"  →  TrackedJob[]
 *
 * Kept in this browser until accounts sync it. Each item holds a snapshot of the role as the search showed it (an
 * `AiJob`), the run it came from (so its prep page, referral note and posting text can be fetched), its stage, a note,
 * and the date it was applied to.
 *
 * Storage is not trusted: what is read back is checked item by item (`parseTracked`), so a corrupt entry is dropped and
 * the rest still show. The list holds at most 200 roles (`MAX_TRACKED`); saving one more makes room by letting go of the
 * oldest closed role, then the oldest one still only saved, and never one the person has applied to. Every storage access
 * is wrapped: with storage blocked or full the list lives for this page only. A change in another tab is picked up.
 *
 * Moving a role to Applied for the first time writes today's date (the person's own day) as the date applied, wherever
 * it is moved from; the date can then be changed or cleared.
 */
import { useMemo, useSyncExternalStore } from 'react';
import type { AiJob } from '../../../shared/jobsearch';
import { arr, isObj, isoDay, isoTime, oneOf, text } from '../prepare/read';
import { localDate } from '../prepare/words';
import { readStoredJob } from './stored';

export type TrackStage = 'saved' | 'applied' | 'interviewing' | 'offer' | 'closed';

export const STAGES: readonly TrackStage[] = ['saved', 'applied', 'interviewing', 'offer', 'closed'];

export const STAGE_LABEL: Record<TrackStage, string> = {
  saved: 'Saved',
  applied: 'Applied',
  interviewing: 'Interviewing',
  offer: 'Offer',
  closed: 'Closed',
};

export interface TrackedJob {
  job: AiJob;
  /** The search run it was found in (absent for a role saved before runs were kept). */
  runId?: string;
  stage: TrackStage;
  note: string;
  /** YYYY-MM-DD, when the person applied. */
  appliedAt?: string;
  savedAt: string;
  updatedAt: string;
  /** YYYY-MM-DD, when the employer's board stopped listing the role ("The employer took this posting down on 12 Oct"). */
  takenDownAt?: string;
}

export interface Tracker {
  items: readonly TrackedJob[];
  isSaved(id: string): boolean;
  get(id: string): TrackedJob | undefined;
  save(job: AiJob, runId?: string): void;
  /** Save when not saved, remove when saved. */
  toggle(job: AiJob, runId?: string): void;
  remove(id: string): void;
  setStage(id: string, stage: TrackStage): void;
  setNote(id: string, note: string): void;
  setAppliedAt(id: string, date?: string): void;
}

export const applicationsKey = (profileId: string) => `tayyari.discover.applications.${profileId}`;

/** The most roles one list holds. */
export const MAX_TRACKED = 200;
/** The longest note, in characters. */
export const MAX_NOTE = 300;

const RUN_ID_MAX = 96;

/* ───────────────────────── reading what was stored ───────────────────────── */

/**
 * Whatever was stored, as a clean list: valid roles only, no repeated ids (the first wins), at most `MAX_TRACKED`.
 * The list itself is an array; `{ items: [...] }` is read too, so an older shape is not lost.
 */
export function parseTracked(raw: unknown): TrackedJob[] {
  const list = Array.isArray(raw) ? raw : isObj(raw) ? arr(raw.items) : [];
  const seen = new Set<string>();
  const out: TrackedJob[] = [];
  for (const entry of list) {
    if (out.length >= MAX_TRACKED) break;
    if (!isObj(entry)) continue;
    const stage = oneOf(STAGES, entry.stage);
    if (!stage) continue;
    const now = new Date().toISOString();
    const savedAt = isoTime(entry.savedAt) ?? isoTime(entry.updatedAt) ?? now;
    const job = readStoredJob(entry.job, savedAt);
    if (!job || seen.has(job.id)) continue;
    seen.add(job.id);
    const runId = text(entry.runId, RUN_ID_MAX);
    const appliedAt = isoDay(entry.appliedAt);
    const takenDownAt = isoDay(entry.takenDownAt);
    out.push({
      job,
      ...(runId ? { runId } : {}),
      stage,
      note: typeof entry.note === 'string' ? entry.note.slice(0, MAX_NOTE) : '',
      ...(appliedAt ? { appliedAt } : {}),
      savedAt,
      updatedAt: isoTime(entry.updatedAt) ?? savedAt,
      ...(takenDownAt ? { takenDownAt } : {}),
    });
  }
  return out;
}

/* ───────────────────────── the store ───────────────────────── */

const EMPTY: readonly TrackedJob[] = [];
const cache = new Map<string, readonly TrackedJob[]>();
const listeners = new Set<() => void>();

function read(profileId: string): readonly TrackedJob[] {
  const held = cache.get(profileId);
  if (held) return held;
  let items: readonly TrackedJob[] = EMPTY;
  try {
    const raw = window.localStorage.getItem(applicationsKey(profileId));
    items = raw ? parseTracked(JSON.parse(raw)) : EMPTY;
  } catch {
    items = EMPTY;
  }
  if (items.length === 0) items = EMPTY;
  cache.set(profileId, items);
  return items;
}

function write(profileId: string, items: readonly TrackedJob[]): void {
  const next = items.length > 0 ? items : EMPTY;
  cache.set(profileId, next);
  try {
    if (next.length > 0) window.localStorage.setItem(applicationsKey(profileId), JSON.stringify(next));
    else window.localStorage.removeItem(applicationsKey(profileId));
  } catch {
    // storage blocked or full: the list lives for this page only
  }
  listeners.forEach((l) => l());
}

const PREFIX = applicationsKey('');

/** Another tab changed (or cleared) a list: forget what this page held and read it again. */
function onStorage(e: StorageEvent): void {
  if (e.key === null) {
    cache.clear();
  } else if (e.key.startsWith(PREFIX)) {
    cache.delete(e.key.slice(PREFIX.length));
  } else {
    return;
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  if (listeners.size === 0) {
    try {
      window.addEventListener('storage', onStorage);
    } catch {
      /* not in a browser */
    }
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      try {
        window.removeEventListener('storage', onStorage);
      } catch {
        /* not in a browser */
      }
    }
  };
}

const stamp = () => new Date().toISOString();

/** The list with room for one more: the oldest closed role goes first, then the oldest one only saved; null if none can. */
function makeRoom(items: readonly TrackedJob[]): readonly TrackedJob[] | null {
  if (items.length < MAX_TRACKED) return items;
  const oldest = (stage: TrackStage) => items.filter((t) => t.stage === stage).sort((a, b) => a.savedAt.localeCompare(b.savedAt))[0];
  const drop = oldest('closed') ?? oldest('saved');
  return drop ? items.filter((t) => t !== drop) : null;
}

function actionsFor(profileId: string): Omit<Tracker, 'items' | 'isSaved' | 'get'> {
  const update = (id: string, fn: (t: TrackedJob) => TrackedJob) => {
    const cur = read(profileId);
    if (!cur.some((t) => t.job.id === id)) return;
    write(profileId, cur.map((t) => (t.job.id === id ? { ...fn(t), updatedAt: stamp() } : t)));
  };
  const remove = (id: string) => {
    const cur = read(profileId);
    if (cur.some((t) => t.job.id === id)) write(profileId, cur.filter((t) => t.job.id !== id));
  };
  const save = (job: AiJob, runId?: string) => {
    const cur = read(profileId);
    if (cur.some((t) => t.job.id === job.id)) return;
    const room = makeRoom(cur);
    if (!room) return;
    const now = stamp();
    const run = text(runId, RUN_ID_MAX);
    write(profileId, [...room, { job, ...(run ? { runId: run } : {}), stage: 'saved', note: '', savedAt: now, updatedAt: now }]);
  };
  return {
    save,
    remove,
    toggle: (job, runId) => (read(profileId).some((t) => t.job.id === job.id) ? remove(job.id) : save(job, runId)),
    setStage: (id, stage) => {
      if (!STAGES.includes(stage)) return;
      const cur = read(profileId).find((t) => t.job.id === id);
      if (!cur || cur.stage === stage) return;
      update(id, (t) => ({ ...t, stage, ...(stage === 'applied' && !t.appliedAt ? { appliedAt: localDate() } : {}) }));
    },
    setNote: (id, note) => update(id, (t) => ({ ...t, note: String(note).slice(0, MAX_NOTE) })),
    setAppliedAt: (id, date) =>
      update(id, (t) => {
        const day = isoDay(date);
        const next = { ...t };
        if (day) next.appliedAt = day;
        else delete next.appliedAt;
        return next;
      }),
  };
}

/** Replace a profile's whole list; for the dev gallery and its checks only. */
export function seedApplications(profileId: string, items: readonly TrackedJob[]): void {
  write(profileId, parseTracked(items));
}

export function useTracker(profileId: string): Tracker {
  const items = useSyncExternalStore(
    subscribe,
    () => read(profileId),
    () => EMPTY,
  );
  const actions = useMemo(() => actionsFor(profileId), [profileId]);
  return useMemo(
    () => ({
      items,
      isSaved: (id: string) => items.some((t) => t.job.id === id),
      get: (id: string) => items.find((t) => t.job.id === id),
      ...actions,
    }),
    [items, actions],
  );
}
