/**
 * "How it narrowed it down": the funnel counts of a search, one box for each stage, and under them the trail with the
 * searches that were run. Every number is the server's count for this search; nothing is estimated here.
 *
 * Also the state where nothing fitted: the same counts, larger, the last one in the colour of a gap, so the person
 * sees where the roles dropped out, with the next steps that could change it.
 */
import { useId } from 'react';
import type { ReactNode } from 'react';
import type { FunnelStep, TrailStep } from '../../../shared/jobsearch';
import { STAGE_LABEL } from '../jobsearch/words';

const fmt = (n: number) => n.toLocaleString('en-IN');

/** The searches a scout ran and what each stage found, as a plain list (no markers: this is the finished search). */
function TrailList({ steps }: { steps: readonly TrailStep[] }) {
  return (
    <ul className="dc-trail-plain">
      {steps.map((t, i) => (
        <li key={i}>
          <p>
            <span className="kicker dc-trail-kick">{STAGE_LABEL[t.stage]}</span>
            {t.text}
          </p>
          {t.queries && t.queries.length > 0 && (
            <p className="dc-queries-line numeral">
              <span className="sr-only">Searches this scout ran: </span>
              {t.queries.join(' · ')}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

export function Funnel({ funnel, trail, note }: { funnel: readonly FunnelStep[]; trail: readonly TrailStep[]; note?: string }) {
  const id = useId();
  if (funnel.length === 0 && trail.length === 0) return null;
  return (
    <section className="panel dc-funnel" aria-labelledby={id}>
      <div className="dc-funnel-head">
        <span id={id} className="kicker">
          How it narrowed it down
        </span>
        <span className="dc-muted dc-small">Every count is from this search. The words on each card are quoted, never written by us.</span>
      </div>
      {funnel.length > 0 && (
        <ol className="dc-funnel-list" aria-label="How many roles were left after each stage">
          {funnel.map((step, i) => (
            <li key={i}>
              <span className="numeral dc-funnel-n">{fmt(step.count)}</span>
              <span className="dc-muted dc-funnel-label">{step.label}</span>
            </li>
          ))}
        </ol>
      )}
      {note && <p className="dc-muted dc-funnel-note">{note}</p>}
      {trail.length > 0 && (
        <details className="dc-details">
          <summary>Show the searches</summary>
          <TrailList steps={trail} />
        </details>
      )}
    </section>
  );
}

/** Nothing fitted this time: where the roles dropped out, and what could change that. It is the page's one heading. */
export function NothingFitted({ funnel, note, trail, children }: { funnel: readonly FunnelStep[]; note?: string; trail: readonly TrailStep[]; children?: ReactNode }) {
  return (
    <section className="panel dc-nothing" aria-label="Nothing fit this time">
      <div>
        <h1 className="serif dc-nothing-title" data-dc-h1 tabIndex={-1}>
          Nothing fit this time
        </h1>
        <p className="dc-muted">None of the roles read against your evidence fit after the checks. Here is where the rest dropped out.</p>
      </div>
      {funnel.length > 0 && (
        <ol className="dc-nothing-list" aria-label="How many roles were left after each stage">
          {funnel.map((step, i) => (
            <li key={i} className={i === funnel.length - 1 ? 'is-last' : undefined}>
              <span className="numeral dc-nothing-n">{fmt(step.count)}</span>
              <span className="dc-muted dc-funnel-label">{step.label}</span>
            </li>
          ))}
        </ol>
      )}
      {note && <p className="dc-muted">{note}</p>}
      {trail.length > 0 && (
        <details className="dc-details">
          <summary>Show the searches</summary>
          <TrailList steps={trail} />
        </details>
      )}
      {children && <div className="dc-actions">{children}</div>}
    </section>
  );
}
