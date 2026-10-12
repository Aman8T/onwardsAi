/**
 * The overlay the account screens share: a dialog over a gentle dim, centred on wide screens and a bottom sheet
 * under 760 px (the same rule every popover in the app follows).
 *
 * Rendered in a portal on `document.body`, because the app's view frame animates a transform and a fixed element
 * inside a transformed ancestor stops being fixed to the viewport. Keyboard: focus lands on the dialog when it
 * opens, Tab stays inside it, Escape closes it, and focus goes back to whatever opened it. Motion is opacity plus a
 * short rise; under reduced motion there is no entrance at all.
 */
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useEffect, useRef } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { EASE, NARROW_QUERY, useMediaQuery } from '../lib/ui';
import '../styles/auth.css';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** How far the page behind is dimmed: a hint that it is out of reach, not a curtain. */
const SCRIM_OPACITY = 0.14;

interface SheetProps {
  open: boolean;
  onClose: () => void;
  /** Id of the element that names the dialog. */
  labelledBy: string;
  className?: string;
  children: ReactNode;
}

export function Sheet({ open, onClose, labelledBy, className, children }: SheetProps) {
  const reduced = useReducedMotion() ?? false;
  const narrow = useMediaQuery(NARROW_QUERY);
  const dialog = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.focus({ preventScroll: true });
    return () => opener.current?.focus({ preventScroll: true });
  }, [open]);

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
    if (e.shiftKey && (active === first || active === dialog.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };

  if (typeof document === 'undefined') return null;

  const hidden = reduced ? false : narrow ? { opacity: 0, y: 48 } : { opacity: 0, y: 8 };
  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="auth-layer" key="layer">
          <motion.div
            className="auth-scrim"
            aria-hidden="true"
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: SCRIM_OPACITY }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25, ease: EASE }}
            onClick={onClose}
          />
          <motion.div
            ref={dialog}
            className={['panel auth-sheet', className].filter(Boolean).join(' ')}
            role="dialog"
            aria-modal="true"
            aria-labelledby={labelledBy}
            tabIndex={-1}
            initial={hidden}
            animate={{ opacity: 1, y: 0 }}
            exit={narrow ? { opacity: 0, y: 48 } : { opacity: 0, y: 4 }}
            transition={{ duration: 0.25, ease: EASE }}
            onKeyDown={onKeyDown}
          >
            {children}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
