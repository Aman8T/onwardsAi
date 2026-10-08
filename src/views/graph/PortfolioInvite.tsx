/**
 * The invitation to the portfolio, between the Skill Graph's title and the radar (visible without scrolling).
 *
 * The header's Portfolio button is easy to miss, so the graph itself points at the feature: a quiet card with
 * the person's own radar glyph and one primary action. It fades in once, just after the radar has drawn — the
 * onset of that single motion is what catches the eye (no glow, no loop). Hidden while a domain is drilled into.
 */
import { motion, useReducedMotion } from 'motion/react';
import type { Profile } from '../../../shared/types';
import { RadarGlyph } from '../../components/Radar';
import { EASE } from '../../lib/ui';
import { actions } from '../../store';

/** After the radar's 900 ms reveal (and its dots), so the card arrives as the chart settles. */
const ENTER_DELAY_S = 1.3;

export function PortfolioInvite({ profile, cinematic }: { profile: Profile; cinematic: boolean }) {
  const reduced = useReducedMotion() ?? false;
  const animate = cinematic && !reduced;
  return (
    <motion.aside
      className="now-invite"
      aria-labelledby="now-invite-title"
      initial={animate ? { opacity: 0, y: 8 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: EASE, delay: animate ? ENTER_DELAY_S : 0 }}
    >
      <RadarGlyph className="now-invite-glyph" values={profile.domains.map((d) => d.score)} size={36} />
      <div className="now-invite-copy">
        <p className="kicker now-invite-kicker">New · Portfolio</p>
        <h3 id="now-invite-title" className="now-invite-title">
          Turn this graph into a portfolio page
        </h3>
      </div>
      <button type="button" className="btn btn-primary now-invite-btn" onClick={() => void actions.openPortfolio()}>
        Build my portfolio
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M3 7h8M7.5 3.5 11 7l-3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
    </motion.aside>
  );
}
