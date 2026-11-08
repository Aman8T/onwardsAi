/**
 * The extra parts of a work sample's debrief: the task's checks and what each showed (written before the task began and
 * shown only now), what was seen of the assistant's use (facts, never a grade), and the person's own work.
 *
 * A check is Defended when the work, the test note or an answer showed it, and the quote that shows it is word for word
 * from there. A check with no quote says so plainly and is never described as the person's fault.
 */
import { useId, useState } from 'react';
import type { CheckResult, Debrief, WorkTask } from '../../../shared/interview';
import { VerdictChip } from '../Pips';

/** Whether a quote reads as code (set in the mono face) or as words. */
function looksLikeCode(quote: string, task: WorkTask | undefined): boolean {
  if (quote.includes('\n')) return true;
  return task?.materialKind === 'code' && /[{}();=<>]/.test(quote);
}

function Check({ check, task }: { check: CheckResult; task: WorkTask | undefined }) {
  return (
    <li className="pc-check">
      <div className="pc-check-head">
        <VerdictChip verdict={check.verdict} />
        <span className="pc-check-text">{check.check}</span>
      </div>
      {check.quote ? (
        looksLikeCode(check.quote, task) ? (
          <pre className="pc-code">{check.quote}</pre>
        ) : (
          <p className="pc-said">“{check.quote}”</p>
        )
      ) : (
        <p className="pc-muted pc-small">{check.verdict === 'not_assessed' ? 'Not assessed never counts against you.' : 'Nothing in your work or your answers showed this one.'}</p>
      )}
    </li>
  );
}

export function ChecksPanel({ debrief }: { debrief: Debrief }) {
  const headingId = useId();
  const checks = debrief.checks ?? [];
  if (checks.length === 0 && !debrief.assistantUse) return null;
  return (
    <section className="panel pc-sec" aria-labelledby={headingId}>
      <header className="pc-sec-head">
        <p className="kicker">The checks</p>
        <h2 className="title pc-h2" id={headingId}>
          What this task was looking for
        </h2>
        <p className="pc-muted">Written before you started, shown now. Each one is Defended when your work or your words showed it.</p>
      </header>
      {checks.length > 0 && (
        <ul className="pc-checks">
          {checks.map((c, i) => (
            <Check key={i} check={c} task={debrief.task} />
          ))}
        </ul>
      )}
      {debrief.assistantUse && (
        <div className="pc-block">
          <div className="kicker">How you used the assistant</div>
          <p>{debrief.assistantUse}</p>
          <p className="pc-muted pc-small">Seen, not graded.</p>
        </div>
      )}
    </section>
  );
}

/** The task and the person's work, folded away until asked for. */
export function WorkPanel({ debrief }: { debrief: Debrief }) {
  const base = useId();
  const [open, setOpen] = useState(false);
  const { task, work } = debrief;
  if (!task && !work) return null;
  return (
    <section className="panel pc-sec" aria-labelledby={`${base}-h`}>
      <header className="pc-sec-head">
        <p className="kicker">The task</p>
        <h2 className="title pc-h2" id={`${base}-h`}>
          {task?.task ?? 'Your work'}
        </h2>
      </header>
      {work && (
        <>
          <div>
            <button type="button" className="btn btn-sm pc-btn-sm" aria-expanded={open} aria-controls={`${base}-work`} onClick={() => setOpen((o) => !o)}>
              {open ? 'Hide your work' : 'Show your work'}
            </button>
          </div>
          {open && (
            <div id={`${base}-work`}>
              {task && <p className="pc-cite">{task.materialName}</p>}
              <pre className="pc-code">{work}</pre>
            </div>
          )}
        </>
      )}
    </section>
  );
}
