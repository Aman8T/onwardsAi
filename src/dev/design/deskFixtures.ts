/**
 * What the dashboard's desk reads, as fixtures for `/dev/design?screen=dashboard`: a few weeks of practice, a plan, a
 * match, tracked roles (one with a note) and a conversation, dated back from today so the squares and the streak have
 * something to show. `?desk=empty` shows the desk of someone who has only the analysis.
 */
import type { PracticeHistory, SessionSummary, Verdict } from '../../../shared/interview';
import type { AiJob } from '../../../shared/jobsearch';
import type { TrackedJob } from '../../discover/applications/tracker';
import type { DashboardSeed } from '../../views/Dashboard';

const DAY_MS = 24 * 3600 * 1000;
const ago = (days: number, hour = 19): string => {
  const d = new Date(Date.now() - days * DAY_MS);
  d.setHours(hour, 10, 0, 0);
  return d.toISOString();
};
const day = (days: number): string => ago(days).slice(0, 10);

function session(id: string, days: number, title: string, verdicts: Verdict[], status: SessionSummary['status'] = 'done'): SessionSummary {
  const count = (v: Verdict) => verdicts.filter((x) => x === v).length;
  return {
    id,
    kind: 'resume',
    title,
    createdAt: ago(days),
    status,
    verdicts,
    counts: { defended: count('defended'), shaky: count('shaky'), couldnt: count('couldnt'), notAssessed: count('not_assessed') },
  };
}

const HISTORY: PracticeHistory = {
  sessions: [
    session('s5', 1, 'Defend your resume', ['defended', 'defended', 'defended', 'shaky', 'defended']),
    session('s4', 8, 'Behavioural stories', ['defended', 'defended', 'shaky', 'defended']),
    session('s3', 15, 'Defend your resume', ['defended', 'shaky', 'shaky', 'couldnt', 'defended']),
    session('s2', 22, 'Defend your resume', ['shaky', 'couldnt', 'defended', 'shaky']),
    session('s1', 36, 'Defend your resume', ['couldnt', 'shaky', 'shaky', 'couldnt']),
  ],
  claims: [
    { text: 'Rebuilt the checkout flow in React, cutting drop-off', source: 'Resume', best: 'defended', on: day(1), marks: [{ sessionId: 's5', date: day(1), verdict: 'defended' }] },
    { text: 'Led the design system used by four product teams', source: 'Resume', best: 'defended', on: day(8), marks: [{ sessionId: 's4', date: day(8), verdict: 'defended' }] },
    { text: 'Shipped motion guidelines adopted across the app', source: 'Portfolio', best: 'defended', on: day(15), marks: [{ sessionId: 's3', date: day(15), verdict: 'defended' }] },
    { text: 'Ran usability tests with 12 customers', source: 'Resume', best: 'shaky', on: day(1), marks: [{ sessionId: 's5', date: day(1), verdict: 'shaky' }] },
    { text: 'Improved Lighthouse performance on the marketing site', source: 'GitHub', best: 'couldnt', on: day(15), marks: [{ sessionId: 's3', date: day(15), verdict: 'couldnt' }] },
  ],
  facts: [],
  leftToday: 3,
};

function tracked(id: string, title: string, employer: string, stage: TrackedJob['stage'], savedDays: number, updatedDays: number, note = ''): TrackedJob {
  const job = { id, title, employer, place: { label: 'Bengaluru', precision: 'city' }, workMode: 'hybrid', checkedAt: ago(savedDays), applyUrl: '#', applyHost: 'job-boards.greenhouse.io', foundVia: 'web', fit: 'good', level: 'at', met: [], gaps: [], asks: { met: 4, partly: 1, notShown: 1 } };
  return {
    job: job as unknown as AiJob,
    stage,
    note,
    savedAt: ago(savedDays),
    updatedAt: ago(updatedDays),
    ...(stage === 'saved' ? {} : { appliedAt: day(updatedDays) }),
  };
}

export const DESK_SEED: DashboardSeed = {
  history: HISTORY,
  plans: [{ id: 'p1', roleId: 'design-engineer', roleTitle: 'Design Engineer', requirement: 'Accessibility audits', createdAt: ago(3) }],
  matches: [
    { id: 'm2', title: 'Senior Design Engineer', company: 'Razorpay', band: 'strong', createdAt: ago(5) },
    { id: 'm1', title: 'Product Designer', company: 'Zerodha', band: 'good', createdAt: ago(29) },
  ],
  tracked: [
    tracked('j1', 'Design Engineer', 'Postman', 'interviewing', 16, 2, 'Second round is a take-home. Ask Meera how long she had for hers.'),
    tracked('j2', 'Senior Design Engineer', 'Razorpay', 'applied', 6, 5),
    tracked('j3', 'Frontend Engineer, Design Systems', 'Atlassian', 'saved', 12, 12),
  ],
  threads: [
    {
      id: 't1',
      origin: { kind: 'free', title: 'Ask', caption: 'About you' },
      createdAt: Date.now() - 4 * DAY_MS,
      messages: [
        { role: 'user', text: 'Which of my projects should lead my resume?' },
        { role: 'assistant', text: 'The checkout rebuild: it is the one with a number.' },
      ],
    },
  ],
};

export const EMPTY_SEED: DashboardSeed = { history: { sessions: [], claims: [], facts: [] }, plans: [], matches: [], tracked: [], threads: [] };
