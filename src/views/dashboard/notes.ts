/**
 * The person's own slips on the desk: a note they wrote to themselves, or a line they pinned from under the greeting.
 * Kept in this browser, per profile, the way the tracker keeps saved roles; nothing here is sent anywhere or read by a
 * model. A note on a tracked role is not stored here: it lives with that role (discover/applications/tracker).
 */
import { useCallback, useSyncExternalStore } from 'react';

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

function write(profileId: string, notes: readonly DeskNote[]) {
  cache.set(profileId, notes);
  try {
    localStorage.setItem(KEY + profileId, JSON.stringify(notes));
  } catch {
    // a full or blocked store: the note lasts for this visit
  }
  listeners.forEach((fn) => fn());
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
