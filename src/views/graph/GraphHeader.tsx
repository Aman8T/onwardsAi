/**
 * The header of the dashboard and of a next role opened from it: the wordmark, the sections of the app as quiet
 * chips (Dashboard is where the graph lives; there is no Now or Next in any header), and the right-hand cluster:
 * sources status, the Atmosphere menu, Your applications, Share, Ask, the New-analysis confirm and the account control
 * (a "Sign in" button, or the signed-in person's hairline-circle avatar and menu). Sticky, on a solid field tone with
 * a 1px hairline underneath. Styles live in shell.css.
 */
import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useId, useRef, useState } from 'react';
import type { Profile } from '../../../shared/types';
import { AccountMenu } from '../../auth/AccountMenu';
import { Atmosphere } from '../../components/Atmosphere';
import { ShareButton } from '../../card/ShareButton';
import { Brand } from '../../components/Brand';
import { useTracker } from '../../discover/applications/tracker';
import { EASE, plural, timeAgo, useNow } from '../../lib/ui';
import { freeAction } from '../../lib/prompts';
import { actions } from '../../store';

export type GraphHeaderCurrent = 'dashboard' | 'role';

export function GraphHeader({ profile, current }: { profile: Profile; current: GraphHeaderCurrent }) {
  const now = useNow(30_000);
  const nSources = profile.sources.filter((s) => s.ok).length;
  const name = profile.person.name || 'You';
  const analysedAt = Date.parse(profile.createdAt);
  // the roles still moving: a closed one is history, not something waiting on the person
  const open = useTracker(profile.id).items.filter((t) => t.stage !== 'closed').length;

  return (
    <header className="gv-header">
      <Brand onClick={() => actions.showDashboard()} />

      <nav className="gv-nav" aria-label="Sections">
        <button
          type="button"
          className={`gv-navbtn${current === 'dashboard' ? ' is-current' : ''}`}
          aria-current={current === 'dashboard' ? 'page' : undefined}
          onClick={() => actions.showDashboard()}
        >
          Dashboard
        </button>
        <button type="button" className="gv-navbtn" onClick={() => void actions.openPortfolio()}>
          Portfolio
        </button>
        <button type="button" className="gv-navbtn" onClick={() => actions.openMatch()}>
          Match
        </button>
        <button type="button" className="gv-navbtn" onClick={() => actions.openDiscover()}>
          Discover
        </button>
        <button type="button" className="gv-navbtn" onClick={() => actions.openPractice({ screen: 'hub' })}>
          Practice
        </button>
        <button type="button" className="gv-navbtn" onClick={() => actions.openResume()}>
          Resumes
        </button>
      </nav>

      <div className="gv-actions">
        <span className="kicker gv-status">
          {plural(nSources, 'source')} · analysed {Number.isFinite(analysedAt) ? timeAgo(analysedAt, now) : 'recently'}
        </span>
        <button type="button" className="gv-navbtn" onClick={() => actions.openDiscover({ tab: 'applications' })}>
          Your applications{open > 0 ? ` · ${open}` : ''}
        </button>
        <Atmosphere className="gv-atmo" />
        <ShareButton profileId={profile.id} />
        <button type="button" className="btn btn-quiet gv-ask" aria-label="Ask Leafout" onClick={() => actions.openChat(freeAction().origin, '')}>
          Ask
        </button>
        <NewAnalysis name={name} />
        <AccountMenu />
      </div>
    </header>
  );
}

/** "New analysis" button with a small inline confirm panel. */
function NewAnalysis({ name }: { name: string }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const id = useId();
  const first = name.trim().split(/\s+/)[0] || 'this';

  useEffect(() => {
    if (!open) return;
    keep.current?.focus();
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      setOpen(false);
      btn.current?.focus();
    };
    document.addEventListener('pointerdown', onDown);
    window.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      window.removeEventListener('keydown', onKey, true);
    };
  }, [open]);

  return (
    <div className="gv-confirm-anchor" ref={wrap}>
      <button
        ref={btn}
        type="button"
        className="btn gv-new"
        aria-label="New analysis"
        title="New analysis"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? `${id}-dlg` : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        New analysis
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            id={`${id}-dlg`}
            className="panel gv-confirm"
            role="dialog"
            aria-labelledby={`${id}-t`}
            aria-describedby={`${id}-d`}
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.25, ease: EASE }}
          >
            <span className="kicker">New analysis</span>
            <p id={`${id}-t`} className="gv-confirm-title">
              Start over with new sources?
            </p>
            <p id={`${id}-d`} className="gv-confirm-text">
              This clears {first === 'this' ? 'this' : `${first}’s`} graph and every chat thread on this device.
            </p>
            <div className="gv-confirm-actions">
              <button
                ref={keep}
                type="button"
                className="btn"
                onClick={() => {
                  setOpen(false);
                  btn.current?.focus();
                }}
              >
                Keep graph
              </button>
              <button type="button" className="btn btn-primary" onClick={() => actions.startOver()}>
                Start over
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
