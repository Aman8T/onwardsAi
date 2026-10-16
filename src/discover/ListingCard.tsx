/**
 * The card for one listing, in the order a person needs it: the role and who is hiring; where it is, how far
 * (a straight line) and how fresh; the quick-fit word with its counts; two reasons, each citing the person's own
 * evidence; the biggest gap, named; the salary only if the employer states it; then the actions, with the apply
 * button naming where it goes; and last, what the fit was read from.
 *
 * On wide screens it is a popover beside the row it opened from; under 760 px it is a bottom sheet (the shared
 * `Sheet`). Keyboard: opening moves focus into the card, Tab stays inside the sheet, Escape closes and focus goes
 * back to the row's title. A click outside closes the popover.
 */
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ListingCard } from '../../shared/discover';
import { Sheet } from '../auth/Sheet';
import { EASE, NARROW_QUERY, useMediaQuery } from '../lib/ui';
import { ApplyLink, FitChip, SaveButton } from './ListingRow';
import { citeLine, countLine, freshLine, notFoundLine, placeLine, salaryLine } from './model';
import type { TrackedRole } from './tracker';

export interface CardActions {
  tracked?: TrackedRole;
  onSave: () => void;
  onSeeMatch: () => void;
  /** This card's match is being started. */
  matchBusy: boolean;
  /** Another match is already being read; starting a second would be refused. */
  matchLocked: boolean;
  /** A sentence about the last attempt ("That role is no longer on the employer's board"). */
  notice?: string;
}

interface BodyProps extends CardActions {
  card: ListingCard;
  areaCity?: string;
  now: number;
  titleId: string;
  onClose: () => void;
}

export function CardBody({ card, areaCity, now, titleId, onClose, tracked, onSave, onSeeMatch, matchBusy, matchLocked, notice }: BodyProps) {
  // a reason without a fact behind it is not shown
  const reasons = card.fit.reasons.filter((r) => r.cite).slice(0, 2);
  const gap = notFoundLine(card.fit);
  return (
    <>
      <button type="button" className="dc-close" aria-label="Close" onClick={onClose}>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" aria-hidden="true" focusable="false">
          <path d="M2 2l8 8M10 2l-8 8" />
        </svg>
      </button>

      <div className="dc-card-head">
        <h3 id={titleId} className="dc-card-title">
          {card.title}
        </h3>
        <p className="dc-emp">{card.employer}</p>
      </div>

      <div className="dc-card-lines">
        <p className="dc-card-place">{placeLine(card, areaCity)}</p>
        <p className="dc-faint dc-small">{freshLine(card, now)}</p>
      </div>

      <div className="dc-row-fit">
        <FitChip word={card.fit.word} />
        <span className="dc-count">{countLine(card.fit)}</span>
      </div>

      {reasons.length > 0 && (
        <ul className="dc-reasons" aria-label="Why it fits">
          {reasons.map((r, i) => (
            <li key={i}>
              <p className="dc-reason-line">{r.text}</p>
              {r.cite && <p className="dc-cite">{citeLine(r.cite)}</p>}
            </li>
          ))}
        </ul>
      )}

      {gap && (
        <p>
          <span className="dc-gap">{gap}</span>
        </p>
      )}

      <p className="dc-faint dc-small">{salaryLine(card)}</p>

      <div className="dc-card-actions">
        <ApplyLink card={card} className="btn btn-primary btn-sm dc-btn-sm dc-card-apply" />
        <button type="button" className="btn btn-sm dc-btn-sm" aria-disabled={matchBusy || matchLocked || undefined} onClick={() => !(matchBusy || matchLocked) && onSeeMatch()}>
          {matchBusy ? 'Opening…' : 'See full match'}
        </button>
        <SaveButton tracked={tracked} onToggle={onSave} describedBy={titleId} />
      </div>
      {matchLocked && !matchBusy && <p className="dc-faint dc-small">A posting is already being read. Try again in a moment.</p>}
      <p className="dc-notice" role="status">
        {notice}
      </p>

      <div className="dc-card-foot">
        <p className="dc-faint dc-tiny">Read from the skills the posting names. The full match checks your evidence.</p>
        <p className="dc-muted dc-tiny dc-strong">{card.sourceLabel}</p>
      </div>
    </>
  );
}

/* ───────────────────────── the popover (wide screens) ───────────────────────── */

interface AnchoredProps extends Omit<BodyProps, 'onClose'> {
  rowEl: HTMLElement | null;
  containerEl: HTMLElement | null;
  /** `restoreFocus` is true when the keyboard closed it. */
  onDismiss: (restoreFocus: boolean) => void;
}

function AnchoredCard({ rowEl, containerEl, onDismiss, ...body }: AnchoredProps) {
  const reduced = useReducedMotion() ?? false;
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });

  // beside the row it opened from, kept inside the visible part of the page and inside the list's box
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !rowEl || !containerEl) return;
    const place = () => {
      const c = containerEl.getBoundingClientRect();
      const r = rowEl.getBoundingClientRect();
      const h = el.offsetHeight;
      let top = r.top - c.top;
      top = Math.min(top, window.innerHeight - 12 - c.top - h);
      top = Math.max(top, 12 - c.top);
      top = Math.max(0, Math.min(top, containerEl.offsetHeight - h));
      const left = Math.max(0, containerEl.clientWidth - el.offsetWidth);
      setPos((p) => (p.top === top && p.left === left ? p : { top, left }));
    };
    place();
    window.addEventListener('resize', place);
    if (typeof ResizeObserver === 'undefined') return () => window.removeEventListener('resize', place);
    const observer = new ResizeObserver(place);
    observer.observe(el);
    observer.observe(containerEl);
    return () => {
      window.removeEventListener('resize', place);
      observer.disconnect();
    };
  }, [rowEl, containerEl, body.card.id]);

  // focus moves in when the card opens; Escape closes it; a press outside closes it (a press on a row opens that row)
  useEffect(() => {
    ref.current?.focus({ preventScroll: true });
  }, [body.card.id]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      e.preventDefault();
      onDismiss(true);
    };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element | null;
      if (!t || ref.current?.contains(t) || t.closest('.dc-row')) return;
      onDismiss(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onDown);
    };
  }, [onDismiss]);

  return (
    <motion.div
      ref={ref}
      className="dc-card"
      role="dialog"
      aria-labelledby={body.titleId}
      tabIndex={-1}
      style={pos}
      initial={reduced ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2, ease: EASE }}
    >
      <CardBody {...body} onClose={() => onDismiss(true)} />
    </motion.div>
  );
}

/* ───────────────────────── the host ───────────────────────── */

export interface CardHostProps extends CardActions {
  card: ListingCard | null;
  areaCity?: string;
  now: number;
  rowEl: HTMLElement | null;
  containerEl: HTMLElement | null;
  onDismiss: (restoreFocus: boolean) => void;
}

/** The card as a popover beside its row, or, under 760 px, as a bottom sheet. */
export function CardHost({ card, rowEl, containerEl, onDismiss, areaCity, now, ...actions }: CardHostProps) {
  const narrow = useMediaQuery(NARROW_QUERY);
  const titleId = useId();
  // the sheet keeps the last card while it leaves
  const last = useRef<ListingCard | null>(card);
  if (card) last.current = card;
  const shown = card ?? last.current;

  if (narrow) {
    return (
      <Sheet open={card !== null} onClose={() => onDismiss(true)} labelledBy={titleId} className="dc-sheet">
        {shown && (
          <div className="dc-card-body">
            <CardBody card={shown} areaCity={areaCity} now={now} titleId={titleId} onClose={() => onDismiss(true)} {...actions} />
          </div>
        )}
      </Sheet>
    );
  }
  return (
    <AnimatePresence>
      {card && <AnchoredCard key={card.id} card={card} areaCity={areaCity} now={now} titleId={titleId} rowEl={rowEl} containerEl={containerEl} onDismiss={onDismiss} {...actions} />}
    </AnimatePresence>
  );
}
