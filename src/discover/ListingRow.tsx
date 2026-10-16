/**
 * One listing as a row: the role and who is hiring, where it is and how far (always a straight line), how fresh it
 * is, the quick-fit word with its counts, a link that names where it goes, and a Save button. Clicking the row (or
 * pressing Enter on its title) opens the card.
 *
 * Everything a source wrote (title, employer, place) is a text node; the apply link renders only when it is a plain
 * web address.
 */
import type { MouseEvent } from 'react';
import type { ListingCard, QuickFitWord } from '../../shared/discover';
import { FIT_LABEL, applyLabel, countLine, freshLine, placeLine, safeHref } from './model';
import type { TrackedRole } from './tracker';
import { STATUS_LABEL } from './tracker';

export function FitChip({ word }: { word: QuickFitWord }) {
  return (
    <span className={`dc-fit dc-fit-${word}`} data-word={word}>
      {FIT_LABEL[word]}
    </span>
  );
}

/** "Apply on jobs.lever.co ↗", opening the employer's own listing in a new tab. */
export function ApplyLink({ card, className }: { card: Pick<ListingCard, 'applyUrl' | 'applyHost' | 'title' | 'employer'>; className: string }) {
  const href = safeHref(card.applyUrl);
  if (!href) return <span className="dc-faint">{`Apply on ${card.applyHost}`}</span>;
  return (
    <a className={className} href={href} target="_blank" rel="noopener" aria-label={`Apply on ${card.applyHost} for ${card.title} at ${card.employer}, opens in a new tab`}>
      {applyLabel(card)}
    </a>
  );
}

/** The bookmark button. A role that has moved past "Saved" stays on the list; pressing it does nothing then. */
export function SaveButton({ tracked, onToggle, describedBy, className = '' }: { tracked?: TrackedRole; onToggle: () => void; describedBy?: string; className?: string }) {
  const held = tracked !== undefined;
  const locked = held && tracked.status !== 'saved';
  return (
    <button
      type="button"
      className={`btn btn-sm btn-quiet dc-icon-btn ${className}`.trim()}
      aria-label="Save this role"
      aria-pressed={held}
      aria-disabled={locked || undefined}
      aria-describedby={describedBy}
      title={locked ? `On Your applications as ${STATUS_LABEL[tracked.status]}` : held ? 'Saved. Press to remove' : 'Save this role'}
      onClick={(e) => {
        e.stopPropagation();
        if (!locked) onToggle();
      }}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill={held ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true" focusable="false">
        <path d="M6 3.5h12v17l-6-4.2-6 4.2z" />
      </svg>
    </button>
  );
}

export interface ListingRowProps {
  card: ListingCard;
  areaCity?: string;
  now: number;
  open: boolean;
  tracked?: TrackedRole;
  /** Titles of the groups this listing also fits, with their ids. */
  alsoFits: readonly { id: string; title: string }[];
  /** The row's element, so the card can sit beside it. */
  rowRef: (el: HTMLLIElement | null) => void;
  titleRef: (el: HTMLButtonElement | null) => void;
  onOpen: () => void;
  onSave: () => void;
  onAlso: (groupId: string) => void;
}

export function ListingRow({ card, areaCity, now, open, tracked, alsoFits, rowRef, titleRef, onOpen, onSave, onAlso }: ListingRowProps) {
  const titleId = `dc-t-${card.id}`;
  const onRowClick = (e: MouseEvent<HTMLLIElement>) => {
    // links and buttons inside the row do their own thing
    if ((e.target as HTMLElement).closest('a, button, select, input')) return;
    onOpen();
  };

  return (
    <li ref={rowRef} className={`dc-row${open ? ' is-open' : ''}`} data-listing={card.id} onClick={onRowClick}>
      <div className="dc-row-top">
        <div className="dc-row-title">
          <button ref={titleRef} id={titleId} type="button" className="dc-title-btn" aria-haspopup="dialog" aria-expanded={open} onClick={onOpen}>
            {card.title}
          </button>
          <span className="dc-emp"> · {card.employer}</span>
        </div>
        <div className="dc-row-actions">
          <ApplyLink card={card} className="btn btn-sm dc-btn-sm" />
          <SaveButton tracked={tracked} onToggle={onSave} describedBy={titleId} />
        </div>
      </div>
      <p className="dc-row-sub">
        <span>{placeLine(card, areaCity)}</span>
        <span className="dc-faint"> · {freshLine(card, now)}</span>
      </p>
      <div className="dc-row-fit">
        <FitChip word={card.fit.word} />
        <span className="dc-count">{countLine(card.fit)}</span>
      </div>
      {alsoFits.length > 0 && (
        <p className="dc-also">
          {alsoFits.map((g, i) => (
            <span key={g.id}>
              {i > 0 && ', '}
              <button type="button" className="dc-link" onClick={() => onAlso(g.id)}>
                Also fits {g.title}
              </button>
            </span>
          ))}
        </p>
      )}
    </li>
  );
}
