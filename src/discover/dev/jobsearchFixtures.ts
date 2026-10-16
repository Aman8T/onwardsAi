/**
 * Fixtures for "Search with AI" in the dev gallery (`/dev/discover?state=ai-…`): the stream of `POST /api/jobsearch`
 * as a script, so the screen runs with no server, no account and no spend. All employers are invented, in the same
 * world as `fixtures.ts` (Riya, a frontend engineer in Bengaluru with about two and a half years).
 *
 * `scriptedStream` plays a script through the real reader (`readJobSearchEvent`), one JSON round trip per line, so
 * the gallery exercises the same defensive parsing as the app: the script holds a few lines that must be dropped
 * (an unknown type, a stage that does not exist, a role whose apply address is `javascript:`), and none of them may
 * reach the screen. A script can also refuse before the stream starts (the HTTP failures) or stop and wait, which is
 * how the "running" state is held still for a look.
 */
import type { AiJob, JobSearchEvent } from '../../../shared/jobsearch';
import { JobSearchApiError, readJobSearchEvent } from '../jobsearch/api';
import type { JobSearchStream } from '../jobsearch/api';
import { ago } from './fixtures';

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

/* ───────────────────────── the roles ───────────────────────── */

const GH = 'job-boards.greenhouse.io';
const LEVER = 'jobs.lever.co';

const jobBase = {
  workMode: 'hybrid' as const,
  quickFit: { word: 'worth_a_look' as const, named: 8, matched: 6, notFound: [], reasons: [] },
};

/** Arrives second, but is only a "good" fit: it sits under the strong ones that come later. */
const gHarbour: AiJob = {
  ...jobBase,
  id: 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
  title: 'Frontend Engineer, Design Systems',
  employer: 'Harbourline',
  place: { label: 'Indiranagar, Bengaluru', city: 'Bengaluru', locality: 'Indiranagar', precision: 'locality', km: 8 },
  postedAt: ago(3),
  checkedAt: ago(0),
  applyUrl: `https://${GH}/harbourline/jobs/5120031`,
  applyHost: GH,
  foundVia: 'web',
  foundBy: 'site:boards.greenhouse.io "design systems" frontend engineer Bengaluru',
  fit: 'good',
  level: 'at',
  levelQuote: '2+ years of experience building UI with React',
  yearsAsked: 2,
  met: [
    {
      ask: 'Experience building and maintaining a component library in React and TypeScript',
      cite: { source: 'resume', text: 'Built and maintained a React and TypeScript component library used by 4 product teams' },
      partly: false,
    },
    {
      ask: 'You care about accessibility and can test for it',
      cite: { source: 'GitHub', text: 'riya/ui-kit: keyboard and screen reader tests with Testing Library' },
      partly: true,
    },
  ],
  gaps: ['Experience with Storybook visual regression testing (Chromatic or similar)'],
  salary: { text: '₹22–30 lakh a year' },
};

/** Arrives first with the stretch: a staff role that asks for far more years. */
const gKestrel: AiJob = {
  ...jobBase,
  id: 'b2c3d4e5f60718293a4b5c6d7e8f9001',
  title: 'Staff Frontend Engineer',
  employer: 'Kestrel Pay',
  place: { label: 'Koramangala, Bengaluru', city: 'Bengaluru', locality: 'Koramangala', precision: 'locality', km: 4 },
  checkedAt: ago(0),
  applyUrl: `https://${LEVER}/kestrelpay/6f1c7e0a-93b4-4d3e-8a55-0c1b2d3e4f50`,
  applyHost: LEVER,
  foundVia: 'tayyari',
  fit: 'stretch',
  level: 'step_up',
  levelQuote: '8+ years of professional frontend experience, including leading a team',
  yearsAsked: 8,
  met: [
    {
      ask: 'Deep expertise in React and TypeScript',
      cite: { source: 'resume', text: 'React and TypeScript for every product surface at Lumen, 2 years' },
      partly: true,
    },
  ],
  gaps: ['Lead and mentor a team of six or more engineers', 'Own the frontend architecture across several product lines', 'Experience with GraphQL federation at scale'],
};

const gCedar: AiJob = {
  ...jobBase,
  id: 'c3d4e5f60718293a4b5c6d7e8f900112',
  title: 'Frontend Engineer',
  employer: 'Cedar Cloud',
  place: { label: 'HSR Layout, Bengaluru', city: 'Bengaluru', locality: 'HSR Layout', precision: 'locality', km: 1 },
  workMode: 'onsite',
  postedAt: ago(1),
  checkedAt: ago(0),
  applyUrl: `https://${LEVER}/cedarcloud/2b7d9e4c-18aa-4f60-b3c1-7d5e6f708192`,
  applyHost: LEVER,
  foundVia: 'web',
  foundBy: 'site:jobs.lever.co react typescript frontend engineer Bengaluru OR remote India',
  fit: 'strong',
  level: 'at',
  levelQuote: '2 to 4 years of experience',
  yearsAsked: 2,
  met: [
    {
      ask: 'Strong React and TypeScript, with hooks and state management',
      cite: { source: 'resume', text: 'React 18 with hooks, Redux Toolkit and React Query; TypeScript strict mode throughout' },
      partly: false,
    },
    {
      ask: 'Write unit and integration tests with Jest and Testing Library',
      cite: { source: 'resume', text: 'Raised test coverage on the checkout flow from 38% to 81% with Jest and Testing Library' },
      partly: false,
    },
    {
      ask: 'Comfortable with CI pipelines and code review',
      cite: { source: 'GitHub', text: 'riya/ui-kit: GitHub Actions workflow running lint, tests and a build on every pull request' },
      partly: false,
    },
  ],
  gaps: ['Familiarity with Next.js server components'],
};

const gLumen: AiJob = {
  ...jobBase,
  id: 'd4e5f60718293a4b5c6d7e8f90011223',
  title: 'Senior Frontend Engineer',
  employer: 'Lumen Retail',
  place: { label: 'Remote, India', precision: 'country' },
  workMode: 'remote',
  postedAt: ago(6),
  checkedAt: ago(0),
  applyUrl: `https://${GH}/lumenretail/jobs/5098872`,
  applyHost: GH,
  foundVia: 'web',
  foundBy: 'site:boards.greenhouse.io senior frontend engineer remote India react',
  fit: 'strong',
  level: 'step_up',
  levelQuote: '4+ years building production web applications',
  yearsAsked: 4,
  met: [
    {
      ask: 'Production experience with React and a typed language',
      cite: { source: 'resume', text: 'Shipped the React and TypeScript checkout rewrite to 1.2M monthly users' },
      partly: false,
    },
    {
      ask: 'Improve performance and Core Web Vitals',
      cite: { source: 'resume', text: 'Cut largest contentful paint on the product page from 4.1 s to 2.3 s by splitting the bundle' },
      partly: false,
    },
  ],
  gaps: ['4+ years of professional experience'],
  salary: { text: '₹30–40 lakh a year' },
};

const gSaffron: AiJob = {
  ...jobBase,
  id: 'e5f60718293a4b5c6d7e8f9001122334',
  title: 'UI Engineer, Dispatch Console',
  employer: 'Saffron Logistics',
  place: { label: 'Bengaluru', city: 'Bengaluru', precision: 'city' },
  checkedAt: ago(0),
  applyUrl: `https://${LEVER}/saffronlogistics/9a8b7c6d-5e4f-4321-8765-4a3b2c1d0e9f`,
  applyHost: LEVER,
  foundVia: 'tayyari',
  fit: 'good',
  level: 'unclear',
  met: [
    {
      ask: 'Build dense, data-heavy interfaces with React',
      cite: { source: 'resume', text: 'Built the admin console for 40k SKUs: virtualised tables, filters and bulk edits' },
      partly: false,
    },
  ],
  gaps: ['Work with maps and real-time vehicle data', 'Experience with WebSockets'],
};

export const ROLES = { gHarbour, gKestrel, gCedar, gLumen, gSaffron };

/* ───────────────────────── the scripts ───────────────────────── */

const QUERIES = [
  ['site:boards.greenhouse.io "design systems" frontend engineer Bengaluru', 'site:boards.greenhouse.io react typescript engineer Bengaluru hybrid'],
  ['site:jobs.lever.co react typescript frontend engineer Bengaluru OR remote India', 'site:jobs.lever.co "frontend" "2 years" Bengaluru'],
  ['site:boards.greenhouse.io senior frontend engineer remote India react', 'site:jobs.lever.co ui engineer react remote India'],
];

const planAndSearch: Beat[] = [
  { type: 'step', step: { stage: 'plan', text: 'Read your profile: frontend engineer, about 2.5 years, Bengaluru. Next roles: senior frontend engineer, design engineer.' } },
  { wait: 700 },
  { type: 'step', step: { stage: 'search', text: 'First scout looked for employers hiring in your kind of work in Bengaluru.', queries: QUERIES[0] } },
  { wait: 600 },
  { type: 'step', step: { stage: 'search', text: 'Second scout looked at Lever boards for React and TypeScript roles.', queries: QUERIES[1] } },
  { raw: { type: 'mystery', payload: 'a line this client does not know' } },
  { raw: { type: 'step', step: { stage: 'warp', text: 'a stage that does not exist' } } },
  { wait: 600 },
  { type: 'step', step: { stage: 'search', text: 'Third scout looked for remote roles open to India.', queries: QUERIES[2] } },
  { wait: 800 },
];

const boardsAndNarrow: Beat[] = [
  { type: 'step', step: { stage: 'boards', text: "Found 14 employers' boards from the searches and read them, with Tayyari's own 51: 1,268 roles are listed today.", count: 1268 } },
  { wait: 900 },
  { type: 'step', step: { stage: 'narrow', text: 'In Bengaluru, or remote in India: 212 roles.', count: 212 } },
  { wait: 500 },
  { type: 'step', step: { stage: 'narrow', text: 'Of your kind of work: 61 roles.', count: 61 } },
  { wait: 500 },
  { type: 'step', step: { stage: 'narrow', text: 'At your level or a step up: 23 roles.', count: 23 } },
  { wait: 700 },
  { type: 'step', step: { stage: 'read', text: 'Reading 23 postings against your evidence, a few at a time.', count: 23 } },
  { wait: 1200 },
];

/** The first batch: a "good" and a "stretch" arrive before the strong ones. */
const firstBatch: Beat[] = [
  { type: 'job', job: gHarbour },
  { wait: 300 },
  { type: 'job', job: gKestrel },
  // an apply address that is not a plain web address: this role must never reach the screen
  { raw: { type: 'job', job: { ...gCedar, id: 'bad-link', title: 'Should never show', applyUrl: 'javascript:alert(1)' } } },
  { wait: 1400 },
];

const secondBatch: Beat[] = [
  { type: 'job', job: gCedar },
  { wait: 300 },
  { type: 'job', job: gLumen },
  { wait: 1200 },
  { type: 'job', job: gSaffron },
  { wait: 600 },
];

const FUNNEL = [
  { label: "Employers' boards read", count: 65 },
  { label: 'Roles listed today', count: 1268 },
  { label: 'In Bengaluru or remote in India', count: 212 },
  { label: 'Of your kind of work', count: 61 },
  { label: 'At your level or a step up', count: 23 },
  { label: 'Read against your evidence', count: 23 },
  { label: 'Shown', count: 5 },
];

/** A whole run, 5 roles. */
export const FULL_RUN: Beat[] = [
  ...planAndSearch,
  ...boardsAndNarrow,
  ...firstBatch,
  ...secondBatch,
  { type: 'step', step: { stage: 'result', text: 'Worked out the fit for 5 roles from the quotes that checked out against the postings and your evidence.' } },
  { type: 'done', jobs: 5, funnel: FUNNEL, note: 'Two postings were left out because they could not be read in full.' },
];

/** Held still part-way: the trail has grown, two roles are in, and nothing more arrives. */
export const MIDWAY: Beat[] = [...planAndSearch, ...boardsAndNarrow, ...firstBatch, { hang: true }];

/** Nothing fitted: the funnel is the answer. */
export const NOTHING_FITTED: Beat[] = [
  ...planAndSearch,
  { type: 'step', step: { stage: 'boards', text: "Found 9 employers' boards from the searches and read them, with Tayyari's own 51: 904 roles are listed today.", count: 904 } },
  { type: 'step', step: { stage: 'narrow', text: 'In Coimbatore, or remote in India: 31 roles.', count: 31 } },
  { type: 'step', step: { stage: 'narrow', text: 'Of your kind of work: 4 roles.', count: 4 } },
  { type: 'step', step: { stage: 'narrow', text: 'At your level or a step up: 0 roles.', count: 0 } },
  {
    type: 'done',
    jobs: 0,
    funnel: [
      { label: "Employers' boards read", count: 60 },
      { label: 'Roles listed today', count: 904 },
      { label: 'In Coimbatore or remote in India', count: 31 },
      { label: 'Of your kind of work', count: 4 },
      { label: 'At your level or a step up', count: 0 },
      { label: 'Shown', count: 0 },
    ],
    note: 'Four accounting roles are open in Coimbatore today, and all four ask for five or more years.',
  },
];

/** Stops on the server's `error` event after some work was done. */
export const FAILS_MIDWAY: Beat[] = [
  ...planAndSearch,
  ...boardsAndNarrow.slice(0, 2),
  { type: 'job', job: gCedar },
  { type: 'error', message: 'The search could not finish reading postings. The roles above were checked in full.' },
];

export const refused = (status: number, message: string, retryAfter?: number): Beat[] => [{ refuse: { status, message, retryAfter } }];
