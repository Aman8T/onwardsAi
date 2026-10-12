/**
 * The quiet invitation to posting match, at the foot of the graph screen's profile rail. Same family as the
 * Portfolio and "Save this to your account" invitations (a soft card, one action, one entrance) but smaller and
 * out of their way: they sit under the header and above the radar, this one waits beside the graph. It fades in once,
 * after the radar has drawn and the other two have arrived; no glow, no loop.
 */
import { motion, useReducedMotion } from 'motion/react';
import { EASE } from '../lib/ui';
import { actions } from '../store';
import '../styles/match.css';

/** After the radar's reveal and the two invitations above it, so the cards arrive one after the other. */
const ENTER_DELAY_S = 1.9;

export function MatchInvite({ cinematic }: { cinematic: boolean }) {
  const reduced = useReducedMotion() ?? false;
  const animate = cinematic && !reduced;
  return (
    <motion.aside
      className="mt-invite"
      aria-labelledby="mt-invite-title"
      initial={animate ? { opacity: 0, y: 8 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: EASE, delay: animate ? ENTER_DELAY_S : 0 }}
    >
      <p className="kicker mt-invite-kicker">New · Posting match</p>
      <h3 id="mt-invite-title" className="mt-invite-title">
        See what a job posting asks, against what you can prove
      </h3>
      <button type="button" className="btn mt-btn mt-invite-btn" onClick={() => actions.openMatch()}>
        Match a posting
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M3 7h8M7.5 3.5 11 7l-3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
    </motion.aside>
  );
}
