/**
 * Pure helpers over a plan: the numbers code owns (weeks from hours, totals, progress), the labels, the "Read this
 * as text" paragraphs written from the blocks, and the calendar file. Nothing here touches the DOM or the store.
 */
import type { FixTask } from '../../shared/interview';
import type { PrepareEvidence, PrepareGap, PrepareMove, PreparePhase, PreparePlan, PrepareState, PrepareStrong } from '../../shared/prepare';
import { rankLabel } from '../lib/layout';

/** The hours a week the screen offers. */
export const HOUR_OPTIONS: readonly number[] = [3, 6, 10];

export interface PhaseSpan {
  start: number;
  end: number;
  /** "Week 3" or "Weeks 1–2". */
  label: string;
}

/**
 * Which weeks each phase takes at `hoursPerWeek`: a phase starts in the week its first hour falls in and ends in the
 * week its last hour falls in, so two phases may share a week when the hours do not divide evenly.
 */
export function phaseSpans(phases: PreparePhase[], hoursPerWeek: number, extra?: WeekLoad): PhaseSpan[] {
  const h = Math.max(1, hoursPerWeek);
  // practice fixes (I2) take their hours at the start of the week they land in, so what comes after moves back
  const at = extra && extra.hours > 0 ? (Math.max(1, extra.week) - 1) * h : Infinity;
  let added = false;
  let spent = 0;
  return phases.map((p) => {
    if (!added && spent >= at && extra) {
      spent += extra.hours;
      added = true;
    }
    const start = Math.floor(spent / h) + 1;
    spent += Math.max(0, p.hours);
    if (!added && spent > at && extra) {
      spent += extra.hours;
      added = true;
    }
    const end = Math.max(start, Math.ceil(spent / h));
    return {
      start,
      end,
      label: start === end ? `Week ${start}` : `Weeks ${start}–${end}`,
    };
  });
}

export const totalHours = (move: PrepareMove): number => move.phases.reduce((a, p) => a + Math.max(0, p.hours), 0);

export const totalWeeks = (move: PrepareMove, hoursPerWeek: number, extraHours = 0): number =>
  Math.ceil((totalHours(move) + Math.max(0, extraHours)) / Math.max(1, hoursPerWeek));

/** Hours that land in one week of the plan, on top of its phases: the unticked Fix steps from practice (I2). */
export interface WeekLoad {
  week: number;
  hours: number;
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

/** The week of the plan a day falls in: week 1 is the week the plan was made, capped at the plan's last week. */
export function currentWeek(createdAt: string, weeks: number, now: Date = new Date()): number {
  const made = Date.parse(createdAt);
  const n = Number.isFinite(made) ? Math.floor(Math.max(0, now.getTime() - made) / WEEK_MS) + 1 : 1;
  return Math.max(1, Math.min(n, Math.max(1, weeks)));
}

/** The minutes of the Fix steps from practice that are not ticked yet. */
export const practiceMinutesLeft = (practice: readonly FixTask[] | undefined): number =>
  (practice ?? []).reduce((sum, t) => sum + (t.doneAt ? 0 : Math.max(0, t.minutes)), 0);

/** The key of a task in `PrepareState.done`. */
export const taskKey = (moveId: string, phaseIndex: number, taskIndex: number): string => `${moveId}:${phaseIndex}:${taskIndex}`;

export interface Progress {
  done: number;
  all: number;
}

export function phaseProgress(move: PrepareMove, phaseIndex: number, done: readonly string[]): Progress {
  const phase = move.phases[phaseIndex];
  if (!phase) return { done: 0, all: 0 };
  const set = new Set(done);
  return {
    done: phase.tasks.filter((_, j) => set.has(taskKey(move.id, phaseIndex, j))).length,
    all: phase.tasks.length,
  };
}

export function moveProgress(move: PrepareMove, done: readonly string[]): Progress {
  return move.phases.reduce(
    (acc, _, i) => {
      const p = phaseProgress(move, i, done);
      return { done: acc.done + p.done, all: acc.all + p.all };
    },
    { done: 0, all: 0 },
  );
}

/** "From your resume". */
export const evidenceLabel = (e: PrepareEvidence): string => `From ${e.sourceLabel}`;

export function evidenceIndex(plan: PreparePlan): Map<string, PrepareEvidence> {
  return new Map(plan.evidence.map((e) => [e.id, e]));
}

/** The plan's place line: "closest of your 5 roles · ~2 months". */
export const standingLine = (plan: PreparePlan): string => `${rankLabel(plan.rank, plan.roleCount)} · ${plan.eta}`;

/** The sentence code writes under a gap when the model's why is absent: numbers only. */
export const numbersLine = (g: PrepareGap): string => `You are at ${g.current}; the role expects ${g.target}.`;

const list = (items: string[]): string => (items.length <= 1 ? (items[0] ?? '') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`);

const strongText = (s: PrepareStrong[]): string =>
  s.length ? `Already strong: ${list(s.map((x) => `${x.name} at ${x.current} (the role expects ${x.target})`))}.` : '';

/**
 * "Read this as text": paragraphs written from the blocks, so the text can never say more than they do. Numbers are the
 * plan's own; the weeks come from the hours a week the person picked.
 */
export function planText(plan: PreparePlan, move: PrepareMove | undefined, hoursPerWeek: number): string[] {
  const out: string[] = [];
  const where =
    plan.scope === 'requirement' && plan.requirement
      ? `This is ${plan.requirement} for the ${plan.roleTitle} role, ${rankLabel(plan.rank, plan.roleCount)}, about ${plan.eta.replace(/^~\s*/, '')} away.`
      : `${plan.roleTitle} is ${rankLabel(plan.rank, plan.roleCount)}, about ${plan.eta.replace(/^~\s*/, '')} away.`;
  out.push([where, plan.headline ?? ''].filter(Boolean).join(' '));

  const gaps = plan.gaps.map((g) => {
    const head = `${g.name}: ${g.current} today, the role expects ${g.target}${g.biggest && plan.gaps.length > 1 ? ' (the biggest gap)' : ''}.`;
    return g.why ? `${head} ${g.why}` : head;
  });
  if (gaps.length) out.push(gaps.join(' '));
  const strong = strongText(plan.strong);
  if (strong) out.push(strong);

  if (move) {
    const have = move.have.map((h) => h.text);
    const parts = [`The move: ${move.title}.`, move.body];
    if (have.length) parts.push(`You already have ${list(have)}.`);
    if (move.works.length) parts.push(`It works on ${list(move.works)}, about ${totalHours(move)} hours in all.`);
    out.push(parts.join(' '));

    const spans = phaseSpans(move.phases, hoursPerWeek);
    const phases = move.phases.map((p, i) => {
      const tasks = p.tasks.length ? ` Tasks: ${p.tasks.join('; ')}.` : '';
      return `${spans[i].label}, ${p.title} (${p.hours} h):${tasks} You'll have ${p.deliverable}.`;
    });
    out.push(`At ${hoursPerWeek} hours a week that is ${totalWeeks(move, hoursPerWeek)} weeks. ${phases.join(' ')}`);
  }
  return out;
}

/* ───────────────────────── the calendar file ───────────────────────── */

export interface CalendarEvent {
  week: number;
  start: Date;
  title: string;
  tasks: string[];
}

/** Monday first, as the week is read here; the value is JavaScript's `getDay()`. */
export const WEEKDAYS: readonly {
  value: number;
  label: string;
  long: string;
}[] = [
  { value: 1, label: 'Mon', long: 'Monday' },
  { value: 2, label: 'Tue', long: 'Tuesday' },
  { value: 3, label: 'Wed', long: 'Wednesday' },
  { value: 4, label: 'Thu', long: 'Thursday' },
  { value: 5, label: 'Fri', long: 'Friday' },
  { value: 6, label: 'Sat', long: 'Saturday' },
  { value: 0, label: 'Sun', long: 'Sunday' },
];

/** The first `weekday` at least one day after `from`, at `time` ("19:00"). */
export function firstBlock(weekday: number, time: string, from: Date = new Date()): Date {
  const [hh, mm] = time.split(':').map((s) => Number(s));
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 1, Number.isFinite(hh) ? hh : 19, Number.isFinite(mm) ? mm : 0, 0, 0);
  while (d.getDay() !== weekday) d.setDate(d.getDate() + 1);
  return d;
}

/** One block a week for as long as the plan: each week carries the title of the phase (or phases) it falls in. */
export function calendarEvents(move: PrepareMove, hoursPerWeek: number, weekday: number, time: string, from: Date = new Date()): CalendarEvent[] {
  const spans = phaseSpans(move.phases, hoursPerWeek);
  const weeks = totalWeeks(move, hoursPerWeek);
  const first = firstBlock(weekday, time, from);
  const out: CalendarEvent[] = [];
  for (let w = 1; w <= weeks; w++) {
    const phases = move.phases.filter((_, i) => spans[i].start <= w && w <= spans[i].end);
    const start = new Date(first);
    start.setDate(first.getDate() + 7 * (w - 1));
    out.push({
      week: w,
      start,
      title: phases.map((p) => p.title).join(' · ') || move.title,
      tasks: phases.flatMap((p) => p.tasks),
    });
  }
  return out;
}

const two = (n: number) => String(n).padStart(2, '0');
const local = (d: Date) => `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}T${two(d.getHours())}${two(d.getMinutes())}00`;
const utc = (d: Date) =>
  `${d.getUTCFullYear()}${two(d.getUTCMonth() + 1)}${two(d.getUTCDate())}T${two(d.getUTCHours())}${two(d.getUTCMinutes())}${two(d.getUTCSeconds())}Z`;
const escapeText = (s: string) => s.replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n');

/** RFC 5545 lines are at most 75 octets; longer ones continue on the next line after a space. */
function fold(line: string): string[] {
  const out: string[] = [];
  let rest = line;
  while (rest.length > 72) {
    out.push(rest.slice(0, 72));
    rest = ` ${rest.slice(72)}`;
  }
  out.push(rest);
  return out;
}

/** The .ics text: floating local times (the person's own clock), one event a week, `hours` long. */
export function icsText(events: CalendarEvent[], hours: number, planTitle: string, uidBase: string, now: Date = new Date()): string {
  const lines: string[] = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Leafout//Prepare//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH'];
  const stamp = utc(now);
  for (const e of events) {
    const description = [`${planTitle} · week ${e.week} of ${events.length}`, ...e.tasks.map((t) => `– ${t}`)].join('\n');
    lines.push(
      'BEGIN:VEVENT',
      `UID:${uidBase}-w${e.week}@tayyari`,
      `DTSTAMP:${stamp}`,
      `DTSTART:${local(e.start)}`,
      `DURATION:PT${Math.max(1, Math.round(hours))}H`,
      `SUMMARY:${escapeText(e.title)}`,
      `DESCRIPTION:${escapeText(description)}`,
      'END:VEVENT',
    );
  }
  lines.push('END:VCALENDAR');
  return `${lines.flatMap(fold).join('\r\n')}\r\n`;
}

/** "26 Oct" */
export const shortDate = (d: Date): string => d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });

/** Only web links are kept and shown. */
export function webUrl(raw: string): string | null {
  try {
    const u = new URL(raw.trim());
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null;
  } catch {
    return null;
  }
}

/** "github.com/riya/notes-ai" for a link row. */
export function hostPath(url: string): string {
  try {
    const u = new URL(url);
    const path = u.pathname === '/' ? '' : u.pathname.replace(/\/$/, '');
    const s = `${u.host.replace(/^www\./, '')}${path}`;
    return s.length > 56 ? `${s.slice(0, 55)}…` : s;
  } catch {
    return url;
  }
}

/** The hours options plus the stored value when it is not one of them (a plan saved at 4 h still shows 4 h). */
export function hourChoices(state: PrepareState): number[] {
  return HOUR_OPTIONS.includes(state.hoursPerWeek) ? [...HOUR_OPTIONS] : [...HOUR_OPTIONS, state.hoursPerWeek].sort((a, b) => a - b);
}
