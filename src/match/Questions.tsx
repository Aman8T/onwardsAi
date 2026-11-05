/**
 * "Close the gaps · N questions": up to three questions about the gaps and the things the sources could not settle.
 * An answer becomes a fact on the person's profile marked "from you"; it is never rewritten into a claim they did not
 * make. Re-checking is free and never uses a posting.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { Question } from '../../shared/match';
import { ANSWER_MAX_CHARS } from '../../shared/match_api';
import type { AnswerInput } from './types';

export interface RecheckState {
  status: 'idle' | 'saving' | 'done' | 'error';
  /** The sentence to announce after a re-check ("Re-checked. 2 rows changed."). */
  message?: string;
}

interface Props {
  questions: readonly Question[];
  recheck: RecheckState;
  onSubmit: (answers: AnswerInput[]) => void;
  /** Ref callback for the first answer box, so "Close the gaps" in the summary can move the cursor here. */
  firstRef?: (el: HTMLTextAreaElement | null) => void;
}

/** The server's limit (`contract/match_api.py`, mirrored in `shared/match_api.ts`). */
const MAX_ANSWER_CHARS = ANSWER_MAX_CHARS;

export function Questions({ questions, recheck, onSubmit, firstRef }: Props) {
  const base = useId();
  const [text, setText] = useState<Record<string, string>>({});
  const sent = useRef<string[]>([]);
  const saving = recheck.status === 'saving';
  const filled = questions.filter((q) => (text[q.id] ?? '').trim().length > 0);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (filled.length === 0 || saving) return;
    sent.current = filled.map((q) => q.id);
    onSubmit(filled.map((q) => ({ questionId: q.id, text: (text[q.id] ?? '').trim() })));
  };

  // the server has taken the answers: the boxes they were typed in start empty again
  useEffect(() => {
    if (recheck.status !== 'done') return;
    setText((t) => Object.fromEntries(Object.entries(t).filter(([id]) => !sent.current.includes(id))));
  }, [recheck.status]);

  if (questions.length === 0) return null;

  return (
    <form className="mt-questions" onSubmit={submit} noValidate>
      <div className="mt-q-grid">
        {questions.map((q, i) => {
          const id = `${base}-${q.id}`;
          return (
            <div key={q.id} className="mt-q">
              <label htmlFor={id} className="mt-q-label">
                <span className="numeral">{i + 1}</span> · {q.text}
              </label>
              {q.why && (
                <p id={`${id}-why`} className="mt-q-why">
                  {q.why}
                </p>
              )}
              <textarea
                id={id}
                ref={i === 0 ? firstRef : undefined}
                className="field mt-box"
                rows={3}
                maxLength={MAX_ANSWER_CHARS}
                value={text[q.id] ?? ''}
                aria-describedby={q.why ? `${id}-why` : undefined}
                placeholder="In your own words. Or no."
                readOnly={saving}
                onChange={(e) => setText((t) => ({ ...t, [q.id]: e.target.value }))}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-actions">
        <button type="submit" className="btn btn-primary mt-btn" aria-disabled={filled.length === 0 || saving}>
          {saving ? 'Re-checking…' : 'Save answers and re-check'}
        </button>
        <span className="mt-muted">Re-checking is free. The match updates in a few seconds.</span>
      </div>
      <p className={`mt-status${recheck.status === 'error' ? ' is-error' : ''}`} role={recheck.status === 'error' ? 'alert' : 'status'}>
        {recheck.message}
      </p>
    </form>
  );
}
