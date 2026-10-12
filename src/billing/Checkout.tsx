/**
 * Taking a payment. Two providers, one flow for the page:
 *
 *   INR  -> Razorpay: the server creates an order, Checkout.js opens its modal over the page, and when the customer
 *           has paid it hands the page three values that go to `POST /api/billing/verify`. The server checks the
 *           signature and settles the order; the signed webhook does the same and is the source of truth, so a failed
 *           hand-off only means "wait a moment" (phase `pending`).
 *   USD  -> Stripe: the server creates a Checkout Session and the browser goes to its hosted page; Stripe sends the
 *           person back to `/pricing?checkout=success|cancelled` (see `route.ts`) and the webhook grants.
 *
 * Checkout.js is third-party script. `useCheckoutScript` inserts its tag when the pricing page mounts and removes it
 * when the page unmounts, and only when INR is on sale at all; no other screen ever loads it (the CSP allows its
 * host on every page, which is why the tag's life is kept this short).
 *
 * Nothing here knows a price: the amount comes back from the server with the order, never from this code.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { MeEntitlements } from '../../shared/auth';
import type { Currency, Product } from '../../shared/billing';
import * as api from '../lib/api';
import { purchaseSummary } from './format';

const CHECKOUT_JS = 'https://checkout.razorpay.com/v1/checkout.js';
const POLL_EVERY_MS = 3000;
const POLL_TIMES = 8;
const NO_WINDOW = 'The payment window did not load. Check your connection, or a blocker that stops it, and try again.';

/** The parts of Checkout.js's API this page uses. */
interface RazorpayResponse {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}
interface RazorpayOptions {
  key: string;
  amount: number;
  currency: string;
  order_id: string;
  name?: string;
  description?: string;
  prefill?: { email?: string };
  handler: (response: RazorpayResponse) => void;
  modal?: { ondismiss?: () => void };
}
interface RazorpayCheckout {
  open: () => void;
}
declare global {
  interface Window {
    Razorpay?: new (options: RazorpayOptions) => RazorpayCheckout;
  }
}

export type ScriptState = 'off' | 'loading' | 'ready' | 'failed';

/** Insert Checkout.js while the calling page is mounted (and only when `enabled`); take it out again afterwards. */
export function useCheckoutScript(enabled: boolean): ScriptState {
  const [state, setState] = useState<ScriptState>('off');
  useEffect(() => {
    if (!enabled) {
      setState('off');
      return;
    }
    let live = true;
    setState(window.Razorpay ? 'ready' : 'loading');
    const tag = document.createElement('script');
    tag.src = CHECKOUT_JS;
    tag.async = true;
    tag.dataset.tayyari = 'checkout';
    tag.onload = () => live && setState('ready');
    tag.onerror = () => live && setState('failed');
    document.body.appendChild(tag);
    return () => {
      live = false;
      tag.remove();
      window.Razorpay = undefined;
    };
  }, [enabled]);
  return state;
}

export type CheckoutPhase = 'idle' | 'starting' | 'paying' | 'verifying' | 'redirecting' | 'pending' | 'paid' | 'cancelled' | 'failed';

export interface CheckoutState {
  phase: CheckoutPhase;
  product?: Product;
  message?: string;
}

/** True while a purchase is in progress and the buttons should wait. */
export const isBusy = (phase: CheckoutPhase) => phase === 'starting' || phase === 'paying' || phase === 'verifying' || phase === 'redirecting';

function timeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

interface UseCheckout {
  /** Called after a payment settles (or may have): reload the balance. */
  onSettled: () => void | Promise<void>;
  /** Called when the server says the visitor has to sign in. */
  onSignInNeeded: () => void;
}

export function useCheckout({ onSettled, onSignInNeeded }: UseCheckout) {
  const [state, setState] = useState<CheckoutState>({ phase: 'idle' });
  const settled = useRef(onSettled);
  settled.current = onSettled;
  const phase = useRef<CheckoutPhase>('idle');
  phase.current = state.phase;

  const start = useCallback(
    async (product: Product, currency: Currency) => {
      if (isBusy(phase.current)) return;
      // no order is created at Razorpay for a window that cannot open
      if (currency === 'INR' && !window.Razorpay) {
        setState({ phase: 'failed', product, message: NO_WINDOW });
        return;
      }
      setState({ phase: 'starting', product });
      try {
        const order = await api.createCheckout({ product, currency, timezone: timeZone() });
        if (order.provider === 'stripe' && order.url) {
          setState({ phase: 'redirecting', product });
          window.location.assign(order.url);
          return;
        }
        const Razorpay = window.Razorpay;
        if (!Razorpay || !order.keyId || !order.orderId || order.amount === undefined || !order.currency) {
          setState({ phase: 'failed', product, message: NO_WINDOW });
          return;
        }
        const checkout = new Razorpay({
          key: order.keyId,
          amount: order.amount,
          currency: order.currency,
          order_id: order.orderId,
          name: order.name,
          description: order.description,
          prefill: order.prefillEmail ? { email: order.prefillEmail } : undefined,
          handler: (response) => {
            setState({ phase: 'verifying', product });
            api
              .verifyPayment({
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
              })
              .then(() => setState({ phase: 'paid', product }))
              // paid at Razorpay, not yet confirmed here: the webhook will, so wait for it instead of alarming the person
              .catch(() => setState({ phase: 'pending', product }))
              .finally(() => void settled.current());
          },
          modal: {
            ondismiss: () => setState((s) => (s.phase === 'paying' ? { phase: 'cancelled', product } : s)),
          },
        });
        setState({ phase: 'paying', product });
        checkout.open();
      } catch (err) {
        if (err instanceof api.ApiError && err.status === 401) {
          setState({ phase: 'idle' });
          onSignInNeeded();
          return;
        }
        const message =
          err instanceof api.ApiError && err.status === 409
            ? 'That plan is not on sale yet.'
            : err instanceof Error
              ? err.message
              : 'Something went wrong. Please try again.';
        setState({ phase: 'failed', product, message });
      }
    },
    [onSignInNeeded],
  );

  /** Show what happened off-page (Stripe's return URL). */
  const restore = useCallback((phase: 'pending' | 'cancelled') => setState({ phase }), []);
  const dismiss = useCallback(() => setState({ phase: 'idle' }), []);

  // After Stripe (or a hand-off that could not be confirmed) the webhook is what grants: look again a few times.
  useEffect(() => {
    if (state.phase !== 'pending') return;
    let tries = 0;
    const id = window.setInterval(() => {
      tries += 1;
      void settled.current();
      if (tries >= POLL_TIMES) window.clearInterval(id);
    }, POLL_EVERY_MS);
    return () => window.clearInterval(id);
  }, [state.phase]);

  return { state, start, restore, dismiss };
}

interface NoticeProps {
  state: CheckoutState;
  entitlements: MeEntitlements;
  onDismiss: () => void;
  /** What to do after a paid order: go and use it. */
  onContinue: () => void;
}

/** One calm line about where the payment is. A live region, so a screen reader hears each change. */
export function CheckoutNotice({ state, entitlements, onDismiss, onContinue }: NoticeProps) {
  const { phase, product, message } = state;
  if (phase === 'idle') return <p className="bl-notice" role="status" aria-live="polite" />;

  if (phase === 'failed') {
    return (
      <div className="bl-notice is-problem" role="alert">
        <p>{message ?? 'The payment could not be started. Please try again.'}</p>
        <button type="button" className="btn btn-quiet btn-sm" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    );
  }

  const text: Record<Exclude<CheckoutPhase, 'idle' | 'failed'>, string> = {
    starting: 'Opening checkout…',
    paying: 'Finish the payment in the checkout window.',
    verifying: 'Confirming your payment…',
    redirecting: 'Taking you to Stripe…',
    pending: 'Thank you. The payment is confirmed in the background, and your plan appears on this page as soon as it arrives.',
    paid: `Payment received. ${product ? purchaseSummary(product, entitlements) : ''}`.trim(),
    cancelled: 'Checkout closed. Nothing was charged.',
  };
  const done = phase === 'paid' || phase === 'pending' || phase === 'cancelled';
  return (
    <div className="bl-notice" role="status" aria-live="polite">
      <p>{text[phase]}</p>
      {done && (
        <div className="bl-notice-actions">
          {phase === 'paid' && (
            <button type="button" className="btn btn-primary btn-sm" onClick={onContinue}>
              Match a posting
            </button>
          )}
          <button type="button" className="btn btn-quiet btn-sm" onClick={onDismiss}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
