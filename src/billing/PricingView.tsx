/**
 * The pricing screens, `/pricing` and `/pricing?bill=day-one`.
 *
 * `/pricing` is the plan screen (`PlanScreen.tsx`, board L of the plans canvas): all four plans in price order and a
 * stage that is entirely the chosen plan. `/pricing?bill=day-one` is the bill (`BillView.tsx`). Anyone can read the
 * plan screen; paying needs an account, so the bill's Pay press opens the sign-in sheet for a visitor. Lowkey and
 * Locked In cannot be bought: they show a price and "opening soon · tell me when", and the wish is kept per account
 * (`PUT /api/billing/notify`, read back from `offer.notify`).
 *
 * Every number comes from `useEntitlements()`; nothing is typed into the screen.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ClosedPlan } from '../../shared/billing';
import { signInSheet, useMe } from '../auth/useMe';
import * as api from '../lib/api';
import { actions, useApp } from '../store';
import { BillView } from './BillView';
import { Frame } from './Frame';
import { PlanScreen } from './PlanScreen';
import type { Pick } from './PlanScreen';
import { BILL_PATH, PRICING_PATH, navigate, useAddress } from './route';
import { useEntitlements } from './useEntitlements';

const PLAN_NAME: Record<ClosedPlan, string> = { lowkey: 'Lowkey', locked_in: 'Locked In' };

export function PricingView() {
  return useAddress() === 'bill' ? <BillView /> : <PlansView />;
}

function PlansView() {
  const profile = useApp((s) => s.profile);
  const me = useMe();
  const ent = useEntitlements();
  const heading = useRef<HTMLHeadingElement>(null);
  const [now] = useState(() => Date.now());
  const [pick, setPick] = useState<Pick>('day');

  // "tell me when": what this account asked for, kept here until the server's answer is read back
  const [told, setTold] = useState<ClosedPlan[] | null>(null);
  const [busy, setBusy] = useState<ClosedPlan | null>(null);
  const [say, setSay] = useState('');
  const userId = me.user?.id;
  useEffect(() => {
    setTold(null);
    setSay('');
  }, [userId]);

  const notify = useCallback(
    async (plan: ClosedPlan, on: boolean) => {
      if (!me.user) {
        signInSheet.open('account', undefined, PRICING_PATH);
        return;
      }
      setBusy(plan);
      setSay('');
      try {
        const res = await api.putBillingNotify({ plan, on });
        setTold(res.notify);
        setSay(on ? `We will email you when ${PLAN_NAME[plan]} opens.` : `Not telling you about ${PLAN_NAME[plan]}.`);
        void ent.refresh();
      } catch {
        setSay('That did not save. Please try again.');
      } finally {
        setBusy(null);
      }
    },
    [me.user, ent.refresh],
  );

  // the page title and the heading take focus, like a new screen
  useEffect(() => {
    const before = document.title;
    document.title = 'Plans · Leafout';
    heading.current?.focus({ preventScroll: true });
    return () => {
      document.title = before;
    };
  }, []);

  const back = () => actions.closePricing();
  const backLabel = profile ? 'Back to your desk' : 'Back';
  const { offer, usage, resetsAt } = ent;

  if (!offer || !usage || !resetsAt) {
    const failed = ent.status === 'error';
    return (
      <Frame backLabel={backLabel} onBack={back} busy={!failed}>
        <section className="m-hero bl-hero bl-bare" aria-label="The plans">
          <p className="kicker">Plans</p>
          <h1 className="serif bl-title" ref={heading} tabIndex={-1}>
            {failed ? 'The plans could not be loaded.' : 'Getting the plans ready.'}
          </h1>
          <div className={`bl-status${failed ? ' is-problem' : ''}`} role="status" aria-live="polite">
            {failed && (
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

  return (
    <Frame backLabel={backLabel} onBack={back}>
      <PlanScreen
        plan={ent.plan}
        usage={usage}
        resetsAt={resetsAt}
        offer={offer}
        signedIn={!!me.user}
        pick={pick}
        onPick={setPick}
        notify={told ?? offer.notify}
        notifyBusy={busy}
        notifySay={say}
        onNotify={(plan, on) => void notify(plan, on)}
        onCarryOn={back}
        onBill={() => navigate(BILL_PATH)}
        now={now}
        headingRef={heading}
      />
    </Frame>
  );
}
