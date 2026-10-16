/**
 * The live trail (board "Searching" of the Discover canvas): what the search is doing, a step at a time. A step is done
 * once the server has said so (its sentence is written in code from counts, never by a model), the step the search is
 * in the middle of says so, and the stages still to come wait, quietly, in the order they will happen. The searches a
 * scout ran are shown as small monospace lines under their step.
 *
 * The list is a polite live region, so a screen reader hears each step as it lands without being interrupted. A new
 * row enters with opacity and a short rise; nothing loops. Everything a scout or the server wrote is a text node.
 */
import { motion, useReducedMotion } from 'motion/react';
import type { TrailStage, TrailStep } from '../../../shared/jobsearch';
import { EASE } from '../../lib/ui';
import { IconCheck } from '../roles/icons';
import { STAGE_LABEL } from './words';

type Mood = 'done' | 'now' | 'wait';

interface Row {
  key: string;
  label: string;
  /** A stage that repeats (several narrowing cuts) is named once; screen readers still get it. */
  repeat: boolean;
  mood: Mood;
  status: string;
  text: string;
  queries: readonly string[];
}

const ORDER: readonly TrailStage[] = ['plan', 'search', 'boards', 'narrow', 'read'];

const WAITING_TEXT: Record<'search' | 'boards' | 'narrow' | 'read', string> = {
  search: "Another search on employers' own job sites.",
  boards: 'Read the employer boards the searches found.',
  narrow: 'Keep the roles in your place that are your kind of role and within reach of your experience.',
  read: 'Read each posting against your evidence.',
};

/** "3 searches" in the plan's sentence tells how many scouts there will be. */
const plannedSearches = (steps: readonly TrailStep[]): number => {
  const plan = steps.find((s) => s.stage === 'plan');
  const m = plan ? /\b(\d+)\s+searches?\b/i.exec(plan.text) : null;
  return m ? Math.min(Number(m[1]), 6) : 0;
};

/** The rows of the trail: the steps that arrived, the one in progress, and the stages still to come. */
export function trailRows(steps: readonly TrailStep[], running: boolean): Row[] {
  const rows: Row[] = [];
  let searches = 0;
  steps.forEach((step, i) => {
    if (step.stage === 'search') searches++;
    const repeat = i > 0 && steps[i - 1].stage === step.stage && step.stage !== 'search';
    rows.push({
      key: `s${i}`,
      label: step.stage === 'search' ? `Search ${searches}` : STAGE_LABEL[step.stage],
      repeat,
      mood: 'done',
      status: 'done',
      text: step.text,
      queries: step.queries ?? [],
    });
  });
  if (!running || steps.some((s) => s.stage === 'result')) return rows;

  const reached = steps.reduce((hi, s) => Math.max(hi, ORDER.indexOf(s.stage)), -1);
  const pending: ('search' | 'boards' | 'narrow' | 'read')[] = [];
  // more scouts are still searching while the plan says there are more searches than have come back
  const planned = plannedSearches(steps);
  if (reached <= 1) for (let n = searches; n < planned; n++) pending.push('search');
  for (const stage of ORDER.slice(Math.max(reached + 1, 2)) as ('boards' | 'narrow' | 'read')[]) pending.push(stage);
  if (reached < 1 && planned === 0) pending.unshift('search');

  let searchNo = searches;
  pending.forEach((stage, j) => {
    if (stage === 'search') searchNo++;
    // the scouts search side by side, so every search still out is in progress; after them, one stage at a time
    const now = stage === 'search' || j === 0;
    rows.push({
      key: `p${j}`,
      label: stage === 'search' ? `Search ${searchNo}` : STAGE_LABEL[stage],
      repeat: false,
      mood: now ? 'now' : 'wait',
      status: now ? (stage === 'search' ? 'searching…' : 'working…') : 'waiting',
      text: WAITING_TEXT[stage],
      queries: [],
    });
  });
  return rows;
}

export function Trail({ steps, running }: { steps: readonly TrailStep[]; running: boolean }) {
  const reduced = useReducedMotion() ?? false;
  const rows = trailRows(steps, running);
  return (
    <ol className="dc-trail" aria-label="What the search is doing" aria-live="polite" aria-relevant="additions text">
      {rows.map((row, i) => (
        <motion.li
          key={row.key}
          className={`dc-trail-row is-${row.mood}`}
          initial={reduced ? false : { opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25, ease: EASE }}
        >
          <div className="dc-trail-mark" aria-hidden="true">
            <span className="dc-trail-dot">{row.mood === 'done' ? <IconCheck size={12} /> : row.mood === 'now' ? <span className="dc-trail-now" /> : null}</span>
            {i < rows.length - 1 && <span className="dc-trail-line" />}
          </div>
          <div className="dc-trail-body">
            <div className="dc-trail-head">
              <span className="kicker dc-trail-stage">{row.repeat ? <span className="sr-only">{row.label}</span> : row.label}</span>
              <span className="numeral dc-trail-status">{row.status}</span>
            </div>
            <p className="dc-trail-text">{row.text}</p>
            {row.queries.length > 0 && (
              <ul className="dc-queries" aria-label="Searches this scout ran">
                {row.queries.map((q, j) => (
                  <li key={j} className="dc-query numeral">
                    {q}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </motion.li>
      ))}
    </ol>
  );
}
