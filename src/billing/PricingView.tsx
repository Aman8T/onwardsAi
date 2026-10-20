/**
 * The pricing page (`/pricing`), per the "Free and paid" board of the posting-match canvas: a serif line, a currency
 * switch, three columns (Free, Per posting, Monthly) and three quiet notes.
 *
 * Anyone can read it. Buying needs an account, so an anonymous visitor's "Buy" opens the sign-in sheet first. A plan
 * whose price is not set on the server (or whose payment provider is not configured) has no price in the response:
 * the page shows "Coming soon" there and no placeholder number, and its button is disabled.
 *
 * "Monthly" is a 30-day pass bought once: it does not renew by itself, there is no subscription to cancel and there is
 * no annual plan (`project-soul.md`: "No annual lock-in"). Lines that name something not built yet say so.
 *
 * The payment itself is `Checkout.tsx`; Checkout.js is on the page only while this page is.
 */
import { motion, useReducedMotion } from 'motion/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Currency, Product } from '../../shared/billing';
import { AccountMenu } from '../auth/AccountMenu';
import { SignInLink } from '../auth/SignInLink';
import { signInSheet, useMe } from '../auth/useMe';
import { Atmosphere } from '../components/Atmosphere';
import { Brand } from '../components/Brand';
import { EASE } from '../lib/ui';
import { actions, useApp } from '../store';
import { CheckoutNotice, isBusy, useCheckout, useCheckoutScript } from './Checkout';
import { EntitlementChip } from './EntitlementChip';
import { formatPrice } from './format';
import { PRICING_PATH, parseCheckoutReturn, parsePreselect, preselectPath } from './route';
import { useEntitlements } from './useEntitlements';
import '../styles/billing.css';

interface Item {
  text: string;
  /** Names something that is planned but not built; the page says so. */
  soon?: boolean;
}

const FREE_ITEMS: Item[] = [
  { text: 'Your skill graph and next roles' },
  { text: 'A public portfolio page' },
  { text: 'The radar card for GitHub and LinkedIn', soon: true },
  { text: 'One posting match a month, with the gap list' },
];
const PACK_ITEMS: Item[] = [
  { text: 'Everything in Free' },
  { text: 'Resume and cover note built for each posting' },
  { text: 'PDF and DOCX, checked before download' },
  { text: 'Re-check after you close a gap, free' },
];
const PASS_ITEMS: Item[] = [
  { text: 'Unlimited postings for 30 days' },
  { text: 'Mock interviews grounded in your own work', soon: true },
  { text: 'A prep plan, and your graph updated as you finish it', soon: true },
  { text: 'Portfolio on your own domain', soon: true },
];

const CURRENCIES: { id: Currency; label: string }[] = [
  { id: 'INR', label: '₹ India' },
  { id: 'USD', label: '$ Everywhere else' },
];
const OTHER: Record<Currency, { id: Currency; where: string }> = {
  INR: { id: 'USD', where: 'outside India' },
  USD: { id: 'INR', where: 'in India' },
};

export function PricingView() {
  const reduced = useReducedMotion() ?? false;
  const profile = useApp((s) => s.profile);
  const me = useMe();
  const ent = useEntitlements();
  const heading = useRef<HTMLHeadingElement>(null);

  // Checkout.js belongs to this page: in while it is mounted, out when it is not, and only when INR is on sale
  const script = useCheckoutScript(ent.razorpayOffered);

  const settle = useCallback(async () => {
    await Promise.all([ent.refresh(), me.refresh()]);
  }, [ent.refresh, me.refresh]);
  const needSignIn = useCallback(() => signInSheet.open('buy'), []);
  const checkout = useCheckout({ onSettled: settle, onSignInNeeded: needSignIn });
  const { restore } = checkout;

  // what a signed-out visitor chose before signing in: shown selected, never bought without another click
  const [preselect] = useState(() => parsePreselect(window.location.search));
  const setCurrency = ent.setCurrency;
  useEffect(() => {
    if (preselect.currency) setCurrency(preselect.currency);
    if (!preselect.product && !preselect.currency) return;
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete('product');
      url.searchParams.delete('currency');
      window.history.replaceState(null, '', url.pathname + url.search);
    } catch {
      /* no address bar to read */
    }
  }, [preselect, setCurrency]);

  // back from Stripe: say what happened, then take the marker out of the address so a reload does not repeat it.
  // Signed out (the session did not survive the trip), the page cannot show the plan: it asks to sign in instead.
  const [paidSignedOut, setPaidSignedOut] = useState(false);
  const returned = useRef<string | null | undefined>(undefined);
  if (returned.current === undefined) returned.current = parseCheckoutReturn(window.location.search);
  const handled = useRef(false);
  const meLoaded = me.status === 'ready' || me.status === 'error';
  const signedIn = !!me.user;
  useEffect(() => {
    if (handled.current || !returned.current || !meLoaded) return;
    handled.current = true;
    if (returned.current === 'success' && !signedIn) setPaidSignedOut(true);
    else restore(returned.current === 'success' ? 'pending' : 'cancelled');
    try {
      window.history.replaceState(null, '', window.location.pathname);
    } catch {
      /* no address bar to read */
    }
  }, [restore, meLoaded, signedIn]);
  useEffect(() => {
    if (signedIn && paidSignedOut) {
      setPaidSignedOut(false);
      restore('pending');
    }
  }, [signedIn, paidSignedOut, restore]);

  // the page title and the heading take focus, like a new screen
  useEffect(() => {
    const before = document.title;
    document.title = 'Pricing · Leafout';
    heading.current?.focus({ preventScroll: true });
    return () => {
      document.title = before;
    };
  }, []);

  const busy = isBusy(checkout.state.phase);
  // INR waits for Checkout.js (a failed load is let through: the click then says so, before any order exists)
  const ready = me.loaded && ent.loaded && !busy && (ent.currency !== 'INR' || script !== 'loading');
  // the chosen plan takes focus once the page is ready, so Enter completes what was started before sign-in
  const focused = useRef(false);
  useEffect(() => {
    if (!preselect.product || !ready || focused.current) return;
    focused.current = true;
    const el = document.getElementById(`bl-buy-${preselect.product}`);
    el?.focus({ preventScroll: true });
    el?.scrollIntoView({ block: 'center' });
  }, [preselect.product, ready]);
  const buy = (product: Product) => {
    if (!me.user) {
      // the choice rides along in `next`, so the page comes back with the plan selected (and still unbought)
      signInSheet.open('buy', undefined, preselectPath(product, ent.currency));
      return;
    }
    void checkout.start(product, ent.currency);
  };

  const other = OTHER[ent.currency];
  const back = () => actions.closePricing();

  return (
    <div className="bl-root">
      <div className="wrap bl-wrap">
        <header className="bl-header">
          <Brand onClick={back} />
          <div className="bl-header-end">
            <button type="button" className="btn btn-quiet" onClick={back}>
              {profile ? 'Back to your graph' : 'Back'}
            </button>
            <EntitlementChip />
            <Atmosphere />
            <AccountMenu />
          </div>
        </header>

        <motion.main
          className="bl-main"
          initial={reduced ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, ease: EASE }}
          aria-busy={ent.status === 'loading' || undefined}
        >
          <section className="bl-intro">
            <div className="bl-intro-copy">
              <p className="kicker">Free and paid</p>
              <h1 ref={heading} className="serif bl-title" tabIndex={-1}>
                Pay per posting, or by the month. Never by the year.
              </h1>
              <p className="bl-lede">The graph, the portfolio and your first match are free. Prices are shown in your currency.</p>
            </div>
            <div className="bl-seg" role="group" aria-label="Currency">
              {CURRENCIES.map((c) => (
                <button key={c.id} type="button" className="bl-seg-btn" aria-pressed={ent.currency === c.id} onClick={() => ent.setCurrency(c.id)}>
                  {c.label}
                </button>
              ))}
            </div>
          </section>

          {ent.status === 'error' && (
            <div className="bl-notice is-problem" role="alert">
              <p>The prices could not be loaded.</p>
              <button type="button" className="btn btn-quiet btn-sm" onClick={() => void ent.refresh()}>
                Try again
              </button>
            </div>
          )}

          {paidSignedOut && (
            <div className="bl-notice" role="status" aria-live="polite">
              <p>Payment received. Sign in to see it.</p>
              <div className="bl-notice-actions">
                <SignInLink className="btn btn-primary btn-sm" path={PRICING_PATH} reason="buy">
                  Sign in
                </SignInLink>
              </div>
            </div>
          )}

          <CheckoutNotice
            state={checkout.state}
            entitlements={{ credits: ent.credits, passUntil: ent.passUntil, freeMatchAvailable: ent.freeMatchAvailable }}
            onDismiss={checkout.dismiss}
            onContinue={() => actions.openMatch()}
          />

          <section className="bl-plans" aria-label="Plans">
            <article className="panel bl-plan" aria-labelledby="bl-free">
              <header className="bl-plan-head">
                <p className="kicker" id="bl-free">
                  Free
                </p>
                <p className="serif bl-price">{formatPrice(0, ent.currency)}</p>
                <p className="bl-sub">For as long as you like</p>
              </header>
              <PlanList items={FREE_ITEMS} />
              <div className="bl-plan-foot">
                <button type="button" className="btn" onClick={back}>
                  {profile ? 'Open my graph' : 'Start free'}
                </button>
              </div>
            </article>

            <Plan
              id="pack3"
              kicker="Per posting"
              sub="A pack of three postings. No expiry."
              items={PACK_ITEMS}
              price={ent.priced.pack3}
              currency={ent.currency}
              otherPrice={ent.prices[other.id].pack3}
              otherCurrency={other}
              cta="Buy three postings"
              primary
              enabled={ready}
              onBuy={buy}
              busyHere={busy && checkout.state.product === 'pack3'}
              selected={preselect.product === 'pack3'}
            />

            <Plan
              id="pass30"
              kicker="Monthly"
              unit="/ 30 days"
              sub="30 days from purchase. It does not renew by itself. No annual plan."
              items={PASS_ITEMS}
              price={ent.priced.pass30}
              currency={ent.currency}
              otherPrice={ent.prices[other.id].pass30}
              otherCurrency={other}
              cta="Get 30 days"
              enabled={ready}
              onBuy={buy}
              busyHere={busy && checkout.state.product === 'pass30'}
              selected={preselect.product === 'pass30'}
            />
          </section>

          <section className="bl-notes" aria-label="Good to know">
            <aside className="panel bl-note">
              <p className="kicker">Students</p>
              <p>Same prices. Nobody has to buy a year up front to find out if it helps.</p>
            </aside>
            <aside className="panel bl-note">
              <p className="kicker">Paying</p>
              <p>UPI, cards and net banking in India through Razorpay. Cards everywhere else through Stripe. We never see your card details.</p>
            </aside>
            <aside className="panel bl-note">
              <p className="kicker">What never costs</p>
              <p>Seeing a gap. Answering a question about yourself. Re-checking after you did.</p>
            </aside>
          </section>
        </motion.main>
      </div>
    </div>
  );
}

function PlanList({ items }: { items: Item[] }) {
  return (
    <ul className="bl-list">
      {items.map((item) => (
        <li key={item.text}>
          <span className="numeral bl-tick" aria-hidden="true">
            ✓
          </span>
          <span>
            {item.text}
            {item.soon && <span className="bl-soon">Coming</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

interface PlanProps {
  id: Product;
  kicker: string;
  sub: string;
  items: Item[];
  /** Minor units; undefined when the plan is not on sale in this currency. */
  price: number | undefined;
  currency: Currency;
  unit?: string;
  otherPrice: number | undefined;
  otherCurrency: { id: Currency; where: string };
  cta: string;
  primary?: boolean;
  enabled: boolean;
  busyHere: boolean;
  /** Chosen before signing in: marked, and its button takes focus. */
  selected?: boolean;
  onBuy: (product: Product) => void;
}

function Plan({ id, kicker, sub, items, price, currency, unit, otherPrice, otherCurrency, cta, primary, enabled, busyHere, selected, onBuy }: PlanProps) {
  const onSale = price !== undefined;
  return (
    <article className={['panel bl-plan', (primary || selected) && 'is-featured'].filter(Boolean).join(' ')} aria-labelledby={`bl-${id}`} aria-current={selected ? 'true' : undefined}>
      <header className="bl-plan-head">
        <p className="kicker" id={`bl-${id}`}>
          {kicker}
        </p>
        {onSale ? (
          <p className="serif bl-price">
            {formatPrice(price, currency)}
            {unit && <span className="bl-unit"> {unit}</span>}
          </p>
        ) : (
          <p className="serif bl-price is-soon">Coming soon</p>
        )}
        <p className="bl-sub">{sub}</p>
        {onSale && otherPrice !== undefined && (
          <p className="bl-alt">
            {formatPrice(otherPrice, otherCurrency.id)} {otherCurrency.where}
          </p>
        )}
      </header>
      <PlanList items={items} />
      <div className="bl-plan-foot">
        <button id={`bl-buy-${id}`} type="button" className={['btn', primary && 'btn-primary'].filter(Boolean).join(' ')} disabled={!onSale || !enabled} onClick={() => onBuy(id)}>
          {!onSale ? 'Coming soon' : busyHere ? 'One moment…' : cta}
        </button>
      </div>
    </article>
  );
}
