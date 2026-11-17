/**
 * The one panel every counted screen shows when a Just Looking count has run out (a 402 `limit_reached`, or the older
 * `no_credits` of a posting match, which is the `matches` count). It says what ran out in one serif line built from the
 * numbers, the count as pips and in words, when it comes back, what is safe, and the two ways on: Day One (a link to
 * the plan screen, where it is already chosen) or waiting.
 *
 * `LimitNote` reads the plan and the counts from `useEntitlements()` and shows nothing for an account with no limits
 * (Day One, or the server's unlimited list); `LimitPanel` only draws and takes everything as props (the dev gallery uses it).
 * No number or date is typed here: they are the limit the server sent, the counts it holds and the offer's price.
 */
import type { Feature, PlanOffer } from '../../shared/billing';
import { Pips } from '../components/materials';
import { FEATURE_KICKER, formatPrice, limitHeadline, limitReturns, utcDay } from './format';
import { COUNTED_LABEL, leftText, pipsOf } from './model';
import { PRICING_PATH } from './route';
import { dayOnePrice, useEntitlements } from './useEntitlements';
import '../styles/billing.css';

export interface LimitPanelProps {
  feature: Feature;
  /** How many Just Looking allows; the sentence leaves the number out when it is not known. */
  limit?: number;
  /** ISO timestamp: when the monthly count starts again; absent for `applications`. */
  resetsAt?: string;
  offer?: PlanOffer;
  /** What is safe, in one line ("Your posting is still in the box. Nothing was read and nothing was charged."). */
  safe?: string;
  /** Dismiss the panel ("Wait until 1 Dec"). */
  onWait?: () => void;
  className?: string;
}

export function LimitPanel({ feature, limit, resetsAt, offer, safe, onWait, className }: LimitPanelProps) {
  const returns = limitReturns(feature, limit, resetsAt);
  const use = limit !== undefined ? { limit, used: limit } : null;
  const pips = use ? pipsOf(use) : null;
  const onSale = offer?.dayOne !== undefined;
  const wait = resetsAt && feature !== 'applications' ? `Wait until ${utcDay(resetsAt)}` : 'Not now';
  return (
    <section className={`bl-limit m-plain${className ? ` ${className}` : ''}`} role="status" aria-live="polite" aria-label={`${FEATURE_KICKER[feature]}, Just Looking`}>
      <p className="kicker">{FEATURE_KICKER[feature]} · Just Looking</p>
      <h2 className="serif bl-limit-title">{limitHeadline(feature, limit)}</h2>
      {use && (
        <div className="bl-limit-row">
          <span>{COUNTED_LABEL[feature]}</span>
          <span className="bl-limit-end">
            {pips && <Pips pips={pips} r={4} gap={5} label={leftText(use)} />}
            <span>{leftText(use)}</span>
          </span>
        </div>
      )}
      {(safe || returns) && <p className="m-fine bl-limit-fine">{[safe, returns].filter(Boolean).join(' ')}</p>}
      <div className="bl-limit-actions">
        <a className="btn btn-primary" href={PRICING_PATH}>
          {onSale && offer ? <span className="numeral">Day One: no counting · {formatPrice(dayOnePrice(offer), 'INR')} once</span> : 'See the plans'}
        </a>
        {onWait && (
          <button type="button" className="btn btn-quiet" onClick={onWait}>
            {wait}
          </button>
        )}
      </div>
    </section>
  );
}

export interface LimitNoteProps {
  feature: Feature;
  /** From the 402 when it carried them; else the counts the server holds. */
  limit?: number;
  resetsAt?: string;
  safe?: string;
  onWait?: () => void;
  className?: string;
}

export function LimitNote({ feature, limit, resetsAt, safe, onWait, className }: LimitNoteProps) {
  const e = useEntitlements();
  // an account with no limits never reaches a count; say nothing rather than something wrong
  if (e.unlimited || e.plan === 'day_one') return null;
  return (
    <LimitPanel
      feature={feature}
      limit={limit ?? e.usage?.[feature].limit}
      resetsAt={feature === 'applications' ? undefined : (resetsAt ?? e.resetsAt)}
      offer={e.offer}
      safe={safe}
      onWait={onWait}
      className={className}
    />
  );
}
