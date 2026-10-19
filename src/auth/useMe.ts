/**
 * Who is signed in. `useMe()` loads `/api/me` once (the first component to ask starts it) and exposes the `user`,
 * the account's `profiles`, the `entitlements` (and `unlimited`, whether the account is on the server's list with no
 * limits: every screen that would pre-judge a limit reads that one flag), the sign-in `providers`, whether the server
 * matches postings without an account (`anonymousMatch`, summed up as `matchAccess`) and `refresh()`.
 *
 * The state lives in this module, like the app store, so the account menu, the sign-in sheet and the invitation all
 * read the same answer. `useAutoClaim()` (mounted once, in App) attaches the profile on screen to the account as soon
 * as there is both: after a sign-in, and after every analysis a signed-in person runs. A sign-in that goes through
 * Google leaves the page and comes back, which is why the claim runs from here and not from the sheet.
 *
 * `/api/me` answers anonymous visitors too (no `user`), so "not signed in" is an answer, not an error. If the
 * request itself fails the app simply behaves as anonymous.
 */
import { useEffect, useSyncExternalStore } from 'react';
import type { MeEntitlements, MeProfile, MeProviders, MeResponse, MeUser } from '../../shared/auth';
import { isUnlimited } from '../billing/format';
import * as api from '../lib/api';
import { actions, getState, useApp } from '../store';
import { currentPath, offersSignIn, signInHref, takeBounce } from './links';

export type MeStatus = 'idle' | 'loading' | 'ready' | 'error';

interface MeState {
  status: MeStatus;
  me: MeResponse | null;
  /** The site sign-in had to be refreshed and the loop guard refused to send the browser there again. */
  stuck: boolean;
}

let state: MeState = { status: 'idle', me: null, stuck: false };
const listeners = new Set<() => void>();

function set(next: Partial<MeState>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

const NO_PROVIDERS: MeProviders = { google: false, magic: false, leafout: false };
const NO_ENTITLEMENTS: MeEntitlements = { credits: 0, freeMatchAvailable: false };

let inflight: Promise<void> | null = null;

/** Ask the server who is signed in. Calls made while one is in flight share it. */
export function refresh(): Promise<void> {
  if (inflight) return inflight;
  if (state.status === 'idle') set({ status: 'loading' });
  inflight = api
    .getMe()
    .then((me) => {
      set({ status: 'ready', me, stuck: me.reauth ? state.stuck : false });
      sendThroughSiteSignIn(me);
    })
    .catch(() => set({ status: state.me ? 'ready' : 'error' }))
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/**
 * `reauth`: the app just ended a Leafout-bridged session because the site sign-in must be refreshed. Send the browser
 * through the site's sign-in with `next` set to this page, unless two such redirects already happened in the last
 * minute (the loop guard): then stay put and let the page say so.
 */
function sendThroughSiteSignIn(me: MeResponse) {
  if (!me.reauth || me.user) return;
  const href = signInHref(me, currentPath());
  if (!href) return;
  if (takeBounce()) window.location.replace(href);
  else if (!state.stuck) set({ stuck: true });
}

// A sign-in or sign-out in another tab: look again when this one comes back into view.
let watching = false;
function watchFocus() {
  if (watching || typeof window === 'undefined') return;
  watching = true;
  const again = () => {
    if (document.visibilityState === 'visible' && state.status !== 'idle') void refresh();
  };
  window.addEventListener('focus', again);
  document.addEventListener('visibilitychange', again);
}

/** Plain words for a bridge the server refused (`MeResponse.signInIssue`). */
export const SIGN_IN_ISSUES: Record<string, string> = {
  email_taken: 'That email already belongs to a Tayyari account that is linked to a different Leafout sign-in. Sign in with the Leafout account you used before.',
};

/**
 * How the posting match treats the person on this server, from what `/api/me` last said:
 *   account    signed in
 *   anonymous  signed out, and the server matches without an account: nobody is ever asked to sign in
 *   sign-in    signed out, an account is needed, and there is a way to sign in (Google or an emailed link)
 *   stuck      signed out, an account is needed, and this server has no way to sign in: the sheet has nothing to offer
 *   unknown    `/api/me` has not answered (or failed): try the server and let its answer decide
 * A server that does not send `anonymousMatch` is read as "false", so an older one behaves as it always did.
 */
export type MatchAccess = 'account' | 'anonymous' | 'sign-in' | 'stuck' | 'unknown';

function accessOf(s: MeState): MatchAccess {
  const me = s.me;
  if (!me) return 'unknown';
  if (me.user) return 'account';
  if (me.anonymousMatch === true) return 'anonymous';
  return offersSignIn(me.providers) ? 'sign-in' : 'stuck';
}

/** The same answer for code that is not a component (the match store). */
export const matchAccess = (): MatchAccess => accessOf(state);

/** Sample runs belong to nobody: they are never saved to an account. */
export const isSampleProfile = (id: string) => id.startsWith('sample-');

/**
 * End the session. A graph on screen that the account owns is cleared from this device with it (it stays in the
 * account); one that was never saved is left alone, because signing out must not destroy unsaved work.
 */
export async function signOut(): Promise<void> {
  const shownId = getState().profile?.id;
  const owned = !!shownId && !!state.me?.profiles.some((p) => p.id === shownId);
  try {
    await api.logout();
  } catch {
    /* the cookie may already be gone; ask the server what it thinks below */
  }
  await refresh();
  if (owned && !state.me?.user) actions.startOver();
}

export interface MeView {
  status: MeStatus;
  me: MeResponse | null;
  user: MeUser | undefined;
  profiles: MeProfile[];
  entitlements: MeEntitlements;
  /** The account has no limits and is never charged (`entitlements.unlimited`); false for a visitor and when the server says nothing. */
  unlimited: boolean;
  providers: MeProviders;
  /** The server matches postings without an account (it said so; false when it said nothing). */
  anonymousMatch: boolean;
  /** What the match flow does for this person: see `MatchAccess`. */
  matchAccess: MatchAccess;
  /** The sign-in must be refreshed and the loop guard stopped the redirect: show "We couldn't confirm your sign-in". */
  stuck: boolean;
  /** A sentence for a refused bridge (`signInIssue`), when there is one. */
  issue: string | undefined;
  /** True once the server has answered, whether or not anyone is signed in. */
  loaded: boolean;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

export function useMe(): MeView {
  const snap = useSyncExternalStore(subscribe, () => state);
  useEffect(() => {
    if (state.status === 'idle') void refresh();
    watchFocus();
  }, []);
  const { me, status } = snap;
  return {
    status,
    me,
    user: me?.user,
    profiles: me?.profiles ?? [],
    entitlements: me?.entitlements ?? NO_ENTITLEMENTS,
    unlimited: isUnlimited(me?.entitlements),
    providers: me?.providers ?? NO_PROVIDERS,
    anonymousMatch: me?.anonymousMatch === true,
    matchAccess: accessOf(snap),
    stuck: snap.stuck,
    issue: me?.signInIssue ? SIGN_IN_ISSUES[me.signInIssue] : undefined,
    loaded: status === 'ready',
    refresh,
    signOut,
  };
}

/* ───────────────────────── the sign-in sheet ───────────────────────── */

export type SignInReason = 'save' | 'account' | 'buy';

export interface SignInUi {
  open: boolean;
  reason: SignInReason;
  /** A sentence to show above the form, e.g. after a Google sign-in that did not complete. */
  notice?: string;
  /** Where the person returns to after signing in through Leafout (a same-origin path); the current page when absent. */
  next?: string;
  /** Changes every time the sheet is opened, so the form inside starts fresh. */
  opened: number;
}

let ui: SignInUi = { open: false, reason: 'account', opened: 0 };
const uiListeners = new Set<() => void>();

function setUi(next: SignInUi) {
  ui = next;
  uiListeners.forEach((l) => l());
}

export const signInSheet = {
  open(reason: SignInReason = 'account', notice?: string, next?: string) {
    setUi({ open: true, reason, notice, next, opened: ui.opened + 1 });
  },
  close() {
    setUi({ ...ui, open: false });
  },
};

export function useSignInSheet(): SignInUi {
  return useSyncExternalStore(
    (cb) => {
      uiListeners.add(cb);
      return () => uiListeners.delete(cb);
    },
    () => ui,
  );
}

/* ───────────────────────── claiming ───────────────────────── */

/** Pairs already tried, so a refused claim (someone else's profile) is not retried in a loop. */
const claimed = new Set<string>();

/**
 * Attach the profile on screen to the signed-in account, once per account and profile. A profile that already
 * belongs to another account (a shared device) is cleared from the screen instead: it is not this person's.
 */
export function useAutoClaim() {
  const profileId = useApp((s) => s.profile?.id);
  const { me } = useMe();
  const userId = me?.user?.id;
  const listed = me?.profiles.some((p) => p.id === profileId) ?? false;

  useEffect(() => {
    if (!userId || !profileId || isSampleProfile(profileId) || listed) return;
    const key = `${userId}:${profileId}`;
    if (claimed.has(key)) return;
    claimed.add(key);
    api
      .claimProfile(profileId)
      .then((next) => set({ status: 'ready', me: next }))
      .catch((err: unknown) => {
        if (err instanceof api.ApiError && err.status === 409) actions.startOver();
        else void refresh();
      });
  }, [userId, profileId, listed]);
}
