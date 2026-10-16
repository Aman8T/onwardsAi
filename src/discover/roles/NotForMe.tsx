/**
 * "Not for me": a small question asked of a role the person does not want. "Why not this one? It helps the next
 * search." and six reasons. Every reason hides the role and teaches the search (see `jobsearch/avoid.ts`); the person
 * is never asked to type anything.
 *
 * On wide screens it is a popover above the button that opened it; under 760 px it is a bottom sheet (the same rule
 * every popover in the app follows). Keyboard: focus lands on the first reason when it opens, Escape closes it and
 * returns focus to the button, and a pointer outside closes it.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { AiJob } from '../../../shared/jobsearch';
import { NO_REASONS, NO_REASON_LABEL } from '../jobsearch/avoid';
import type { NoReason } from '../jobsearch/avoid';
import { useLayout } from '../layout';
import { IconClose } from './icons';
import { Sheet } from './Sheet';

/** The question and the reasons; the popover and the sheet both wrap it. */
export function ReasonPicker({ titleId, onChoose, firstRef }: { titleId: string; onChoose: (reason: NoReason) => void; firstRef?: RefObject<HTMLButtonElement | null> }) {
  return (
    <div className="dc-reasons">
      <div>
        <p id={titleId} className="dc-reasons-title">
          Why not this one?
        </p>
        <p className="dc-muted dc-reasons-sub">It helps the next search.</p>
      </div>
      <div className="dc-reasons-list">
        {NO_REASONS.map((reason, i) => (
          <button key={reason} ref={i === 0 ? firstRef : undefined} type="button" className="dc-filter" onClick={() => onChoose(reason)}>
            {NO_REASON_LABEL[reason]}
          </button>
        ))}
      </div>
    </div>
  );
}

export interface NotForMeProps {
  job: Pick<AiJob, 'title' | 'employer'>;
  /** The icon-only button on a card, the small text button in the details panel, or the full-width one in the sheet. */
  variant: 'icon' | 'text' | 'wide';
  /** Which edge of the button the popover lines up with. */
  align?: 'left' | 'right';
  /** Open on first paint (the dev gallery's "not-for-me" state). */
  defaultOpen?: boolean;
  onChoose: (reason: NoReason) => void;
}

export function NotForMe({ job, variant, align = 'right', defaultOpen = false, onChoose }: NotForMeProps) {
  const { narrow } = useLayout();
  const [open, setOpen] = useState(defaultOpen);
  const titleId = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const first = useRef<HTMLButtonElement>(null);

  const close = (restoreFocus: boolean) => {
    setOpen(false);
    if (restoreFocus) requestAnimationFrame(() => button.current?.focus({ preventScroll: true }));
  };

  // the popover: focus on the first reason, a pointer outside or Escape closes it
  useEffect(() => {
    if (!open || narrow) return;
    first.current?.focus({ preventScroll: true });
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      close(true);
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open, narrow]);

  const choose = (reason: NoReason) => {
    setOpen(false);
    onChoose(reason);
  };

  const label = `Not for me: ${job.title} at ${job.employer}`;
  return (
    <span ref={wrap} className="dc-pop-anchor">
      {variant === 'icon' ? (
        <button ref={button} type="button" className="dc-icon-btn" aria-label="Not for me" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <IconClose />
        </button>
      ) : (
        <button
          ref={button}
          type="button"
          className={variant === 'wide' ? 'btn dc-btn dc-btn-fit' : 'btn btn-sm btn-quiet dc-btn-sm'}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          Not for me
        </button>
      )}
      {open && !narrow && (
        <div className={`dc-pop is-${align}`} role="dialog" aria-labelledby={titleId}>
          <ReasonPicker titleId={titleId} onChoose={choose} firstRef={first} />
        </div>
      )}
      <Sheet open={open && narrow} onClose={() => close(true)} label={label}>
        <ReasonPicker titleId={titleId} onChoose={choose} />
      </Sheet>
    </span>
  );
}
