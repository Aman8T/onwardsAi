/**
 * Auto-apply's addresses. Pure functions only, so the gallery, the shell and the entry points elsewhere in the app can
 * all build links to the same places.
 *
 *   /apply                                              Applications (canvas 6)
 *   /apply/new?match=<id>&resume=tailored|saved         Apply for me (canvas 1, with the first check and consent first)
 *             [&listing=<listing id>&run=<run id>]      the Discover listing the match came from
 *   /apply/details                                      Your details (canvas 2)
 *   /apply/<id>                                         one application, shown by its stage
 *
 * The old tracker's `/apply?id=<id>` still opens that application; `canonicalApplyPath` says where to `replaceState` it.
 */
import type { ResumeChoice } from '../../shared/apply';

export type ApplyRoute =
  | { screen: 'list' }
  | { screen: 'new'; matchId?: string; resume: ResumeChoice; listingId?: string; runId?: string }
  | { screen: 'details' }
  | { screen: 'one'; id: string };

/** Application, match and run ids: the server's own shape. */
const ID = /^[A-Za-z0-9_-]{1,96}$/;
/** Discover listing ids may carry a board prefix (`greenhouse:acme:123`). */
const LISTING = /^[A-Za-z0-9_.:-]{1,200}$/;

export const LIST_ROUTE: ApplyRoute = { screen: 'list' };
export const DETAILS_ROUTE: ApplyRoute = { screen: 'details' };

const idOr = (raw: string | null, shape: RegExp): string | undefined => (raw && shape.test(raw) ? raw : undefined);

/** The route a pathname and query name, or null when the address is not under `/apply`. */
export function parseApplyRoute(pathname: string, search: string): ApplyRoute | null {
  const parts = pathname.replace(/\/+$/, '').split('/').filter(Boolean);
  if (parts[0] !== 'apply') return null;
  const q = new URLSearchParams(search);
  if (parts.length === 1) {
    const legacy = idOr(q.get('id'), ID);
    return legacy ? { screen: 'one', id: legacy } : LIST_ROUTE;
  }
  if (parts.length !== 2) return null;
  const [, second] = parts;
  if (second === 'details') return DETAILS_ROUTE;
  if (second === 'new') {
    const route: ApplyRoute = { screen: 'new', resume: q.get('resume') === 'tailored' ? 'tailored' : 'saved' };
    const matchId = idOr(q.get('match'), ID);
    const listingId = idOr(q.get('listing'), LISTING);
    const runId = idOr(q.get('run'), ID);
    if (matchId) route.matchId = matchId;
    if (listingId) route.listingId = listingId;
    if (runId) route.runId = runId;
    return route;
  }
  return ID.test(second) ? { screen: 'one', id: second } : null;
}

/** The address of a route. Query parameters come in a fixed order so equal routes give equal strings. */
export function applyPath(route: ApplyRoute): string {
  switch (route.screen) {
    case 'list':
      return '/apply';
    case 'details':
      return '/apply/details';
    case 'one':
      return `/apply/${encodeURIComponent(route.id)}`;
    case 'new': {
      const q = new URLSearchParams();
      if (route.matchId) q.set('match', route.matchId);
      q.set('resume', route.resume);
      if (route.listingId) q.set('listing', route.listingId);
      if (route.runId) q.set('run', route.runId);
      return `/apply/new?${q.toString()}`;
    }
  }
}

/**
 * Where the address bar should say the page is: the route's own path when the current address spells it differently
 * (`/apply?id=x`, a trailing slash, unknown query keys), else null (nothing to replace).
 */
export function canonicalApplyPath(pathname: string, search: string): string | null {
  const route = parseApplyRoute(pathname, search);
  if (!route) return null;
  const canonical = applyPath(route);
  return canonical === `${pathname}${search}` ? null : canonical;
}

export const sameApplyRoute = (a: ApplyRoute, b: ApplyRoute): boolean => applyPath(a) === applyPath(b);

/** Move inside the apply shell; `replace` swaps the history entry (a changed choice on the same screen). */
export type Navigate = (route: ApplyRoute, opts?: { replace?: boolean }) => void;

interface ClickLike {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  preventDefault(): void;
}

/**
 * Props for a real `<a href>` that moves inside the apply shell without a page load. A modified click (new tab, new
 * window) or a middle click is left to the browser.
 */
export function linkTo(route: ApplyRoute, navigate: Navigate): { href: string; onClick: (e: ClickLike) => void } {
  return {
    href: applyPath(route),
    onClick: (e) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      navigate(route);
    },
  };
}
