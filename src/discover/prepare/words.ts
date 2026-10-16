/**
 * The words of the prepare, compare, share and applications views, kept apart from the markup so they can be read (and
 * checked) on their own. Pure functions of the contract in `shared/jobsearch.ts`; nothing here touches the DOM.
 *
 * The rules this file keeps (project-soul.md): a fit is a word, never a number; the pips are a picture of counts, not a
 * score; "listed today" is said only when the employer's board listed the role today; an open-for-a-year posting says
 * so; a salary is shown only when the employer states it; the apply button names where it goes.
 */
import type { DiscoverWorkMode } from '../../../shared/discover';
import type { AiFitWord, AiJob, AskCounts, LevelWord } from '../../../shared/jobsearch';
import { plural } from '../../lib/ui';
import { checkedWord, hostOf, safeHref } from '../util';

export const FIT_WORD: Record<AiFitWord, string> = {
  strong: 'Strong fit',
  good: 'Good fit',
  stretch: 'A stretch',
};

/** The class that gives each word its look: told apart by fill and outline as well as by hue. */
export const FIT_CLASS: Record<AiFitWord, string> = {
  strong: 'dm-fit dm-fit-strong',
  good: 'dm-fit dm-fit-good',
  stretch: 'dm-fit dm-fit-stretch',
};

export const LEVEL_WORD: Record<LevelWord, string> = {
  below: 'Below your level',
  at: 'At your level',
  step_up: 'A step up',
  unclear: 'Level not stated',
};

/** How a role is worked, in a word; nothing when it is not known. */
const MODE_LABEL: Record<DiscoverWorkMode, string> = {
  onsite: 'on-site',
  hybrid: 'hybrid',
  remote: 'remote',
  unknown: '',
};

/** "AWS", "AWS and GraphQL", "Resume, GitHub and Portfolio". */
export const joinNames = (names: readonly string[]) => (names.length <= 1 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`);

/* ───────────────────────── the pips ───────────────────────── */

const count = (n: unknown): number => (typeof n === 'number' && Number.isFinite(n) && n > 0 ? Math.floor(n) : 0);

/** The counts as whole numbers, whatever arrived: a missing or odd count is zero. */
export const cleanAsks = (a: Partial<AskCounts> | undefined): AskCounts => ({ met: count(a?.met), partly: count(a?.partly), notShown: count(a?.notShown) });

export const asksTotal = (a: Partial<AskCounts> | undefined) => {
  const c = cleanAsks(a);
  return c.met + c.partly + c.notShown;
};

/** "3 asks checked · 2 met · 1 not on your resume". Counts only, never a score. */
export function asksLine(raw: Partial<AskCounts> | undefined): string {
  const a = cleanAsks(raw);
  const total = asksTotal(a);
  if (total === 0) return 'No asks checked';
  const parts = [`${plural(total, 'ask')} checked`];
  if (a.met > 0) parts.push(`${a.met} met`);
  if (a.partly > 0) parts.push(`${a.partly} partly`);
  if (a.notShown > 0) parts.push(`${a.notShown} not on your resume`);
  return parts.join(' · ');
}

/** The same for a screen reader: "3 asks checked: 2 met, 1 not on your resume". */
export function asksLabel(raw: Partial<AskCounts> | undefined): string {
  const a = cleanAsks(raw);
  const total = asksTotal(a);
  if (total === 0) return 'No asks checked';
  const parts: string[] = [];
  if (a.met > 0) parts.push(`${a.met} met`);
  if (a.partly > 0) parts.push(`${a.partly} partly`);
  if (a.notShown > 0) parts.push(`${a.notShown} not on your resume`);
  return `${plural(total, 'ask')} checked: ${parts.join(', ')}`;
}

/* ───────────────────────── the role, in words ───────────────────────── */

/** "Koramangala, Bengaluru · hybrid": the place, and how it is worked when that is known. */
export function placeLine(job: Pick<AiJob, 'place' | 'workMode'>): string {
  const parts = [job.place.label];
  const mode = MODE_LABEL[job.workMode];
  if (mode && !job.place.label.toLowerCase().startsWith('remote')) parts.push(mode);
  return parts.join(' · ');
}

/** "Found on the web · search “Frontend Engineer Bengaluru”" or "From Tayyari's employers". */
export function foundLine(job: Pick<AiJob, 'foundVia' | 'foundBy'>): string {
  if (job.foundVia === 'tayyari') return "From Tayyari's employers";
  return job.foundBy ? `Found on the web · search “${job.foundBy}”` : 'Found on the web';
}

const DAY = 86_400_000;

/** How long a posting has been open, in the words the brief uses: "over a year", "almost 2 years". */
export function openFor(postedAt: string | undefined, now: number = Date.now()): string {
  const at = postedAt ? Date.parse(postedAt) : NaN;
  if (!Number.isFinite(at)) return 'over a year';
  const years = (now - at) / (365 * DAY);
  if (years < 1.25) return 'over a year';
  if (years < 1.75) return 'about a year and a half';
  if (years < 2) return 'almost 2 years';
  return `over ${Math.floor(years)} years`;
}

/**
 * The freshness lines: when the employer's board listed it, then when it was posted or, for a posting first published
 * over a year ago and still listed, that it is worth asking whether it is still hiring.
 */
export function freshLines(job: Pick<AiJob, 'checkedAt' | 'postedAt' | 'openLong'>, now: number = Date.now()): string[] {
  const when = checkedWord(job.checkedAt, now);
  const lines = [when === 'recently' ? "Listed on the employer's board" : `Listed on the employer's board ${when}`];
  if (job.openLong) {
    lines.push(`Open ${openFor(job.postedAt, now)} · worth asking if it's still hiring`);
  } else if (job.postedAt) {
    const posted = checkedWord(job.postedAt, now);
    if (posted !== 'recently') lines.push(`Posted ${posted}`);
  }
  return lines;
}

/** The two freshness lines as one: "Listed on the employer's board today · posted 32 days ago". */
export const freshLine = (job: Pick<AiJob, 'checkedAt' | 'postedAt' | 'openLong'>, now: number = Date.now()): string => {
  const [listed, second] = freshLines(job, now);
  if (!second) return listed;
  return `${listed} · ${second.charAt(0).toLowerCase()}${second.slice(1)}`;
};

export const salaryLine = (job: Pick<AiJob, 'salary'>) => (job.salary ? `${job.salary.text} · stated by employer` : 'Salary not stated');

/** The button's words: it names where it goes. */
export const applyText = (job: Pick<AiJob, 'applyHost'>) => `Apply on ${job.applyHost} ↗`;

/** The host an apply link goes to, from the job or, failing that, from the link itself. */
export const hostFor = (job: Pick<AiJob, 'applyHost' | 'applyUrl'>) => job.applyHost || hostOf(job.applyUrl) || 'the employer';

/** The apply link, only when it is a plain web address. */
export const applyHref = (job: Pick<AiJob, 'applyUrl'>) => safeHref(job.applyUrl);

/* ───────────────────────── dates ───────────────────────── */

const pad = (n: number) => String(n).padStart(2, '0');

/** Today (or `d`) as `YYYY-MM-DD` in the person's own time zone: the date they would write on a form. */
export const localDate = (d: Date = new Date()): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** "14 Oct" from `2026-10-14`. */
export function dayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}
