/**
 * What a person types on a prep page is theirs and stays in this browser:
 *
 *   tayyari.discover.story.<profileId>.<jobId>   →  { "<slot>": "text they wrote" }   (the dashed boxes)
 *   tayyari.discover.prep.<profileId>.<jobId>    →  { "<item>": true }                  (the "Before you apply" ticks)
 *
 * Nothing here is sent to the server. Every storage access is wrapped, so with storage blocked or full the page still
 * works and the words live until the page is left. What is read back is checked, never trusted.
 */
import { useCallback, useRef, useState } from 'react';
import { isObj } from './read';

export const storyKey = (profileId: string, jobId: string) => `tayyari.discover.story.${profileId}.${jobId}`;
export const checklistKey = (profileId: string, jobId: string) => `tayyari.discover.prep.${profileId}.${jobId}`;

/** The longest thing a person can type into one dashed box. */
export const MAX_STORY_CHARS = 2000;
const MAX_SLOTS = 80;

export function readJson(key: string): unknown {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** Store a value, or forget the key when `value` is null. Failure (blocked, full) is silent. */
export function writeJson(key: string, value: unknown): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked or full: kept for this page only */
  }
}

/** The dashed boxes: slot → text. */
export function parseStory(raw: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!isObj(raw)) return out;
  for (const [slot, value] of Object.entries(raw).slice(0, MAX_SLOTS)) {
    if (typeof value === 'string' && value !== '') out[slot] = value.slice(0, MAX_STORY_CHARS);
  }
  return out;
}

export const CHECKLIST_ITEMS = ['fit', 'resume', 'cover', 'apply'] as const;
export type ChecklistItem = (typeof CHECKLIST_ITEMS)[number];

/** The ticks: item → true. Only the items on the list are read. */
export function parseChecklist(raw: unknown): Record<string, true> {
  const out: Record<string, true> = {};
  if (!isObj(raw)) return out;
  for (const item of CHECKLIST_ITEMS) if (raw[item] === true) out[item] = true;
  return out;
}

type Setter<V> = (slot: string, value: V | undefined) => void;

/**
 * A record kept in localStorage under `key`: read once when the component mounts, written on every change. Setting a
 * slot to `undefined` (or an empty string) forgets it; an empty record forgets the key. A component that can be shown
 * for another key is given a `key` of its own, so it mounts again and reads the new record.
 */
export function useStoredRecord<V extends string | true>(key: string, parse: (raw: unknown) => Record<string, V>): [Record<string, V>, Setter<V>] {
  const [record, setRecord] = useState(() => parse(readJson(key)));
  const latest = useRef(record);
  const set = useCallback<Setter<V>>(
    (slot, value) => {
      const next = { ...latest.current };
      if (value === undefined || value === '') delete next[slot];
      else next[slot] = value;
      latest.current = next;
      setRecord(next);
      writeJson(key, Object.keys(next).length > 0 ? next : null);
    },
    [key],
  );
  return [record, set];
}
