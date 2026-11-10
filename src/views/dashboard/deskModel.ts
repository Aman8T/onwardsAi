/**
 * Pure helpers for the dashboard's desk, the cards that show what the person has been doing here:
 *
 *   pickUps         the last thing touched in each feature, newest first ("Pick up where you left off")
 *   pinnedNote      the person's own newest note on a tracked role
 *   activeDays      the days something was done here, counted from the dates the features already store
 *   weekStreak      weeks in a row with at least one such day, and the longest run on record
 *   weeksStrip      the last twelve weeks as columns of days, for the squares
 *   bestWeekday     the weekday that leads, when one clearly does
 *   practiceTrend   claims that held up, and the share defended in each judged session
 *
 * Nothing is scored and nothing is phrased by a model. A visit is not recorded anywhere, so a "day" is a day with a
 * dated record: an analysis, a practice session, a conversation, a posting match, a plan, a saved or updated role.
 * No DOM, no network.
 */
import type { ChatOrigin, Profile } from '../../../shared/types';
import type { PracticeHistory, SessionSummary } from '../../../shared/interview';
import type { PreparePlanSummary } from '../../../shared/prepare';
import type { TrackedJob } from '../../discover/applications/tracker';
import type { MatchSummary } from '../../match/types';

/** The part of a chat thread the desk reads (the store's Thread fits). */
export interface ThreadLike {
  id: string;
  origin: ChatOrigin;
  createdAt: number;
  messages: readonly { role: 'user' | 'assistant'; text: string }[];
  /** A thread that opens a Prepare plan; the plan itself stands for it on the desk. */
  prepare?: unknown;
}

export interface DeskInput {
  profile: Profile;
  previous: Profile | null;
  history: PracticeHistory | null;
  plans: readonly PreparePlanSummary[];
  matches: readonly MatchSummary[];
  tracked: readonly TrackedJob[];
  threads: readonly ThreadLike[];
}

const DAY_MS = 24 * 3600 * 1000;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

const ms = (iso: string | undefined): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : 0;
};

/* ───────────────────────── pick up where you left off ───────────────────────── */

export type PickUp =
  | { kind: 'practice'; id: string; at: number; session: SessionSummary }
  | { kind: 'plan'; id: string; at: number; plan: PreparePlanSummary }
  | { kind: 'thread'; id: string; at: number; thread: ThreadLike }
  | { kind: 'match'; id: string; at: number; match: MatchSummary }
  | { kind: 'application'; id: string; at: number; job: TrackedJob };

const newest = <T>(list: readonly T[], at: (item: T) => number): T | undefined => {
  let best: T | undefined;
  let bestAt = 0;
  for (const item of list) {
    const t = at(item);
    if (t > bestAt) {
      best = item;
      bestAt = t;
    }
  }
  return best;
};

/** The newest record of each feature, newest first: at most one session, plan, conversation, match and tracked role. */
export function pickUps(input: Pick<DeskInput, 'history' | 'plans' | 'matches' | 'tracked' | 'threads'>): PickUp[] {
  const out: PickUp[] = [];
  const session = newest(input.history?.sessions ?? [], (s) => ms(s.createdAt));
  if (session) out.push({ kind: 'practice', id: session.id, at: ms(session.createdAt), session });
  const plan = newest(input.plans, (p) => ms(p.createdAt));
  if (plan) out.push({ kind: 'plan', id: plan.id, at: ms(plan.createdAt), plan });
  const thread = newest(
    input.threads.filter((t) => !t.prepare && t.messages.some((m) => m.role === 'user' && m.text.trim())),
    (t) => t.createdAt,
  );
  if (thread) out.push({ kind: 'thread', id: thread.id, at: thread.createdAt, thread });
  const match = newest(input.matches, (m) => ms(m.createdAt));
  if (match) out.push({ kind: 'match', id: match.id, at: ms(match.createdAt), match });
  const job = newest(
    input.tracked.filter((t) => t.stage !== 'closed'),
    (t) => ms(t.updatedAt) || ms(t.savedAt),
  );
  if (job) out.push({ kind: 'application', id: job.job.id, at: ms(job.updatedAt) || ms(job.savedAt), job });
  return out.sort((a, b) => b.at - a.at);
}

/** The newest note the person wrote on a tracked role, or null. The words on the pinned slip are theirs. */
export function pinnedNote(tracked: readonly TrackedJob[]): TrackedJob | null {
  return (
    newest(
      tracked.filter((t) => t.note.trim()),
      (t) => ms(t.updatedAt) || ms(t.savedAt),
    ) ?? null
  );
}

/* ───────────────────────── the days ───────────────────────── */

/** A local calendar day as YYYY-MM-DD. */
export function dayKey(t: number): string {
  const d = new Date(t);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Days since 1970-01-01 for a YYYY-MM-DD key, free of clock changes. */
const dayNumber = (key: string): number => {
  const [y, m, d] = key.split('-').map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / DAY_MS);
};

const keyOfNumber = (n: number): string => new Date(n * DAY_MS).toISOString().slice(0, 10);

/** Weeks since the Monday of 1969-12-29: every Monday starts a new one. */
const weekNumber = (day: number): number => Math.floor((day + 3) / 7);

/** Monday 0 … Sunday 6. */
const weekdayOf = (day: number): number => (((day + 3) % 7) + 7) % 7;

/**
 * Every day something was done here, with how many different things. The same session, role or conversation counts
 * once a day however many dates it carries.
 */
export function activeDays(input: DeskInput): Map<string, number> {
  const seen = new Map<string, Set<string>>();
  const add = (when: string | number | undefined, what: string) => {
    if (when === undefined || when === '') return;
    let key: string;
    if (typeof when === 'string' && ISO_DAY.test(when)) key = when;
    else {
      const t = typeof when === 'number' ? when : ms(when);
      if (!t) return;
      key = dayKey(t);
    }
    const things = seen.get(key) ?? new Set<string>();
    things.add(what);
    seen.set(key, things);
  };
  add(input.profile.createdAt, `analysis:${input.profile.id}`);
  if (input.previous) add(input.previous.createdAt, `analysis:${input.previous.id}`);
  for (const s of input.history?.sessions ?? []) add(s.createdAt, `session:${s.id}`);
  // sessions are deleted after a while; the marks on their claims keep the day
  for (const c of input.history?.claims ?? []) for (const m of c.marks) add(m.date, `session:${m.sessionId}`);
  for (const p of input.plans) add(p.createdAt, `plan:${p.id}`);
  for (const m of input.matches) add(m.createdAt, `match:${m.id}`);
  for (const t of input.threads) add(t.createdAt, `thread:${t.id}`);
  for (const t of input.tracked) {
    add(t.savedAt, `role:${t.job.id}`);
    add(t.appliedAt, `role:${t.job.id}`);
    add(t.updatedAt, `role:${t.job.id}`);
  }
  const out = new Map<string, number>();
  for (const [key, things] of seen) out.set(key, things.size);
  return out;
}

export interface Streak {
  /** Weeks in a row up to this one. A week that has only just begun does not break the run. */
  weeks: number;
  longest: number;
  /** Something was done this week already. */
  thisWeek: boolean;
}

export function weekStreak(days: ReadonlyMap<string, number>, now: number): Streak {
  const today = dayNumber(dayKey(now));
  const thisWeek = weekNumber(today);
  const weeks = new Set<number>();
  for (const key of days.keys()) {
    const n = dayNumber(key);
    // a date from the future (a clock set wrong) is not a week kept
    if (n <= today) weeks.add(weekNumber(n));
  }
  let run = 0;
  for (let w = weeks.has(thisWeek) ? thisWeek : thisWeek - 1; weeks.has(w); w--) run++;
  let longest = 0;
  for (const w of weeks) {
    if (weeks.has(w - 1)) continue;
    let n = 1;
    while (weeks.has(w + n)) n++;
    if (n > longest) longest = n;
  }
  return { weeks: run, longest, thisWeek: weeks.has(thisWeek) };
}

export interface DayCell {
  key: string;
  /** 0 nothing, 1 one thing, 2 two or more. */
  level: 0 | 1 | 2;
  today: boolean;
  /** Later this week: not a missed day. */
  ahead: boolean;
}

export const STRIP_WEEKS = 12;

/** The last twelve weeks, oldest first, each Monday to Sunday; the last column is this week. */
export function weeksStrip(days: ReadonlyMap<string, number>, now: number, weeks: number = STRIP_WEEKS): DayCell[][] {
  const today = dayNumber(dayKey(now));
  const monday = today - weekdayOf(today);
  const out: DayCell[][] = [];
  for (let w = weeks - 1; w >= 0; w--) {
    const column: DayCell[] = [];
    for (let d = 0; d < 7; d++) {
      const n = monday - w * 7 + d;
      const key = keyOfNumber(n);
      const count = n > today ? 0 : (days.get(key) ?? 0);
      column.push({ key, level: count >= 2 ? 2 : count === 1 ? 1 : 0, today: n === today, ahead: n > today });
    }
    out.push(column);
  }
  return out;
}

const WEEKDAYS = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'];
const WEEKDAY_MIN_DAYS = 4;

/** "Sundays" when one weekday clearly leads the days on record (four or more days, the leader alone on at least two). */
export function bestWeekday(days: ReadonlyMap<string, number>): string | null {
  if (days.size < WEEKDAY_MIN_DAYS) return null;
  const counts = new Array<number>(7).fill(0);
  for (const key of days.keys()) counts[weekdayOf(dayNumber(key))]++;
  const top = Math.max(...counts);
  if (top < 2 || counts.filter((c) => c === top).length !== 1) return null;
  return WEEKDAYS[counts.indexOf(top)];
}

/* ───────────────────────── practice, over time ───────────────────────── */

export interface PracticeTrend {
  /** Claims whose best verdict is Defended. */
  defended: number;
  /** Claims asked about so far. */
  claims: number;
  /** The share defended in each judged session, oldest first, the last eight. */
  bars: number[];
}

const BARS_MAX = 8;

export function practiceTrend(history: PracticeHistory | null): PracticeTrend | null {
  if (!history || (history.sessions.length === 0 && history.claims.length === 0)) return null;
  const bars: number[] = [];
  for (const s of history.sessions.slice().sort((a, b) => ms(a.createdAt) - ms(b.createdAt))) {
    if (!s.counts) continue;
    const judged = s.counts.defended + s.counts.shaky + s.counts.couldnt;
    if (judged > 0) bars.push(s.counts.defended / judged);
  }
  return {
    defended: history.claims.filter((c) => c.best === 'defended').length,
    claims: history.claims.length,
    bars: bars.slice(-BARS_MAX),
  };
}
