/**
 * The pricing page's address. Like the match section it is a real URL, because payment providers send the person back
 * to it (Stripe's success and cancel pages) and the paywall in the match screens links to it.
 *
 *   /pricing                       the plans
 *   /pricing?checkout=success      back from Stripe after paying (the webhook is what grants; the page waits for it)
 *   /pricing?checkout=cancelled    back from Stripe without paying
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
