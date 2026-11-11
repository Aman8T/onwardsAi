/**
 * The person's own slips on the desk: a note they wrote to themselves, or a line they pinned from under the greeting.
 * Kept in this browser, per profile, the way the tracker keeps saved roles; never read by a model. For a signed-in
 * person the list also follows the account (/api/notes, notesApi.ts): on mount it is merged with the server's (by id,
 * the newer "at" wins, at most 12; a note this browser last saw on the server and that is gone from one side was
 * deleted there, so it stays deleted) and every change is sent after a short pause. The browser's copy stays the truth
 * for this visit: with no account, or when the API fails, nothing changes and nothing is shown. A note on a tracked role
 * is not stored here: it lives with that role (discover/applications/tracker).
 */
import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { useMe } from '../../auth/useMe';
import { getNotes, putNotes } from './notesApi';

export interface DeskNote {
  id: string;
  /** `own`: written on the desk. `pinned`: a line kept from under the greeting. */
  kind: 'own' | 'pinned';
  text: string;
  /** For a pinned line: its theme ("Quote", "Tip of the day"). */
  label?: string;
  /** For a pinned line: its small print (who said it). */
  by?: string;
  at: number;
}

export const NOTE_MAX_CHARS = 240;
const NOTES_MAX = 12;
const KEY = 'tayyari.desk.notes.';
const EMPTY: readonly DeskNote[] = [];

const listeners = new Set<() => void>();
const cache = new Map<string, readonly DeskNote[]>();

function isNote(v: unknown): v is DeskNote {
  if (!v || typeof v !== 'object') return false;
  const n = v as Record<string, unknown>;
  return typeof n.id === 'string' && (n.kind === 'own' || n.kind === 'pinned') && typeof n.text === 'string' && typeof n.at === 'number';
}

function read(profileId: string): readonly DeskNote[] {
  const hit = cache.get(profileId);
  if (hit) return hit;
  let notes: readonly DeskNote[] = EMPTY;
  try {
    const raw = localStorage.getItem(KEY + profileId);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(parsed)) notes = parsed.filter(isNote).slice(0, NOTES_MAX);
  } catch {
    notes = EMPTY;
  }
  cache.set(profileId, notes);
  return notes;
}

function writeLocal(profileId: string, notes: readonly DeskNote[]) {
  cache.set(profileId, notes);
  try {
    localStorage.setItem(KEY + profileId, JSON.stringify(notes));
  } catch {
    // a full or blocked store: the note lasts for this visit
  }
  listeners.forEach((fn) => fn());
}

function write(profileId: string, notes: readonly DeskNote[]) {
  writeLocal(profileId, notes);
  schedulePush(profileId);
}

// ── following the account ──

const PUSH_DELAY_MS = 600;
/** Profiles whose list follows the account right now (a signed-in person is looking at them). */
const synced = new Set<string>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const sending = new Set<string>();
const dirty = new Set<string>();

function schedulePush(profileId: string) {
  if (!synced.has(profileId)) return;
  const t = timers.get(profileId);
  if (t) clearTimeout(t);
  timers.set(
    profileId,
    setTimeout(() => {
      timers.delete(profileId);
      void push(profileId);
    }, PUSH_DELAY_MS),
  );
}

/** Send the current list; one request at a time per profile, the last write wins. Failures are silent. */
async function push(profileId: string) {
  if (!synced.has(profileId)) return;
  if (sending.has(profileId)) {
    dirty.add(profileId);
    return;
  }
  sending.add(profileId);
  try {
    const sent = read(profileId);
    await putNotes(profileId, sent);
    writeSeen(profileId, sent);
  } catch {
    // the browser's copy is the truth for this visit
  } finally {
    sending.delete(profileId);
    if (dirty.delete(profileId)) schedulePush(profileId);
  }
}

// the ids this browser last knew to be on the server: what tells "deleted elsewhere" from "new here"
const SEEN_KEY = 'tayyari.desk.notes.seen.';

function readSeen(profileId: string): Set<string> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(SEEN_KEY + profileId) ?? '[]');
    return new Set(Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : []);
  } catch {
    return new Set();
  }
}

function writeSeen(profileId: string, notes: readonly DeskNote[]) {
  try {
    localStorage.setItem(SEEN_KEY + profileId, JSON.stringify(notes.map((n) => n.id)));
  } catch {
    // without it a deleted note may come back once from another device; nothing else is lost
  }
}

const sig = (n: DeskNote) => JSON.stringify([n.id, n.kind, n.text, n.label ?? null, n.by ?? null, n.at]);
const sameList = (a: readonly DeskNote[], b: readonly DeskNote[]) => {
  if (a.length !== b.length) return false;
  const keys = new Set(a.map(sig));
  return b.every((n) => keys.has(sig(n)));
};

/**
 * The two lists as one: by id, the newer "at" wins (the local one on a tie), newest first, at most NOTES_MAX. A note
 * that `seen` says was on the server at the last sync and is now missing from one side was deleted on that side: it is
 * left out, so a note removed on one device does not come back from another.
 */
export function mergeNotes(local: readonly DeskNote[], remote: readonly DeskNote[], seen: ReadonlySet<string> = new Set()): DeskNote[] {
  const localIds = new Set(local.map((n) => n.id));
  const remoteIds = new Set(remote.map((n) => n.id));
  const byId = new Map<string, DeskNote>();
  for (const n of local) {
    if (seen.has(n.id) && !remoteIds.has(n.id)) continue; // deleted on another device
    byId.set(n.id, n);
  }
  for (const n of remote) {
    if (seen.has(n.id) && !localIds.has(n.id)) continue; // deleted here, not sent yet
    const mine = byId.get(n.id);
    if (!mine || n.at > mine.at) byId.set(n.id, n);
  }
  return [...byId.values()].sort((a, b) => b.at - a.at || a.id.localeCompare(b.id)).slice(0, NOTES_MAX);
}

/** On mount: merge the server's list into the browser's, then send the merge when it differs from the server's. */
async function pull(profileId: string, signal: AbortSignal) {
  let remote: DeskNote[];
  try {
    remote = (await getNotes(profileId, signal)).filter(isNote).slice(0, NOTES_MAX);
  } catch {
    return;
  }
  if (signal.aborted) return;
  const local = read(profileId);
  const merged = mergeNotes(local, remote, readSeen(profileId));
  if (!sameList(merged, local)) writeLocal(profileId, merged);
  if (!sameList(merged, remote)) schedulePush(profileId);
  else writeSeen(profileId, merged);
}

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
};

const clean = (text: string) => text.replace(/\s+/g, ' ').trim().slice(0, NOTE_MAX_CHARS);
const newId = () => `n${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

export interface DeskNotes {
  notes: readonly DeskNote[];
  /** Write a note, or rewrite the one with this id. Empty text removes it. */
  save(text: string, id?: string): void;
  remove(id: string): void;
  /** Keep a line from under the greeting, or let it go if it is already kept. */
  togglePin(line: { text: string; label: string; by?: string }): void;
  isPinned(text: string): boolean;
}

export function useDeskNotes(profileId: string): DeskNotes {
  const notes = useSyncExternalStore(
    subscribe,
    () => read(profileId),
    () => EMPTY,
  );
  const { user } = useMe();
  const userId = user?.id;
  useEffect(() => {
    if (!userId || profileId.startsWith('sample-')) return;
    synced.add(profileId);
    const ctl = new AbortController();
    void pull(profileId, ctl.signal);
    return () => {
      ctl.abort();
      synced.delete(profileId);
    };
  }, [profileId, userId]);
  const save = useCallback(
    (text: string, id?: string) => {
      const t = clean(text);
      const now = read(profileId);
      if (id) {
        write(profileId, t ? now.map((n) => (n.id === id ? { ...n, text: t, at: Date.now() } : n)) : now.filter((n) => n.id !== id));
      } else if (t) {
        write(profileId, [{ id: newId(), kind: 'own' as const, text: t, at: Date.now() }, ...now].slice(0, NOTES_MAX));
      }
    },
    [profileId],
  );
  const remove = useCallback((id: string) => write(profileId, read(profileId).filter((n) => n.id !== id)), [profileId]);
  const togglePin = useCallback(
    (line: { text: string; label: string; by?: string }) => {
      const now = read(profileId);
      const t = clean(line.text);
      const kept = now.find((n) => n.kind === 'pinned' && n.text === t);
      if (kept) write(profileId, now.filter((n) => n.id !== kept.id));
      else if (t) write(profileId, [{ id: newId(), kind: 'pinned' as const, text: t, label: line.label, ...(line.by ? { by: line.by } : {}), at: Date.now() }, ...now].slice(0, NOTES_MAX));
    },
    [profileId],
  );
  const isPinned = useCallback((text: string) => notes.some((n) => n.kind === 'pinned' && n.text === clean(text)), [notes]);
  return { notes, save, remove, togglePin, isPinned };
}
