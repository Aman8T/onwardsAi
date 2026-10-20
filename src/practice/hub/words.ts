/**
 * The small words and formatters the Practice screens around the room share: dates and clock times, the sentence a
 * failed call shows, and the fit chip's class. Pure functions; nothing here touches the DOM or the store.
 */
import type { AiFitWord } from '../../../shared/jobsearch';
import type { SessionSummary } from '../../../shared/interview';
import { dayLabel } from '../../discover/prepare/words';
import { ApiError } from '../../lib/api';

/** Told apart by fill and outline as well as by hue; the same three looks as Discover's fit chips. */
export const FIT_PC: Record<AiFitWord, string> = {
  strong: 'pc-fit pc-fit-strong',
  good: 'pc-fit pc-fit-good',
  stretch: 'pc-fit pc-fit-stretch',
};

/** What the person reads when the server sent no sentence of its own. */
export const TROUBLE = 'Leafout ran into trouble. Please try again in a moment.';
export const OFFLINE = "We couldn't reach Leafout. Check your connection and try again.";

/** Said wherever a screen shows what is kept: nothing leaves this person's account. */
export const NOTHING_SHARED = 'Nothing here is shared with an employer.';

/** The sentence the person reads for a failed call: the server's own when it sent one. */
export function sentenceOf(err: unknown): string {
  if (err instanceof ApiError) return err.message || TROUBLE;
  return err instanceof TypeError ? OFFLINE : TROUBLE;
}

export const statusOf = (err: unknown): number | undefined => (err instanceof ApiError ? err.status : undefined);

/** "16 Oct" from a day (`2026-10-16`) or a time (`2026-10-16T10:30:00Z`), in the person's own time zone; '' when it is neither. */
export function dateOf(value: string | undefined): string {
  if (!value) return '';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return dayLabel(value);
  const at = Date.parse(value);
  return Number.isFinite(at) ? new Date(at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : '';
}

/** "2:31" from 151 whole seconds into the interview. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** What a past session is about, under its kind: the role, the work family, or that no posting was used. */
export function sessionSubtitle(s: Pick<SessionSummary, 'kind' | 'title'>): string {
  if (s.kind === 'role') return s.title;
  if (s.kind === 'work') return s.title.replace(/^Work sample\s*·\s*/, '');
  return 'No posting';
}

/** A free-text note clipped for one line, at a word boundary. */
export function clip(text: string, max: number): string {
  const t = text.trim().replace(/\s+/g, ' ');
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const at = cut.lastIndexOf(' ');
  return `${(at > max * 0.6 ? cut.slice(0, at) : cut).replace(/[\s,.;:]+$/, '')}…`;
}
