/**
 * The phone's way through the roles (boards "PhoneDeck" and "PhoneSheet"): one large card at a time, with the next one
 * peeking out behind it. Drag a card right to save it and left for "not for me"; past a threshold it leaves the screen
 * on the house easing, and a short question ("Why not this one?") follows a "not for me". Three round 56 px buttons do
 * the same without a finger: Not for me, Details (the role in full, as a bottom sheet) and Save, with Apply for me under
 * them (the posting match runs first, as steps on the screen) and the employer's own link quietly after it; where Apply
 * for me is not offered, the employer's link and why. "Show as a list" turns the deck into the single-column list of cards.
 *
 * Under reduced motion there is no drag and no animation: the buttons do everything. The drag is written straight to
 * the card's style, not to React state, so a finger moving across the screen causes no renders. A vertical movement
 * is left to the page to scroll.
 *
 * Nothing is lost by swiping: a saved role is on Your applications, a hidden one is brought back by Undo (or by the
 * toolbar's "Show" in the list), and the line under the deck says what the last swipe did.
 */
import { animate, useReducedMotion } from 'motion/react';
import { useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import type { AiJob } from '../../../shared/jobsearch';
import { EASE, clamp } from '../../lib/ui';
import type { NoReason } from '../jobsearch/avoid';
import { employerLine, freshnessLine, provenanceLine } from '../jobsearch/words';
import { IconBookmark, IconClose, IconList, IconUp } from './icons';
import type { RoleCtl } from './JobCard';
import { ReasonPicker } from './NotForMe';
import { ApplyFor, AskLine, FitLine } from './parts';
import { Sheet } from './Sheet';

export interface DeckProps {
  /** Every role of the search except those hidden before this visit, in order. */
  jobs: readonly AiJob[];
  /** Hidden this visit (the deck steps over them). */
  hidden: ReadonlySet<string>;
  now: number;
  ctlOf: (job: AiJob) => RoleCtl;
  /** The roles saved among these. */
  savedCount: number;
  /** "Bengaluru and remote in India · Searched today at 7:31 pm". */
  where: string;
  onDetails: (job: AiJob) => void;
  onList: () => void;
  onReviewSaved: () => void;
  onSearchAgain: () => void;
  /** Search again is not possible right now (paused, profile missing). */
  searchBlocked: boolean;
  /** Where the deck starts (the dev gallery opens on the third card). */
  startAt?: number;
}

interface Last {
  kind: 'save' | 'hide';
  job: AiJob;
  /** The cursor before the swipe, so Undo puts the card back on top. */
  at: number;
}

const SWIPE_MAX = 110;

export function Deck({ jobs, hidden, now, ctlOf, savedCount, where, onDetails, onList, onReviewSaved, onSearchAgain, searchBlocked, startAt = 0 }: DeckProps) {
  const reduced = useReducedMotion() ?? false;
  const [at, setAt] = useState(startAt);
  const [last, setLast] = useState<Last | null>(null);
  const [asking, setAsking] = useState<AiJob | null>(null);
  const card = useRef<HTMLElement>(null);
  const stampYes = useRef<HTMLSpanElement>(null);
  const stampNo = useRef<HTMLSpanElement>(null);
  const busy = useRef(false);
  const drag = useRef({ id: -1, x0: 0, y0: 0, dx: 0, locked: false });

  // the first card the person has not dealt with: hidden ones are stepped over
  const next = (from: number) => {
    let i = from;
    while (i < jobs.length && hidden.has(jobs[i].id)) i++;
    return i;
  };
  const index = next(at);
  const job: AiJob | undefined = jobs[index];
  const total = jobs.length;
  const done = index >= total;

  // the deck's cards change when a new search lands: start over rather than point past the end
  useEffect(() => {
    setAt((a) => Math.min(a, jobs.length));
  }, [jobs.length]);

  const place = (dx: number) => {
    const el = card.current;
    if (!el) return;
    el.style.transform = dx === 0 ? '' : `translateX(${dx}px) rotate(${dx / 28}deg)`;
    const edge = Math.min(SWIPE_MAX, el.offsetWidth * 0.28);
    if (stampYes.current) stampYes.current.style.opacity = String(clamp(dx / edge, 0, 1));
    if (stampNo.current) stampNo.current.style.opacity = String(clamp(-dx / edge, 0, 1));
  };

  /** The card leaves on `dir` (1 = right, -1 = left); `then` runs once it is gone. */
  const leave = (dir: 1 | -1, then: () => void) => {
    if (busy.current || !job) return;
    if (reduced || !card.current) {
      then();
      return;
    }
    busy.current = true;
    const from = drag.current.dx;
    const to = dir * (window.innerWidth + 80);
    animate(from, to, {
      duration: 0.25,
      ease: EASE,
      onUpdate: place,
      onComplete: () => {
        busy.current = false;
        drag.current.dx = 0;
        then();
      },
    });
  };

  const saveCard = () => {
    if (!job) return;
    const ctl = ctlOf(job);
    // a card already saved only moves on; the button is how a save is taken back
    if (!ctl.saved) ctl.onToggleSave();
    leave(1, () => {
      setLast({ kind: 'save', job, at: index });
      setAt(index + 1);
    });
  };

  const hideCard = () => {
    if (!job) return;
    const picked = job;
    leave(-1, () => {
      ctlOf(picked).onHide('other');
      setLast({ kind: 'hide', job: picked, at: index });
      setAt(index + 1);
      setAsking(picked);
    });
  };

  const undo = () => {
    if (!last) return;
    const ctl = ctlOf(last.job);
    if (last.kind === 'save') {
      if (ctl.saved) ctl.onToggleSave();
    } else {
      ctl.onUnhide();
    }
    setAt(last.at);
    setLast(null);
    setAsking(null);
  };

  /* ───────── the drag ───────── */

  const onPointerDown = (e: ReactPointerEvent<HTMLElement>) => {
    if (reduced || busy.current || (e.pointerType === 'mouse' && e.button !== 0)) return;
    drag.current = { id: e.pointerId, x0: e.clientX, y0: e.clientY, dx: 0, locked: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (d.id !== e.pointerId) return;
    const dx = e.clientX - d.x0;
    const dy = e.clientY - d.y0;
    if (!d.locked) {
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) {
        d.id = -1; // a vertical move belongs to the page
        return;
      }
      if (Math.abs(dx) < 6) return;
      d.locked = true;
    }
    d.dx = dx;
    place(dx);
  };

  const settle = (e: ReactPointerEvent<HTMLElement>, cancelled: boolean) => {
    const d = drag.current;
    if (d.id !== e.pointerId) return;
    d.id = -1;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    const edge = Math.min(SWIPE_MAX, (card.current?.offsetWidth ?? 320) * 0.28);
    if (!cancelled && Math.abs(d.dx) >= edge) {
      if (d.dx > 0) saveCard();
      else hideCard();
      return;
    }
    // not far enough: back to the middle
    if (d.dx !== 0 && !reduced) {
      busy.current = true;
      animate(d.dx, 0, {
        duration: 0.25,
        ease: EASE,
        onUpdate: place,
        onComplete: () => {
          busy.current = false;
          d.dx = 0;
        },
      });
    } else {
      place(0);
    }
  };

  const reason = (r: NoReason) => {
    if (asking) ctlOf(asking).onHide(r);
    setAsking(null);
  };

  const ctl = job ? ctlOf(job) : undefined;
  const pair = job?.met.slice(0, 2) ?? [];

  return (
    <div className="dc-deck">
      <div className="dc-deck-top">
        <h1 className="serif dc-deck-title">Discover</h1>
        <span className="numeral dc-muted dc-deck-count" role="status" aria-live="polite">
          {done ? `${total} of ${total}` : `${index + 1} of ${total}`}
        </span>
        <button type="button" className="dc-icon-btn is-xl" aria-label="Show as a list" onClick={onList}>
          <IconList />
        </button>
      </div>

      <div className="dc-deck-bar" aria-hidden="true">
        {jobs.map((j, i) => (
          <span key={j.id} className={i < index ? 'is-done' : i === index ? 'is-now' : ''} />
        ))}
      </div>

      {done ? (
        <section className="panel dc-deck-end" aria-label="Done">
          <h2 className="title dc-deck-end-title">You've seen all {total}</h2>
          <p className="dc-muted">
            {savedCount} saved
            {savedCount > 0 && (
              <>
                {' · '}
                <button type="button" className="dc-link dc-link-pad" onClick={onReviewSaved}>
                  Review saved
                </button>
              </>
            )}
          </p>
          <div className="dc-deck-end-actions">
            <button type="button" className="btn btn-primary dc-btn" aria-disabled={searchBlocked || undefined} onClick={() => !searchBlocked && onSearchAgain()}>
              Search again
            </button>
            <button type="button" className="btn btn-quiet dc-btn" onClick={onList}>
              Show as a list
            </button>
          </div>
        </section>
      ) : (
        job &&
        ctl && (
          <>
            <div className="dc-deck-stage">
              {index + 2 < total && <div className="dc-deck-edge is-far" aria-hidden="true" />}
              {index + 1 < total && <div className="dc-deck-edge is-near" aria-hidden="true" />}
              <article
                key={job.id}
                ref={card}
                className="dc-deck-card"
                aria-label={`${job.title} at ${job.employer}`}
                data-job={job.id}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={(e) => settle(e, false)}
                onPointerCancel={(e) => settle(e, true)}
              >
                <span ref={stampYes} className="dc-stamp is-yes" aria-hidden="true">
                  Save
                </span>
                <span ref={stampNo} className="dc-stamp is-no" aria-hidden="true">
                  Not for me
                </span>
                <FitLine job={job} />
                <div>
                  <h2 className="title dc-deck-card-title">{job.title}</h2>
                  <p className="dc-deck-card-sub">{employerLine(job)}</p>
                </div>
                <AskLine asks={job.asks} />
                {pair.length > 0 && (
                  <div className="dc-pair dc-deck-pair">
                    {pair.map((m, i) => (
                      <div key={i} className="dc-deck-ask">
                        <p>
                          <span className="dc-lbl">Asks </span>“{m.ask}”
                        </p>
                        <p>
                          <span className="dc-lbl">You </span>“{m.cite.text}”
                          {m.cite.source && <span className="dc-cite"> {m.cite.source}</span>}
                          {m.partly && <span className="dc-partly"> · partly</span>}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
                {job.gaps[0] && (
                  <p className="dc-gapline">
                    <span className="dc-pip dc-pip-gap" aria-hidden="true" />
                    <span>
                      <span className="dc-gaplbl">Not on your resume:</span> “{job.gaps[0]}”
                    </span>
                  </p>
                )}
                <p className="dc-prov dc-deck-prov">
                  {provenanceLine(job)}
                  <br />
                  {freshnessLine(job, now)}
                </p>
              </article>
            </div>

            <div className="dc-deck-buttons">
              <div className="dc-deck-btn">
                <button type="button" className="dc-round" aria-label="Not for me" onClick={hideCard}>
                  <IconClose size={22} />
                </button>
                <span className="dc-muted">Not for me</span>
              </div>
              <div className="dc-deck-btn">
                <button type="button" className="dc-round is-details" aria-label="Details" aria-haspopup="dialog" onClick={() => onDetails(job)}>
                  <IconUp size={22} />
                </button>
                <span className="dc-muted">Details</span>
              </div>
              <div className="dc-deck-btn">
                <button type="button" className="dc-round is-save" aria-label={ctl.saved ? 'Saved. Press to remove' : 'Save'} aria-pressed={ctl.saved} onClick={ctl.saved ? ctl.onToggleSave : saveCard}>
                  <IconBookmark size={22} filled={ctl.saved} />
                </button>
                <span className="dc-muted">{ctl.saved ? 'Saved' : 'Save'}</span>
              </div>
            </div>

            <p className="dc-faint dc-deck-hint">{reduced ? 'Use the buttons to save a role or hide it' : 'Swipe right to save · left for not for me'}</p>

            <ApplyFor
              job={job}
              ctl={ctl}
              primaryClass="btn btn-primary dc-btn dc-apply dc-deck-apply"
              buttonClass="dc-btn dc-apply dc-deck-apply"
              quietClass="btn btn-quiet dc-btn dc-apply"
            />
            <p className="dc-notice" role="status">
              {ctl.notice}
            </p>
          </>
        )
      )}

      <div className="dc-deck-foot">
        <p className="dc-muted dc-small" role="status" aria-live="polite">
          {last ? (
            <>
              {last.kind === 'save' ? 'Saved' : 'Hidden'} · {last.job.title} at {last.job.employer}{' '}
              <button type="button" className="dc-link dc-link-pad" onClick={undo}>
                Undo
              </button>
            </>
          ) : (
            where
          )}
        </p>
      </div>

      <Sheet open={asking !== null} onClose={() => setAsking(null)} label="Why not this one?">
        <ReasonPicker titleId="dc-deck-why" onChoose={reason} />
      </Sheet>
    </div>
  );
}

