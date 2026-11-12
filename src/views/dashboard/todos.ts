/**
 * "Today": the features, written as things to do. Pure (no DOM, no network, no clock but the `now` it is given).
 *
 *   todayTodos       at most five computed items, each with the short fact it rests on (`cite`) and where it goes
 *   withMarks        the computed list with the person's own ticks and the items they wrote, ready to draw
 *
 * The app sets `done` only from a record dated today (a practice session, a role moved to applied today). The person
 * can tick any item by hand; those ticks and the items they write are kept by todoMarks.ts. Nothing here is scored.
 *
 * Candidates, in priority order: practise for a role at "interviewing" that has no session since it moved; re-drill the
 * wobbly claims; explore your next roles (always); apply to two jobs today (always); match a posting you like (none yet);
 * practise one interview (none yet); pick up a running plan. Only five fit: the two "always" items stay, the rest fill
 * the remaining places in that order.
 */
import type { PracticeHistory } from '../../../shared/interview';
import type { PreparePlanSummary } from '../../../shared/prepare';
import type { TrackedJob } from '../../discover/applications/tracker';
import type { MatchSummary } from '../../match/types';
import { dayKey } from './deskModel';

export type TodoState = 'open' | 'half' | 'done';

/** Where an item opens; the component maps each to the store's actions. */
export type TodoGo =
  | { kind: 'practice' }
  | { kind: 'redrill' }
  | { kind: 'role'; roleId: string | null }
  | { kind: 'discover' }
  | { kind: 'match' }
  | { kind: 'plan'; planId: string };

export interface Todo {
  id: string;
  text: string;
  /** The fact behind the item, short: "GitLab interviewing · no session yet". */
  cite: string;
  go: TodoGo;
  state: TodoState;
}

export interface TodoInput {
  /** The closest next role, when the graph has one. */
  topRole: { id: string; title: string; alignment: number } | null;
  tracked: readonly TrackedJob[];
  /** Null while the sessions are still being read: no item that needs them is made up. */
  history: PracticeHistory | null;
  plans: readonly PreparePlanSummary[];
  matches: readonly MatchSummary[];
  /** The last Discover search, when there is one. */
  run: { createdAt: string; found: number } | null;
  now: number;
}

export const TODO_MAX = 5;

const DAY_MS = 24 * 3600 * 1000;

const ms = (iso: string | undefined): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : 0;
};

/** A YYYY-MM-DD date, or an ISO time, as a local day key. */
const dayOf = (when: string): string => (/^\d{4}-\d{2}-\d{2}$/.test(when) ? when : ms(when) ? dayKey(ms(when)) : '');

const shortDay = (key: string): string => {
  const [y, m, d] = key.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
};

function when(at: number, now: number): string {
  const days = Math.floor(Math.max(0, now - at) / DAY_MS);
  if (dayKey(at) === dayKey(now)) return 'today';
  if (days < 2) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  return `on ${new Date(at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}`;
}

const nounClaims = (n: number) => (n === 1 ? 'wobbly claim' : 'wobbly claims');

interface Candidate extends Todo {
  /** Kept whatever else is on the list. */
  always?: boolean;
}

export function todayTodos(input: TodoInput): Todo[] {
  const { topRole, tracked, history, plans, matches, run, now } = input;
  const today = dayKey(now);
  const out: Candidate[] = [];

  // 1. a role at "interviewing" with no practice session since it moved
  const interviewing = tracked
    .filter((t) => t.stage === 'interviewing')
    .sort((a, b) => (ms(b.updatedAt) || ms(b.savedAt)) - (ms(a.updatedAt) || ms(a.savedAt)))[0];
  if (interviewing && history) {
    const moved = ms(interviewing.updatedAt) || ms(interviewing.savedAt);
    const since = history.sessions.filter((s) => ms(s.createdAt) > moved);
    const doneToday = since.some((s) => dayKey(ms(s.createdAt)) === today);
    if (since.length === 0 || doneToday) {
      const employer = interviewing.job.employer.trim() || interviewing.job.title;
      out.push({
        id: `practise:${interviewing.job.id}`,
        text: `Practise for ${employer}`,
        cite: doneToday ? `${employer} interviewing · practised today` : `${employer} interviewing · no session yet`,
        go: { kind: 'practice' },
        state: doneToday ? 'done' : 'open',
      });
    }
  }

  // 2. claims that did not fully hold up
  if (history) {
    const weak = history.claims.filter((c) => c.best === 'shaky' || c.best === 'couldnt');
    if (weak.length > 0) {
      const shaky = weak.filter((c) => c.best === 'shaky').length;
      const held = weak.length - shaky;
      const marked = weak.filter((c) => c.marks.some((m) => dayOf(m.date) === today)).length;
      const latest = weak.map((c) => dayOf(c.on)).filter(Boolean).sort().pop();
      const parts = [shaky > 0 ? `${shaky} shaky` : '', held > 0 ? `${held} not held` : ''].filter(Boolean).join(', ');
      out.push({
        id: 'redrill',
        text: `Re-drill ${weak.length} ${nounClaims(weak.length)}`,
        cite: `${latest ? `${shortDay(latest)} · ` : ''}${parts}`,
        go: { kind: 'redrill' },
        state: marked === 0 ? 'open' : marked >= weak.length ? 'done' : 'half',
      });
    }
  }

  // 3. always: the closest role
  out.push({
    id: 'explore',
    text: 'Explore your next roles',
    cite: topRole ? `your graph · ${topRole.title} ${topRole.alignment}%` : 'your graph',
    go: { kind: 'role', roleId: topRole ? topRole.id : null },
    state: 'open',
    always: true,
  });

  // 4. always: two applications today, counted from roles moved to applied today
  const sent = tracked.filter((t) => (t.stage === 'applied' || t.stage === 'interviewing' || t.stage === 'offer') && t.appliedAt === today).length;
  const search = run ? `${run.found} found ${when(ms(run.createdAt), now)}` : 'no search yet';
  out.push({
    id: 'apply',
    text: 'Apply to two jobs today',
    cite: `${search} · ${Math.min(sent, 2)} of 2 so far`,
    go: { kind: 'discover' },
    state: sent >= 2 ? 'done' : sent === 1 ? 'half' : 'open',
    always: true,
  });

  // 5. no posting match yet
  if (matches.length === 0) {
    out.push({ id: 'match', text: 'Match a posting you like', cite: 'no posting match yet', go: { kind: 'match' }, state: 'open' });
  }

  // 6. nothing practised yet
  if (history && history.sessions.length === 0 && history.claims.length === 0) {
    out.push({ id: 'practise-first', text: 'Practise one interview', cite: 'no session yet', go: { kind: 'practice' }, state: 'open' });
  }

  // 7. a running plan. The dashboard does not load a plan's steps, so this says only that the plan is there.
  const plan = plans[0];
  if (plan) {
    out.push({
      id: `plan:${plan.id}`,
      text: `Pick up your plan: ${plan.requirement ? `${plan.roleTitle} · ${plan.requirement}` : plan.roleTitle}`,
      cite: `plan started ${when(ms(plan.createdAt), now)}`,
      go: { kind: 'plan', planId: plan.id },
      state: 'open',
    });
  }

  // five places: the two that are always there keep theirs, the others fill the rest in priority order
  let room = TODO_MAX - out.filter((c) => c.always).length;
  return out
    .filter((c) => {
      if (c.always) return true;
      room -= 1;
      return room >= 0;
    })
    .map((c) => ({ id: c.id, text: c.text, cite: c.cite, go: c.go, state: c.state }));
}

/* ───────────────────────── the person's own ticks and lines ───────────────────────── */

export const OWN_MAX = 3;
export const OWN_MAX_CHARS = 80;

/** What todoMarks.ts keeps for one profile and one local day. */
export interface TodoMarks {
  day: string;
  /** Ticked (true) or unticked (false) by hand, by item id. Absent: the computed state stands. */
  ticks: Record<string, boolean>;
  own: Array<{ id: string; text: string }>;
}

export const emptyMarks = (day: string): TodoMarks => ({ day, ticks: {}, own: [] });

/** One line of plain text, at most OWN_MAX_CHARS; empty when nothing is left. */
export const cleanOwn = (raw: string): string => raw.replace(/[\u0000-\u001f<>]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, OWN_MAX_CHARS);

export interface TodoRow extends Omit<Todo, 'go'> {
  /** Where it opens; the person's own lines go nowhere. */
  go: TodoGo | null;
  /** Written by the person, not computed. */
  own: boolean;
}

/** The computed items, then the person's own, each with its state once their ticks are laid over it. */
export function withMarks(todos: readonly Todo[], marks: TodoMarks): TodoRow[] {
  const state = (id: string, computed: TodoState): TodoState => {
    const tick = marks.ticks[id];
    if (tick === true) return 'done';
    if (tick === false) return computed === 'done' ? 'open' : computed;
    return computed;
  };
  const rows: TodoRow[] = todos.map((t) => ({ ...t, state: state(t.id, t.state), own: false }));
  for (const o of marks.own.slice(0, OWN_MAX)) {
    rows.push({ id: o.id, text: o.text, cite: 'yours · written today', go: null, state: state(o.id, 'open'), own: true });
  }
  return rows;
}
