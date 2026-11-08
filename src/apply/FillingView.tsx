/**
 * Filling (canvas 3): the worker fills the employer's form behind a guard that cannot send anything. The form's
 * sections with static marks (done, now, to do: no spinner, nothing loops), the fields filled so far in a polite live
 * list, and the two ways on: leave, or review it once it waits for the person.
 *
 * "Ready: review it" is enabled only at `waiting_for_you`; before that it is aria-disabled with the reason. The screen
 * reads `detail.progress`, which the worker writes while the row is filling, and says honestly when there is none yet.
 */
import { useId } from 'react';
import type { ReactNode } from 'react';
import type { ApplicationDetail, ProgressSection, ProgressState } from '../../shared/apply';
import { IconCheck } from '../discover/roles/icons';
import { plural } from '../lib/ui';
import { STAGE_LABEL } from './api';
import { LIST_ROUTE, linkTo } from './route';
import type { Navigate } from './route';
import { sourceText } from './ReviewGroup';

export interface FillingViewProps {
  detail: ApplicationDetail;
  navigate: Navigate;
  /** Go on to the questions or the review; only called at `waiting_for_you`. */
  onReady: () => void;
}

const MARK: Record<ProgressState, ReactNode> = { done: <IconCheck size={14} />, now: '•', todo: '' };
const MARK_WORD: Record<ProgressState, string> = { done: 'Done', now: 'Now', todo: 'Still to do' };

/** The line under a section: its count, what waits for the person, and the worker's note. */
export function sectionLine(s: ProgressSection): string {
  const parts = [`${s.filled} of ${s.total}`];
  if (s.waiting) parts.push(`${s.waiting} ${s.waiting === 1 ? 'waits' : 'wait'} for you`);
  if (s.note) parts.push(s.note);
  return parts.join(' · ');
}

/** Why "Ready: review it" is off, or null when it is on. */
export function readyBlock(stage: ApplicationDetail['stage']): string | null {
  if (stage === 'waiting_for_you') return null;
  if (stage === 'queued') return 'It opens once the form is filled. It has not started yet.';
  return 'It opens once the form is filled.';
}

export function FillingView({ detail, navigate, onReady }: FillingViewProps) {
  const base = useId();
  const company = detail.company ?? detail.domain;
  const progress = detail.progress;
  const sections = progress?.sections ?? [];
  const filled = progress?.fields ?? [];
  const block = readyBlock(detail.stage);
  const started = detail.stage !== 'queued';
  const ready = block === null;

  return (
    <div className="ap-stack">
      <div className="ap-head">
        <p className="kicker">
          <a {...linkTo(LIST_ROUTE, navigate)}>Applications</a> · {[detail.title, company].filter(Boolean).join(' · ')}
        </p>
        <h1 className="serif ap-title">{ready ? `The ${company} form is filled.` : started ? `Filling the ${company} form.` : `Waiting to fill the ${company} form.`}</h1>
        <div className="ap-actions" role="status" aria-live="polite">
          <span className={`chip ap-chip ${ready ? 'ap-tone-you' : ''}`}>{ready ? 'Waiting for you' : started ? 'Filling' : STAGE_LABEL.queued}</span>
          <span className="chip ap-chip">Nothing has been sent</span>
          {!ready && <span className="chip ap-chip">Usually 1 to 2 minutes</span>}
        </div>
      </div>

      <div className="ap-split">
        <section className="panel ap-panel ar-steps-panel" aria-labelledby={`${base}-steps`}>
          <h2 id={`${base}-steps`} className="kicker">
            The form, section by section
          </h2>
          <ol className="ar-steps">
            <li className="ar-step">
              <span className={`ar-mark ar-mark-${started ? 'done' : 'now'}`} aria-hidden="true">
                {started ? MARK.done : MARK.now}
              </span>
              <div>
                <p className="ap-strong">
                  <span className="sr-only">{started ? MARK_WORD.done : MARK_WORD.now}: </span>
                  {started ? 'Opened the form' : 'Waiting for a browser'}
                </p>
                <p className="ap-src">{detail.domain}</p>
              </div>
            </li>
            {sections.map((s) => (
              <li key={s.label} className="ar-step">
                <span className={`ar-mark ar-mark-${s.state}`} aria-hidden="true">
                  {MARK[s.state]}
                </span>
                <div>
                  <p className="ap-strong">
                    <span className="sr-only">{MARK_WORD[s.state]}: </span>
                    {s.label}
                  </p>
                  <p className="ap-src numeral">{sectionLine(s)}</p>
                </div>
              </li>
            ))}
            <li className="ar-step">
              <span className="ar-mark ar-mark-todo" aria-hidden="true" />
              <div>
                <p className="ap-strong">
                  <span className="sr-only">{MARK_WORD.todo}: </span>
                  Resume
                </p>
                <p className="ap-src">Held back. Attached only when you approve.</p>
              </div>
            </li>
          </ol>
        </section>

        <section className="panel ap-panel ar-filled" aria-labelledby={`${base}-live`}>
          <div className="ap-ghead">
            <h2 id={`${base}-live`} className="kicker">
              Filled so far
            </h2>
            {progress && (
              <span className="ap-src numeral">
                {progress.filled} of {plural(progress.total, 'field')}
              </span>
            )}
          </div>
          <ul className="ar-fields" aria-live="polite" aria-relevant="additions">
            {filled.map((f) => (
              <li key={f.key} className="ar-fld">
                <span className="ap-q">{f.label}</span>
                <span className={f.value ? 'ap-v' : 'ap-v ap-muted'}>{f.value || 'Waiting for you'}</span>
                <span className="ap-src">{f.value ? sourceText(f, detail.resumeChoice) : 'Not in your details'}</span>
              </li>
            ))}
          </ul>
          {filled.length === 0 && <p className="ap-muted ap-small">{started ? 'The first fields show here as they are filled.' : 'Nothing is filled yet. The form opens when a browser is free.'}</p>}
        </section>
      </div>

      <section className="panel ap-panel ar-guard">
        <div className="ap-measure">
          <p className="ap-strong">The browser we fill in for you cannot send anything.</p>
          <p className="ap-muted ap-small">
            It can read the form and type into it. Sending is switched on only after you approve, only for this form's own site, and only for the values
            you saw.
          </p>
        </div>
        <div className="ap-actions">
          <a className="btn" {...linkTo(LIST_ROUTE, navigate)}>
            Leave and tell me when it's ready
          </a>
          <button
            type="button"
            className={ready ? 'btn btn-primary' : 'btn'}
            aria-disabled={ready ? undefined : 'true'}
            aria-describedby={block ? `${base}-ready` : undefined}
            onClick={() => {
              if (ready) onReady();
            }}
          >
            Ready: review it
          </button>
        </div>
        {block && (
          <p id={`${base}-ready`} className="ap-muted ap-small ar-guard-why">
            {block}
          </p>
        )}
      </section>
    </div>
  );
}
