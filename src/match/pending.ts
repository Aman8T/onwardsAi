/**
 * "Resumes after sign-in". Asking for a match while signed out opens the sign-in sheet; the posting the person pasted
 * is kept here (localStorage, 30 minutes) so that when they come back signed in, the match they asked for starts
 * without a second paste.
 *
 * Both sign-in routes leave the page and return to `/` (Google by redirect, the emailed link in a new tab), so this
 * cannot live in component state; it is read back by `useResumeAfterSignIn`, mounted once in `App`. The key is
 * removed before anything starts, so two tabs, or React's double effect, can never run it twice.
 */
import { useEffect } from 'react';
import { useMe } from '../auth/useMe';
import { actions, useApp } from '../store';
import { pickProfileId } from './model';

const KEY = 'tayyari.match.pending';
const TTL_MS = 30 * 60_000;

export interface Pending {
  text?: string;
  url?: string;
}

export function rememberPending(p: Pending): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ at: Date.now(), ...p }));
  } catch {
    /* storage blocked: the person pastes again after signing in */
  }
}

function peek(): Pending | null {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? 'null');
    if (!raw || typeof raw !== 'object' || typeof raw.at !== 'number' || Date.now() - raw.at > TTL_MS) return null;
    const text = typeof raw.text === 'string' ? raw.text : undefined;
    const url = typeof raw.url === 'string' ? raw.url : undefined;
    return text || url ? { text, url } : null;
  } catch {
    return null;
  }
}

export function clearPending(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to clear */
  }
}

/** True when a recent request is waiting for a sign-in. */
export const hasPending = () => peek() !== null;

/**
 * When a signed-in person with a profile the account owns has a waiting request, open the paste screen and run it.
 * Waiting for the profile to be listed on the account matters: the sign-in claims the profile on screen a moment
 * after it completes, and a match for a profile the account does not own yet would be refused.
 */
export function useResumeAfterSignIn(): void {
  const { user, profiles } = useMe();
  const shownId = useApp((s) => s.profile?.id);
  const profileId = pickProfileId(shownId, profiles);
  const userId = user?.id;

  useEffect(() => {
    if (!userId || !profileId) return;
    const pending = peek();
    if (!pending) return;
    clearPending();
    actions.openMatch({ screen: 'paste' });
    void import('./useMatch').then(({ matchActions }) => matchActions.submit({ profileId, ...pending }, { signedIn: true }));
  }, [userId, profileId]);
}
