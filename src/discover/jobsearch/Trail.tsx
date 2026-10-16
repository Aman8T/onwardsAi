/**
 * The trail: how the search narrowed things down, one line per step as it arrives. Each line has a small stage
 * label, the server's sentence (written in code from counts, never by a model), the count when the sentence does not
 * already carry it, and, on the web-search steps, the searches a scout ran as quiet monospace lines.
 *
 * While the search runs the list is a polite live region, so a screen reader hears each step as it lands without
 * being interrupted. A new line enters with opacity and a short rise; nothing loops. Everything a scout or the
 * server wrote is a text node.
 */
import { motion, useReducedMotion } from 'motion/react';
import type { TrailStep } from '../../../shared/jobsearch';
import { EASE } from '../../lib/ui';
import { STAGE_LABEL } from './words';

export function Trail({ steps, live }: { steps: readonly TrailStep[]; live: boolean }) {
  const reduced = useReducedMotion() ?? false;
  return (
    <ol className="dc-js-trail" aria-label="How the search is narrowing things down" {...(live ? { 'aria-live': 'polite' as const, 'aria-relevant': 'additions' as const } : {})}>
      {steps.map((step, i) => {
        // a stage that repeats (three scouts, several cuts) is named once; screen readers still get it
        const repeat = i > 0 && steps[i - 1].stage === step.stage;
        // a count the sentence already carries ("1,268 roles") is not printed twice
        const showCount = step.count !== undefined && !step.text.replace(/,/g, '').includes(String(step.count));
        return (
          <motion.li
            key={i}
            className="dc-js-step"
            initial={live && !reduced ? { opacity: 0, y: 6 } : false}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, ease: EASE }}
          >
            <span className="dc-js-stage">{repeat ? <span className="sr-only">{STAGE_LABEL[step.stage]}</span> : STAGE_LABEL[step.stage]}</span>
            <div className="dc-js-step-body">
              <p className="dc-js-step-text">
                {step.text}
                {showCount && <span className="numeral dc-js-count"> {step.count}</span>}
              </p>
              {step.queries && step.queries.length > 0 && (
                <ul className="dc-js-queries" aria-label="Searches this scout ran">
                  {step.queries.map((q, j) => (
                    <li key={j} className="dc-js-query">
                      {q}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </motion.li>
        );
      })}
    </ol>
  );
}
