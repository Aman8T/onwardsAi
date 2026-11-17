/**
 * The Day One bill, the page after the plan screen: one slip that lists every line Day One changes, and the same slip
 * is the wait and the receipt. Board `Pay` of the plans canvas; the moments around it are in `PlanMoments`.
 *
 * This file only draws. `BillView` feeds it the server's offer and counts, the payment phase and the receipt; the dev
 * gallery feeds it fixtures. The slip is straight and in mono (it is a measured document); prices, dates and counts
 * are never turned and never in the pen font. No GST line: Leafout has no GSTIN yet, so no page says "includes GST".
 */
import type { CSSProperties, MouseEvent, Ref } from 'react';
import type { PlanOffer, PlanUsage, Receipt } from '../../shared/billing';
import { initials } from '../lib/ui';
import { formatPrice, fullDate } from './format';
import { DAY_ONE_LABEL, FEATURES, earlyWindow, limitText } from './model';
import { PRICING_PATH } from './route';

/** Where the payment is, as the bill shows it. */
export type BillPhase = 'bill' | 'starting' | 'paying' | 'verifying' | 'pending' | 'cancelled' | 'failed' | 'paid';

export interface BillProps {
  phase: BillPhase;
  offer: PlanOffer;
  usage: PlanUsage;
  /** What Day One costs on this bill, in paise. */
  price: number;
  /** Who is paying; absent for a visitor. */
  who?: { name: string; email?: string };
  /** The line for the live region: Razorpay's reason, a dismissed window, a failure. */
  message?: string;
  /** The paid order: the slip's receipt lines. */
  receipt?: Receipt;
  /** A paid account whose receipt is still being fetched, or could not be found. */
  receiptState?: 'ready' | 'loading' | 'missing';
  /** The Pay press waits (Checkout.js is still loading). */
  payDisabled?: boolean;
  now: number;
  onPay: () => void;
  onDismiss: () => void;
  onTryAgain: () => void;
  onPlans: () => void;
  onDesk: () => void;
  onPrint: () => void;
  headingRef?: Ref<HTMLHeadingElement>;
}

const HERO: Record<BillPhase, { h1: string; lede: string }> = {
  bill: { h1: 'Read it once. Pay it once.', lede: 'Every line you are paying for is on the slip. Nothing is charged until you press Pay.' },
  starting: { h1: 'One moment. Your bill is waiting behind the payment.', lede: 'Opening the checkout window. If you close it, nothing is charged and this bill stays as it is.' },
  paying: { h1: 'One moment. Your bill is waiting behind the payment.', lede: 'Finish the payment in the checkout window. If you close it, nothing is charged and this bill stays as it is.' },
  verifying: { h1: 'One moment. Your bill is waiting behind the payment.', lede: 'Confirming your payment. Please keep this page open.' },
  pending: { h1: 'Thank you. We are confirming it.', lede: 'The payment is confirmed in the background, and your plan appears on this page as soon as it arrives.' },
  cancelled: { h1: 'Read it once. Pay it once.', lede: 'Every line you are paying for is on the slip. Nothing is charged until you press Pay.' },
  failed: { h1: 'Read it once. Pay it once.', lede: 'Every line you are paying for is on the slip. Nothing is charged until you press Pay.' },
  paid: { h1: 'You are in from day one.', lede: 'Payment received. Every count is off, and what we build from here is yours too. Tell us what to build first.' },
};

const SLIP_HEAD: Record<BillPhase, string> = {
  bill: 'Not paid. This is what you would pay.',
  starting: 'Not paid yet. Waiting for the checkout window.',
  paying: 'Not paid yet. Waiting for the checkout window.',
  verifying: 'Payment sent. Confirming it.',
  pending: 'Payment sent. Waiting for it to be confirmed.',
  cancelled: 'Not paid. The bill is as you left it.',
  failed: 'Not paid. The bill is as you left it.',
  paid: 'Paid. Keep this.',
};

const WAIT: Partial<Record<BillPhase, string>> = {
  starting: 'Opening checkout…',
  paying: "Razorpay's own window is open over this page: UPI, cards, net banking. It is theirs, so it is not drawn here. We never see your card details.",
  verifying: 'Confirming your payment…',
  pending: 'Confirming your payment in the background…',
};

const LEGAL_LINKS: readonly { href: string; label: string }[] = [
  { href: 'https://leafout.in/terms', label: 'Terms' },
  { href: 'https://leafout.in/privacy', label: 'Privacy' },
  { href: 'https://leafout.in/refunds', label: 'Refunds' },
  { href: 'https://leafout.in/contact', label: 'Contact' },
];

export function Bill(p: BillProps) {
  const { phase, offer, price } = p;
  const paid = phase === 'paid';
  const win = earlyWindow(offer, p.now);
  const early = offer.early && !paid;
  const onSale = offer.dayOne !== undefined;
  const hero = HERO[phase];
  const wait = WAIT[phase];
  const problem = phase === 'failed';
  const today = fullDate(new Date(p.now).toISOString());
  const plans = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    p.onPlans();
  };

  return (
    <div className="m-screen bl-screen">
      <section className="m-hero bl-hero" aria-label="Your bill">
        <p className="kicker">{paid ? 'Day One · your receipt' : 'Day One · your bill'}</p>
        <h1 className="serif bl-title" ref={p.headingRef} tabIndex={-1}>
          {hero.h1}
        </h1>
        <p className="bl-lede">{hero.lede}</p>

        {/* the payment's status: one live region, always in the page, so each change is announced */}
        <div className={`bl-status${problem ? ' is-problem' : ''}`} role="status" aria-live="polite">
          {p.message ? <p>{p.message}</p> : null}
          {phase === 'cancelled' && (
            <div className="bl-status-actions">
              <button type="button" className="btn btn-quiet btn-sm" onClick={p.onDismiss}>
                Dismiss
              </button>
            </div>
          )}
        </div>
        {problem && (
          <div className="bl-status-actions">
            <button type="button" className="btn" onClick={p.onTryAgain}>
              Try again
            </button>
            <a className="btn btn-quiet" href={PRICING_PATH} onClick={plans}>
              Back to the plans
            </a>
          </div>
        )}

        <div className="bl-who">
          {p.who ? (
            <>
              <span className="bl-avatar numeral" aria-hidden="true">
                {initials(p.who.name)}
              </span>
              <span className="bl-who-main">
                <span className="m-strong">{p.who.name}</span>
                <span className="m-fine">{p.who.email ? `${p.who.email} · the receipt goes here` : 'the receipt goes to your account'}</span>
              </span>
            </>
          ) : (
            <span className="bl-who-main">
              <span className="m-strong">Not signed in</span>
              <span className="m-fine">You will sign in first. Nothing is charged until you pay.</span>
            </span>
          )}
        </div>

        {wait ? (
          <div className="m-tile bl-theirs">
            <p>{wait}</p>
          </div>
        ) : paid ? (
          <button type="button" className="m-tile m-ink bl-press" onClick={p.onDesk}>
            <b>Open your desk →</b>
            <span className="m-fine">Nothing is counted any more.</span>
          </button>
        ) : onSale ? (
          <button type="button" className="m-tile m-ink bl-press" disabled={p.payDisabled} aria-busy={p.payDisabled || undefined} onClick={p.onPay}>
            <b className="numeral">Pay {formatPrice(price, 'INR')} once →</b>
            <span className="m-fine">UPI, cards and net banking. Razorpay opens over this page; we never see your card details.</span>
          </button>
        ) : (
          <div className="m-tile m-quiet bl-press is-off">
            <b>Coming soon</b>
            <span className="m-fine">Day One is not on sale here yet. Nothing can be charged.</span>
          </div>
        )}

        <div className="bl-plain-say is-bare">
          <span>One payment. Nothing renews, and there is no monthly charge.</span>
          {!paid && <span>Full refund within {offer.refundDays} days of paying, for any reason.</span>}
        </div>
        <nav className="bl-terms" aria-label="Terms and policies">
          <span>By paying you agree to the Terms.</span>
          {LEGAL_LINKS.map((l) => (
            <a key={l.href} href={l.href}>
              {l.label}
            </a>
          ))}
        </nav>
      </section>

      <div className="m-mosaic bl-mosaic">
        <section className="m-tile m-desk m-c4 m-r4 bl-slipwrap" aria-label="Your bill">
          <div className="bl-slip">
            <div className="bl-slip-top">
              <span>Leafout</span>
              {paid ? <span className="stamp bl-stamp">paid</span> : <span className="numeral">{today}</span>}
            </div>
            <p className="bl-slip-head">{SLIP_HEAD[phase]}</p>
            <div className="bl-cut" />
            <div className="bl-row is-strong">
              <span>Day One</span>
              <span>{formatPrice(paid && p.receipt ? p.receipt.amount : price, 'INR')}</span>
            </div>
            <p className="bl-sub">one payment · every line below</p>
            <div className="bl-cut" />
            <p className="bl-slip-head">Stops being counted</p>
            {FEATURES.map((f) => (
              <div key={f}>
                <div className="bl-row">
                  <span>{DAY_ONE_LABEL[f]}</span>
                  <span>no limit</span>
                </div>
                <p className="bl-sub">Just Looking: {limitText(f, p.usage[f].limit)}</p>
              </div>
            ))}
            <div className="bl-cut" />
            <p className="bl-slip-head">Included</p>
            <div className="bl-row">
              <span>Everything that was already free</span>
              <span>yes</span>
            </div>
            <div className="bl-row">
              <span>Everything we build later</span>
              <span>yes</span>
            </div>
            <div className="bl-row">
              <span>A say in what we build</span>
              <span>yes</span>
            </div>
            <div className="bl-cut" />
            <div className="bl-row is-total">
              <span>{paid ? 'Paid, once' : 'Total, once'}</span>
              <span>{formatPrice(paid && p.receipt ? p.receipt.amount : price, 'INR')}</span>
            </div>
            <div className="bl-row">
              <span>Renews</span>
              <span>never</span>
            </div>
            <div className="bl-row">
              <span>Monthly charge</span>
              <span>none</span>
            </div>
            {early && (
              <div className="bl-row">
                <span>From {win.afterDay} this bill reads</span>
                <span>{formatPrice(offer.dayOneAfter, 'INR')}</span>
              </div>
            )}
            {paid && (
              <>
                <div className="bl-cut" />
                {p.receipt ? (
                  <>
                    <div className="bl-row">
                      <span>Receipt</span>
                      <span>{p.receipt.number}</span>
                    </div>
                    <div className="bl-row">
                      <span>Paid on</span>
                      <span>{fullDate(p.receipt.paidAt)}</span>
                    </div>
                    <div className="bl-row">
                      <span>Through</span>
                      <span>{p.receipt.paymentId ? `Razorpay · ${p.receipt.paymentId}` : 'Razorpay'}</span>
                    </div>
                  </>
                ) : (
                  <p className="bl-sub is-flush">{p.receiptState === 'loading' ? 'Loading your receipt…' : 'Day One is on your account. Its receipt could not be found here: write to us from the Contact page and we will send it.'}</p>
                )}
              </>
            )}
            <div className="bl-cut" />
            <p className="bl-say">Full refund within {offer.refundDays} days of paying, for any reason.</p>
            <p className="bl-say">No plan limits. The two ceilings left are the ones every account shares: the service's daily budget and its anti-abuse rate windows.</p>
          </div>
        </section>

        {early && (
          <section className="m-tile m-field m-c2" aria-label="How long this price lasts">
            <p className="kicker numeral">
              Until {win.endDay}, {win.endTime} IST
            </p>
            <p className="m-count">
              <span className="m-big">{win.daysLeft}</span>
              <span>
                {win.daysLeft === 1 ? 'day' : 'days'} left
                <br />
                at {formatPrice(price, 'INR')}
              </span>
            </p>
          </section>
        )}
        <section className="m-tile m-quiet m-c2 bl-coffee-tile">
          <div className="postit m-turn-r bl-coffee" role="note" style={{ '--pp': 'var(--paper-yellow)' } as CSSProperties}>
            <p className="hand">In Bengaluru? First coffee's on us. Come tell us what to build.</p>
          </div>
        </section>
        <section className="m-tile m-plain m-c2">
          <p className="kicker">{paid ? 'Your receipt' : 'Refund and delivery'}</p>
          <p className="m-fine">
            {paid
              ? 'It stays under Your plan in the account menu. Print it from here whenever you need it.'
              : `Full refund within ${offer.refundDays} days of paying, for any reason. It is digital: it is on your account as soon as the payment settles.`}
          </p>
          {paid && p.receipt && (
            <button type="button" className="btn bl-print" onClick={p.onPrint}>
              Print receipt
            </button>
          )}
        </section>

      </div>
    </div>
  );
}
