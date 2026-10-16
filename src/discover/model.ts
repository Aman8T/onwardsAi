/**
 * Words and small decisions for the Discover screens, kept apart from the markup so they can be read (and checked)
 * on their own. Pure functions of the contract in `shared/discover.ts`; nothing here touches the DOM or the store.
 *
 * The rules this file keeps (project-soul.md): a quick fit is a word and counts, never a number out of a hundred;
 * a salary is shown only when the employer states it; every distance says "straight line"; the apply button names
 * where it goes; the person's own skills are never written from a posting.
 */
import type {
  Cite,
  DiscoverReach,
  DiscoverResponse,
  DiscoverWorkMode,
  LensStop,
  ListingCard,
  Place,
  QuickFit,
  QuickFitWord,
} from '../../shared/discover';
import { plural } from '../lib/ui';

export const FIT_LABEL: Record<QuickFitWord, string> = {
  looks_close: 'Looks close',
  worth_a_look: 'Worth a look',
  stretch: 'A stretch',
  cant_tell: "Can't tell yet",
};

export const MODE_LABEL: Record<DiscoverWorkMode, string> = {
  onsite: 'on-site',
  hybrid: 'hybrid',
  remote: 'remote',
  unknown: '',
};

/** Roles shown as one flat list, with no group headings, when there are fewer than this. */
export const FLAT_BELOW = 12;
/** Rows a group shows before "See all N". */
export const GROUP_ROWS = 3;

export const REACH_ORDER: readonly DiscoverReach[] = ['3', '5', '10', '25', 'city'];

/* ───────────────────────── a listing, in words ───────────────────────── */

const trimYears = (n: number) => String(Number(n.toFixed(1)));

/** "Names 9 skills · 7 in your profile · 2 not found" (and the two honest variants: years, and too little to tell). */
export function countLine(fit: QuickFit): string {
  const { word, named, matched, yearsAsked, yearsYou } = fit;
  if (word === 'cant_tell') {
    return named < 3
      ? `The posting names ${plural(named, 'skill')}, too few to tell.`
      : `The posting names ${named} skills. Your sources mention ${matched} of them.`;
  }
  if (word === 'stretch' && yearsAsked !== undefined && yearsYou !== undefined && yearsAsked - yearsYou >= 3) {
    return `Asks ${yearsAsked}+ years · you show ${trimYears(yearsYou)}`;
  }
  return `Names ${plural(named, 'skill')} · ${matched} in your profile · ${Math.max(0, named - matched)} not found`;
}

/** "Not found by name: GraphQL, AWS", or '' when the posting named nothing the profile lacks. */
export function notFoundLine(fit: QuickFit): string {
  return fit.notFound.length > 0 ? `Not found by name: ${fit.notFound.join(', ')}` : '';
}

export const fmtKm = (km: number) => (Number.isInteger(km) ? String(km) : km.toFixed(1));

/** The place as a person reads it: the locality alone when the city is the one they are looking in. */
export function placeText(place: Place, areaCity?: string): string {
  if (place.locality && place.city) return areaCity && place.city === areaCity ? place.locality : `${place.locality}, ${place.city}`;
  return place.label;
}

/** "Koramangala · hybrid · 5 km straight line". The distance appears only when the server gave one. */
export function placeLine(card: ListingCard, areaCity?: string): string {
  const parts = [placeText(card.place, areaCity)];
  const mode = MODE_LABEL[card.workMode];
  if (mode && !card.place.label.toLowerCase().startsWith('remote')) parts.push(mode);
  if (card.place.km !== undefined) parts.push(`${fmtKm(card.place.km)} km straight line`);
  return parts.join(' · ');
}

const dayStart = (t: number) => {
  const d = new Date(t);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
};

/** "today", "yesterday", "3 days ago": how long ago the employer's board last listed the role. */
export function checkedWord(iso: string, now: number = Date.now()): string {
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return 'recently';
  const days = Math.max(0, Math.round((dayStart(now) - dayStart(at)) / 86_400_000));
  return days === 0 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`;
}

/** "Posted 3 days ago · checked today"; the other freshness phrases already say what they mean. */
export function freshLine(card: ListingCard, now: number = Date.now()): string {
  return /^Posted\b/.test(card.freshness) ? `${card.freshness} · checked ${checkedWord(card.checkedAt, now)}` : card.freshness;
}

export const salaryLine = (card: ListingCard) => (card.salary ? `${card.salary.text} · stated by employer` : 'Salary not stated');

/** `resume · lines 9–12` or `GitHub · riya/ui-kit`. */
export const citeLine = (cite: Cite) => [cite.source, cite.text].filter(Boolean).join(' · ');

/** The button's words: it names where it goes. */
export const applyLabel = (card: Pick<ListingCard, 'applyHost'>) => `Apply on ${card.applyHost} ↗`;

/** A link from a source we do not control renders only when it is a plain web address. */
export function safeHref(url: string): string | undefined {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' || u.protocol === 'http:' ? url : undefined;
  } catch {
    return undefined;
  }
}

export function hostOf(url: string): string | undefined {
  try {
    return new URL(url).hostname.replace(/^www\./, '') || undefined;
  } catch {
    return undefined;
  }
}

/* ───────────────────────── the screen, in words ───────────────────────── */

const roles = (n: number) => (n === 1 ? 'role' : 'roles');

/** Where the numbers apply: "within 10 km" or "in Bengaluru". */
export function whereText(res: DiscoverResponse): string {
  if (res.lane === 'remote') return 'open to India, remote';
  if (res.lane === 'city') return `in ${res.area.city ?? 'that city'}`;
  return res.reach === 'city' || !res.area.locality ? `in ${res.area.city ?? 'your city'}` : `within ${res.reach} km`;
}

/** The serif line: "22 roles in Bengaluru fit where you're heading". */
export function headline(res: DiscoverResponse, otherCity?: string): string {
  const n = res.total;
  // the city lane shows strong fits only, whatever the breadth
  const fit = res.lane === 'city' ? "where you're heading" : res.breadth === 'role' ? 'the work you do now' : res.breadth === 'stretch' ? '' : "where you're heading";
  const verb = n === 1 ? 'fits' : 'fit';
  const base =
    res.lane === 'remote'
      ? `${n} remote ${roles(n)} open to India`
      : res.lane === 'city'
        ? `${n} ${roles(n)} in ${otherCity ?? res.area.city ?? 'that city'}`
        : `${n} ${roles(n)} ${whereText(res)}`;
  if (n === 0) {
    return res.lane === 'remote' ? 'No remote roles open to India fit yet' : `No roles ${res.lane === 'city' ? `in ${otherCity ?? 'that city'}` : whereText(res)} fit yet`;
  }
  return fit ? `${base} ${verb} ${fit}` : `${base}, some of them a stretch`;
}

/** The count after a group's title: "· 11 within 10 km". */
export function groupCountText(res: DiscoverResponse, count: number, otherCity?: string): string {
  if (res.lane === 'remote') return `· ${count} India-eligible`;
  if (res.lane === 'city') return `· ${count} in ${otherCity ?? res.area.city ?? 'that city'}`;
  return `· ${count} ${res.reach === 'city' || !res.area.locality ? `in ${res.area.city ?? 'your city'}` : `within ${res.reach} km`}`;
}

/** The area as one line: "HSR Layout, Bengaluru". */
export const areaText = (area: DiscoverResponse['area']) => [area.locality, area.city].filter(Boolean).join(', ');

/** The one suggested widening as a button: the next reach that adds roles. */
export function widenAction(res: DiscoverResponse): { label: string; reach: DiscoverReach } | null {
  if (res.lane !== 'near' || !res.suggestion) return null;
  const at = REACH_ORDER.indexOf(res.reach);
  for (const reach of REACH_ORDER.slice(at + 1)) {
    const stop = res.reachStops.find((s) => s.value === reach);
    if (stop && stop.count > 0) return { label: reach === 'city' ? 'Show the whole city' : `Widen to ${stop.label}`, reach };
  }
  return null;
}

/* ───────────────────────── states ───────────────────────── */

/** True while the employers' boards have never been read: nothing to show yet, and the server says so. */
export const isFirstRefresh = (res: DiscoverResponse) => !res.refreshedAt && res.coverage.listings === 0 && res.total === 0;

/** "AWS", "AWS and GraphQL", "AWS, GraphQL and Angular". */
export const joinNames = (names: readonly string[]) => (names.length <= 1 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`);

/** The skills postings name most that the profile does not show, for "Most of these ask for X and Y". */
export function topNotFound(listings: readonly ListingCard[], limit = 3): string[] {
  const counts = new Map<string, number>();
  for (const l of listings) for (const name of l.fit.notFound) counts.set(name, (counts.get(name) ?? 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([name]) => name);
}

/** Most of what is listed reads "Can't tell yet": the profile is too short for a closer word. */
export const isThin = (listings: readonly ListingCard[]) => listings.length > 0 && listings.filter((l) => l.fit.word === 'cant_tell').length * 2 >= listings.length;

/** Every listed role is a stretch or can't be told: nothing here fits well yet. */
export const isAllWeak = (listings: readonly ListingCard[]) => listings.length > 0 && listings.every((l) => l.fit.word === 'stretch' || l.fit.word === 'cant_tell');

export type EmptyKind =
  | { kind: 'reach'; stop: LensStop; remote: number }
  | { kind: 'breadth'; stop: LensStop; remote: number }
  | { kind: 'none'; remote: number };

/** Why a lane is empty, and what would change that: a wider reach, a wider breadth, or nothing yet. */
export function emptyReason(res: DiscoverResponse): EmptyKind {
  const remote = res.lane === 'near' ? res.laneCounts.remote : 0;
  if (res.lane === 'near') {
    const at = REACH_ORDER.indexOf(res.reach);
    for (const reach of REACH_ORDER.slice(at + 1)) {
      const stop = res.reachStops.find((s) => s.value === reach);
      if (stop && stop.count > 0) return { kind: 'reach', stop, remote };
    }
  }
  const order = ['role', 'next', 'stretch'];
  for (const b of order.slice(order.indexOf(res.breadth) + 1)) {
    const stop = res.breadthStops.find((s) => s.value === b);
    if (stop && stop.count > 0) return { kind: 'breadth', stop, remote };
  }
  return { kind: 'none', remote };
}
