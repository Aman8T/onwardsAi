/**
 * "Recent" for people with no account. `GET /api/matches` lists the matches an account owns, so for a visitor who
 * matched without signing in it is empty; this keeps the last few on this device instead, in localStorage:
 *
 *   tayyari.matches.recent  →  [{ id, title, company?, band, createdAt }, …]   newest first, at most 10
 *
 * Written when a match finishes while nobody is signed in; read for the paste screen's side list when the server has
 * nothing to show; an entry goes when opening it comes back 404 (the server no longer has that match). It holds only
 * what the list prints (the posting's title and company, the band in words), never the posting or the result.
 *
 * Every storage access is wrapped: with storage blocked the list is simply empty and nothing else changes. The pure
 * parts (`parseRecent`) take and return plain values so they can be tried without a browser.
 */
import { BAND_LABEL } from './model';
import type { MatchSummary } from './types';

export const RECENT_KEY = 'tayyari.matches.recent';
export const RECENT_MAX = 10;

const ID = /^[A-Za-z0-9_-]{1,64}$/;
const BANDS = Object.keys(BAND_LABEL);

/** Whatever was stored, as a clean list: valid entries only, no repeated ids (the first wins), at most `RECENT_MAX`. */
export function parseRecent(raw: unknown): MatchSummary[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: MatchSummary[] = [];
  for (const entry of raw) {
    if (typeof entry !== 'object' || entry === null) continue;
    const { id, title, company, band, createdAt } = entry as Record<string, unknown>;
    if (typeof id !== 'string' || !ID.test(id) || seen.has(id)) continue;
    if (typeof title !== 'string' || title.trim() === '') continue;
    if (typeof band !== 'string' || !BANDS.includes(band)) continue;
    seen.add(id);
    out.push({
      id,
      title,
      ...(typeof company === 'string' && company !== '' ? { company } : {}),
      band: band as MatchSummary['band'],
      createdAt: typeof createdAt === 'string' ? createdAt : '',
    });
    if (out.length === RECENT_MAX) break;
  }
  return out;
}

export function readRecent(): MatchSummary[] {
  try {
    return parseRecent(JSON.parse(localStorage.getItem(RECENT_KEY) ?? 'null'));
  } catch {
    return [];
  }
}

function writeRecent(list: readonly MatchSummary[]): void {
  try {
    if (list.length > 0) localStorage.setItem(RECENT_KEY, JSON.stringify(list));
    else localStorage.removeItem(RECENT_KEY);
  } catch {
    /* storage blocked or full: the list lives for this page only */
  }
}

/** Put a finished match at the front (a repeated id moves up) and keep the newest ten. */
export function rememberRecent(entry: MatchSummary): MatchSummary[] {
  const next = parseRecent([entry, ...readRecent()]);
  writeRecent(next);
  return next;
}

/** Drop one entry, e.g. when the server says it no longer has that match. */
export function forgetRecent(id: string): MatchSummary[] {
  const next = readRecent().filter((m) => m.id !== id);
  writeRecent(next);
  return next;
}
