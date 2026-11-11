/**
 * "Second wording": the coach's proposed words for one line of the resume in view, shown under its answer. Nothing has
 * changed until the person presses "Accept in this draft", which sends the ordinary edit op; the card says the draft
 * has the words only after that succeeded. Before and after are a plain word diff (measured, so straight).
 *
 * `RewriteCard` is presentational (the gallery drives it); `ChatRewrite` is the one the chat view mounts, wired to
 * the API, the store and the address.
 */
import { useState } from 'react';
import { ApiError } from '../lib/api';
import { actions } from '../store';
import type { ChatMessage, ChatRewrite } from '../store';
import { patchResume } from './api';
import { Now, Was } from './diff';
import { resumePath } from './route';
import '../styles/resume.css';

export type RewritePhase = 'offer' | 'saving' | 'accepted' | 'changed' | 'failed' | 'dismissed';

export interface RewriteCardProps {
  rewrite: ChatRewrite;
  phase: RewritePhase;
  onAccept: () => void;
  onEdit: () => void;
  onDismiss: () => void;
  /** Go to the resume (the link after Accept, and "Open the resume" when the draft changed). */
  onOpen: () => void;
}

function OpenLink({ resumeId, lineId, onOpen, children }: { resumeId: string; lineId?: string; onOpen: () => void; children: string }) {
  return (
    <a
      href={resumePath({ screen: 'resume', id: resumeId, ...(lineId ? { line: lineId } : {}) })}
      className="rs-link"
      onClick={(e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        onOpen();
      }}
    >
      {children}
    </a>
  );
}

export function RewriteCard({ rewrite, phase, onAccept, onEdit, onDismiss, onOpen }: RewriteCardProps) {
  const { before, after, resumeId } = rewrite;

  if (phase === 'dismissed') {
    return (
      <p className="rs-fine rs-rewrite-done" role="status">
        Suggestion dismissed. Your resume is unchanged.
      </p>
    );
  }

  const settled = phase === 'accepted';
  return (
    <section className="panel rs-rewrite" aria-label="A second wording for one line" data-phase={phase}>
      <div className="rs-card-head">
        <span className="kicker">Second wording</span>
        <span className="rs-fine">{settled ? 'In your draft.' : 'Nothing changes until you accept.'}</span>
      </div>
      <p className="rs-small rs-was">
        <span className="sr-only">Before: </span>
        <Was before={before} after={after} />
      </p>
      <p className="rs-small">
        <span className="sr-only">After: </span>
        <Now before={before} after={after} />
      </p>

      {phase === 'accepted' ? (
        <p className="rs-small" role="status">
          In your draft. Finalise checks it. <OpenLink resumeId={resumeId} onOpen={onOpen}>Back to the resume</OpenLink>
        </p>
      ) : phase === 'changed' ? (
        <p className="rs-small" role="alert">
          The draft has changed since this was written, so the line was not touched. <OpenLink resumeId={resumeId} lineId={rewrite.lineId} onOpen={onOpen}>Open the resume</OpenLink>
        </p>
      ) : (
        <>
          <div className="rs-actions">
            <button type="button" className="btn btn-sm btn-primary rs-btn" disabled={phase === 'saving'} aria-busy={phase === 'saving'} onClick={onAccept}>
              {phase === 'saving' ? 'Saving…' : 'Accept in this draft'}
            </button>
            <button type="button" className="btn btn-sm rs-btn" disabled={phase === 'saving'} onClick={onEdit}>
              Edit the wording
            </button>
            <button type="button" className="btn btn-sm btn-quiet rs-btn" disabled={phase === 'saving'} onClick={onDismiss}>
              Dismiss
            </button>
          </div>
          {phase === 'failed' && (
            <p className="rs-notice" role="alert">
              The line could not be saved just now. Your resume is unchanged. Try again in a moment.
            </p>
          )}
        </>
      )}
    </section>
  );
}

/** The card under a coach answer in the chat view. */
export function ChatRewrite({ message }: { message: ChatMessage }) {
  const rewrite = message.rewrite;
  const [phase, setPhase] = useState<RewritePhase>(message.rewriteState ?? 'offer');
  if (!rewrite) return null;

  const accept = async () => {
    setPhase('saving');
    try {
      await patchResume(rewrite.resumeId, [{ op: 'edit', bulletId: rewrite.lineId, text: rewrite.after }]);
      setPhase('accepted');
      actions.settleRewrite(message.id, 'accepted');
    } catch (err) {
      // a 4xx: the line is gone or the draft moved on; anything else may be tried again
      setPhase(err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 429 ? 'changed' : 'failed');
    }
  };

  return (
    <RewriteCard
      rewrite={rewrite}
      phase={phase}
      onAccept={() => void accept()}
      onEdit={() => actions.openResume({ screen: 'resume', id: rewrite.resumeId, line: rewrite.lineId })}
      onDismiss={() => {
        setPhase('dismissed');
        actions.settleRewrite(message.id, 'dismissed');
      }}
      onOpen={() => actions.openResume({ screen: 'resume', id: rewrite.resumeId })}
    />
  );
}
