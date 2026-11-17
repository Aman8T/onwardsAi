/**
 * Dev page (`/dev/pricing`, development builds only): the plan screen and the bill in every state, on fixtures, with no
 * server and no account. Pick a state in the bar, or link straight to one:
 *
 *   /dev/pricing?state=plans        (`&scheme=dark` for the dark scheme, `&h=120` to pin the hue)
 *
 *   The plan screen
 *     plans            Day One chosen, early price, a signed-in account on Just Looking
 *     free · free-visitor   Just Looking chosen: this month's counts with pips; a visitor sees the limits alone
 *     told             both closed plans asked about ("we will email you when it opens", with Undo)
 *     late             the early window is over: the after-price, no days-left or "from" line
 *     not-on-sale      Day One is not on sale here: the configured price and a disabled "Coming soon"
 *     mine             an account already on Day One: the stage is theirs and links to its receipt
 *     loading · error  the prices are loading; they could not be loaded
 *   When a count runs out (the panel every counted screen shows, `LimitNote`)
 *     limit-matches · limit-searches · limit-practice · limit-analyses   the month's counts, with pips and the day they come back
 *     limit-coach   a limit above five is a number alone, with no pips
 *     limit-applications   counted once, so no day; limit-off-sale   Day One not on sale: "See the plans"
 *   The bill
 *     bill · bill-visitor   before paying (signed in; a visitor, whose Pay opens the sign-in sheet)
 *     starting · paying · paying-retry · verifying   the wait (Razorpay's window is theirs and not drawn)
 *     pending · cancelled · failed   confirming in the background; the window was closed; the payment did not go through
 *     paid · paid-loading · paid-missing   the slip as the receipt (Print receipt works); its receipt still loading; not found
 *     bill-late · bill-not-on-sale   the bill after the early window; Day One not on sale
 *
 * Buttons that leave the screen say where they would go. Resize the window (or use the browser pane's phone preset) to see
 * the stacked layout under 1100 px and the single column under 760 px.
 */
import { useCallback, useMemo, useState } from 'react';
import type { ClosedPlan, Feature, PlanOffer, PlanUsage, Receipt } from '../../../shared/billing';
import { Brand } from '../../components/Brand';
import { Bill } from '../Bill';
import { LimitPanel } from '../LimitNote';
import type { BillPhase } from '../Bill';
import { PlanScreen } from '../PlanScreen';
import type { Pick } from '../PlanScreen';
import '../../styles/billing.css';

const DAY = 86_400_000;

const STATES = [
  ['plans', 'Plans · Day One'],
  ['free', 'Plans · Just Looking'],
  ['free-visitor', 'Plans · a visitor'],
  ['told', 'Plans · told'],
  ['late', 'Plans · after the window'],
  ['not-on-sale', 'Plans · not on sale'],
  ['mine', 'Plans · on Day One'],
  ['loading', 'Plans · loading'],
  ['error', 'Plans · error'],
  ['bill', 'Bill'],
  ['bill-visitor', 'Bill · a visitor'],
  ['starting', 'Bill · opening'],
  ['paying', 'Bill · paying'],
  ['paying-retry', 'Bill · attempt failed'],
  ['verifying', 'Bill · confirming'],
  ['pending', 'Bill · pending'],
  ['cancelled', 'Bill · closed'],
  ['failed', 'Bill · did not go through'],
  ['paid', 'Receipt'],
  ['paid-loading', 'Receipt · loading'],
  ['paid-missing', 'Receipt · not found'],
  ['bill-late', 'Bill · after the window'],
  ['bill-not-on-sale', 'Bill · not on sale'],
  ['limit-matches', 'Ran out · matches'],
  ['limit-searches', 'Ran out · searches'],
  ['limit-practice', 'Ran out · practice'],
  ['limit-coach', 'Ran out · coach'],
  ['limit-analyses', 'Ran out · analyses'],
  ['limit-applications', 'Ran out · applications'],
  ['limit-off-sale', 'Ran out · not on sale'],
] as const;
type StateId = (typeof STATES)[number][0];

const isState = (s: string | null): s is StateId => STATES.some(([id]) => id === s);

function readState(): StateId {
  const s = new URLSearchParams(window.location.search).get('state');
  return isState(s) ? s : 'plans';
}

const NOW = Date.now();

const USAGE: PlanUsage = {
  matches: { limit: 3, used: 2 },
  searches: { limit: 5, used: 2 },
  practice: { limit: 3, used: 0 },
  coach: { limit: 30, used: 12 },
  analyses: { limit: 2, used: 0 },
  applications: { limit: 5, used: 0 },
};

const RESETS = (() => {
  const d = new Date(NOW);
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)).toISOString();
})();

const OFFER: PlanOffer = {
  lowkey: 39900,
  lockedIn: 129900,
  dayOne: 99900,
  dayOneEarly: 99900,
  dayOneAfter: 999900,
  // the roadmap's own end of the early price (B2): 15 Dec 2026, 23:59 IST
  dayOneEndsAt: '2026-12-15T23:59:00+05:30',
  early: true,
  refundDays: 7,
  notify: [],
};

const RECEIPT: Receipt = {
  number: 'LF-261117-A1B2C3',
  product: 'dayone',
  amount: 99900,
  currency: 'INR',
  paidAt: new Date(NOW).toISOString(),
  paymentId: 'pay_Q1w2E3r4T5y6U7',
};

/** What each screen says is safe (the real ones live with their screens). */
const SAFE: Record<Feature, string> = {
  matches: 'Your posting is still in the box below. Nothing was read and nothing was charged.',
  searches: 'No search ran and nothing was charged.',
  practice: 'Your plan is as you left it. Nothing was started and nothing was charged.',
  coach: 'Nothing was charged. Choosing to wait puts your question back in the box, so you can send it later.',
  analyses: 'Nothing was read and nothing was charged. Your current graph, if you have one, is unchanged.',
  applications: 'Nothing was sent and nothing was charged.',
};

const WHO = { name: 'Meera Nair', email: 'meera@example.com' };

interface Scene {
  kind: 'plans' | 'bill' | 'limit';
  /** The count that ran out (`limit` scenes). */
  feature?: Feature;
  offer: PlanOffer;
  signedIn: boolean;
  plan: 'just_looking' | 'day_one';
  pick: Pick;
  phase: BillPhase;
  message?: string;
  receipt?: Receipt;
  receiptState?: 'ready' | 'loading' | 'missing';
  /** The plan screen's own states. */
  bare?: 'loading' | 'error';
}

const LATE: PlanOffer = { ...OFFER, dayOne: 999900, early: false, dayOneEndsAt: new Date(NOW - 3 * DAY).toISOString() };
const OFF_SALE: PlanOffer = { ...OFFER, dayOne: undefined };

function sceneFor(id: StateId): Scene {
  const plans: Scene = { kind: 'plans', offer: OFFER, signedIn: true, plan: 'just_looking', pick: 'day', phase: 'bill' };
  const bill: Scene = { ...plans, kind: 'bill' };
  switch (id) {
    case 'plans':
      return plans;
    case 'free':
      return { ...plans, pick: 'free' };
    case 'free-visitor':
      return { ...plans, pick: 'free', signedIn: false };
    case 'told':
      return { ...plans, offer: { ...OFFER, notify: ['lowkey', 'locked_in'] } };
    case 'late':
      return { ...plans, offer: LATE };
    case 'not-on-sale':
      return { ...plans, offer: OFF_SALE };
    case 'mine':
      return { ...plans, plan: 'day_one' };
    case 'loading':
      return { ...plans, bare: 'loading' };
    case 'error':
      return { ...plans, bare: 'error' };
    case 'bill':
      return bill;
    case 'bill-visitor':
      return { ...bill, signedIn: false };
    case 'starting':
      return { ...bill, phase: 'starting', message: 'Opening checkout…' };
    case 'paying':
      return { ...bill, phase: 'paying', message: 'Finish the payment in the checkout window.' };
    case 'paying-retry':
      return { ...bill, phase: 'paying', message: 'The payment did not go through. Your bank declined it. Try again in the checkout window, or close it.' };
    case 'verifying':
      return { ...bill, phase: 'verifying', message: 'Confirming your payment…' };
    case 'pending':
      return { ...bill, phase: 'pending', message: 'Thank you. The payment is confirmed in the background, and your plan appears on this page as soon as it arrives.' };
    case 'cancelled':
      return { ...bill, phase: 'cancelled', message: 'Checkout closed. Nothing was charged.' };
    case 'failed':
      return { ...bill, phase: 'failed', message: 'The payment did not go through. Nothing was charged. Your bank declined it.' };
    case 'paid':
      return { ...bill, phase: 'paid', plan: 'day_one', message: 'Payment received. Day One is yours, for life.', receipt: RECEIPT, receiptState: 'ready' };
    case 'paid-loading':
      return { ...bill, phase: 'paid', plan: 'day_one', receiptState: 'loading' };
    case 'paid-missing':
      return { ...bill, phase: 'paid', plan: 'day_one', receiptState: 'missing' };
    case 'bill-late':
      return { ...bill, offer: LATE };
    case 'bill-not-on-sale':
      return { ...bill, offer: OFF_SALE };
    case 'limit-matches':
      return { ...plans, kind: 'limit', feature: 'matches' };
    case 'limit-searches':
      return { ...plans, kind: 'limit', feature: 'searches' };
    case 'limit-practice':
      return { ...plans, kind: 'limit', feature: 'practice' };
    case 'limit-coach':
      return { ...plans, kind: 'limit', feature: 'coach' };
    case 'limit-analyses':
      return { ...plans, kind: 'limit', feature: 'analyses' };
    case 'limit-applications':
      return { ...plans, kind: 'limit', feature: 'applications' };
    case 'limit-off-sale':
      return { ...plans, kind: 'limit', feature: 'searches', offer: OFF_SALE };
  }
}

export function PricingGallery() {
  const [state, setState] = useState<StateId>(readState);
  const [note, setNote] = useState('');

  const go = (next: StateId) => {
    setState(next);
    setNote('');
    const q = new URLSearchParams(window.location.search);
    q.set('state', next);
    window.history.replaceState(null, '', `/dev/pricing?${q.toString()}`);
  };

  return (
    <div className="bl-dev">
      <header className="bl-dev-header">
        <Brand />
        <span className="kicker">Dev only · the plan screen and the bill on fixtures, no server</span>
        <nav className="bl-dev-nav" aria-label="States">
          {STATES.map(([id, label]) => (
            <button key={id} type="button" className={`btn btn-sm${state === id ? ' btn-primary' : ' btn-quiet'}`} aria-pressed={state === id} onClick={() => go(id)}>
              {label}
            </button>
          ))}
        </nav>
        <p className="m-fine" role="status">
          {note}
        </p>
      </header>
      <Stage key={state} id={state} onNote={setNote} />
    </div>
  );
}

function Stage({ id, onNote }: { id: StateId; onNote: (s: string) => void }) {
  const scene = useMemo(() => sceneFor(id), [id]);
  const [pick, setPick] = useState<Pick>(scene.pick);
  const [told, setTold] = useState<readonly ClosedPlan[]>(scene.offer.notify);
  const [say, setSay] = useState('');

  const notify = useCallback(
    (plan: ClosedPlan, on: boolean) => {
      if (!scene.signedIn) {
        onNote('Gallery: a visitor would get the sign-in sheet, returning to /pricing.');
        return;
      }
      setTold((t) => (on ? [...t.filter((p) => p !== plan), plan] : t.filter((p) => p !== plan)));
      setSay(on ? 'We will email you when it opens.' : 'Not telling you about it.');
    },
    [scene.signedIn, onNote],
  );

  if (scene.bare) {
    const failed = scene.bare === 'error';
    return (
      <main className="bl-main" aria-busy={!failed || undefined}>
        <section className="m-hero bl-hero bl-bare" aria-label="The plans">
          <p className="kicker">Plans</p>
          <h1 className="serif bl-title">{failed ? 'The plans could not be loaded.' : 'Getting the plans ready.'}</h1>
          <div className={`bl-status${failed ? ' is-problem' : ''}`} role="status" aria-live="polite">
            {failed && (
              <>
                <p>The prices could not be loaded.</p>
                <div className="bl-status-actions">
                  <button type="button" className="btn btn-sm" onClick={() => onNote('Gallery: asks the server again.')}>
                    Try again
                  </button>
                </div>
              </>
            )}
          </div>
        </section>
      </main>
    );
  }

  if (scene.kind === 'limit' && scene.feature) {
    const feature = scene.feature;
    return (
      <main className="bl-main">
        <LimitPanel
          feature={feature}
          limit={USAGE[feature].limit}
          resetsAt={RESETS}
          offer={scene.offer}
          safe={SAFE[feature]}
          onWait={() => onNote('Gallery: dismisses the panel.')}
        />
      </main>
    );
  }

  return (
    <main className="bl-main">
      {scene.kind === 'plans' ? (
        <PlanScreen
          plan={scene.plan}
          usage={USAGE}
          resetsAt={RESETS}
          offer={scene.offer}
          signedIn={scene.signedIn}
          pick={pick}
          onPick={setPick}
          notify={told}
          notifyBusy={null}
          notifySay={say}
          onNotify={notify}
          onCarryOn={() => onNote('Gallery: back to the desk.')}
          onBill={() => onNote('Gallery: goes to /pricing?bill=day-one.')}
          now={NOW}
        />
      ) : (
        <Bill
          phase={scene.phase}
          offer={scene.offer}
          usage={USAGE}
          price={scene.offer.dayOne ?? (scene.offer.early ? scene.offer.dayOneEarly : scene.offer.dayOneAfter)}
          who={scene.signedIn ? WHO : undefined}
          message={scene.message}
          receipt={scene.receipt}
          receiptState={scene.receiptState}
          now={NOW}
          onPay={() => onNote(scene.signedIn ? "Gallery: Razorpay's window would open over the page." : 'Gallery: a visitor would get the sign-in sheet, returning to the bill.')}
          onDismiss={() => onNote('Gallery: clears the line.')}
          onTryAgain={() => onNote('Gallery: starts the checkout again.')}
          onPlans={() => onNote('Gallery: back to /pricing.')}
          onDesk={() => onNote('Gallery: opens the desk.')}
          onPrint={() => window.print()}
        />
      )}
    </main>
  );
}
