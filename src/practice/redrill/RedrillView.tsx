/**
 * Re-drill what wobbled (board 1 of the "Re-drill weak claims" canvas), presentational: the claims arrive as a prop, in
 * the server's order (code's: a ticked Fix step first, then Couldn't defend, then the oldest), and everything the person
 * does goes out as a callback. `RedrillScreen` feeds it from the server; the dev page feeds it from fixtures.
 *
 * Left, what comes back: one row per claim with its last verdict, how often it was asked, where it would open, and its Fix
 * step (done, or not yet). Short ticks the top 3, All up to 6; the person can untick any. Right, how to answer, the start,
 * the rules of a re-drill in four lines, and the Fix steps kept in Practice (ticked and taken out here). No score, no
 * streak: words and counts only.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { FixTask, RedrillClaim, RedrillView as RedrillData } from '../../../shared/interview';
import { plural } from '../../lib/ui';
import { VerdictChip } from '../Pips';
import { DAILY_INTERVIEWS, ErrorNote, LimitNotice, SampleNotice, freeLine } from '../hub/Notice';
import type { PracticeNav } from '../hub/nav';
import type { Load } from '../hub/useLoad';
import { NOTHING_SHARED, dateOf } from '../hub/words';
import type { AnswerPref } from '../speech';
import { REDRILL_MAX, REDRILL_MINUTES, REDRILL_RULES, REDRILL_SHORT, START_LABEL, timesWord } from '../words';
import '../../styles/practice.css';
import '../../styles/practice-room.css';

export interface RedrillViewProps {
  data: Load<RedrillData>;
  sample: boolean;
  unlimited?: boolean;
  answer: AnswerPref;
  starting: boolean;
  startError?: string;
  onAnswer: (answer: AnswerPref) => void;
  /** Start with these claim keys, in the order shown. */
  onStart: (keys: string[]) => void;
  /** Tick or untick a kept Fix step; rejects with the reason when it cannot. */
  onTick: (task: FixTask, done: boolean) => Promise<void>;
  /** Take a kept Fix step out; rejects with the reason when it cannot. */
  onRemove: (task: FixTask) => Promise<void>;
  onReload: () => void;
  nav: PracticeNav;
}

type Length = 'short' | 'all';

const topKeys = (claims: readonly RedrillClaim[], n: number) => new Set(claims.slice(0, n).map((c) => c.key));

export function RedrillView(props: RedrillViewProps) {
  const { data, sample, unlimited = false, nav } = props;
  const titleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  const claims = data.status === 'ready' ? data.data.claims : [];
  const left = data.status === 'ready' && !unlimited ? data.data.leftToday : undefined;

  return (
    <div className="pc-page pc-redrill">
      <header className="pc-head">
        <button type="button" className="pc-back" onClick={nav.hub}>
          <span aria-hidden="true">←</span> Practice
        </button>
        <p className="kicker">Practice · Re-drill</p>
        <h1 className="serif pc-title" ref={titleRef} tabIndex={-1}>
          Re-drill what wobbled
        </h1>
        <p className="pc-lede">The claims you were asked about and didn’t fully back up, from all your sessions. Each one comes back where it wobbled last time.</p>
      </header>

      {sample && <SampleNotice onAnalyse={nav.analyse} />}
      {left === 0 && <LimitNotice used={DAILY_INTERVIEWS} />}

      {data.status === 'loading' && (
        <p className="pc-muted" role="status">
          Finding what wobbled…
        </p>
      )}
      {data.status === 'error' && <ErrorNote message={data.message} onRetry={props.onReload} onBack={nav.hub} />}
      {data.status === 'ready' && claims.length === 0 && <Nothing kept={data.data.kept} {...props} />}
      {data.status === 'ready' && claims.length > 0 && <Ready key={claims.map((c) => c.key).join('|')} claims={claims} left={left} {...props} />}

      <footer className="pc-footer">
        <p>{NOTHING_SHARED}</p>
      </footer>
    </div>
  );
}

/* ───────────────────────── nothing wobbled ───────────────────────── */

function Nothing({ kept, nav, ...rest }: RedrillViewProps & { kept: readonly FixTask[] }) {
  return (
    <>
      <section className="panel pc-notice" aria-label="Nothing to re-drill">
        <div className="pc-notice-text">
          <p>Every claim you’ve been asked about is Defended. Practise something new to find the next one.</p>
        </div>
        <div className="pc-actions">
          <button type="button" className="btn pc-btn" onClick={nav.hub}>
            Back to Practice
          </button>
        </div>
      </section>
      {kept.length > 0 && <Kept kept={kept} nav={nav} {...rest} />}
    </>
  );
}

/* ───────────────────────── the list and the start ───────────────────────── */

function Ready({ claims, left, answer, starting, startError, onAnswer, onStart, unlimited = false, ...rest }: RedrillViewProps & { claims: readonly RedrillClaim[]; left: number | undefined }) {
  const listId = useId();
  const startId = useId();
  const [length, setLength] = useState<Length>('short');
  const [picked, setPicked] = useState<ReadonlySet<string>>(() => topKeys(claims, REDRILL_SHORT));
  const all = Math.min(claims.length, REDRILL_MAX);
  const chosen = claims.filter((c) => picked.has(c.key)).map((c) => c.key);
  const full = chosen.length >= REDRILL_MAX;
  const limited = left === 0;
  const ready = rest.data.status === 'ready' ? rest.data.data : undefined;
  const kept = ready?.kept ?? [];

  const pickLength = (next: Length) => {
    setLength(next);
    setPicked(topKeys(claims, next === 'all' ? REDRILL_MAX : REDRILL_SHORT));
  };
  const toggle = (key: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else if (next.size < REDRILL_MAX) next.add(key);
      return next;
    });

  const summary =
    chosen.length === 0
      ? 'Pick at least one claim'
      : `${plural(chosen.length, 'claim')} · about ${chosen.length * REDRILL_MINUTES} min · ${answer === 'speech' ? 'speaking' : 'typing'}`;

  return (
    <div className="pc-rd-cols">
      <section className="panel pc-sec pc-rd-list" aria-labelledby={listId}>
        <header className="pc-sec-head pc-sec-head-row">
          <div className="pc-sec-head">
            <p className="kicker">{plural(claims.length, 'claim')}</p>
            <h2 className="title pc-h2" id={listId}>
              What comes back
            </h2>
          </div>
          <p className="pc-muted pc-small pc-rd-order">Fix steps you did come first, then Couldn’t defend, then the oldest.</p>
        </header>
        <ul className="pc-rd-claims">
          {claims.map((c) => (
            <ClaimRow key={c.key} claim={c} on={picked.has(c.key)} disabled={!picked.has(c.key) && full} onToggle={() => toggle(c.key)} />
          ))}
        </ul>
        {claims.length > REDRILL_MAX && <p className="pc-muted pc-small">A re-drill asks at most {REDRILL_MAX}. The rest wait for the next one.</p>}
      </section>

      <aside className="pc-rd-side" aria-label="Start and the rules">
        <section className="panel pc-sec" aria-labelledby={startId}>
          <h2 className="title pc-h2" id={startId}>
            Start
          </h2>
          <div className="pc-rd-field">
            <p className="kicker">Length</p>
            <div className="rm-choices" role="group" aria-label="Length">
              <button type="button" className="rm-choice" aria-pressed={length === 'short'} onClick={() => pickLength('short')}>
                Short · top {Math.min(REDRILL_SHORT, claims.length)}
              </button>
              {all > REDRILL_SHORT && (
                <button type="button" className="rm-choice" aria-pressed={length === 'all'} onClick={() => pickLength('all')}>
                  All {all}
                </button>
              )}
            </div>
          </div>
          <div className="pc-rd-field">
            <p className="kicker">Answer by</p>
            <div className="rm-choices" role="group" aria-label="Answer by">
              <button type="button" className="rm-choice" aria-pressed={answer === 'speech'} onClick={() => onAnswer('speech')}>
                Speaking
              </button>
              <button type="button" className="rm-choice" aria-pressed={answer === 'type'} onClick={() => onAnswer('type')}>
                Typing
              </button>
            </div>
          </div>
          <p className="numeral pc-muted pc-small" aria-live="polite">
            {summary}
          </p>
          <div className="pc-actions">
            <button type="button" className="btn btn-primary pc-btn" disabled={chosen.length === 0 || starting || limited} onClick={() => onStart(chosen)}>
              {starting ? 'Starting…' : chosen.length === 0 ? 'Pick a claim to start' : 'Start the re-drill'}
            </button>
          </div>
          {startError && (
            <p className="pc-danger" role="alert">
              {startError}
            </p>
          )}
          <p className="pc-muted pc-small">{freeLine(left, unlimited)} · A re-drill counts as one.</p>
        </section>

        <section className="panel pc-sec" aria-label="How a re-drill differs">
          <h2 className="title pc-h3">How a re-drill differs</h2>
          <dl className="pc-rd-rules">
            {REDRILL_RULES.map((r) => (
              <div key={r.title} className="pc-rd-rule">
                <dt className="pc-strong">{r.title}</dt>
                <dd className="pc-muted">{r.body}</dd>
              </div>
            ))}
          </dl>
        </section>

        {kept.length > 0 && <Kept kept={kept} answer={answer} starting={starting} onAnswer={onAnswer} onStart={onStart} {...rest} />}
      </aside>
    </div>
  );
}

function ClaimRow({ claim, on, disabled, onToggle }: { claim: RedrillClaim; on: boolean; disabled: boolean; onToggle: () => void }) {
  const id = useId();
  const fix = claim.fix;
  return (
    <li className={`pc-rd-claim${on ? '' : ' is-off'}`}>
      <input id={id} type="checkbox" className="pc-rd-check" checked={on} disabled={disabled} onChange={onToggle} />
      <label htmlFor={id} className="pc-rd-body">
        <span className="pc-rd-text">
          <span className="pc-fact">“{claim.text}”</span>
          {claim.source && <span className="pc-cite">{claim.source}</span>}
        </span>
        <span className="pc-rd-meta">
          <VerdictChip verdict={claim.last.verdict} />
          <span className="pc-muted">
            Asked {timesWord(claim.times)} · last on {dateOf(claim.last.on)} · Starts at {START_LABEL[claim.start]}
          </span>
        </span>
        {fix && (
          <span className="pc-rd-meta">
            <span className={`chip${fix.doneAt ? ' pc-chip-have' : ''}`}>{fix.doneAt ? `Fix step done · ${dateOf(fix.doneAt)}` : `Fix step not done yet · about ${fix.minutes} min`}</span>
            <span className="pc-muted pc-small">{fix.text}</span>
          </span>
        )}
      </label>
    </li>
  );
}

/* ───────────────────────── kept in Practice ───────────────────────── */

function Kept({ kept, onTick, onRemove }: RedrillViewProps & { kept: readonly FixTask[] }) {
  const headingId = useId();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  const run = async (task: FixTask, act: () => Promise<void>) => {
    setBusy(task.id);
    setError(null);
    try {
      await act();
    } catch (err) {
      setError({ id: task.id, message: err instanceof Error && err.message ? err.message : 'That didn’t go through. Please try again.' });
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="panel pc-sec" aria-labelledby={headingId}>
      <p className="kicker">Kept in Practice</p>
      <h2 className="title pc-h3" id={headingId}>
        Your Fix steps
      </h2>
      <ul className="pc-rd-fixes">
        {kept.map((t) => (
          <FixRow key={t.id} task={t} busy={busy === t.id} error={error?.id === t.id ? error.message : undefined} onTick={(done) => void run(t, () => onTick(t, done))} onRemove={() => void run(t, () => onRemove(t))} />
        ))}
      </ul>
      <p className="pc-muted pc-small">Ticking a step changes no verdict. It puts its claim first in your next re-drill.</p>
    </section>
  );
}

function FixRow({ task, busy, error, onTick, onRemove }: { task: FixTask; busy: boolean; error?: string; onTick: (done: boolean) => void; onRemove: () => void }) {
  const id = useId();
  return (
    <li className="pc-rd-fix">
      <input id={id} type="checkbox" className="pc-rd-check" checked={!!task.doneAt} disabled={busy} onChange={(e) => onTick(e.target.checked)} />
      <label htmlFor={id} className="pc-rd-body">
        <span className={task.doneAt ? 'pc-muted pc-done' : ''}>{task.text}</span>
        <span className="pc-muted pc-small">
          For “{task.claimText}” · about {task.minutes} min
        </span>
      </label>
      <button type="button" className="btn btn-quiet btn-sm pc-btn-sm" disabled={busy} aria-label={`Take out: ${task.text}`} onClick={onRemove}>
        Take out
      </button>
      {error && (
        <p className="pc-danger pc-small" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}
