/**
 * The subscription panel (canvas "Subscribing, and past editions"): where the paper goes, which day, which parts, and
 * Save, Pause for a month, Unsubscribe. The address comes from the account and is shown, not edited here. A reader who
 * has never subscribed (status `off`) sees "Subscribe, free" in place of Save; one who is paused sees "Resume now".
 * `save` is injectable so the dev gallery runs without a server.
 */
import { useId, useState } from 'react';
import type { WeeklyDay, WeeklySection, WeeklySubscription, WeeklySubscriptionUpdate } from '../../shared/weekly';
import { saveSubscription } from './api';
import { shortDate } from './format';

const SECTIONS: Array<[WeeklySection, string]> = [
  ['jobs', 'Openings that fit your graph'],
  ['progress', 'Your week: plan, gaps, practice'],
  ['news', 'News from your field'],
  ['tip', 'A tip and a quote'],
];
const ALL: WeeklySection[] = ['jobs', 'progress', 'news', 'tip'];
const PAUSE_DAYS = 30;

export const PANEL_ID = 'wk-subscription';
export const UNSUBSCRIBE_ID = 'wk-unsubscribe';

export interface SubscriptionPanelProps {
  /** The reader's subscription; `null` while it is not known (the form then waits). */
  subscription: WeeklySubscription;
  save?: (update: WeeklySubscriptionUpdate) => Promise<WeeklySubscription>;
  onSaved: (next: WeeklySubscription) => void;
}

export function SubscriptionPanel({ subscription, save = saveSubscription, onSaved }: SubscriptionPanelProps) {
  const uid = useId();
  const off = subscription.status === 'off';
  const paused = subscription.status === 'paused';
  const [day, setDay] = useState<WeeklyDay>(subscription.day);
  const [sections, setSections] = useState<WeeklySection[]>(subscription.sections.length > 0 ? subscription.sections : ALL);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');

  const toggle = (s: WeeklySection) =>
    setSections((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : ALL.filter((x) => x === s || cur.includes(x))));

  async function run(update: WeeklySubscriptionUpdate, done: string) {
    setBusy(true);
    setError('');
    setNote('');
    setConfirming(false);
    try {
      const next = await save(update);
      onSaved(next);
      setDay(next.day);
      if (next.sections.length > 0) setSections(next.sections);
      setNote(done);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const noSections = sections.length === 0;

  return (
    <section className="panel wk-panel" id={PANEL_ID} aria-labelledby={`${uid}-h`} tabIndex={-1}>
      <p className="kicker">Subscription</p>
      <h2 className="serif wk-panel-h" id={`${uid}-h`}>
        Your weekly paper
      </h2>

      <form
        className="wk-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (noSections || busy) return;
          void run(
            { status: 'active', day, sections },
            off ? 'Subscribed. Your first edition arrives on the next ' + (day === 'mon' ? 'Monday.' : 'Friday.') : 'Saved.',
          );
        }}
      >
        <div className="wk-field">
          <label className="wk-label" htmlFor={`${uid}-email`}>
            Send it to
          </label>
          {subscription.email ? (
            <>
              <input id={`${uid}-email`} className="field" type="email" value={subscription.email} readOnly aria-describedby={`${uid}-email-note`} />
              <span className="wk-hint" id={`${uid}-email-note`}>
                The email of your Leafout account.
              </span>
            </>
          ) : (
            <>
              <input id={`${uid}-email`} className="field" value="No email on this account" readOnly aria-describedby={`${uid}-email-note`} />
              <span className="wk-hint" id={`${uid}-email-note`}>
                Without an email, editions are here in the app only.
              </span>
            </>
          )}
        </div>

        <div className="wk-field">
          <label className="wk-label" htmlFor={`${uid}-day`}>
            Day
          </label>
          <select id={`${uid}-day`} className="field wk-select" value={day} onChange={(e) => setDay(e.target.value === 'fri' ? 'fri' : 'mon')}>
            <option value="mon">Monday morning</option>
            <option value="fri">Friday morning</option>
          </select>
        </div>

        <fieldset className="wk-sections">
          <legend className="wk-label">In each edition</legend>
          {SECTIONS.map(([id, label]) => (
            <label className="wk-check" key={id}>
              <input type="checkbox" checked={sections.includes(id)} onChange={() => toggle(id)} />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>
        {noSections && (
          <p className="wk-warn" role="alert">
            Choose at least one part for the paper.
          </p>
        )}

        <div className="wk-actions">
          <button type="submit" className="btn btn-primary wk-btn" disabled={busy || noSections} aria-disabled={busy || noSections}>
            {busy ? 'Saving…' : off ? 'Subscribe, free' : paused ? 'Save and resume' : 'Save'}
          </button>
          {!off && !paused && (
            <button
              type="button"
              className="btn wk-btn"
              disabled={busy}
              onClick={() => void run({ status: 'paused', day, sections, pauseDays: PAUSE_DAYS }, 'Paused for a month.')}
            >
              Pause for a month
            </button>
          )}
          {paused && (
            <button
              type="button"
              className="btn wk-btn"
              disabled={busy}
              onClick={() => void run({ status: 'active', day, sections }, 'Back on. The next edition arrives on your day.')}
            >
              Resume now
            </button>
          )}
          {!off && !confirming && (
            <button type="button" className="btn btn-quiet wk-btn" id={UNSUBSCRIBE_ID} disabled={busy} onClick={() => setConfirming(true)}>
              Unsubscribe
            </button>
          )}
        </div>
      </form>

      {confirming && (
        <div className="wk-confirm" role="group" aria-label="Confirm unsubscribe">
          <p>Stop the weekly paper? Past editions stay here to read.</p>
          <div className="wk-actions">
            <button
              type="button"
              className="btn wk-btn"
              disabled={busy}
              onClick={() => void run({ status: 'off', day, sections }, 'Unsubscribed. No more editions will be sent.')}
            >
              Yes, unsubscribe
            </button>
            <button type="button" className="btn btn-quiet wk-btn" autoFocus onClick={() => setConfirming(false)}>
              Keep it
            </button>
          </div>
        </div>
      )}

      {paused && subscription.pausedUntil && <p className="wk-hint">Paused until {shortDate(subscription.pausedUntil)}.</p>}
      <p className="wk-status" role="status">
        {note}
      </p>
      {error && (
        <p className="wk-error" role="alert">
          {error}
        </p>
      )}
      <p className="wk-hint">Free while you have a Leafout account. Every email has a one-click unsubscribe.</p>
    </section>
  );
}
