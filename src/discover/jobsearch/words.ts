/**
 * The words of Discover, kept apart from the markup so they can be read (and checked) on their own. Pure functions of
 * the contract in `shared/jobsearch.ts`; nothing here touches the DOM or the store.
 *
 * The rules this file keeps (project-soul.md): a fit is a word and a requirement count, never a number out of a
 * hundred; the level is said from the posting's own words; "listed today" is said only when the employer's board
 * listed the role today; a salary is shown only when the employer states it; every distance, where there is one, is
 * a straight line.
 */
import type { AiFitWord, AiJob, AskCounts, LevelWord, TrailStage } from '../../../shared/jobsearch';
import { MODE_LABEL, checkedWord } from '../util';

export const FIT_WORD: Record<AiFitWord, string> = {
  strong: 'Strong fit',
  good: 'Good fit',
  stretch: 'A stretch',
};

/** The order roles are shown in: strong, then good, then a stretch. */
export const FIT_RANK: Record<AiFitWord, number> = { strong: 0, good: 1, stretch: 2 };

export const LEVEL_WORD: Record<LevelWord, string> = {
  below: 'Below your level',
  at: 'At your level',
  step_up: 'A step up',
  unclear: 'Level not stated',
};

export const STAGE_LABEL: Record<TrailStage, string> = {
  plan: 'Plan',
  search: 'Search',
  boards: 'Boards',
  narrow: 'Narrow',
  read: 'Read',
  result: 'Result',
};

/** "Bengaluru · hybrid": where the role is and how it is worked. */
export function placeLine(job: Pick<AiJob, 'place' | 'workMode'>): string {
  const parts = [job.place.label];
  const mode = MODE_LABEL[job.workMode];
  if (mode && !job.place.label.toLowerCase().startsWith('remote')) parts.push(mode);
  return parts.join(' · ');
}

/** "Headout · Bengaluru": who is hiring and where, the line under a title. */
export const employerLine = (job: Pick<AiJob, 'employer' | 'place' | 'workMode'>) => `${job.employer} · ${placeLine(job)}`;

/* ───────────────────────── the pips ───────────────────────── */

/** One pip per requirement the read checked: filled = met, half = partly, red outline = not on your resume. */
export type PipKind = 'met' | 'part' | 'gap';

/** The pips of a role, in a fixed order: met, then partly, then not on the resume. */
export function pipsOf(asks: AskCounts): PipKind[] {
  return [...Array<PipKind>(asks.met).fill('met'), ...Array<PipKind>(asks.partly).fill('part'), ...Array<PipKind>(asks.notShown).fill('gap')];
}

/** "3 asks checked · 2 met · 1 not on your resume". Counts, never a score. */
export function countsLine(asks: AskCounts): string {
  const total = asks.met + asks.partly + asks.notShown;
  if (total === 0) return 'No asks checked';
  const parts = [`${total} ${total === 1 ? 'ask' : 'asks'} checked`];
  if (asks.met > 0) parts.push(`${asks.met} met`);
  if (asks.partly > 0) parts.push(`${asks.partly} partly`);
  if (asks.notShown > 0) parts.push(`${asks.notShown} not on your resume`);
  return parts.join(' · ');
}

/* ───────────────────────── where it came from, and how fresh ───────────────────────── */

/** "Found on the web · search “Frontend Engineer Bengaluru”" or "From Tayyari's employers". */
export function provenanceLine(job: Pick<AiJob, 'foundVia' | 'foundBy'>): string {
  if (job.foundVia === 'tayyari') return "From Tayyari's employers";
  return job.foundBy ? `Found on the web · search “${job.foundBy}”` : 'Found on the web';
}

/**
 * "Listed on the employer's board today · posted 32 days ago". A role that has been open over a year says so and
 * suggests asking, instead of a posting date that would read as an age.
 */
export function freshnessLine(job: Pick<AiJob, 'checkedAt' | 'postedAt' | 'openLong'>, now: number = Date.now()): string {
  const when = checkedWord(job.checkedAt, now);
  const parts = [when === 'recently' ? "Listed on the employer's board" : `Listed on the employer's board ${when}`];
  if (job.openLong) {
    parts.push("open over a year · worth asking if it's still hiring");
  } else if (job.postedAt) {
    const posted = checkedWord(job.postedAt, now);
    if (posted !== 'recently') parts.push(`posted ${posted}`);
  }
  return parts.join(' · ');
}

/** "Salary 28–36 lakh a year · stated by employer": only when the employer states one. */
export const salaryLine = (job: Pick<AiJob, 'salary'>): string | undefined => (job.salary ? `${job.salary.text} · stated by employer` : undefined);

/* ───────────────────────── the search, in words ───────────────────────── */

/** "Bengaluru and remote in India", "Bengaluru", "Remote in India". */
export function areaWords(city: string | undefined, remote: boolean): string {
  if (city) return remote ? `${city} and remote in India` : city;
  return remote ? 'Remote in India' : 'Anywhere we can read';
}

const DAY_MS = 86_400_000;

/** Results older than this carry the note that some roles may have closed. */
export const STALE_AFTER_MS = DAY_MS;

export const isStale = (createdAt: string, now: number = Date.now()): boolean => {
  const at = Date.parse(createdAt);
  return Number.isFinite(at) && now - at > STALE_AFTER_MS;
};

const clock = (at: number) => new Date(at).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true }).replace(/\s?([AP]M)$/i, (_, m: string) => ` ${m.toLowerCase()}`);

/** "today at 7:31 pm", "yesterday at 7:31 pm", "3 days ago": when the search ran. */
export function searchedWhen(createdAt: string, now: number = Date.now()): string {
  const at = Date.parse(createdAt);
  if (!Number.isFinite(at)) return 'recently';
  const word = checkedWord(createdAt, now);
  if (word === 'today' || word === 'yesterday') return `${word} at ${clock(at)}`;
  return word;
}

/** The pause a 429 asks for, in words: "about 40 seconds", "about a minute", "about 12 minutes". */
export function waitWords(seconds: number): string {
  if (seconds < 50) return `about ${Math.max(1, Math.round(seconds))} seconds`;
  const minutes = Math.round(seconds / 60);
  if (minutes <= 1) return 'about a minute';
  if (minutes < 90) return `about ${minutes} minutes`;
  const hours = Math.round(minutes / 60);
  return hours <= 1 ? 'about an hour' : `about ${hours} hours`;
}
