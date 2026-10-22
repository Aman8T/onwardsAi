import { motion, useIsPresent } from 'motion/react';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { EASE, cssVars } from '../lib/ui';
import { ValueLine } from './ValueLine';
import '../styles/kit.css';

/** One "tag + sentence" line, e.g. { tag: 'Strength', text: '…' }. */
export interface PopoverBullet {
  tag: string;
  text: string;
}

/** A skill row inside a domain popover. */
export interface PopoverSkill {
  id: string;
  name: string;
  score: number;
}

/** Everything a popover shows; the Now and Next views each build one per selection. */
export interface PopoverModel {
  /** Identity of the selection; placement is recomputed and focus re-applied when it changes. */
  key: string;
  kicker: string;
  title: string;
  /** 0 to 100. Shows the numeral row and the value line. */
  score?: number;
  /** Text after the numeral, default "/ 100". */
  suffix?: string;
  /** Short chip beside the numeral, e.g. a tier name. */
  badge?: string;
  /** Benchmark tick on the value line, 0 to 100. */
  target?: number;
  summary?: string;
  bullets?: PopoverBullet[];
  foot?: { label: string; value: string };
  /** What practice showed for a skill ("Shaky on 16 Oct"); it never changes the score. */
  practised?: string;
  skills?: PopoverSkill[];
  /** Label of the primary action. */
  cta: string;
}

export interface PopoverProps {
  model: PopoverModel;
  /** Desktop placement: given the popover's own height, returns its `left`/`top` in the parent. */
  place?: (height: number) => { left: number; top: number };
  /** Bottom sheet (narrow screens) instead of an anchored card. */
  sheet?: boolean;
  /** Card width in px, default 304. */
  width?: number;
  /** Where focus lands when the popover opens (keyboard activation); `false` leaves it alone. */
  autoFocus?: 'dialog' | 'cta' | false;
  onClose: () => void;
  onAct: () => void;
  onPickSkill?: (id: string, e: ReactMouseEvent<HTMLButtonElement>) => void;
  className?: string;
}

/**
 * The one popover shell for the Now and Next graphs: a dialog card (or bottom sheet) with kicker, title,
 * score line, copy, skill rows and a primary action. Escape handling stays with the parent.
 */
export function Popover({
  model,
  place,
  sheet = false,
  width = 304,
  autoFocus = false,
  onClose,
  onAct,
  onPickSkill,
  className,
}: PopoverProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const ctaRef = useRef<HTMLButtonElement>(null);
  const isPresent = useIsPresent();
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  // Anchored card: ask the parent for a spot once the real height is known, and again if it changes.
  useLayoutEffect(() => {
    const el = dialogRef.current;
    if (!el || sheet || !place) return;
    const apply = () => {
      const next = place(el.offsetHeight);
      setPos((prev) => (prev && prev.left === next.left && prev.top === next.top ? prev : next));
    };
    apply();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, [model.key, place, sheet]);

  useEffect(() => {
    if (!autoFocus) return;
    (autoFocus === 'cta' ? ctaRef : dialogRef).current?.focus({ preventScroll: true });
  }, [model.key, autoFocus]);

  const { score, bullets, skills, foot, practised } = model;
  const classes = ['pop', sheet && 'pop-sheet', className].filter(Boolean).join(' ');
  const hidden = sheet ? { opacity: 0, y: 48 } : { opacity: 0, y: 8, scale: 0.98 };
  const shown = sheet ? { opacity: 1, y: 0 } : { opacity: 1, y: 0, scale: 1 };
  const leaving = sheet ? { opacity: 0, y: 48 } : { opacity: 0, y: 4 };
  const style = sheet ? undefined : { ...cssVars({ '--pop-w': `${width}px` }), ...pos };

  return (
    <>
      {/* Sheet only: a transparent catcher so a tap outside dismisses. Inert while the sheet is leaving. */}
      {sheet && <div className="pop-scrim" aria-hidden="true" style={isPresent ? undefined : { pointerEvents: 'none' }} onClick={onClose} />}
      <motion.div
        ref={dialogRef}
        className={classes}
        role="dialog"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={style}
        initial={hidden}
        animate={shown}
        exit={leaving}
        transition={{ duration: 0.25, ease: EASE }}
      >
        <button type="button" className="pop-close" aria-label="Close" onClick={onClose}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" aria-hidden="true">
            <path d="M2 2l8 8M10 2l-8 8" />
          </svg>
        </button>

        <p className="kicker">{model.kicker}</p>
        <h3 className="pop-title" id={titleId}>
          {model.title}
        </h3>

        {score !== undefined && (
          <>
            <div className="pop-score">
              <span className="numeral pop-num">{Math.round(score)}</span>
              <span className="pop-suffix">{model.suffix ?? '/ 100'}</span>
              {model.badge && <span className="chip">{model.badge}</span>}
            </div>
            <ValueLine className="pop-vl" value={score} target={model.target} />
          </>
        )}

        {model.summary && <p className="pop-summary">{model.summary}</p>}

        {bullets && bullets.length > 0 && (
          <ul className="pop-bullets">
            {bullets.map((b, i) => (
              <li key={`${b.tag}:${i}`}>
                <span className="kicker pop-tag">{b.tag}</span> <span>{b.text}</span>
              </li>
            ))}
          </ul>
        )}

        {foot && (
          <p className="pop-foot">
            <span className="kicker pop-tag">{foot.label}:</span> {foot.value}
          </p>
        )}

        {practised && (
          <p className="pop-foot">
            <span className="kicker pop-tag">Practised:</span> {practised}
          </p>
        )}

        {skills && skills.length > 0 && (
          <ul className="pop-skills">
            {skills.map((s) => (
              <li key={s.id}>
                <button type="button" className="pop-skill" onClick={(e) => onPickSkill?.(s.id, e)}>
                  <span>{s.name}</span>
                  <span className="numeral">{Math.round(s.score)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <button ref={ctaRef} type="button" className="btn btn-primary pop-cta" onClick={onAct}>
          {model.cta}
        </button>
      </motion.div>
    </>
  );
}
