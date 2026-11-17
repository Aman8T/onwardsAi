/**
 * The account's paid orders (`GET /api/billing/orders`), for the bill's receipt and the account menu's "Your plan"
 * line. One answer is kept in this module and shared; `enabled` is false for a visitor and for an account that has
 * paid nothing, so no request is made for them. `reload` asks again (after a payment settles).
 */
import { useCallback, useEffect, useSyncExternalStore } from 'react';
import type { Receipt } from '../../shared/billing';
import * as api from '../lib/api';

export type OrdersStatus = 'idle' | 'loading' | 'ready' | 'error';

interface State {
  status: OrdersStatus;
  orders: Receipt[];
}

let state: State = { status: 'idle', orders: [] };
const listeners = new Set<() => void>();

function set(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

let inflight: Promise<void> | null = null;

function load(): Promise<void> {
  if (inflight) return inflight;
  if (state.status !== 'ready') set({ status: 'loading' });
  inflight = api
    .getBillingOrders()
    .then((res) => set({ status: 'ready', orders: res.orders }))
    .catch(() => set({ status: state.status === 'ready' ? 'ready' : 'error' }))
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** The newest paid Day One order, or undefined. */
export const dayOneReceipt = (orders: readonly Receipt[]): Receipt | undefined => orders.find((o) => o.product === 'dayone');

let owner: string | undefined;

/** `userId` is who the orders belong to: a different account starts from nothing. */
export function useOrders(enabled: boolean, userId: string | undefined) {
  const snap = useSyncExternalStore(subscribe, () => state);
  useEffect(() => {
    if (owner !== userId) {
      owner = userId;
      set({ status: 'idle', orders: [] });
    }
    if (enabled && userId && state.status === 'idle') void load();
  }, [enabled, userId]);
  const reload = useCallback(() => load(), []);
  return { status: snap.status, orders: snap.orders, receipt: dayOneReceipt(snap.orders), reload };
}
