/**
 * The whole interview as text: every turn with the time it was said, the words each verdict rests on marked, and every
 * turn addressable (`#t-151`) so a verdict's "At 2:31" lands on it. Under it, how long it is kept and a way to delete it
 * now. No audio or video was ever recorded, and the page says so.
 *
 * On a wide screen it is the side panel; on a phone it is a disclosure at the end of the page.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { Debrief } from '../../../shared/interview';
import { clock, dateOf, sentenceOf } from '../hub/words';
import { RUNG_LABEL, exchangeFor, markPieces, turnOfQuote } from './quote';

export interface TranscriptProps {
  debrief: Debrief;
  /** An id for each turn, parallel to `debrief.transcript`. */
  ids: readonly string[];
  /** The turn a verdict just linked to. */
  active: string | null;
  narrow: boolean;
  /** On a phone the transcript is a disclosure; whether it is open is the screen's, so a verdict's link can open it. */
  open: boolean;
  onOpen: (open: boolean) => void;
  onDelete: () => Promise<void>;
}

/** The words each verdict rests on, by the index of the turn they were said in. */
function decisiveWords(debrief: Debrief): Map<number, string[]> {
  const out = new Map<number, string[]>();
  for (const result of debrief.results) {
    const hit = turnOfQuote(exchangeFor(debrief, result), result.quote);
    if (!hit || !result.quote) continue;
    out.set(hit.index, [...(out.get(hit.index) ?? []), result.quote.text]);
  }
  return out;
}

export function TranscriptPanel({ debrief, ids, active, narrow, open, onOpen, onDelete }: TranscriptProps) {
  const base = useId();
  const decisive = useMemo(() => decisiveWords(debrief), [debrief]);
  const turns = debrief.transcript;
  const kept = dateOf(debrief.keptUntil);

  const body = (
    <>
      {turns.length === 0 ? (
        <p className="pc-muted">There is no transcript to show.</p>
      ) : (
        <div className="pc-transcript scroll" role="region" aria-label="Transcript, scrollable" tabIndex={0}>
          {turns.map((turn, i) => {
            const you = turn.speaker === 'you';
            let pieces = markPieces(turn.text, undefined);
            for (const q of you ? (decisive.get(i) ?? []) : []) pieces = pieces.flatMap((p) => (p.mark ? [p] : markPieces(p.text, q)));
            return (
              <div key={ids[i]} id={ids[i]} className={`pc-turn${active === ids[i] ? ' is-active' : ''}`} tabIndex={-1}>
                <span className="numeral pc-turn-time">{clock(turn.at)}</span>
                <div className="pc-turn-body">
                  <div className={`pc-rung${you ? ' is-you' : ''}`}>{you ? 'You' : (RUNG_LABEL[turn.rung ?? 'surface'] ?? 'Interviewer')}</div>
                  <p className={`pc-said pc-turn-text${you ? '' : ' is-interviewer'}`}>
                    {pieces.map((p, k) => (p.mark ? <mark key={k}>{p.text}</mark> : <span key={k}>{p.text}</span>))}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <Keeping kept={kept} onDelete={onDelete} />
    </>
  );

  if (narrow) {
    return (
      <section className="panel pc-side-panel" aria-label="Transcript">
        <button type="button" className="pc-acc-row pc-transcript-toggle" aria-expanded={open} aria-controls={`${base}-t`} onClick={() => onOpen(!open)}>
          <span className="pc-acc-name">
            Transcript <span className="pc-muted">· {turns.length} {turns.length === 1 ? 'turn' : 'turns'}</span>
          </span>
          <svg className={`pc-chev${open ? ' is-open' : ''}`} width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 6l4 4 4-4" />
          </svg>
        </button>
        {open && (
          <div id={`${base}-t`} className="pc-side-body">
            {body}
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="panel pc-side-panel" aria-labelledby={`${base}-h`}>
      <header className="pc-sec-head">
        <p className="kicker">The whole interview</p>
        <h2 className="title pc-h2" id={`${base}-h`}>
          Transcript
        </h2>
      </header>
      <div className="pc-side-body">{body}</div>
    </section>
  );
}

/** "Kept until 15 Nov · Delete now", and the question that comes before anything is deleted. */
function Keeping({ kept, onDelete }: { kept: string; onDelete: () => Promise<void> }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const keep = useRef<HTMLButtonElement>(null);
  const ask = useRef<HTMLButtonElement>(null);
  const was = useRef(false);

  // the question replaces the button: focus goes into it, and comes back to "Delete now" when it is answered "Keep"
  useEffect(() => {
    if (confirming) keep.current?.focus();
    else if (was.current) ask.current?.focus();
    was.current = confirming;
  }, [confirming]);

  const remove = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await onDelete();
    } catch (err) {
      setError(sentenceOf(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pc-keeping">
      {confirming ? (
        <div className="pc-confirm" role="group" aria-label="Delete this practice interview?">
          <p>Delete this practice interview now? Its transcript and debrief can't be brought back.</p>
          <div className="pc-actions">
            <button type="button" className="btn btn-sm pc-btn-sm" disabled={busy} onClick={() => void remove()}>
              {busy ? 'Deleting…' : 'Delete'}
            </button>
            <button ref={keep} type="button" className="btn btn-primary btn-sm pc-btn-sm" disabled={busy} onClick={() => setConfirming(false)}>
              Keep
            </button>
          </div>
        </div>
      ) : (
        <div className="pc-keeping-line">
          <span className="pc-muted">{kept ? `Kept until ${kept} ·` : 'Kept for 30 days ·'}</span>
          <button ref={ask} type="button" className="btn btn-quiet btn-sm pc-btn-sm" onClick={() => setConfirming(true)}>
            Delete now
          </button>
        </div>
      )}
      {error && (
        <p className="pc-danger" role="alert">
          {error}
        </p>
      )}
      <p className="pc-muted pc-small">No audio or video was recorded.</p>
    </div>
  );
}
