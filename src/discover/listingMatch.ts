/**
 * Which posting match "Apply for me" already ran for a Discover listing, in this browser (FE4#7).
 *
 *   localStorage "tayyari.discover.listingMatches"  →  [{ listingId, profileId, matchId, at }, …] (newest first)
 *
 * A second press on the same listing reuses that match (read with a plain `GET /api/match/{id}`, which charges nothing)
 * instead of starting a new, charged one. Only a match that finished and was not blocked is remembered, per profile,
 * and a match that is gone (404) is forgotten. It is a per-browser convenience: storage that is blocked or empty just
 * means a fresh match, as before. Ids only, never the posting or the person's data.
 */

export const LISTING_MATCHES_KEY = 'tayyari.discover.listingMatches';
/** The newest this many are kept. */
export const LISTING_MATCHES_MAX = 50;

const ID = /^[A-Za-z0-9_-]{1,96}$/;

export interface ListingMatch {
  listingId: string;
  profileId: string;
  matchId: string;
  /** When it was remembered (ms since the epoch). */
  at: number;
}

/** The stored list, keeping only well-formed rows (newest first, at most `LISTING_MATCHES_MAX`). */
export function parseListingMatches(raw: unknown): ListingMatch[] {
  if (!Array.isArray(raw)) return [];
  const rows: ListingMatch[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const { listingId, profileId, matchId, at } = item as Record<string, unknown>;
    if (typeof listingId !== 'string' || typeof profileId !== 'string' || typeof matchId !== 'string') continue;
    if (!ID.test(listingId) || !ID.test(profileId) || !ID.test(matchId)) continue;
    rows.push({ listingId, profileId, matchId, at: typeof at === 'number' && Number.isFinite(at) ? at : 0 });
  }
  return rows.sort((a, b) => b.at - a.at).slice(0, LISTING_MATCHES_MAX);
}

function read(): ListingMatch[] {
  try {
    return parseListingMatches(JSON.parse(localStorage.getItem(LISTING_MATCHES_KEY) ?? '[]'));
  } catch {
    return [];
  }
}

function write(rows: readonly ListingMatch[]): void {
  try {
    localStorage.setItem(LISTING_MATCHES_KEY, JSON.stringify(rows.slice(0, LISTING_MATCHES_MAX)));
  } catch {
    /* storage blocked: the next press runs a fresh match, as before */
  }
}

const same = (r: ListingMatch, listingId: string, profileId: string) => r.listingId === listingId && r.profileId === profileId;

/** The match "Apply for me" finished for this listing and profile, if this browser remembers one. */
export function listingMatchFor(listingId: string, profileId: string): string | undefined {
  return read().find((r) => same(r, listingId, profileId))?.matchId;
}

/** Remember the match that finished for this listing (one per listing and profile, the newest wins). */
export function rememberListingMatch(listingId: string, profileId: string, matchId: string, now = Date.now()): void {
  if (!ID.test(listingId) || !ID.test(profileId) || !ID.test(matchId)) return;
  write([{ listingId, profileId, matchId, at: now }, ...read().filter((r) => !same(r, listingId, profileId))]);
}

/** Forget it (the match is gone). */
export function forgetListingMatch(listingId: string, profileId: string): void {
  const rows = read();
  const kept = rows.filter((r) => !same(r, listingId, profileId));
  if (kept.length !== rows.length) write(kept);
}
