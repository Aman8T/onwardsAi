/**
 * Small pure helpers for the report's owner sheet: the words around the counts, the headline, and safe addresses.
 * Nothing here touches the DOM or the network, and nothing here is ever a percentage, a rank or a score.
 */
import type { Pip } from '../components/materials';
import type { ReportArea, ReportContent, ReportRole } from '../../shared/report';

const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'] as const;

/** "three" for 3; past ten, the digits. */
export const inWords = (n: number): string => WORDS[n] ?? String(n);

/** "5 of 6 asks shown": the dots' count in words. */
export const askCount = (role: Pick<ReportRole, 'have' | 'total'>): string => `${role.have} of ${role.total} ${role.total === 1 ? 'ask' : 'asks'} shown`;

/** One dot per ask: filled for an ask shown with evidence, dashed for one not shown yet. */
export function askPips(role: Pick<ReportRole, 'have' | 'total'>): Pip[] {
  const total = Math.max(0, role.total);
  const have = Math.min(Math.max(0, role.have), total);
  return Array.from({ length: total }, (_, i) => (i < have ? 'on' : 'no'));
}

/** `Role.eta` as the graph wrote it, with its "~" said in words ("~2 months" reads "About 2 months"). */
export function etaLabel(eta: string): string {
  const t = eta.trim();
  if (!t) return '';
  if (t.startsWith('~')) return `About ${t.slice(1).trim()}`;
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** Who the page is about: the owner's first name and initial, or "This person". */
export const whoOf = (content: ReportContent): string => content.person.name?.trim() || 'This person';

/** The report's headline, as a visitor reads it. */
export function headline(content: ReportContent): string {
  const who = whoOf(content);
  const n = content.roles.length;
  if (n === 0) return `${who}'s resume doesn't back a move yet.`;
  if (n === 1) return `${who}'s resume backs one role today.`;
  return `${who} can credibly move to ${inWords(n)} roles.`;
}

/** The strongest areas as names (at most `max`), and how many more there are; the graph's order breaks a tie. */
export function topAreas(areas: ReportArea[], max = 3): { names: string[]; more: number } {
  const ranked = areas
    .map((a, i) => ({ a, i }))
    .sort((x, y) => y.a.score - x.a.score || x.i - y.i)
    .slice(0, max)
    .map((x) => x.a.short || x.a.name);
  return { names: ranked, more: Math.max(0, areas.length - ranked.length) };
}

/** A link only if it is an address of ours or a web address: never `javascript:` or the like. */
export const safeHref = (u: string | undefined): string | undefined => (u && /^(https?:\/\/|\/)/i.test(u) ? u : undefined);

/** The paid step's address for one role: the configured link with `role=` added. */
export function offerHref(offerUrl: string | undefined, title: string): string | undefined {
  const base = safeHref(offerUrl);
  if (!base) return undefined;
  return `${base}${base.includes('?') ? '&' : '?'}role=${encodeURIComponent(title)}`;
}
