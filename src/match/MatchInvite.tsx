/**
 * The quiet invitation to posting match, at the foot of the graph screen's profile rail: one human nudge, written on a
 * blue post-it with one action, smaller and out of the way of the invitations above the radar. It fades in once,
 * after the radar has drawn and the other two have arrived; no glow, no loop.
 */
import { motion, useReducedMotion } from 'motion/react';
import type { CSSProperties } from 'react';
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
      <div className="stick is-flow">
        <div className="postit" style={{ '--pp': 'var(--paper-blue)' } as CSSProperties}>
          <p className="pcite mt-invite-kicker">New · Posting match</p>
          <h3 id="mt-invite-title" className="pen mt-invite-title">
            See what a job posting asks, against what you can prove
          </h3>
          <button type="button" className="pbtn mt-invite-btn" onClick={() => actions.openMatch()}>
            Match a posting →
          </button>
        </div>
      </div>
    </motion.aside>
  );
}
