/**
 * Re-drill what wobbled (board 1 of the "Re-drill weak claims" canvas), presentational: the claims arrive as a prop, in
 * the server's order (code's: a ticked Fix step first, then Couldn't defend, then the oldest), and everything the person
 * does goes out as a callback. `RedrillScreen` feeds it from the server; the dev page feeds it from fixtures.
 *
 * A notebook page of what to bring back: one ruled line per claim with a hand-drawn box to pick it (still a checkbox), its
 * last verdict, how often it was asked, where it would open, and its Fix step (done, or not yet). Short ticks the top 3,
 * All up to 6; the person can untick any. Beside it, how to answer, the rules of a re-drill in four lines, the Fix steps
 * kept in Practice (ticked and taken out here), and the one ink block that starts it. No score, no streak: words and counts
 * only.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { RefObject } from 'react';
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

/** A box drawn by hand in front of a line, which is still a real checkbox (the input lies over it, invisible). */
const BOX = [
  'M4.2 4.6c5-.7 10.2-.5 15.4-.1.4 5 .3 10-.1 15.1-5 .6-10.1.5-15.2.1C3.9 14.7 3.8 9.6 4.2 4.6z',
  'M4.5 4.2c5 .3 10-.2 15 .4-.3 5 .2 10.2-.3 15.1-5.1-.2-10 .4-15-.2.5-5-.1-10.2.3-15.3z',
] as const;
const CHECK = 'M6.5 12.5c1.8 1.6 3.2 3.4 4.4 5.4 2.2-5.6 5.6-10.4 10.6-14.6';

function BoxCheck({ id, checked, disabled, index, onChange }: { id: string; checked: boolean; disabled: boolean; index: number; onChange: (checked: boolean) => void }) {
  return (
    <span className={`tick pc-boxcheck${checked ? ' is-done' : ''}`}>
      <input id={id} type="checkbox" className="pc-rd-check" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path className="bx" d={BOX[index % 2]} />
        <path className="ck" d={CHECK} />
      </svg>
    </span>
  );
}

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
      {(sample || left === 0) && (
        <div className="pc-notices">
          {sample && <SampleNotice onAnalyse={nav.analyse} />}
          {left === 0 && <LimitNotice used={DAILY_INTERVIEWS} />}
        </div>
      )}

      {data.status === 'ready' && claims.length > 0 ? (
        <Ready key={claims.map((c) => c.key).join('|')} claims={claims} left={left} titleRef={titleRef} {...props} />
      ) : (
        <div className="m-screen is-stack pc-screen">
          <header className="m-hero pc-hero">
            <Heading titleRef={titleRef} nav={nav} />
          </header>
          {data.status === 'loading' && (
            <p className="pc-muted pc-gutter" role="status">
              Finding what wobbled…
            </p>
          )}
          {data.status === 'error' && <ErrorNote message={data.message} onRetry={props.onReload} onBack={nav.hub} />}
          {data.status === 'ready' && <Nothing kept={data.data.kept} {...props} />}
        </div>
      )}

      <footer className="pc-footer">
        <p>{NOTHING_SHARED}</p>
      </footer>
    </div>
  );
}

function Heading({ titleRef, nav }: { titleRef: RefObject<HTMLHeadingElement | null>; nav: PracticeNav }) {
  return (
    <>
      <button type="button" className="pc-back" onClick={nav.hub}>
        <span aria-hidden="true">←</span> Practice
      </button>
      <p className="kicker">Practice · Re-drill</p>
      <h1 className="serif pc-title" ref={titleRef} tabIndex={-1}>
        Re-drill what wobbled
      </h1>
      <p className="pc-lede">The claims you were asked about and didn’t fully back up, from all your sessions. Each one comes back where it wobbled last time.</p>
    </>
  );
}

/* ───────────────────────── nothing wobbled ───────────────────────── */

function Nothing({ kept, nav, ...rest }: RedrillViewProps & { kept: readonly FixTask[] }) {
  return (
    <>
      <section className="m-tile m-gap pc-notice" aria-label="Nothing to re-drill">
        <p className="pen">Nothing wobbled.</p>
        <div className="pc-notice-text">
          <p>Every claim you’ve been asked about is Defended. Practise something new to find the next one.</p>
        </div>
      </section>
      {kept.length > 0 && <Kept kept={kept} nav={nav} {...rest} />}
      <button type="button" className="m-tile m-ink pc-ink" onClick={nav.hub}>
        Back to Practice <span aria-hidden="true">→</span>
      </button>
    </>
  );
}

/* ───────────────────────── the list and the start ───────────────────────── */

function Ready({
  claims,
  left,
  titleRef,
  answer,
  starting,
  startError,
  onAnswer,
  onStart,
  unlimited = false,
  ...rest
}: RedrillViewProps & { claims: readonly RedrillClaim[]; left: number | undefined; titleRef: RefObject<HTMLHeadingElement | null> }) {
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
    <div className="m-screen pc-screen">
      <header className="m-hero pc-hero pc-held">
        <Heading titleRef={titleRef} nav={rest.nav} />
        <p className="pc-count">
          <span className="m-big is-hero">{claims.length}</span>
          <span className="pc-count-words">{claims.length === 1 ? 'claim to bring back' : 'claims to bring back'}</span>
        </p>
        <p className="pc-fine">Fix steps you did come first, then Couldn’t defend, then the oldest.</p>
      </header>

      <div className="m-mosaic is-12 pc-mosaic">
        <section className="m-tile m-paper pc-s12 pc-rd-page" aria-labelledby={listId}>
          <div className="m-page-head">
            <h2 className="m-page-title pen" id={listId}>
              What comes back
            </h2>
            <span className="m-fine">{plural(claims.length, 'claim')}</span>
          </div>
          <ul className="m-lines pc-rd-claims">
            {claims.map((c, i) => (
              <ClaimRow key={c.key} claim={c} index={i} on={picked.has(c.key)} disabled={!picked.has(c.key) && full} onToggle={() => toggle(c.key)} />
            ))}
          </ul>
          {claims.length > REDRILL_MAX && <p className="m-fine pc-rd-more">A re-drill asks at most {REDRILL_MAX}. The rest wait for the next one.</p>}
        </section>

        <section className="m-tile m-quiet pc-s12 pc-rd-start" aria-labelledby={startId}>
          <h2 className="title pc-h3" id={startId}>
            Start
          </h2>
          <div className="pc-rd-fields">
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
          </div>
          <p className="numeral pc-muted pc-small" aria-live="polite">
            {summary}
          </p>
          <p className="pc-fine">{freeLine(left, unlimited)} · A re-drill counts as one.</p>
        </section>

        <button type="button" className="m-tile m-ink pc-ink pc-s12" disabled={chosen.length === 0 || starting || limited} onClick={() => onStart(chosen)}>
          {starting ? 'Starting…' : chosen.length === 0 ? 'Pick a claim to start' : 'Start the re-drill'} <span aria-hidden="true">→</span>
        </button>
        {startError && (
          <p className="m-tile m-quiet pc-s12 pc-danger" role="alert">
            {startError}
          </p>
        )}

        <section className="m-tile m-quiet pc-s12 pc-rd-rules-tile" aria-label="How a re-drill differs">
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
      </div>
    </div>
  );
}

function ClaimRow({ claim, index, on, disabled, onToggle }: { claim: RedrillClaim; index: number; on: boolean; disabled: boolean; onToggle: () => void }) {
  const id = useId();
  const fix = claim.fix;
  return (
    <li className={`m-line pc-rd-claim${on ? '' : ' is-off'}`}>
      <BoxCheck id={id} checked={on} disabled={disabled} index={index} onChange={onToggle} />
      <label htmlFor={id} className="m-line-main pc-rd-body">
        <span className="pen pc-rd-pen">“{claim.text}”</span>
        {claim.source && <span className="m-cite">{claim.source}</span>}
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
    <section className="m-tile m-paper pc-s12 pc-rd-page" aria-labelledby={headingId}>
      <div className="m-page-head">
        <h2 className="m-page-title pen" id={headingId}>
          Your Fix steps
        </h2>
        <span className="m-fine">Kept in Practice</span>
      </div>
      <ul className="m-lines pc-rd-fixes">
        {kept.map((t, i) => (
          <FixRow key={t.id} task={t} index={i} busy={busy === t.id} error={error?.id === t.id ? error.message : undefined} onTick={(done) => void run(t, () => onTick(t, done))} onRemove={() => void run(t, () => onRemove(t))} />
        ))}
      </ul>
      <p className="m-fine pc-rd-more">Ticking a step changes no verdict. It puts its claim first in your next re-drill.</p>
    </section>
  );
}

function FixRow({ task, index, busy, error, onTick, onRemove }: { task: FixTask; index: number; busy: boolean; error?: string; onTick: (done: boolean) => void; onRemove: () => void }) {
  const id = useId();
  return (
    <li className="m-line pc-rd-fix">
      <BoxCheck id={id} checked={!!task.doneAt} disabled={busy} index={index + 1} onChange={onTick} />
      <label htmlFor={id} className="m-line-main pc-rd-body">
        <span className={`pen pc-rd-pen${task.doneAt ? ' pc-done' : ''}`}>{task.text}</span>
        <span className="m-cite">
          For “{task.claimText}” · about {task.minutes} min
        </span>
        {error && (
          <span className="pc-danger pc-small" role="alert">
            {error}
          </span>
        )}
      </label>
      <button type="button" className="btn btn-quiet btn-sm pc-btn-sm" disabled={busy} aria-label={`Take out: ${task.text}`} onClick={onRemove}>
        Take out
      </button>
    </li>
  );
}
