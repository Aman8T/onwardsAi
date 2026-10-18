/**
 * Practice's address. The screen lives in the query string so a view can be linked to, reloaded and stepped back through:
 *
 *   /practice                                                     the hub: what is coming up, the four kinds, past sessions
 *   /practice?screen=setup&kind=role&run=…&job=…                  the plan for one role of a search with AI, before it starts
 *   /practice?screen=setup&kind=role&graphRole=…                  the plan for one role of the person's graph (no posting)
 *   /practice?screen=setup&kind=resume                            the plan for a kind that needs no role (resume, stories, work)
 *   /practice?screen=room&session=…                               an interview in progress (or carried on after a reload)
 *   /practice?screen=debrief&session=…                            what an interview showed, question by question
 *   /practice?screen=progress                                     what carries over: claims at their best, facts from you
 *
 * A screen keeps only the fields it uses, and a screen without them is the hub. `run` and `job` name a role of a search
 * with AI (`RoleSource`); `graphRole` names a role of the person's graph (`GraphRoleSource`, `Profile.roles[].id`). The
 * setup screen for `role` needs one of the two (a search's role when it has both), the one for `work` may have `run` and
 * `job` (the task's family then comes from the role's title).
 *
 * Pure functions only (the store imports this file), so nothing here may touch the store or the DOM.
 */
import type { InterviewKind } from '../../shared/interview';

export const PRACTICE_PATH = '/practice';

export type PracticeScreen = 'hub' | 'setup' | 'room' | 'debrief' | 'progress';

export interface PracticeQuery {
  screen: PracticeScreen;
  /** `screen=setup`: which kind of practice. */
  kind?: InterviewKind;
  /** `screen=setup`: the search the role came from. */
  run?: string;
  /** `screen=setup`: the role. */
  job?: string;
  /** `screen=setup` with `kind=role`: a role of the person's graph, instead of `run` and `job`. */
  graphRole?: string;
  /** `screen=room` and `screen=debrief`: the interview. */
  session?: string;
}

export const DEFAULT_PRACTICE: PracticeQuery = { screen: 'hub' };

const SCREENS: readonly PracticeScreen[] = ['hub', 'setup', 'room', 'debrief', 'progress'];
const KINDS: readonly InterviewKind[] = ['role', 'resume', 'stories', 'work'];
const ID = /^[A-Za-z0-9_-]{1,64}$/;

const idOf = (value: string | null | undefined): string | undefined => (value !== null && value !== undefined && ID.test(value) ? value : undefined);

export function isPracticePath(pathname: string): boolean {
  return pathname.replace(/\/+$/, '') === PRACTICE_PATH;
}

/** Make the query consistent: a screen keeps only the fields it uses, and a screen that lacks them is the hub. */
function settle(query: PracticeQuery): PracticeQuery {
  const hub: PracticeQuery = { screen: 'hub' };
  switch (query.screen) {
    case 'setup': {
      if (!query.kind || !KINDS.includes(query.kind)) return hub;
      const next: PracticeQuery = { screen: 'setup', kind: query.kind };
      if (query.run && query.job) {
        if (query.kind === 'role' || query.kind === 'work') {
          next.run = query.run;
          next.job = query.job;
        }
      } else if (query.kind === 'role' && query.graphRole) {
        next.graphRole = query.graphRole;
      } else if (query.kind === 'role') {
        return hub;
      }
      return next;
    }
    case 'room':
    case 'debrief':
      return query.session ? { screen: query.screen, session: query.session } : hub;
    case 'progress':
      return { screen: 'progress' };
    default:
      return hub;
  }
}

/** The query an address names, or null when it is not the Practice address. */
export function parsePracticeUrl(pathname: string, search: string): PracticeQuery | null {
  if (!isPracticePath(pathname)) return null;
  const p = new URLSearchParams(search);
  const screen = SCREENS.find((s) => s === p.get('screen')) ?? 'hub';
  const q: PracticeQuery = { screen };
  const kind = KINDS.find((k) => k === p.get('kind'));
  if (kind) q.kind = kind;
  const run = idOf(p.get('run'));
  const job = idOf(p.get('job'));
  const graphRole = idOf(p.get('graphRole'));
  const session = idOf(p.get('session'));
  if (run) q.run = run;
  if (job) q.job = job;
  if (graphRole) q.graphRole = graphRole;
  if (session) q.session = session;
  return settle(q);
}

/** `{ ...query, ...patch }` without undefined keys, so a patch can clear a field by setting it to undefined. */
export function mergePractice(query: PracticeQuery, patch: Partial<PracticeQuery>): PracticeQuery {
  const next = { ...query, ...patch } as Record<string, unknown>;
  for (const key of Object.keys(next)) if (next[key] === undefined) delete next[key];
  return settle(next as unknown as PracticeQuery);
}

/** The pathname and search of a query; the hub is the bare path. */
export function practicePath(query: PracticeQuery): string {
  const q = settle(query);
  if (q.screen === 'hub') return PRACTICE_PATH;
  const p = new URLSearchParams();
  p.set('screen', q.screen);
  if (q.kind) p.set('kind', q.kind);
  if (q.run) p.set('run', q.run);
  if (q.job) p.set('job', q.job);
  if (q.graphRole) p.set('graphRole', q.graphRole);
  if (q.session) p.set('session', q.session);
  return `${PRACTICE_PATH}?${p.toString()}`;
}
