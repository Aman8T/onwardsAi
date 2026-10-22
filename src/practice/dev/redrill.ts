/**
 * Re-drill fixtures for the dev page (I2): Riya Kapoor's claims that wobbled, as the "Re-drill weak claims" canvas drew them
 * (the LCP and React 18 claims with their Fix steps done in her plan, the Chromatic claim she could not defend, the bundle
 * split and a design story), a Fix step kept in Practice, her two Prepare plans, and a re-drill debrief: the golden fixture
 * the backend writes (`backend/tests/data/interview/redrill.debrief.json`, copied here), read through the same reader the
 * screen uses. Typed against the contracts, so a change to them breaks this file at compile time.
 */
import type { Debrief, FixTask, RedrillView } from '../../../shared/interview';
import type { PreparePlanSummary } from '../../../shared/prepare';
import { readDebrief } from '../read';
import golden from './redrill.debrief.json';
import { EARLIER_ID, SESSION_ID } from './fixtures';

export const PLAN_ID = '9f8e7d6c5b4a39281706f5e4d3c2b1a0';

export const PLANS: PreparePlanSummary[] = [
  { id: PLAN_ID, roleId: 'senior-frontend-engineer', roleTitle: 'Senior Frontend Engineer', createdAt: '2026-10-18T09:30:00Z' },
  { id: '0f1e2d3c4b5a69788796a5b4c3d2e1f0', roleId: 'design-engineer', roleTitle: 'Design Engineer', createdAt: '2026-10-14T08:00:00Z' },
];

const fix = (id: string, slotId: string, sessionId: string, claimText: string, text: string, minutes: number, extra: Partial<FixTask> = {}): FixTask => ({
  id,
  sessionId,
  slotId,
  claimText,
  text,
  minutes,
  where: 'plan',
  planId: PLAN_ID,
  addedAt: '2026-10-17T10:00:00Z',
  ...extra,
});

const LCP = 'LCP on the payment step dropped from 4.1s to 1.9s';
const REACT = 'Led the storefront migration to React 18 and Vite';

export const KEPT: FixTask = {
  id: 'c0ffee00c0ffee00c0ffee00c0ffee01',
  sessionId: SESSION_ID,
  slotId: 's5',
  claimText: 'A time you pushed back on a design',
  text: 'Write the result in one line: what shipped, and what changed after.',
  minutes: 10,
  where: 'practice',
  addedAt: '2026-10-17T10:05:00Z',
};

export const REDRILL_VIEW: RedrillView = {
  claims: [
    {
      key: 'lcp on the payment step dropped from 4.1s to 1.9s',
      text: LCP,
      source: 'Resume · line 15',
      best: 'shaky',
      last: { verdict: 'shaky', on: '2026-10-16', rung: 'push' },
      times: 2,
      start: 'push',
      fix: fix('c0ffee00c0ffee00c0ffee00c0ffee02', 's2', SESSION_ID, LCP, 'Find the pull request behind the LCP change and note the one change that moved it most.', 20, {
        doneAt: '2026-10-18T19:00:00Z',
      }),
    },
    {
      key: 'led the storefront migration to react 18 and vite',
      text: REACT,
      source: 'Resume · line 17',
      best: 'shaky',
      last: { verdict: 'shaky', on: '2026-10-12', rung: 'push' },
      times: 1,
      start: 'push',
      fix: fix('c0ffee00c0ffee00c0ffee00c0ffee03', 's4', EARLIER_ID, REACT, 'Write down what broke first in the migration and how you rolled it out.', 15, {
        doneAt: '2026-10-19T19:00:00Z',
      }),
    },
    {
      key: 'set up visual regression tests with chromatic for kite ui',
      text: 'Set up visual regression tests with Chromatic for Kite UI',
      source: 'Resume · line 18',
      best: 'couldnt',
      last: { verdict: 'couldnt', on: '2026-10-16', rung: 'floor' },
      times: 1,
      start: 'surface',
    },
    {
      key: 'split the storefront bundle by route',
      text: 'Split the storefront bundle by route',
      source: 'Resume · line 19',
      best: 'shaky',
      last: { verdict: 'shaky', on: '2026-10-12', rung: 'push' },
      times: 1,
      start: 'push',
    },
    {
      key: 'a time you pushed back on a design',
      text: 'Pairs with designers on Figma-to-code handoffs',
      source: 'Resume · line 22',
      best: 'shaky',
      last: { verdict: 'shaky', on: '2026-10-14', rung: 'push' },
      times: 1,
      start: 'push',
      fix: KEPT,
    },
  ],
  kept: [KEPT],
  leftToday: 2,
};

export const REDRILL_EMPTY: RedrillView = { claims: [], kept: [], leftToday: 3 };

export const DEBRIEF_REDRILL: Debrief = (() => {
  const read = readDebrief({ ...golden, sessionId: '3d4e5f60718293a4b5c6d7e8f90a1b2c' });
  if (!read) throw new Error('the re-drill golden fixture does not read as a debrief');
  return read;
})();
