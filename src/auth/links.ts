/**
 * Pure helpers for signing in and out through the Leafout site account: where the links go and which `next` values
 * are accepted. Nothing here touches the DOM or the store, so each is easy to reason about.
 */
import type { MeProviders, MeResponse } from '../../shared/auth';

/** Where the site's sign-out and account deletion send the browser back to; nothing else is accepted. */
export const SITE_SIGNOUT_URL = 'https://leafout.in/signout?from=app';
export const SITE_HOME_URL = 'https://leafout.in/';
export const SITE_ACCOUNT_URL = 'https://leafout.in/account';
export const SITE_DELETE_FINISH_URL = 'https://leafout.in/account/delete-finish';

/** True when the server offers any way to sign in: the Leafout account, Google or an emailed link. */
export function offersSignIn(p: MeProviders): boolean {
  return p.leafout || p.google || p.magic;
}

/** A same-origin path (with its query) or null: no scheme, no `//host`, no backslashes or control characters. */
export function safePath(raw: string | null | undefined): string | null {
  if (!raw || raw[0] !== '/' || raw[1] === '/' || raw[1] === '\\') return null;
  // eslint-disable-next-line no-control-regex
  if (/[\\\u0000-\u001f]/.test(raw)) return null;
  return raw;
}

/** The sign-in link of the Leafout account that brings the person back to `path` on this origin; null when not offered. */
export function signInHref(me: Pick<MeResponse, 'providers'> | null, path: string, origin = window.location.origin): string | null {
  const url = me?.providers.leafout ? me.providers.leafoutSignInUrl : undefined;
  const safe = safePath(path);
  if (!url || !safe) return null;
  return `${url}${url.includes('?') ? '&' : '?'}next=${encodeURIComponent(origin + safe)}`;
}

/** The page the person is on now, without a leftover `?auth=` code. */
export function currentPath(loc: Pick<Location, 'pathname' | 'search'> = window.location): string {
  const params = new URLSearchParams(loc.search);
  params.delete('auth');
  const q = params.toString();
  return loc.pathname + (q ? `?${q}` : '');
}

/** `next` for `/signout`: the site's sign-out, or its home page; anything else is the default. */
export function signOutNext(raw: string | null): string {
  return raw === SITE_HOME_URL || raw === SITE_SIGNOUT_URL ? raw : SITE_SIGNOUT_URL;
}

/* ───────────── the loop guard ───────────── */

export const BOUNCE_KEY = 'leafout.bounces';
export const BOUNCE_WINDOW_MS = 60_000;
export const BOUNCE_LIMIT = 2;

/** The timestamps still inside the window. */
export function recentBounces(stamps: unknown, now: number): number[] {
  if (!Array.isArray(stamps)) return [];
  return stamps.filter((t): t is number => typeof t === 'number' && now - t < BOUNCE_WINDOW_MS && t <= now);
}

/** Two automatic redirects in the last minute means the sign-in is not holding: stop redirecting. */
export function bounceAllowed(stamps: unknown, now: number): boolean {
  return recentBounces(stamps, now).length < BOUNCE_LIMIT;
}

function read(): unknown {
  try {
    return JSON.parse(sessionStorage.getItem(BOUNCE_KEY) ?? '[]');
  } catch {
    return [];
  }
}

/**
 * Take one automatic redirect if the guard allows it (and note it). Without sessionStorage there is no way to count,
 * so the redirect is refused rather than risked.
 */
export function takeBounce(now = Date.now()): boolean {
  const stamps = recentBounces(read(), now);
  if (stamps.length >= BOUNCE_LIMIT) return false;
  try {
    sessionStorage.setItem(BOUNCE_KEY, JSON.stringify([...stamps, now]));
  } catch {
    return false;
  }
  return true;
}
