/**
 * What the pricing page knows: the visitor's balance, the prices that are on sale and the currency to show.
 *
 * `useEntitlements()` loads `GET /api/billing/entitlements` (open to anonymous visitors) and loads it again when the
 * signed-in account changes, because the balance belongs to the account. The state lives in this module, like
 * `useMe`, so the page and the chip read the same answer. Prices are minor units; a product that is absent from
 * `prices[currency]` is not on sale and the page says "Coming soon".
 *
 * The currency starts as the server's guess (the saved choice, else INR for an Indian locale or time zone, else USD).
 * `setCurrency` changes what the page shows at once and, for a signed-in account, saves the choice
 * (`PATCH /api/me`), so the next visit starts there.
 */
import { useCallback, useEffect, useSyncExternalStore } from 'react';
import type { Currency, EntitlementsResponse, PriceBook, ProductPrices } from '../../shared/billing';
import { useMe } from '../auth/useMe';
import * as api from '../lib/api';
import { isUnlimited } from './format';

export type EntitlementsStatus = 'idle' | 'loading' | 'ready' | 'error';

interface State {
  status: EntitlementsStatus;
  data: EntitlementsResponse | null;
  /** The currency the person picked on this page, if they did; otherwise the server's guess is used. */
  chosen: Currency | null;
}

let state: State = { status: 'idle', data: null, chosen: null };
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

const NO_PRICES: ProductPrices = {};
const BARE: PriceBook = { INR: NO_PRICES, USD: NO_PRICES };

export interface EntitlementsView {
  status: EntitlementsStatus;
  /** True once the server has answered. */
  loaded: boolean;
  credits: number;
  passUntil: string | undefined;
  freeMatchAvailable: boolean;
  /** The account has no limits and is never charged (`GET /api/billing/entitlements` said so); false otherwise. */
  unlimited: boolean;
  /** The currency the page shows: the person's choice, else the server's guess, else USD. */
  currency: Currency;
  prices: PriceBook;
  /** What is on sale in the currency being shown. */
  priced: ProductPrices;
  /** True when at least one plan can be bought in INR (Checkout.js is only worth loading then). */
  razorpayOffered: boolean;
  setCurrency: (currency: Currency) => void;
  refresh: () => Promise<void>;
}

export function useEntitlements(): EntitlementsView {
  const snap = useSyncExternalStore(subscribe, () => state);
  const { user, refresh: refreshMe } = useMe();
  const userId = user?.id;

  // the balance belongs to the account: load on first use, and again whenever someone signs in or out
  useEffect(() => {
    void refreshEntitlements();
  }, [userId]);

  const setCurrency = useCallback(
    (currency: Currency) => {
      set({ chosen: currency });
      if (userId) {
        api
          .patchMe({ currency })
          .then(() => refreshMe())
          .catch(() => {
            /* the choice still holds on this page; it is saved the next time */
          });
      }
    },
    [userId, refreshMe],
  );

  const { data, status, chosen } = snap;
  const prices = data?.prices ?? BARE;
  const currency = chosen ?? data?.currency ?? 'USD';
  return {
    status,
    loaded: status === 'ready',
    credits: data?.credits ?? 0,
    passUntil: data?.passUntil,
    freeMatchAvailable: data?.freeMatchAvailable ?? false,
    unlimited: isUnlimited(data),
    currency,
    prices,
    priced: prices[currency],
    razorpayOffered: prices.INR.pack3 !== undefined || prices.INR.pass30 !== undefined,
    setCurrency,
    refresh: refreshEntitlements,
  };
}
