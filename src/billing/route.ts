/**
 * The pricing screens' addresses. They are real URLs, because the report's paid link and the sign-in return point at
 * the bill, and because Back should work.
 *
 *   /pricing                  the plan screen: four plans and a stage
 *   /pricing?bill=day-one     the bill for Day One: the slip, the wait, the receipt
 *
 * `navigate` moves between the two without a reload and tells whoever is listening (`useAddress`), because the store's
 * view stays `pricing` for both. Pure apart from that: nothing here touches the store.
 */
import { useSyncExternalStore } from 'react';

export const PRICING_PATH = '/pricing';
export const BILL_PATH = '/pricing?bill=day-one';

/** True for `/pricing` (a trailing slash is fine). */
export function isPricingPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, '') === PRICING_PATH;
}

/** True when the address asks for the Day One bill (`?bill=day-one`). */
export function parseBill(search: string): boolean {
  return new URLSearchParams(search).get('bill') === 'day-one';
}

const listeners = new Set<() => void>();

/** Tell every `useAddress` that the address changed (the store calls this after it pushes one). */
export function notifyAddress() {
  listeners.forEach((l) => l());
}

/** Push `path` (when it is not the address already) and tell the listeners. */
export function navigate(path: string) {
  try {
    if (window.location.pathname + window.location.search !== path) window.history.pushState(null, '', path);
  } catch {
    /* no address bar to update */
  }
  notifyAddress();
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  window.addEventListener('popstate', cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener('popstate', cb);
  };
}

/** Which of the two screens the address names. */
export function useAddress(): 'plans' | 'bill' {
  const bill = useSyncExternalStore(
    subscribe,
    () => parseBill(window.location.search),
    () => false,
  );
  return bill ? 'bill' : 'plans';
}
