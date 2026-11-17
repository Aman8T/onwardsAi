/**
 * The frame both pricing screens share: the header (the brand, a way back, the plan chip and the account control) and
 * a main region that fades in. Motion goes through `EASE` and `useReducedMotion`.
 */
import { motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';
import { AccountMenu } from '../auth/AccountMenu';
import { Brand } from '../components/Brand';
import { EASE } from '../lib/ui';
import { actions } from '../store';
import { EntitlementChip } from './EntitlementChip';
import '../styles/billing.css';

export function Frame({ backLabel, onBack, busy, children }: { backLabel: string; onBack: () => void; busy?: boolean; children: ReactNode }) {
  const reduced = useReducedMotion() ?? false;
  return (
    <div className="bl-root">
      <header className="bl-header">
        <Brand onClick={() => actions.closePricing()} />
        <div className="bl-header-end">
          <button type="button" className="btn btn-quiet" onClick={onBack}>
            {backLabel}
          </button>
          <EntitlementChip />
          <AccountMenu />
        </div>
      </header>
      <motion.main className="bl-main" initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: EASE }} aria-busy={busy || undefined}>
        {children}
      </motion.main>
    </div>
  );
}
