/**
 * The quiet invitation to keep a graph: "Save this to your account", under the graph header, for anonymous visitors
 * who have just had an analysis. Same family as the Portfolio invitation (src/views/graph/PortfolioInvite.tsx): a
 * soft card with one primary action that fades in once, after the radar has drawn. No loop, no glow.
 *
 * It never shows for a signed-in person, for the sample persona (nobody owns it), when the server offers no way to
 * sign in, or after "Not now" (remembered per profile on this device). While `/api/me` is still loading it renders
 * nothing, so a signed-in person never sees it flash.
 */
import { motion, useReducedMotion } from 'motion/react';
import { useState } from 'react';
import type { Profile } from '../../shared/types';
import { EASE } from '../lib/ui';
import { signInSheet, isSampleProfile, useMe } from './useMe';
import '../styles/auth.css';

const DISMISSED_KEY = 'tayyari.saveInviteDismissed';
const MAX_REMEMBERED = 20;
/** After the radar's reveal and the Portfolio invitation, so the cards arrive one after the other. */
const ENTER_DELAY_S = 1.6;

function readDismissed(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(DISMISSED_KEY) ?? '[]');
    return Array.isArray(raw) ? raw.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

function writeDismissed(ids: string[]) {
  try {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(ids.slice(-MAX_REMEMBERED)));
  } catch {
    /* storage unavailable: the invitation simply comes back next time */
  }
}

export function SaveInvite({ profile }: { profile: Profile }) {
  const reduced = useReducedMotion() ?? false;
  const { user, providers, loaded } = useMe();
  const [dismissedIds, setDismissedIds] = useState(readDismissed);

  if (!loaded || user || dismissedIds.includes(profile.id) || isSampleProfile(profile.id)) return null;
  if (!providers.google && !providers.magic) return null;

  const dismiss = () => {
    const next = [...dismissedIds, profile.id];
    writeDismissed(next);
    setDismissedIds(next);
  };

  return (
    <motion.aside
      className="auth-invite"
      aria-labelledby="auth-invite-title"
      initial={reduced ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: EASE, delay: reduced ? 0 : ENTER_DELAY_S }}
    >
      <div className="auth-invite-copy">
        <p className="kicker auth-invite-kicker">Your account</p>
        <h3 id="auth-invite-title" className="auth-invite-title">
          Save this to your account
        </h3>
        <p className="auth-invite-text">Come back to it from any device.</p>
      </div>
      <div className="auth-invite-actions">
        <button type="button" className="btn btn-primary" onClick={() => signInSheet.open('save')}>
          Save to my account
        </button>
        <button type="button" className="btn btn-quiet" onClick={dismiss}>
          Not now
        </button>
      </div>
    </motion.aside>
  );
}
