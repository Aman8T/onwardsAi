/**
 * The bottom sheet Discover uses under 760 px: a role's details, the "Why not this one?" reasons. A grab handle, a dim
 * behind it, and the sheet rising from the bottom edge.
 *
 * Rendered in a portal (the app's view frame animates a transform, and a fixed element inside a transformed ancestor
 * stops being fixed to the viewport). In the dev gallery a phone is shown inside a frame, and the sheet goes there
 * instead (`useLayout().frame`). Keyboard: focus lands on the dialog when it opens, Tab stays inside it, Escape closes
 * it, and focus goes back to whatever opened it. The page behind does not scroll. Motion is a rise on EASE; under
 * reduced motion there is no entrance at all.
 */
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useEffect, useRef } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { EASE } from '../../lib/ui';
import { useLayout } from '../layout';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** How far the page behind is dimmed. */
const SCRIM_OPACITY = 0.4;

export interface SheetProps {
  open: boolean;
  onClose: () => void;
  /** Names the dialog for assistive technology. */
  label: string;
  className?: string;
  /** Fixed content under the scrolling body (the actions of a role's details). */
  footer?: ReactNode;
  children: ReactNode;
}

export function Sheet({ open, onClose, label, className, footer, children }: SheetProps) {
  const reduced = useReducedMotion() ?? false;
  const { frame } = useLayout();
  const dialog = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.focus({ preventScroll: true });
    // the page behind stays where it is while the sheet is up (a frame in the gallery has no page to hold still)
    const held = document.body.style.overflow;
    if (!frame) document.body.style.overflow = 'hidden';
    return () => {
      if (!frame) document.body.style.overflow = held;
      opener.current?.focus({ preventScroll: true });
    };
  }, [open, frame]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== 'Tab' || !dialog.current) return;
    const items = Array.from(dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) {
      e.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    // a sheet opened from inside another sheet is its React child: its Tab is its own
    e.stopPropagation();
    if (e.shiftKey && (active === first || active === dialog.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };

  if (typeof document === 'undefined') return null;

  return createPortal(
    <AnimatePresence>
      {open && (
        <div className={`dc-layer${frame ? ' is-frame' : ''}`} key="layer">
          <motion.div
            className="dc-scrim"
            aria-hidden="true"
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: SCRIM_OPACITY }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25, ease: EASE }}
            onClick={onClose}
          />
          <motion.div
            ref={dialog}
            className={['dc-sheet', className].filter(Boolean).join(' ')}
            role="dialog"
            aria-modal="true"
            aria-label={label}
            tabIndex={-1}
            initial={reduced ? false : { y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ duration: 0.25, ease: EASE }}
            onKeyDown={onKeyDown}
          >
            <div className="dc-sheet-grab" aria-hidden="true">
              <span />
            </div>
            <div className="dc-sheet-body">{children}</div>
            {footer && <div className="dc-sheet-foot">{footer}</div>}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    frame ?? document.body,
  );
}
