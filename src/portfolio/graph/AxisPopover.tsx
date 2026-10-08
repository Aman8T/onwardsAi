/**
 * The "Why 85?" popover: a domain's top three skills with one evidence line each.
 *
 * `useAxisPopover` owns the open state and the placement (relative to a positioned "stage" element, so
 * scrolling never moves it); `AxisPopover` draws the dialog. On narrow screens CSS turns the card into a
 * bottom sheet. Escape and a press outside close it, and Escape hands focus back to the control that
 * opened it. Skills under 45 are never listed here, whatever the snapshot carries.
 */
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { EASE, cssVars } from '../../lib/ui';
import type { GraphDomain } from '../../../shared/portfolio';
import { listedSkills } from './geometry';

const POP_W = 340;
const GAP = 10;

/** Open state: where the card sits inside the stage (px) and whether keyboard focus should move into it. */
interface PopState {
  id: string;
  left: number;
  width: number;
  top?: number;
  bottom?: number;
  focus: boolean;
}

export interface AxisPopoverApi {
  /** Attach to the positioned element the card is placed in. */
  stageRef: RefObject<HTMLDivElement | null>;
  state: PopState | null;
  /** Controls call this from their click handler; a second click on the same control closes it. */
  toggle(id: string, trigger: HTMLElement, viaKeyboard: boolean): void;
  close(restoreFocus?: boolean): void;
}

export function useAxisPopover(): AxisPopoverApi {
  const stageRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const [state, setState] = useState<PopState | null>(null);

  const measure = useCallback((id: string, trigger: HTMLElement, focus: boolean): PopState | null => {
    const stage = stageRef.current;
    if (!stage) return null;
    const s = stage.getBoundingClientRect();
    const t = trigger.getBoundingClientRect();
    const width = Math.min(POP_W, s.width);
    const centre = t.left + t.width / 2 - s.left;
    const left = Math.max(0, Math.min(s.width - width, centre - width / 2));
    const upper = t.top + t.height / 2 - s.top < s.height / 2;
    return upper
      ? { id, left, width, top: t.bottom - s.top + GAP, focus }
      : { id, left, width, bottom: s.bottom - t.top + GAP, focus };
  }, []);

  const close = useCallback((restoreFocus = false) => {
    setState(null);
    if (restoreFocus) triggerRef.current?.focus({ preventScroll: true });
  }, []);

  const toggle = useCallback(
    (id: string, trigger: HTMLElement, viaKeyboard: boolean) => {
      if (state?.id === id && triggerRef.current === trigger) {
        close(false);
        return;
      }
      triggerRef.current = trigger;
      setState(measure(id, trigger, viaKeyboard));
    },
    [state?.id, close, measure],
  );

  const openId = state?.id;
  useEffect(() => {
    if (!openId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      close(true);
    };
    const onDown = (e: PointerEvent) => {
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest('[data-pg-pop], [data-pg-trigger]')) return;
      close(false);
    };
    const onResize = () => {
      const el = triggerRef.current;
      // The control may have left the layout (the phone ledger hides above 560px).
      if (!el || el.offsetParent === null) {
        close(false);
        return;
      }
      setState((prev) => (prev ? (measure(prev.id, el, false) ?? prev) : prev));
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown);
    window.addEventListener('resize', onResize);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onDown);
      window.removeEventListener('resize', onResize);
    };
  }, [openId, close, measure]);

  return { stageRef, state, toggle, close };
}

/* ───────────────────────── the card ───────────────────────── */

interface CardProps {
  state: PopState;
  domain: GraphDomain;
  reduced: boolean;
  onClose: () => void;
}

function Card({ state, domain, reduced, onClose }: CardProps) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const rows = listedSkills(domain, 3);

  // Opened with the keyboard: move into the dialog (a pointer open leaves focus where it is).
  useEffect(() => {
    if (state.focus) ref.current?.focus({ preventScroll: true });
  }, [state.focus]);

  const vars: Record<string, string> = { '--pg-pop-l': `${state.left}px`, '--pg-pop-w': `${state.width}px` };
  if (state.top !== undefined) vars['--pg-pop-t'] = `${state.top}px`;
  if (state.bottom !== undefined) vars['--pg-pop-b'] = `${state.bottom}px`;

  return (
    <motion.div
      ref={ref}
      className="pg-pop"
      role="dialog"
      aria-labelledby={titleId}
      tabIndex={-1}
      data-pg-pop=""
      style={cssVars(vars)}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: reduced ? 0.15 : 0.25, ease: EASE }}
    >
      <button type="button" className="pg-pop-close" aria-label="Close" onClick={onClose}>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" aria-hidden="true" focusable="false">
          <path d="M2 2l8 8M10 2l-8 8" />
        </svg>
      </button>
      <div className="pg-pop-head">
        <h3 className="pg-pop-title" id={titleId}>
          Why {domain.score}?
        </h3>
        <span className="pg-pop-sub">
          {domain.name} · {rows.length === 1 ? 'top skill' : `top ${rows.length} skills`}
        </span>
      </div>
      <ul className="pg-pop-list">
        {rows.map((s) => (
          <li key={s.id} className="pg-pop-item">
            <div className="pg-pop-row">
              <span className="pg-pop-name">{s.name}</span>
              <span className="pg-pop-score">{s.score}</span>
            </div>
            {s.evidence ? <p className="pg-pop-ev">{s.evidence}</p> : null}
          </li>
        ))}
      </ul>
    </motion.div>
  );
}

export function AxisPopover({ api, domains }: { api: AxisPopoverApi; domains: GraphDomain[] }) {
  const reduced = useReducedMotion() ?? false;
  const { state } = api;
  const domain = state ? domains.find((d) => d.id === state.id) : undefined;
  return (
    <AnimatePresence>
      {state && domain ? <Card key={state.id} state={state} domain={domain} reduced={reduced} onClose={() => api.close(true)} /> : null}
    </AnimatePresence>
  );
}
