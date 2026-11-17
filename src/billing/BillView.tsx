/**
 * The Day One bill (`/pricing?bill=day-one`): the dedicated page after choosing Day One.
 *
 * Signed out, the Pay press opens the sign-in sheet and comes back here. Signed in, it starts the checkout
 * (`useCheckout`, product `dayone`, rupees): Razorpay's own window opens over the page, and this page says where the
 * payment is (the wait, paid, closed, did not go through, pending). When it is paid the slip becomes the receipt, with
 * the number, the day and the payment id from `verify`; a reload of a paid account's bill reads the same receipt from
 * `GET /api/billing/orders`. Checkout.js is on the page only while this one is mounted.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { signInSheet, useMe } from '../auth/useMe';
import { actions } from '../store';
import { Bill } from './Bill';
import type { BillPhase } from './Bill';
import { useCheckout, useCheckoutScript } from './Checkout';
import { Frame } from './Frame';
import { PRICING_PATH, BILL_PATH, navigate } from './route';
import { dayOnePrice, useEntitlements } from './useEntitlements';
import { useOrders } from './useOrders';

const PENDING = 'Thank you. The payment is confirmed in the background, and your plan appears on this page as soon as it arrives.';

export function BillView() {
  const me = useMe();
  const ent = useEntitlements();
  const heading = useRef<HTMLHeadingElement>(null);
  const [now] = useState(() => Date.now());

  // Checkout.js belongs to this page: in while it is mounted, out when it is not, and only when Day One can be bought
  const script = useCheckoutScript(ent.razorpayOffered);

  const settle = useCallback(async () => {
    await Promise.all([ent.refresh(), me.refresh()]);
  }, [ent.refresh, me.refresh]);
  const needSignIn = useCallback(() => signInSheet.open('buy', undefined, BILL_PATH), []);
  const checkout = useCheckout({ onSettled: settle, onSignInNeeded: needSignIn });
  const { state, start, dismiss } = checkout;

  const onDayOne = ent.plan === 'day_one';
  const orders = useOrders(onDayOne, me.user?.id);

  // the page title and the heading take focus, like a new screen
  useEffect(() => {
    const before = document.title;
    document.title = 'Your bill · Leafout';
    heading.current?.focus({ preventScroll: true });
    return () => {
      document.title = before;
    };
  }, []);

  const offer = ent.offer;
  const usage = ent.usage;
  if (!offer || !usage) {
    return (
      <Frame backLabel="Back to the plans" onBack={() => navigate(PRICING_PATH)} busy={ent.status !== 'error'}>
        <section className="m-hero bl-hero bl-bare" aria-label="Your bill">
          <p className="kicker">Day One · your bill</p>
          <h1 className="serif bl-title" ref={heading} tabIndex={-1}>
            {ent.status === 'error' ? 'The bill could not be loaded.' : 'Getting your bill ready.'}
          </h1>
          <div className="bl-status" role="status" aria-live="polite">
            {ent.status === 'error' && (
              <>
                <p>The prices could not be loaded.</p>
                <div className="bl-status-actions">
                  <button type="button" className="btn btn-sm" onClick={() => void ent.refresh()}>
                    Try again
                  </button>
                </div>
              </>
            )}
          </div>
        </section>
      </Frame>
    );
  }

  const paidNow = state.phase === 'paid' || onDayOne;
  const phase: BillPhase = paidNow ? 'paid' : state.phase === 'idle' ? 'bill' : state.phase === 'redirecting' ? 'starting' : state.phase;
  const receipt = state.receipt ?? orders.receipt;
  const receiptState = receipt ? 'ready' : orders.status === 'idle' || orders.status === 'loading' ? 'loading' : 'missing';

  const message =
    phase === 'paid'
      ? state.phase === 'paid'
        ? 'Payment received. You are in from day one.'
        : undefined
      : phase === 'failed'
        ? (state.message ?? 'The payment could not be started. Please try again.')
        : phase === 'paying'
          ? state.message
            ? `${state.message} Try again in the checkout window, or close it.`
            : 'Finish the payment in the checkout window.'
          : phase === 'pending'
            ? PENDING
            : phase === 'cancelled'
              ? 'Checkout closed. Nothing was charged.'
              : phase === 'starting'
                ? 'Opening checkout…'
                : phase === 'verifying'
                  ? 'Confirming your payment…'
                  : undefined;

  const pay = () => {
    if (!me.user) {
      signInSheet.open('buy', undefined, BILL_PATH);
      return;
    }
    void start('dayone', 'INR');
  };

  const who = me.user ? { name: me.user.name?.trim() || me.user.email?.split('@')[0] || 'Your account', email: me.user.email } : undefined;
  // a signed-in account waits for Checkout.js (a failed load is let through: the click then says so, before any order exists)
  const payDisabled = !ent.loaded || (!!me.user && script === 'loading');

  return (
    <Frame backLabel="Back to the plans" onBack={() => navigate(PRICING_PATH)}>
      <Bill
        phase={phase}
        offer={offer}
        usage={usage}
        price={dayOnePrice(offer)}
        who={who}
        message={message}
        receipt={receipt}
        receiptState={receiptState}
        now={now}
        payDisabled={payDisabled}
        onPay={pay}
        onDismiss={dismiss}
        onTryAgain={() => void start('dayone', 'INR')}
        onPlans={() => navigate(PRICING_PATH)}
        onDesk={() => actions.closePricing()}
        onPrint={() => window.print()}
        headingRef={heading}
      />
    </Frame>
  );
}
