/**
 * What the person did to the "Today" list by hand: the ticks they made and the lines they wrote. Kept in this browser,
 * per profile and per local day (`tayyari.desk.todos.<profileId>`), so tomorrow starts clean. Never read by a model, never
 * sent anywhere. Storage is not trusted: what is read back is checked field by field, and a blocked or full store leaves
 * the list working for this visit.
 */
import { useCallback, useSyncExternalStore } from 'react';
import { OWN_MAX, cleanOwn, emptyMarks } from './todos';
import type { TodoMarks } from './todos';

const KEY = 'tayyari.desk.todos.';

const listeners = new Set<() => void>();
const cache = new Map<string, TodoMarks>();

function parse(raw: unknown, day: string): TodoMarks {
  if (!raw || typeof raw !== 'object') return emptyMarks(day);
  const r = raw as Record<string, unknown>;
  // another day's list is yesterday's: it is not carried over
  if (r.day !== day) return emptyMarks(day);
  const ticks: Record<string, boolean> = {};
  if (r.ticks && typeof r.ticks === 'object') {
    for (const [k, v] of Object.entries(r.ticks as Record<string, unknown>)) if (typeof v === 'boolean') ticks[k.slice(0, 80)] = v;
  }
  const own: TodoMarks['own'] = [];
  if (Array.isArray(r.own)) {
    for (const o of r.own) {
      if (!o || typeof o !== 'object') continue;
      const { id, text } = o as Record<string, unknown>;
      const t = typeof text === 'string' ? cleanOwn(text) : '';
      if (typeof id === 'string' && id && t && own.length < OWN_MAX) own.push({ id: id.slice(0, 40), text: t });
    }
  }
  return { day, ticks, own };
}

function read(profileId: string, day: string): TodoMarks {
  const hit = cache.get(profileId);
  if (hit && hit.day === day) return hit;
  let marks = emptyMarks(day);
  try {
    const raw = localStorage.getItem(KEY + profileId);
    if (raw) marks = parse(JSON.parse(raw), day);
  } catch {
    marks = emptyMarks(day);
  }
  cache.set(profileId, marks);
  return marks;
}

function write(profileId: string, marks: TodoMarks) {
  cache.set(profileId, marks);
  try {
    localStorage.setItem(KEY + profileId, JSON.stringify(marks));
  } catch {
    // blocked or full: the ticks last for this visit
  }
  listeners.forEach((fn) => fn());
}

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
};

const newId = () => `o${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

export interface TodoStore {
  marks: TodoMarks;
  /** Set an item ticked or unticked by hand. */
  tick(id: string, on: boolean): void;
  /** Add a line of their own; false when the list is full or the text is empty. */
  add(text: string): boolean;
  remove(id: string): void;
}

export function useTodoMarks(profileId: string, day: string): TodoStore {
  const marks = useSyncExternalStore(
    subscribe,
    () => read(profileId, day),
    () => emptyMarks(day),
  );
  const tick = useCallback(
    (id: string, on: boolean) => {
      const now = read(profileId, day);
      write(profileId, { ...now, ticks: { ...now.ticks, [id]: on } });
    },
    [profileId, day],
  );
  const add = useCallback(
    (text: string) => {
      const now = read(profileId, day);
      const t = cleanOwn(text);
      if (!t || now.own.length >= OWN_MAX) return false;
      write(profileId, { ...now, own: [...now.own, { id: newId(), text: t }] });
      return true;
    },
    [profileId, day],
  );
  const remove = useCallback(
    (id: string) => {
      const now = read(profileId, day);
      const { [id]: _gone, ...ticks } = now.ticks;
      write(profileId, { ...now, ticks, own: now.own.filter((o) => o.id !== id) });
    },
    [profileId, day],
  );
  return { marks, tick, add, remove };
}
