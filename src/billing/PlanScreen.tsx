/**
 * The plan screen, board L of the plans canvas: a price list on the left (all four plans in price order) and a stage on
 * the right that is entirely the chosen plan. Day One is chosen when the page opens.
 *
 * This file only draws: every number arrives as a prop (`PricingView` feeds it from the server, the dev gallery from
 * fixtures). Lowkey and Locked In show a name, a price and "opening soon · tell me when"; they cannot be chosen.
 * Prices, dates and counts are straight and in the sans or mono face, never turned and never in the pen font.
 */
import type { CSSProperties, MouseEvent, Ref } from 'react';
import type { ClosedPlan, PlanId, PlanOffer, PlanUsage } from '../../shared/billing';
import { Pips } from '../components/materials';
import { formatPrice, utcDay } from './format';
import { COUNTED_LABEL, DAY_ONE_LABEL, FEATURES, earlyWindow, leftText, limitText, pipsOf, threeMonths } from './model';
import { BILL_PATH } from './route';
import { dayOnePrice } from './useEntitlements';

export type Pick = 'free' | 'day';

export interface PlanScreenProps {
  plan: PlanId;
  usage: PlanUsage;
  /** ISO timestamp: when the monthly counts start again. */
  resetsAt: string;
  offer: PlanOffer;
  signedIn: boolean;
  pick: Pick;
  onPick: (pick: Pick) => void;
  /** The closed plans this account asked to be told about. */
  notify: readonly ClosedPlan[];
  /** The plan a request is in flight for. */
  notifyBusy: ClosedPlan | null;
  /** One line about the last "tell me", said in a live region. */
  notifySay: string;
  onNotify: (plan: ClosedPlan, on: boolean) => void;
  /** "Carry on with Just Looking": back to the app. */
  onCarryOn: () => void;
  /** The press on Day One: go to the bill. */
  onBill: () => void;
  now: number;
  headingRef?: Ref<HTMLHeadingElement>;
}

/** The legal pages live on the site (leafout.in), not in the app. */
const LEGAL_LINKS: readonly { href: string; label: string }[] = [
  { href: 'https://leafout.in/terms', label: 'Terms' },
  { href: 'https://leafout.in/privacy', label: 'Privacy' },
  { href: 'https://leafout.in/refunds', label: 'Refunds' },
  { href: 'https://leafout.in/contact', label: 'Contact' },
];

const BOX = [
  'M4.2 4.6c5-.7 10.2-.5 15.4-.1.4 5 .3 10-.1 15.1-5 .6-10.1.5-15.2.1C3.9 14.7 3.8 9.6 4.2 4.6z',
  'M4.5 4.2c5 .3 10-.2 15 .4-.3 5 .2 10.2-.3 15.1-5.1-.2-10 .4-15-.2.5-5-.1-10.2.3-15.3z',
] as const;

/** The hand tick in front of a plan you can choose (two box shapes, so the list does not repeat). */
function Tick({ index }: { index: 0 | 1 }) {
  return (
    <svg className="bl-tick" viewBox="0 0 24 24" aria-hidden="true">
      <path className="bx" d={BOX[index]} />
      <path className="ck" d="M6.5 12.5c1.8 1.6 3.2 3.4 4.4 5.4 2.2-5.6 5.6-10.4 10.6-14.6" />
    </svg>
  );
}

const days = (n: number) => `${n} ${n === 1 ? 'day' : 'days'}`;

export function PlanScreen(p: PlanScreenProps) {
  const { offer, now } = p;
  const price = dayOnePrice(offer);
  const win = earlyWindow(offer, now);
  const onDayOne = p.plan === 'day_one';

  return (
    <div className="m-screen bl-screen">
      <section className="m-hero bl-hero" aria-label="The plans">
        <p className="kicker">Plans</p>
        <h1 className="serif bl-title" ref={p.headingRef} tabIndex={-1}>
          Choose your plan.
        </h1>
        <p className="bl-lede">Four plans, priced side by side. Two are open today; the monthly two open later at the prices shown.</p>

        <div className="bl-list" role="group" aria-label="Plans">
          <button type="button" className={`bl-item${p.pick === 'free' ? ' is-on' : ''}`} aria-pressed={p.pick === 'free'} onClick={() => p.onPick('free')}>
            <Tick index={0} />
            <span className="bl-item-main">
              <span>Just Looking</span>
              <span className="m-fine">where you are now · a little of everything each month</span>
            </span>
            <span className="bl-item-price numeral">{formatPrice(0, 'INR')}</span>
          </button>

          <ClosedItem name="Lowkey" plan="lowkey" price={offer.lowkey} p={p} />

          <button type="button" className={`bl-item${p.pick === 'day' ? ' is-on' : ''}`} aria-pressed={p.pick === 'day'} onClick={() => p.onPick('day')}>
            <Tick index={1} />
            <span className="bl-item-main">
              <span>
                Day One <span className="bl-life">lifetime</span>
              </span>
              <span className="m-fine numeral">
                {onDayOne
                  ? 'yours for life · everything, no plan limits'
                  : offer.early
                    ? `everything, for life · early price until ${win.endDay}, ${days(win.daysLeft)} left`
                    : 'everything, for life · no plan limits'}
              </span>
            </span>
            <span className="bl-item-price numeral">
              {formatPrice(price, 'INR')} <small className="m-fine">once, for life</small>
            </span>
          </button>

          <ClosedItem name="Locked In" plan="locked_in" price={offer.lockedIn} p={p} />
        </div>

        <p className="m-fine bl-say" role="status" aria-live="polite">
          {p.notifySay}
        </p>

        <div className="bl-plain-say">
          <span>Nothing renews by itself.</span>
        </div>
        <ul className="bl-promises" aria-label="What Leafout promises">
          <li>
            <strong>Every skill cites its line.</strong> Your graph points to the resume line or project that proves each skill.
          </li>
          <li>
            <strong>Nothing invented.</strong> No fake scores, no made-up numbers, and never a skill you haven't shown.
          </li>
          <li>
            <strong>Built with the people who use it.</strong> Day One members tell us what to build next, and we build it.
          </li>
        </ul>
        <nav className="bl-terms" aria-label="Terms and policies">
          {LEGAL_LINKS.map((l) => (
            <a key={l.href} href={l.href}>
              {l.label}
            </a>
          ))}
        </nav>
      </section>

      {p.pick === 'day' ? <DayOneStage {...p} price={price} /> : <FreeStage {...p} />}
    </div>
  );
}

function ClosedItem({ name, plan, price, p }: { name: string; plan: ClosedPlan; price: number; p: PlanScreenProps }) {
  const told = p.notify.includes(plan);
  const busy = p.notifyBusy === plan;
  return (
    <div className="bl-item is-closed">
      <span className="bl-lock" aria-hidden="true" />
      <span className="bl-item-main">
        <span>{name}</span>
        {told ? (
          <span className="m-fine bl-told">
            we will email you when it opens
            <button type="button" className="bl-tell" disabled={busy} aria-label={`Don't tell me when ${name} opens`} onClick={() => p.onNotify(plan, false)}>
              Undo
            </button>
          </span>
        ) : (
          <button type="button" className="bl-tell" disabled={busy} aria-label={`${name} is opening soon. Tell me when it opens`} onClick={() => p.onNotify(plan, true)}>
            opening soon · tell me when
          </button>
        )}
      </span>
      <span className="bl-item-price numeral">
        {formatPrice(price, 'INR')} <small className="m-fine">a month</small>
      </span>
    </div>
  );
}

/** Day One's stage: the ink block. */
function DayOneStage(p: PlanScreenProps & { price: number }) {
  const { offer, price, now } = p;
  const win = earlyWindow(offer, now);
  const sum = threeMonths(offer, price);
  const onSale = offer.dayOne !== undefined;
  const mine = p.plan === 'day_one';
  const link = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    p.onBill();
  };
  return (
    <section className="m-tile m-ink bl-stage" aria-label="Day One">
      <p className="kicker numeral">
        {mine
          ? 'Day One · yours for life'
          : offer.early
            ? `Day One · lifetime · early price until ${win.endDay}, ${win.endTime} IST · ${days(win.daysLeft)} left`
            : 'Day One · lifetime'}
      </p>
      <h2 className="serif bl-stage-title">{mine ? 'Day One is yours, for life.' : 'Pay once. Yours for life.'}</h2>
      <p className="bl-say-big">
        {mine
          ? 'Nothing is counted on your account, ever, and everything we build after this is yours too. You will never pay again.'
          : `One payment of ${formatPrice(price, 'INR')}, and you never pay again. No plan limits on anything Leafout does, today or in everything we build after this.`}
      </p>

      <div className="bl-cols">
        <div>
          {FEATURES.map((f) => (
            <div className="bl-lim" key={f}>
              <span>{DAY_ONE_LABEL[f]}</span>
              <span className="bl-lim-end">no limit</span>
            </div>
          ))}
          <div className="bl-lim">
            <span>Everything we build later</span>
            <span className="bl-lim-end">included</span>
          </div>
        </div>

        <div className="bl-col-end">
          {!mine && (
            <div className="bl-sum">
              <p className="kicker">Three months, at the prices in the list</p>
              <div className="bl-c3">
                <span>Lowkey</span>
                <span className="bl-bar" aria-hidden="true">
                  <i style={{ width: `${sum.wLowkey}%` }} />
                </span>
                <span>{formatPrice(sum.lowkey, 'INR')}</span>
                <span>Locked In</span>
                <span className="bl-bar" aria-hidden="true">
                  <i style={{ width: `${sum.wLockedIn}%` }} />
                </span>
                <span>{formatPrice(sum.lockedIn, 'INR')}</span>
                <span>Day One</span>
                <span className="bl-bar" aria-hidden="true">
                  <i className="is-day" style={{ width: `${sum.wDayOne}%` }} />
                </span>
                <span>{formatPrice(sum.dayOne, 'INR')}</span>
              </div>
              {offer.early && (
                <p className="m-fine numeral">
                  The early price ends on {win.endDay}; from {win.afterDay} Day One costs {formatPrice(offer.dayOneAfter, 'INR')}. Buy before, and it is yours for life at {formatPrice(price, 'INR')}.
                </p>
              )}
            </div>
          )}
          <div className="postit m-turn-r bl-coffee" role="note" style={{ '--pp': 'var(--paper-yellow)' } as CSSProperties}>
            <p className="hand">In Bengaluru? First coffee's on us. Come tell us what to build.</p>
          </div>
        </div>
      </div>

      {mine ? (
        <a className="bl-go" href={BILL_PATH} onClick={link}>
          <span>Open your receipt</span>
          <span aria-hidden="true">→</span>
        </a>
      ) : onSale ? (
        <a className="bl-go" href={BILL_PATH} onClick={link}>
          <span className="numeral">Continue to your bill · {formatPrice(price, 'INR')} once</span>
          <span aria-hidden="true">→</span>
        </a>
      ) : (
        <button type="button" className="bl-go is-off" disabled>
          <span>Coming soon</span>
        </button>
      )}
    </section>
  );
}

/** Just Looking's stage: graph paper, with this month's counts for an account. */
function FreeStage(p: PlanScreenProps) {
  const { offer, usage } = p;
  const counted = p.signedIn && p.plan === 'just_looking';
  const price = dayOnePrice(offer);
  const win = earlyWindow(offer, p.now);
  return (
    <section className="m-tile m-graph bl-stage" aria-label="Just Looking">
      <p className="kicker">Just Looking · where you are now</p>
      <h2 className="serif bl-stage-title">A little of everything, every month.</h2>
      <p className="bl-say-big">
        {counted
          ? `Free for as long as you like. This is what is left of your month; the counts come back on ${utcDay(p.resetsAt)}.`
          : 'Free for as long as you like. These are the limits, counted each month.'}
      </p>

      <div className="bl-limits">
        {FEATURES.map((f) => {
          const use = usage[f];
          const pips = counted ? pipsOf(use) : null;
          return (
            <div className="bl-lim" key={f}>
              <span>{COUNTED_LABEL[f]}</span>
              <span className="bl-lim-end">
                {pips && <Pips pips={pips} r={4} gap={5} label={leftText(use)} />}
                <span>{counted ? leftText(use) : limitText(f, use.limit)}</span>
              </span>
            </div>
          );
        })}
      </div>

      <p className="m-fine">Never counted: your skill graph, next roles, the report, radar card, portfolio, prep plans, resumes and the Weekly.</p>
      <button type="button" className="btn bl-carry" onClick={p.onCarryOn}>
        Carry on with Just Looking
      </button>
      <button type="button" className="bl-lift" onClick={() => p.onPick('day')}>
        <span className="numeral">
          Day One stops counting all six, for life. {formatPrice(price, 'INR')} once{offer.early ? `, early price until ${win.endDay}` : ''}.
        </span>
        <span aria-hidden="true">→</span>
      </button>
    </section>
  );
}
