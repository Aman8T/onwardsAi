/**
 * The signed-in reader's subscription (status and unread count), loaded once and shared, so the Home tile and the
 * Weekly screen read the same answer. Anyone not signed in, or a server without the Weekly, reads as "no subscription".
 */
import { useCallback, useEffect, useSyncExternalStore } from 'react';
import type { WeeklySubscription } from '../../shared/weekly';
import { getSubscription } from './api';

export type SubscriptionStatus = 'idle' | 'loading' | 'ready' | 'error';

interface State {
  status: SubscriptionStatus;
  subscription: WeeklySubscription | null;
  /** The sentence for a failed load. */
  error?: string;
  /** The account the answer is for, so a new sign-in does not read the last one's. */
  userId?: string;
}

let state: State = { status: 'idle', subscription: null };
const listeners = new Set<() => void>();

function set(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

let inflight: Promise<void> | null = null;

async function load(userId: string): Promise<void> {
  if (inflight) return inflight;
  set({ status: 'loading', userId });
  inflight = getSubscription()
    .then((subscription) => set({ status: 'ready', subscription, error: undefined }))
    .catch((err: unknown) =>
      set({
        status: 'error',
        error: err instanceof Error ? err.message : String(err),
      }),
    )
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Put a fresh answer in (after a save, or after an edition was read and the unread count moved). */
export function setSubscription(subscription: WeeklySubscription) {
  set({ status: 'ready', subscription, error: undefined });
}

export function useSubscription(userId: string | undefined) {
  const snap = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => state,
  );
  useEffect(() => {
    if (!userId) return;
    if (state.userId !== userId) state = { status: 'idle', subscription: null };
    if (state.status === 'idle') void load(userId);
  }, [userId]);
  const reload = useCallback(() => (userId ? load(userId) : Promise.resolve()), [userId]);
  return {
    ...snap,
    subscription: userId && snap.userId === userId ? snap.subscription : null,
    reload,
  };
}
