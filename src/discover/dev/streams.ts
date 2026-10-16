/**
 * Scripted streams for the dev gallery: `POST /api/jobsearch` as a script, so the screen runs with no server, no account
 * and no spend. The roles are the real run of 2026-10-16 (`fixtures.ts`).
 *
 * `scriptedStream` plays a script through the real reader (`readJobSearchEvent`), one JSON round trip per line, so the
 * gallery exercises the same defensive parsing as the app: the script holds a few lines that must be dropped (an unknown
 * type, a stage that does not exist, a role whose apply address is `javascript:`), and none of them may reach the
 * screen. A script can also refuse before the stream starts (the HTTP failures) or stop and wait, which is how the
 * "searching" state is held still for a look.
 */
import type { JobSearchEvent } from '../../../shared/jobsearch';
import { JobSearchApiError, readJobSearchEvent } from '../jobsearch/api';
import type { JobSearchStream } from '../jobsearch/api';
import { BOARDS, DRIVETRAIN, FUNNEL, GITLAB, HEADOUT, NARROW, NOTHING_FUNNEL, OPENFX, PARTICLE, PLAN, READ, ROLES, RUN_ID, SEARCH_1, SEARCH_2, SEARCH_3, SONICWALL, SONICWALL_PDE, NEURON7, searchedToday } from './fixtures';

export type Beat =
  | JobSearchEvent
  /** A line as it came off the wire, not trusted: it goes through the reader like any other. */
  | { raw: unknown }
  /** Pause, in milliseconds (scaled by the stream's pace). */
  | { wait: number }
  /** Say nothing more until the run is cancelled. */
  | { hang: true }
  /** Refuse before the stream starts, as the server does with a JSON `{ error }` body. */
  | { refuse: { status: number; message: string; retryAfter?: number } };

const aborted = () => new DOMException('The search was cancelled', 'AbortError');

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(aborted());
    const t = window.setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(t);
        reject(aborted());
      },
      { once: true },
    );
  });
}

/** `pace` scales every pause: 1 is the script's own timing, 0 plays it all at once. */
export function scriptedStream(script: readonly Beat[], pace = 1): JobSearchStream {
  return async (_req, onEvent, signal) => {
    for (const beat of script) {
      if (signal?.aborted) throw aborted();
      if ('refuse' in beat) throw new JobSearchApiError(beat.refuse.message, beat.refuse.status, beat.refuse.retryAfter);
      if ('wait' in beat) {
        if (pace > 0) await sleep(beat.wait * pace, signal);
        continue;
      }
      if ('hang' in beat) {
        await new Promise<never>((_, reject) => {
          if (signal?.aborted) return reject(aborted());
          signal?.addEventListener('abort', () => reject(aborted()), { once: true });
        });
        continue;
      }
      const line = 'raw' in beat ? beat.raw : beat;
      const event = readJobSearchEvent(JSON.parse(JSON.stringify(line)));
      if (event) onEvent(event);
    }
  };
}

/* ───────────── the scripts ───────────── */

const step = (s: (typeof PLAN)): Beat => ({ type: 'step', step: s });
const job = (j: (typeof ROLES)[number]): Beat => ({ type: 'job', job: j });

/** The plan and the first two searches. */
const planAndSearches: Beat[] = [
  step(PLAN),
  { wait: 600 },
  step(SEARCH_1),
  { raw: { type: 'mystery', payload: 'a line this client does not know' } },
  { raw: { type: 'step', step: { stage: 'warp', text: 'a stage that does not exist' } } },
  { wait: 700 },
  step(SEARCH_2),
  { wait: 600 },
];

/** Tayyari's own employers answer first, a few seconds in. */
const firstRoles: Beat[] = [
  job(GITLAB),
  // an apply address that is not a plain web address: this role must never reach the screen
  { raw: { type: 'job', job: { ...OPENFX, id: 'bad-link', title: 'Should never show', applyUrl: 'javascript:alert(1)' } } },
  { wait: 1200 },
];

const rest: Beat[] = [
  step(SEARCH_3),
  { wait: 900 },
  step(BOARDS),
  { wait: 500 },
  step(NARROW),
  { wait: 400 },
  step(READ),
  { wait: 800 },
  job(HEADOUT),
  { wait: 300 },
  job(SONICWALL),
  { wait: 300 },
  job(DRIVETRAIN),
  { wait: 400 },
  job(PARTICLE),
  { wait: 400 },
  job(NEURON7),
  { wait: 300 },
  job(SONICWALL_PDE),
  { wait: 300 },
  job(OPENFX),
  { wait: 400 },
];

/** A whole run, eight roles, in about twelve seconds. */
export const FULL_RUN: Beat[] = [
  ...planAndSearches,
  ...firstRoles,
  ...rest,
  { type: 'step', step: { stage: 'result', text: 'Worked out the fit for 8 roles from the quotes that checked out against the postings and your evidence.' } },
  { type: 'done', jobs: ROLES.length, funnel: FUNNEL, runId: RUN_ID, createdAt: searchedToday() },
];

/** Held still about eight seconds in: two searches back, the third still out, the first role in. */
export const MIDWAY: Beat[] = [...planAndSearches, ...firstRoles, { hang: true }];

/** Nothing fitted: the funnel is the answer. */
export const NOTHING_FITTED: Beat[] = [
  step(PLAN),
  step(SEARCH_1),
  step(SEARCH_2),
  step(SEARCH_3),
  step(BOARDS),
  step(NARROW),
  step(READ),
  { type: 'done', jobs: 0, funnel: NOTHING_FUNNEL, runId: RUN_ID, createdAt: searchedToday() },
];

/** Stops on the server's `error` event after some work was done. */
export const FAILS_MIDWAY: Beat[] = [
  step(PLAN),
  step(SEARCH_1),
  step(SEARCH_2),
  { type: 'error', message: 'The search stopped: the model did not answer.' },
];

export const refused = (status: number, message: string, retryAfter?: number): Beat[] => [{ refuse: { status, message, retryAfter } }];
