/**
 * Hand-off from the marketing site. It sends people to `/?handoff=<id>` (their resume and links, already uploaded
 * there) or `/?handoff_error=<code>`. This hook reads and clears both from the address, once, then either starts
 * the analysis exactly as Home's submit does or leaves a short message for Home to show (`useHandoffMessage`).
 *
 * App calls `useHandoff()`; Home reads the message. The message lives in a module-level variable with a subscribe
 * function so Home needs no prop and the store stays out of it.
 */
import { useEffect, useSyncExternalStore } from 'react';
import * as api from './api';
import { actions } from '../store';

const EXPIRED = 'That hand-off expired. Add your resume here instead.';
const GENERIC = 'We could not bring your resume over. Add it here instead.';

let message: string | null = null;
let started = false; // guards StrictMode's second effect run
const listeners = new Set<() => void>();

function setMessage(next: string | null): void {
  message = next;
  listeners.forEach((l) => l());
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};

function toFile(payload: { name: string; type: string; dataBase64: string }): File {
  const bin = atob(payload.dataBase64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new File([bytes], payload.name, { type: payload.type });
}

async function run(id: string | null, errorCode: string | null): Promise<void> {
  if (errorCode) {
    setMessage(api.HANDOFF_ERROR_TEXT[errorCode] ?? GENERIC);
    return;
  }
  if (!id) return;
  try {
    const p = await api.takeHandoff(id);
    const file = p.resume ? toFile(p.resume) : null;
    void actions.startAnalysis({ file, links: p.links ?? [], goal: p.goal, notes: p.notes });
  } catch {
    setMessage(EXPIRED);
  }
}

/** Call once from App. Returns the message to show, or null. */
export function useHandoff(): string | null {
  useEffect(() => {
    if (started) return;
    started = true;
    let id: string | null = null;
    let errorCode: string | null = null;
    try {
      const url = new URL(window.location.href);
      id = url.searchParams.get('handoff');
      errorCode = url.searchParams.get('handoff_error');
      if (id !== null || errorCode !== null) {
        url.searchParams.delete('handoff');
        url.searchParams.delete('handoff_error');
        window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
      }
    } catch {
      return;
    }
    void run(id, errorCode);
  }, []);
  return useSyncExternalStore(subscribe, () => message, () => null);
}

/** For Home: the same message, without starting anything. */
export function useHandoffMessage(): string | null {
  return useSyncExternalStore(subscribe, () => message, () => null);
}
