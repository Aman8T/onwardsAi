/**
 * "Why this order": what the ranking uses, in plain words with the person's own values, what it never uses, and a
 * real switch to see the roles in posted order instead (newest first, no ranking). A dropdown beside its button on
 * wide screens, a bottom sheet under 760 px.
 */
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useEffect, useId, useRef, useState } from 'react';
import type { DiscoverOrder } from '../../shared/discover';
import { Sheet } from '../auth/Sheet';
import { EASE, NARROW_QUERY, useMediaQuery } from '../lib/ui';

export interface WhyOrderProps {
  signals: readonly string[];
  order: DiscoverOrder;
  onOrder: (order: DiscoverOrder) => void;
}

function Body({ signals, order, onOrder, titleId, onClose }: WhyOrderProps & { titleId: string; onClose: () => void }) {
  const switchId = useId();
  const posted = order === 'posted';
  return (
    <div className="dc-why-body">
      <button type="button" className="dc-close" aria-label="Close" onClick={onClose}>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" aria-hidden="true" focusable="false">
          <path d="M2 2l8 8M10 2l-8 8" />
        </svg>
      </button>
      <h2 id={titleId} className="serif dc-why-title">
        Why these roles, in this order
      </h2>

      <section aria-label="What we used">
        <p className="kicker">What we used</p>
        {signals.length > 0 ? (
          <ul className="dc-signals">
            {signals.map((s) => (
              <li key={s}>
                <span className="dc-dot" aria-hidden="true" />
                <span>{s}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="dc-muted">Nothing yet. The order appears once roles are listed.</p>
        )}
        {posted && <p className="dc-faint dc-small">Posted order is on, so none of these are used right now.</p>}
      </section>

      <p>
        <strong className="dc-strong">Never used:</strong> your name, photo, college, age or gender.
      </p>

      <div className="dc-switch-row">
        <input id={switchId} className="dc-switch" type="checkbox" role="switch" checked={posted} onChange={(e) => onOrder(e.target.checked ? 'posted' : 'ranked')} />
        <label htmlFor={switchId} className="dc-switch-label">
          <span className="dc-strong">Show in posted order</span>
          <span className="dc-muted dc-small">Newest first, no ranking.</span>
        </label>
      </div>

      <p className="dc-faint dc-small">The order is worked out in code from the signals above. A model never ranks you.</p>
    </div>
  );
}

export function WhyOrder(props: WhyOrderProps) {
  const narrow = useMediaQuery(NARROW_QUERY);
  const reduced = useReducedMotion() ?? false;
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const panelId = useId();

  const close = (restoreFocus: boolean) => {
    setOpen(false);
    if (restoreFocus) btn.current?.focus();
  };

  // wide screens: focus moves in, Escape and a press outside close it
  useEffect(() => {
    if (!open || narrow) return;
    panel.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      e.preventDefault();
      close(true);
    };
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) close(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onDown);
    };
  }, [open, narrow]);

  return (
    <div className="dc-why-anchor" ref={wrap}>
      <button ref={btn} type="button" className="btn btn-quiet btn-sm dc-btn-sm" aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? panelId : undefined} onClick={() => setOpen((o) => !o)}>
        Why this order
      </button>
      {narrow ? (
        <Sheet open={open} onClose={() => close(true)} labelledBy={titleId} className="dc-sheet">
          <Body {...props} titleId={titleId} onClose={() => close(true)} />
        </Sheet>
      ) : (
        <AnimatePresence>
          {open && (
            <motion.div
              ref={panel}
              id={panelId}
              className="panel dc-why"
              role="dialog"
              aria-labelledby={titleId}
              tabIndex={-1}
              initial={reduced ? false : { opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.25, ease: EASE }}
            >
              <Body {...props} titleId={titleId} onClose={() => close(true)} />
            </motion.div>
          )}
        </AnimatePresence>
      )}
    </div>
  );
}
