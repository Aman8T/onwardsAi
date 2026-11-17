/**
 * What the plan screen and the bill know: the plan, the Just Looking counts and when they come back, the offer (every
 * price, the early window, the refund days) and what the account asked to be told about.
 *
 * `useEntitlements()` loads `GET /api/billing/entitlements` (open to anonymous visitors) and loads it again when the
 * signed-in account changes, because the counts belong to the account. The state lives in this module, like `useMe`,
 * so the screens and the chip read the same answer. Every number comes from here (a server setting or a count); no
 * screen types one. Amounts are INR paise: Day One is sold in rupees only for now.
 *
 * `offer.dayOne` is present only while Day One can be bought here; without it the screens still print the price the
 * server would charge (`dayOnePrice`) and say "Coming soon".
 */
import { useEffect, useSyncExternalStore } from 'react';
import type { EntitlementsResponse, PlanId, PlanOffer, PlanUsage } from '../../shared/billing';
import { useMe } from '../auth/useMe';
import * as api from '../lib/api';
import { isUnlimited } from './format';

export type EntitlementsStatus = 'idle' | 'loading' | 'ready' | 'error';

interface State {
  status: EntitlementsStatus;
  data: EntitlementsResponse | null;
}

let state: State = { status: 'idle', data: null };
const listeners = new Set<() => void>();

function set(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** The IANA zone name the browser reports, only used by the server to guess a currency. */
function timeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

let inflight: Promise<void> | null = null;

/** Ask the server again. Calls made while one is in flight share it. */
export function refreshEntitlements(): Promise<void> {
  if (inflight) return inflight;
  if (state.status === 'idle') set({ status: 'loading' });
  inflight = api
    .getBillingEntitlements(timeZone())
    .then((data) => set({ status: 'ready', data }))
    .catch(() => set({ status: state.data ? 'ready' : 'error' }))
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** What Day One costs on the screens: the price on sale now, else the one the offer would charge today. */
export function dayOnePrice(offer: PlanOffer): number {
  return offer.dayOne ?? (offer.early ? offer.dayOneEarly : offer.dayOneAfter);
}

export interface EntitlementsView {
  status: EntitlementsStatus;
  /** True once the server has answered. */
  loaded: boolean;
  /** The plan the account is on; `just_looking` for a visitor and before the server has answered. */
  plan: PlanId;
  /** The Just Looking limits and what the account has used; undefined until the server has answered. */
  usage: PlanUsage | undefined;
  /** ISO timestamp: when the monthly counts start again. */
  resetsAt: string | undefined;
  /** Every price and date on the screens; undefined until the server has answered. */
  offer: PlanOffer | undefined;
  /** The account is on the server's unlimited list: no limits, nothing charged. */
  unlimited: boolean;
  /** Checkout.js is only worth loading when Day One can be bought (Razorpay is configured). */
  razorpayOffered: boolean;
  refresh: () => Promise<void>;
}

export function useEntitlements(): EntitlementsView {
  const snap = useSyncExternalStore(subscribe, () => state);
  const { user } = useMe();
  const userId = user?.id;

  // the counts belong to the account: load on first use, and again whenever someone signs in or out
  useEffect(() => {
    void refreshEntitlements();
  }, [userId]);

  const { data, status } = snap;
  return {
    status,
    loaded: status === 'ready',
    plan: data?.plan ?? 'just_looking',
    usage: data?.usage,
    resetsAt: data?.resetsAt,
    offer: data?.offer,
    unlimited: isUnlimited(data),
    razorpayOffered: data?.offer.dayOne !== undefined,
    refresh: refreshEntitlements,
  };
}
