/**
 * The words of "Search with AI", kept apart from the markup so they can be read (and checked) on their own. Pure
 * functions of the contract in `shared/jobsearch.ts`; nothing here touches the DOM or the store.
 *
 * The rules this file keeps (project-soul.md): a fit is a word, never a number; the level is said from the posting's
 * own words; "listed today" is said only when the employer's board listed the role today; a salary is shown only
 * when the employer states it.
 */
import type { AiFitWord, AiJob, LevelWord, TrailStage } from '../../../shared/jobsearch';
import { fmtKm, checkedWord, MODE_LABEL } from '../model';

export const FIT_WORD: Record<AiFitWord, string> = {
  strong: 'Strong fit',
  good: 'Good fit',
  stretch: 'A stretch',
};

/** Which Discover chip look each word borrows: soft fill, quiet fill with a hairline, dashed hairline. */
export const FIT_LOOK: Record<AiFitWord, 'looks_close' | 'worth_a_look' | 'stretch'> = {
  strong: 'looks_close',
  good: 'worth_a_look',
  stretch: 'stretch',
};

export const LEVEL_WORD: Record<LevelWord, string> = {
  below: 'Below your level',
  at: 'At your level',
  step_up: 'A step up',
  unclear: 'Level not stated',
};

export const STAGE_LABEL: Record<TrailStage, string> = {
  plan: 'Plan',
  search: 'Web search',
  boards: "Employers' boards",
  narrow: 'Narrowing',
  read: 'Reading',
  result: 'Result',
};

/** "Koramangala, Bengaluru · hybrid", plus the straight-line distance when the server gave one. */
export function jobPlaceLine(job: Pick<AiJob, 'place' | 'workMode'>): string {
  const parts = [job.place.label];
  const mode = MODE_LABEL[job.workMode];
  if (mode && !job.place.label.toLowerCase().startsWith('remote')) parts.push(mode);
  if (job.place.km !== undefined) parts.push(`${fmtKm(job.place.km)} km straight line`);
  return parts.join(' · ');
}

/** "Listed on the employer's board today" (or yesterday, or N days ago). */
export function listedLine(checkedAt: string, now: number = Date.now()): string {
  const when = checkedWord(checkedAt, now);
  return when === 'recently' ? "Listed on the employer's board" : `Listed on the employer's board ${when}`;
}

/** "Posted today", from the date the employer gives; nothing when it gives none. */
export function postedLine(postedAt: string | undefined, now: number = Date.now()): string | undefined {
  if (!postedAt) return undefined;
  const when = checkedWord(postedAt, now);
  return when === 'recently' ? undefined : `Posted ${when}`;
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
