/**
 * Graph header (calm theme): the wordmark, a text segmented control Now | Next with one
 * 1px underline that slides between the two, and the right-hand cluster: sources status,
 * the Atmosphere menu, Portfolio, Ask, the New-analysis confirm and the account control
 * (a "Sign in" button, or the signed-in person's hairline-circle avatar and menu).
 * Sticky, on a solid field tone with a 1px hairline underneath. Styles live in shell.css.
 */
import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { Profile } from '../../../shared/types';
import { AccountMenu } from '../../auth/AccountMenu';
import { Atmosphere } from '../../components/Atmosphere';
import { ShareButton } from '../../card/ShareButton';
import { Brand } from '../../components/Brand';
import { EASE, cssVars, plural, timeAgo, useNow } from '../../lib/ui';
import { freeAction } from '../../lib/prompts';
import { actions, useApp } from '../../store';

const FLIPPED_ONCE_KEY = 'tayyari.flippedOnce';

function readFlippedOnce(): boolean {
  try {
    return localStorage.getItem(FLIPPED_ONCE_KEY) === '1';
  } catch {
    return false;
  }
}
function writeFlippedOnce() {
  try {
    localStorage.setItem(FLIPPED_ONCE_KEY, '1');
  } catch {
    /* storage unavailable — the tease dot just comes back next time */
  }
}

/**
 * Measures the active segment button so one underline can slide under it. Reads the layout
 * box (offsetLeft / offsetWidth, unaffected by ancestor transforms) of the active button
 * relative to the nav, and re-reads whenever the nav or either button changes size
 * (fonts loading, a longer role title, a window resize).
 */
function useSegInk(flipped: boolean) {
  const navRef = useRef<HTMLElement>(null);
  const nowRef = useRef<HTMLButtonElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const [ink, setInk] = useState<{ x: number; w: number } | null>(null);

  useLayoutEffect(() => {
    const measure = () => {
      const btn = (flipped ? nextRef : nowRef).current;
      if (!btn) return;
      const x = btn.offsetLeft;
      const w = btn.offsetWidth;
      setInk((prev) => (prev && prev.x === x && prev.w === w ? prev : { x, w }));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    for (const el of [navRef.current, nowRef.current, nextRef.current]) if (el) observer.observe(el);
    return () => observer.disconnect();
  }, [flipped]);

  return { navRef, nowRef, nextRef, ink };
}

export function GraphHeader({ profile }: { profile: Profile }) {
  const flipped = useApp((s) => s.flipped);
  const [flippedOnce, setFlippedOnce] = useState(() => flipped || readFlippedOnce());
  useEffect(() => {
    if (flipped && !flippedOnce) {
      setFlippedOnce(true);
      writeFlippedOnce();
    }
  }, [flipped, flippedOnce]);
  const { navRef, nowRef, nextRef, ink } = useSegInk(flipped);

  const now = useNow(30_000);
  const nSources = profile.sources.filter((s) => s.ok).length;
  const nPaths = Math.min(6, profile.roles.length);
  const role = profile.person.currentRole || 'Today';
  const name = profile.person.name || 'You';
  const tease = !flipped && !flippedOnce;
  const analysedAt = Date.parse(profile.createdAt);

  return (
    <header className="gv-header">
      <Brand />

      <nav ref={navRef} className="gv-seg" role="group" aria-label="Graph view">
        <button
          ref={nowRef}
          type="button"
          className="gv-seg-btn"
          aria-pressed={!flipped}
          aria-keyshortcuts="F"
          title="Your current role (press F to flip)"
          onClick={() => actions.setFlipped(false)}
        >
          <span className="gv-seg-label">Now</span>
          <span className="kicker gv-seg-sub">{role}</span>
        </button>
        <button
          ref={nextRef}
          type="button"
          className="gv-seg-btn"
          aria-pressed={flipped}
          aria-keyshortcuts="F"
          title="Your most probable next roles (press F to flip)"
          onClick={() => actions.setFlipped(true)}
        >
          <span className="gv-seg-label">
            Next
            {tease && <span className="gv-tease" aria-hidden="true" />}
          </span>
          <span className="kicker gv-seg-sub">
            {plural(nPaths, 'path')}
            <span className="kbd-hint"> · press F</span>
          </span>
        </button>
        {ink && <span className="gv-seg-ink" aria-hidden="true" style={cssVars({ '--ink-x': `${ink.x}px`, '--ink-w': ink.w })} />}
      </nav>

      <div className="gv-actions">
        <span className="kicker gv-status">
          {plural(nSources, 'source')} · analysed {Number.isFinite(analysedAt) ? timeAgo(analysedAt, now) : 'recently'}
        </span>
        <Atmosphere className="gv-atmo" />
        <button
          type="button"
          className="btn btn-quiet gv-portfolio"
          title="Turn this graph into a portfolio page"
          onClick={() => void actions.openPortfolio()}
        >
          Portfolio
        </button>
        <button
          type="button"
          className="btn btn-quiet gv-portfolio"
          title="Paste a job posting and see what it asks for against what you can prove"
          onClick={() => actions.openMatch()}
        >
          Match
        </button>
        <button
          type="button"
          className="btn btn-quiet gv-portfolio"
          title="Roles near you that fit what you can prove"
          onClick={() => actions.openDiscover()}
        >
          Discover
        </button>
        <button
          type="button"
          className="btn btn-quiet gv-portfolio"
          title="Practise the interview: questions about your own work, with credit only for what you can back up"
          onClick={() => actions.openPractice({ screen: 'hub' })}
        >
          Practice
        </button>
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
