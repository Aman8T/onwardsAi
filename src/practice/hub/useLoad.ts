/**
 * One server read with its three states, for the screens that open on data (the hub's history, a debrief, progress).
 * The read is cancelled when the screen goes away or its inputs change, `reload` runs it again, and `patch` changes what
 * was read in place (a fact taken back, a session deleted) without asking the server again.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { sentenceOf, statusOf } from './words';

export type Load<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error'; message: string; code?: number };

export interface Loaded<T> {
  load: Load<T>;
  reload: () => void;
  patch: (fn: (data: T) => T) => void;
}

export function useLoad<T>(read: (signal: AbortSignal) => Promise<T>, deps: readonly unknown[]): Loaded<T> {
  const [load, setLoad] = useState<Load<T>>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const reader = useRef(read);
  reader.current = read;

  useEffect(() => {
    const ctl = new AbortController();
    setLoad((prev) => (prev.status === 'loading' ? prev : { status: 'loading' }));
    reader.current(ctl.signal).then(
      (data) => {
        if (!ctl.signal.aborted) setLoad({ status: 'ready', data });
      },
      (err: unknown) => {
        if (ctl.signal.aborted) return;
        const code = statusOf(err);
        setLoad({ status: 'error', message: sentenceOf(err), ...(code !== undefined ? { code } : {}) });
      },
    );
    return () => ctl.abort();
    // keyed on the caller's inputs and on a manual reload
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, ...deps]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  const patch = useCallback((fn: (data: T) => T) => setLoad((prev) => (prev.status === 'ready' ? { status: 'ready', data: fn(prev.data) } : prev)), []);
  return { load, reload, patch };
}
