/**
 * Where a match came from, when it was started from a Discover listing.
 *
 *   sessionStorage "tayyari.match.origin"  →  { listingId, title, employer, applyUrl, applyHost, sourceLabel,
 *                                               discoverHref, profileId?, matchId?, runId?, intent? }
 *
 * Written by "See full match" (and by a card's "Apply for me") just before the match starts; read by the match screen
 * to show the origin bar (a way back to Discover, and the employer's own apply link). `matchId` is filled in once the
 * match has an address, so the bar shows only on that match and on nothing the person opens later. `runId` is the
 * search the listing came from (the start check and `/apply/new` read the form address from it), and `intent: 'apply'`
 * says the person pressed Apply for me on the card, so the match opens its "How should we apply?" choice on arrival.
 * Cleared when a different match is started from the paste screen. It holds what the bar prints and nothing about the
 * person's area or profile beyond the ids.
 *
 * A small subscribable store over sessionStorage, because sessionStorage raises no event inside its own document.
 * Every storage access is wrapped: with storage blocked there is simply no origin bar.
 */
import { useSyncExternalStore } from 'react';
import { safeHref } from './util';

export const ORIGIN_KEY = 'tayyari.match.origin';

export interface MatchOrigin {
  listingId: string;
  title: string;
  employer: string;
  applyUrl: string;
  applyHost: string;
  sourceLabel: string;
  /** The Discover address to go back to, with the lens as it was. */
  discoverHref: string;
  profileId?: string;
  matchId?: string;
  /** The Discover search the listing came from. */
  runId?: string;
  /** The person pressed Apply for me on the listing: the match opens its choice on arrival. */
  intent?: 'apply';
}

const ID = /^[A-Za-z0-9_:.-]{1,128}$/;
const RUN_ID = /^[A-Za-z0-9_-]{1,96}$/;
const str = (v: unknown, max = 300): string | undefined => (typeof v === 'string' && v !== '' && v.length <= max ? v : undefined);

/** Whatever was stored, as a clean origin or null. Only plain web addresses and same-site Discover paths survive. */
export function parseOrigin(raw: unknown): MatchOrigin | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const o = raw as Record<string, unknown>;
  const listingId = str(o.listingId, 128);
  const title = str(o.title);
  const employer = str(o.employer);
  const applyUrl = str(o.applyUrl, 2000);
  const applyHost = str(o.applyHost, 200);
  const sourceLabel = str(o.sourceLabel);
  const discoverHref = str(o.discoverHref, 1000);
  if (!listingId || !ID.test(listingId) || !title || !employer || !applyUrl || !applyHost || !sourceLabel || !discoverHref) return null;
  if (!safeHref(applyUrl) || !/^\/discover(\?|$)/.test(discoverHref)) return null;
  const profileId = str(o.profileId, 128);
  const matchId = str(o.matchId, 64);
  const runId = str(o.runId, 96);
  return {
    listingId,
    title,
    employer,
    applyUrl,
    applyHost,
    sourceLabel,
    discoverHref,
    ...(profileId ? { profileId } : {}),
    ...(matchId ? { matchId } : {}),
    ...(runId && RUN_ID.test(runId) ? { runId } : {}),
    ...(o.intent === 'apply' ? { intent: 'apply' as const } : {}),
  };
}

function read(): MatchOrigin | null {
  try {
    return parseOrigin(JSON.parse(sessionStorage.getItem(ORIGIN_KEY) ?? 'null'));
  } catch {
    return null;
  }
}

let current: MatchOrigin | null = read();
const listeners = new Set<() => void>();

function commit(next: MatchOrigin | null): void {
  current = next;
  try {
    if (next) sessionStorage.setItem(ORIGIN_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(ORIGIN_KEY);
  } catch {
    /* storage blocked: the origin lives for this page only */
  }
  listeners.forEach((l) => l());
}

export const readOrigin = (): MatchOrigin | null => current;

/** A match is about to start from this listing. */
export function writeOrigin(origin: MatchOrigin): void {
  commit(parseOrigin(origin));
}

/** The match now has an address: remember which one, so the bar shows on that match only. */
export function bindOrigin(matchId: string): void {
  if (current && !current.matchId) commit({ ...current, matchId });
}

/** A different match is starting (or the person left): the bar goes. */
export function clearMatchOrigin(): void {
  if (current) commit(null);
}

export function useOrigin(): MatchOrigin | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => current,
    () => null,
  );
}
