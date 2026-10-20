/** Small formatters for the paper: dates, counts in words, and which links may be followed. */
import type { WeeklyFit, WeeklyJob } from '../../shared/weekly';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];

/** An ISO date (`2026-10-26`, or a full timestamp) as its calendar parts, read in UTC so the day never slips. */
function parts(iso: string): { y: number; m: number; d: number; dow: number } | null {
  const t = Date.parse(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(t)) return null;
  const date = new Date(t);
  return {
    y: date.getUTCFullYear(),
    m: date.getUTCMonth(),
    d: date.getUTCDate(),
    dow: date.getUTCDay(),
  };
}

/** "Monday, 26 October 2026". */
export function longDate(iso: string): string {
  const p = parts(iso);
  return p ? `${DAYS[p.dow]}, ${p.d} ${MONTHS[p.m]} ${p.y}` : iso;
}

/** "26 Oct". */
export function shortDate(iso: string): string {
  const p = parts(iso);
  return p ? `${p.d} ${MONTHS[p.m].slice(0, 3)}` : iso;
}

/** "Monday" or "Friday": the day this edition's week is dated by. */
export function dayName(iso: string): string {
  const p = parts(iso);
  return p ? DAYS[p.dow] : 'week';
}

/** A count in words up to ten, digits after that. */
export function numWord(n: number): string {
  return n >= 0 && n < WORDS.length ? WORDS[n] : String(n);
}

export const capital = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export const FIT_WORD: Record<WeeklyFit, string> = {
  looks_close: 'Looks close',
  worth_a_look: 'Worth a look',
  stretch: 'A stretch',
  cant_tell: "Can't tell yet",
};

const FIT_PHRASE: Record<WeeklyFit, [string, string]> = {
  looks_close: ['is a close fit', 'are close fits'],
  worth_a_look: ['is worth a look', 'are worth a look'],
  stretch: ['is a stretch', 'are stretches'],
  cant_tell: ['is hard to tell from the posting', 'are hard to tell from the posting'],
};

/** "One is a close fit; two are stretches." from the fit words, so the line can never disagree with the jobs. */
export function deckFor(jobs: WeeklyJob[]): string {
  const order: WeeklyFit[] = ['looks_close', 'worth_a_look', 'stretch', 'cant_tell'];
  const clauses = order
    .map((fit) => {
      const n = jobs.filter((j) => j.fit === fit).length;
      return n === 0 ? '' : `${numWord(n)} ${FIT_PHRASE[fit][n === 1 ? 0 : 1]}`;
    })
    .filter(Boolean);
  return clauses.length ? `${capital(clauses.join('; '))}.` : '';
}

/** Only web links are followed: anything else (empty, a placeholder, a script URL) renders as plain text. */
export function safeHref(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : undefined;
  } catch {
    return undefined;
  }
}

/** A bracketed line is a placeholder in the fixtures; it is set quieter. */
export const isPlaceholder = (s: string) => s.trim().startsWith('[');
