/**
 * The match section's addresses. The rest of the app keeps its view in the store and never touches the URL; match
 * pages are the exception because a posting match is something to come back to, link to and press Back from.
 *
 *   /match               paste a posting
 *   /match/:id           the match
 *   /match/:id/resume    the resume built for it
 *   /match/:id/cover     the cover note
 *
 * Pure functions only (the store imports this file), so nothing here may touch the store or the DOM.
 */

export type MatchScreen = 'paste' | 'match' | 'resume' | 'cover';

export interface MatchRoute {
  screen: MatchScreen;
  /** The match id; absent on the paste screen. */
  id?: string;
}

const ID = /^[A-Za-z0-9_-]{1,64}$/;

/** The route a pathname names, or null when it is not a match address. */
export function parseMatchPath(pathname: string): MatchRoute | null {
  const parts = pathname.replace(/\/+$/, '').split('/').filter(Boolean);
  if (parts[0] !== 'match') return null;
  if (parts.length === 1) return { screen: 'paste' };
  const id = parts[1];
  if (!ID.test(id)) return null;
  if (parts.length === 2) return { screen: 'match', id };
  if (parts.length === 3 && parts[2] === 'resume') return { screen: 'resume', id };
  if (parts.length === 3 && parts[2] === 'cover') return { screen: 'cover', id };
  return null;
}

/** The pathname of a route. Screens that need an id fall back to the paste screen without one. */
export function matchPath(route: MatchRoute): string {
  if (!route.id || route.screen === 'paste') return '/match';
  const base = `/match/${encodeURIComponent(route.id)}`;
  return route.screen === 'match' ? base : `${base}/${route.screen}`;
}

export const PASTE_ROUTE: MatchRoute = { screen: 'paste' };

export const sameRoute = (a: MatchRoute, b: MatchRoute) => a.screen === b.screen && (a.id ?? '') === (b.id ?? '');
