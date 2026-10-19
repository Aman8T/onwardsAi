/**
 * The pricing page's address. Like the match section it is a real URL, because payment providers send the person back
 * to it (Stripe's success and cancel pages) and the paywall in the match screens links to it.
 *
 *   /pricing                       the plans
 *   /pricing?checkout=success      back from Stripe after paying (the webhook is what grants; the page waits for it)
 *   /pricing?checkout=cancelled    back from Stripe without paying
 *   /pricing?product=pack3&currency=INR   what a signed-out visitor chose before signing in: preselected, never bought
 *
 * Pure functions only (the store imports this file), so nothing here may touch the store or the DOM.
 */

export const PRICING_PATH = '/pricing';

export type CheckoutReturn = 'success' | 'cancelled';

/** True for `/pricing` (a trailing slash is fine). */
export function isPricingPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, '') === PRICING_PATH;
}

/** What the provider's return URL says happened, or null when the page was opened normally. */
export function parseCheckoutReturn(search: string): CheckoutReturn | null {
  const value = new URLSearchParams(search).get('checkout');
  return value === 'success' || value === 'cancelled' ? value : null;
}

export interface Preselect {
  product?: 'pack3' | 'pass30';
  currency?: 'INR' | 'USD';
}

/** What the visitor chose before signing in (`?product=&currency=`). Only known values count; checkout never starts from it. */
export function parsePreselect(search: string): Preselect {
  const q = new URLSearchParams(search);
  const product = q.get('product');
  const currency = q.get('currency');
  return {
    product: product === 'pack3' || product === 'pass30' ? product : undefined,
    currency: currency === 'INR' || currency === 'USD' ? currency : undefined,
  };
}

/** The address a signed-out Buy returns to after sign-in. */
export function preselectPath(product: string, currency: string): string {
  return `${PRICING_PATH}?product=${encodeURIComponent(product)}&currency=${encodeURIComponent(currency)}`;
}
