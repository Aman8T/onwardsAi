/**
 * Questions (canvas 12): the form asks things we do not know yet. One card per need, the onSite ones without an input.
 * "Save and continue" sends every answer in one batch (`applyApi.answer`); a saved answer fills every other waiting form
 * that asks the same question, and the screen says how many carried on (`requeued`). "Skip this application" cancels it
 * (the hold is released). A form that waits only for a file (the resume added in Your details, the tailored resume
 * finished on the match) has nothing to type: "Fill it again" queues it (`applyApi.retry`, `fillAgainOffered`).
 *
 * Signatures wait for Review, where the person signs the form they are about to approve.
 */
import { useId, useState } from 'react';
import type { AnswerRequest, ApplicationDetail } from '../../shared/apply';
import { plural } from '../lib/ui';
import { errorText } from './api';
import { NeedCard, answerBatch, initialDrafts, isAttestation, isFileNeed, isQuestion, keyOf, missingRequired } from './NeedCard';
import type { NeedDraft } from './NeedCard';
import { DETAILS_ROUTE, LIST_ROUTE, linkTo } from './route';
import type { Navigate } from './route';

export interface QuestionsViewProps {
  detail: ApplicationDetail;
  navigate: Navigate;
  onAnswer: (body: AnswerRequest) => Promise<void>;
  onSkip: () => Promise<void>;
  /** Queue the form again once its file is there (only offered when every open required need is a file). */
  onFillAgain?: () => Promise<void>;
}

/** Mirrors `apply.service._waits_only_for_files`: every open required need is a file, so nothing is typed here. */
export function fillAgainOffered(detail: Pick<ApplicationDetail, 'stage' | 'needs'>): boolean {
  if (detail.stage !== 'waiting_for_you') return false;
  const open = detail.needs.filter((n) => n.required);
  return open.length > 0 && open.every((n) => isFileNeed(n) && !n.onSite);
}

/** The headline's count, from the needs (code, not the server's wording). */
export function questionsTitle(asked: number, files = 0): string {
  if (asked === 0) return files > 0 ? 'This form needs a file we don’t have yet.' : 'This form needs you on the employer’s page.';
  return `This form asks ${asked === 1 ? 'one thing' : `${asked} things`} we don't know yet.`;
}

export function QuestionsView({ detail, navigate, onAnswer, onSkip, onFillAgain }: QuestionsViewProps) {
  const questions = detail.needs.filter(isQuestion);
  const onSite = detail.needs.filter((n) => n.onSite);
  const files = detail.needs.filter((n) => !n.onSite && isFileNeed(n));
  const toSign = detail.needs.filter((n) => !n.onSite && isAttestation(n));
  const shown = [...questions, ...onSite, ...files];
  const [drafts, setDrafts] = useState<Record<string, NeedDraft>>(() => initialDrafts(questions));
  const [busy, setBusy] = useState<'save' | 'skip' | 'again' | null>(null);
  const [confirmSkip, setConfirmSkip] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reasonId = useId();
  const company = detail.company ?? detail.domain;

  const missing = missingRequired(questions, drafts);
  const batch = answerBatch(questions, drafts);
  const blocked = missing.length > 0 ? `Answer ${missing.length === 1 ? `“${missing[0].label}”` : plural(missing.length, 'required question')} first.` : batch.length === 0 ? 'Answer at least one question first.' : null;

  const save = async () => {
    if (blocked || busy) return;
    setBusy('save');
    setError(null);
    try {
      await onAnswer({ answers: batch });
    } catch (err) {
      setError(errorText(err, 'Could not save your answers. Try again.'));
    } finally {
      setBusy(null);
    }
  };

  const fillAgain = async () => {
    if (busy || !onFillAgain) return;
    setBusy('again');
    setError(null);
    try {
      await onFillAgain();
    } catch (err) {
      setError(errorText(err, 'Could not fill the form again. Try again.'));
    } finally {
      setBusy(null);
    }
  };

  const skip = async () => {
    if (busy) return;
    setBusy('skip');
    setError(null);
    try {
      await onSkip();
    } catch (err) {
      setError(errorText(err, 'Could not skip this application. Try again.'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="ap-stack">
      <div className="ap-head">
        <p className="kicker">
          <a {...linkTo(LIST_ROUTE, navigate)}>Applications</a> · {[detail.title, company].filter(Boolean).join(' · ')} · while filling
        </p>
        <h1 className="serif ap-title">{questionsTitle(questions.length, files.length)}</h1>
        {onSite.length > 0 && <p className="ap-small">And {onSite.length === 1 ? 'one that’s' : `${onSite.length} that are`} always yours to type.</p>}
        {files.length > 0 && questions.length > 0 && <p className="ap-small">And {files.length === 1 ? 'a file' : `${files.length} files`} to add in Your details.</p>}
        <p className="ap-muted ap-measure">Everything else is filled. Answer these and we carry on; whatever you save, no form asks you again.</p>
      </div>

      <div className="ap-split">
        <div className="ap-main ap-stack-s">
          {shown.map((need) => (
            <NeedCard
              key={keyOf(need)}
              need={need}
              company={company}
              draft={drafts[keyOf(need)]}
              disabled={busy !== null}
              onChange={(next) => setDrafts((d) => ({ ...d, [keyOf(need)]: next }))}
            />
          ))}
          {toSign.length > 0 && (
            <p className="ap-muted ap-small">
              {toSign.length === 1 ? `“${toSign[0].label}” is` : `${plural(toSign.length, 'declaration')} are`} yours to sign in Review, on the form you approve.
            </p>
          )}
          {error && (
            <p className="ap-error" role="alert">
              {error}
            </p>
          )}
          <div className="ap-actions ap-inkbar m-ink">
            {questions.length > 0 && (
              <button
                type="button"
                className="btn btn-primary"
                aria-disabled={blocked || busy ? 'true' : undefined}
                aria-describedby={blocked ? reasonId : undefined}
                onClick={() => void save()}
              >
                {busy === 'save' ? 'Saving' : 'Save and continue'}
              </button>
            )}
            {questions.length === 0 && onFillAgain && fillAgainOffered(detail) && (
              <button type="button" className="btn btn-primary" aria-disabled={busy ? 'true' : undefined} onClick={() => void fillAgain()}>
                {busy === 'again' ? 'Queuing' : 'Fill it again'}
              </button>
            )}
            {confirmSkip ? (
              <>
                <button type="button" className="btn" aria-disabled={busy ? 'true' : undefined} onClick={() => void skip()}>
                  {busy === 'skip' ? 'Skipping' : 'Yes, skip it'}
                </button>
                <button type="button" className="btn btn-quiet" onClick={() => setConfirmSkip(false)}>
                  Keep it
                </button>
              </>
            ) : (
              <button type="button" className="btn btn-quiet" onClick={() => setConfirmSkip(true)}>
                Skip this application
              </button>
            )}
          </div>
          {blocked && questions.length > 0 && (
            <p id={reasonId} className="ap-muted ap-small">
              {blocked}
            </p>
          )}
          {confirmSkip && <p className="ap-muted ap-small">Nothing is sent, and the hold on your credit is released.</p>}
        </div>

        <aside className="ap-aside ap-stack-s">
          <section className="panel ap-panel ap-stack-s" aria-labelledby={`${reasonId}-known`}>
            <h2 id={`${reasonId}-known`} className="kicker">
              What forms are told
            </h2>
            <p className="ap-small">Your answers are kept in Your details with where each came from. We ask only what a form needs.</p>
            <a className="ap-small" {...linkTo(DETAILS_ROUTE, navigate)}>
              See and change all of it
            </a>
          </section>
          {detail.manualUrl && (
            <section className="panel ap-panel ap-stack-s">
              <h2 className="kicker">Rather do it yourself?</h2>
              <a className="btn" href={detail.manualUrl} target="_blank" rel="noreferrer noopener">
                Open the form on {company}'s site
              </a>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
