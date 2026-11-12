/**
 * Where an application stopped, or how it ended (canvas 5). Every stop says what happened and what the person can do.
 *
 *   approved, submitting     Sending: the page updates on its own; nothing to press once the send has started
 *   needs_signin             a login wall, a check meant for a person, or an emailed code: Open the form, Copy my
 *                            answers, I applied there (we never sign in or pass a check for the person)
 *   waiting_for_you + form_changed   the form changed after the review: Review the new version
 *   check_this_one           no clear outcome after the click: It went through (resolve 'sent') or It didn't: refund
 *                            me (resolve 'not_sent'); never sent again on our own
 *   failed                   before the click, the hold released: the employer's site, or fill it again
 *   closed, cancelled        nothing was sent
 *   sent and later           the time, the host, the values, the resume and the confirmation page; Practise the
 *                            interview, Mark a reply
 *
 * A "Try again" is never offered for submitting, check_this_one or sent (`outcomeActions`): the send may already have
 * reached the employer, and a second one could reach them twice.
 */
import { useId, useState } from 'react';
import type { ApplicationDetail, ApplicationStage, EmailClassification, EmailClassifyRequest, ResolveOutcome } from '../../shared/apply';
import { plural } from '../lib/ui';
import { matchPath } from '../match/route';
import { APPROVED_LINE, STAGE_LABEL, STOP_REASON_TEXT, TRACKING_STAGES, errorText, isSent, stageTone } from './api';
import { EmailPanel } from './EmailPanel';
import { ResumeLink, CopyLink, sentWithLine } from './ResumeNamed';
import { ReviewGroup, groupFields } from './ReviewGroup';
import { LIST_ROUTE, linkTo } from './route';
import type { Navigate } from './route';

/* ───────────────────────── what code computes ───────────────────────── */

export type OutcomeKind =
  | 'sending'
  | 'signin'
  | 'challenge'
  | 'code'
  | 'form_changed'
  | 'check'
  | 'failed'
  | 'closed'
  | 'cancelled'
  | 'sent';

/** Which outcome panel an application shows, or null when its stage has a screen of its own (filling, questions, review). */
export function outcomeKind(detail: Pick<ApplicationDetail, 'stage' | 'stopReason'>): OutcomeKind | null {
  const { stage, stopReason } = detail;
  if (stage === 'approved' || stage === 'submitting') return 'sending';
  if (stage === 'needs_signin') return stopReason === 'challenge' ? 'challenge' : stopReason === 'verification_code' ? 'code' : 'signin';
  if (stage === 'waiting_for_you' && stopReason === 'form_changed') return 'form_changed';
  if (stage === 'check_this_one') return 'check';
  if (stage === 'failed') return 'failed';
  if (stage === 'closed') return 'closed';
  if (stage === 'cancelled') return 'cancelled';
  if (isSent(stage)) return 'sent';
  return null;
}

export type OutcomeAction =
  | 'open'
  | 'copy'
  | 'applied_there'
  | 'skip'
  | 'review_new'
  | 'went_through'
  | 'refund'
  | 'employer_site'
  | 'fill_again'
  | 'resume'
  | 'dont_send'
  | 'practise'
  | 'mark_reply'
  | 'match'
  | 'back';

/** The actions an outcome offers, in order. A fill again is offered only before any click (`failed`). */
export function outcomeActions(kind: OutcomeKind, detail: Pick<ApplicationDetail, 'stage' | 'review' | 'progress' | 'resumeChoice' | 'matchId'>): OutcomeAction[] {
  const canCopy = (detail.review?.fields.length ?? detail.progress?.fields?.length ?? 0) > 0;
  const copy: OutcomeAction[] = canCopy ? ['copy'] : [];
  switch (kind) {
    case 'sending':
      return detail.stage === 'approved' ? ['dont_send'] : [];
    case 'signin':
    case 'code':
      return ['open', ...copy, 'applied_there'];
    case 'challenge':
      return ['open', ...copy, 'applied_there', 'skip'];
    case 'form_changed':
      return ['review_new'];
    case 'check':
      return ['went_through', 'refund'];
    case 'failed':
      return ['employer_site', ...(detail.resumeChoice === 'tailored' && detail.matchId ? (['resume'] as const) : []), 'fill_again'];
    case 'closed':
      return detail.matchId ? ['match', 'back'] : ['back'];
    case 'cancelled':
      return ['back'];
    case 'sent':
      return ['practise', 'mark_reply'];
  }
}

/** "6 Nov 2026, 14:06": the time of a send, in the person's own time zone. */
export function whenText(iso: string | undefined): string {
  const at = iso ? Date.parse(iso) : Number.NaN;
  if (!Number.isFinite(at)) return '';
  return new Date(at).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** "Label: value" lines for the person to paste into the employer's form; signatures are theirs to type, so left out. */
export function answersText(detail: Pick<ApplicationDetail, 'review' | 'progress'>): string {
  const fields = detail.review?.fields ?? detail.progress?.fields ?? [];
  return fields
    .filter((f) => f.value && !f.attestation)
    .map((f) => `${f.label}: ${f.value}`)
    .join('\n');
}

/** Where the form was sent: the host and path of the form, without the query. */
export function sentTo(detail: Pick<ApplicationDetail, 'applyUrl' | 'jobUrl' | 'domain'>): string {
  const raw = detail.applyUrl ?? detail.jobUrl;
  try {
    const u = new URL(raw);
    return `${u.host}${u.pathname.replace(/\/+$/, '')}`;
  } catch {
    return detail.domain;
  }
}

/* ───────────────────────── the view ───────────────────────── */

export interface OutcomeViewProps {
  detail: ApplicationDetail;
  navigate: Navigate;
  /** The employer's confirmation page, captured after sending; absent in the gallery. */
  confirmation?: string;
  onResolve: (outcome: ResolveOutcome) => Promise<void>;
  onAppliedThere: () => Promise<void>;
  onCancel: () => Promise<void>;
  onFillAgain: () => Promise<void>;
  onStage: (stage: ApplicationStage) => Promise<void>;
  onReview: () => void;
  onEmail?: (body: EmailClassifyRequest) => Promise<EmailClassification>;
}

interface Copy {
  chip: string;
  title: string;
  body: string;
  well?: string;
}

function copyFor(kind: OutcomeKind, detail: ApplicationDetail): Copy {
  const company = detail.company ?? detail.domain;
  const released = detail.creditState === 'released';
  const why = detail.stageReason ?? (detail.stopReason ? STOP_REASON_TEXT[detail.stopReason] : undefined);
  switch (kind) {
    case 'sending':
      return detail.stage === 'approved'
        ? {
            chip: 'Approved',
            title: APPROVED_LINE,
            body: 'We fill the form again with exactly the values you approved, check it still matches, and only then send. This page updates on its own.',
          }
        : {
            chip: 'Sending',
            title: `Sending your application to ${company}`,
            body: 'The send has started, so it can no longer be stopped from here. This page updates on its own when the employer’s page answers.',
          };
    case 'signin':
      return {
        chip: STAGE_LABEL.needs_signin,
        title: 'The site wants you to sign in or create an account',
        body: `${company}'s form asks every applicant to sign in first. That is yours to do, and we never store a password for you. Nothing was sent.`,
        well: 'Your answers are kept here, ready to copy, so you don’t type them twice.',
      };
    case 'challenge':
      return {
        chip: 'A check for you',
        title: 'The site asked to check you’re a person',
        body: 'We stop at any CAPTCHA or “are you human” step and never try to pass it. Nothing was sent, and nothing was charged.',
        well: 'Your answers are kept here, ready to copy, so you don’t type them twice.',
      };
    case 'code':
      return {
        chip: 'Needs a code from you',
        title: `Type the code ${company} emailed you`,
        body: 'The form asked for a code it sent to your email. We don’t read your inbox, so the last step is yours, on the employer’s page. Nothing was sent.',
        well: 'Your answers are kept here, ready to copy, so you don’t type them twice.',
      };
    case 'form_changed':
      return {
        chip: 'The form changed',
        title: `${company} changed the form after you reviewed it`,
        body: `Just before sending we compared the form with what you approved, and it was different${detail.stageReason ? `: ${detail.stageReason}` : ''}. We stopped, so nothing was sent. Your approval no longer matches.`,
      };
    case 'check':
      return {
        chip: 'Check this one',
        title: 'We sent it, but didn’t see a confirmation',
        body: `${detail.stageReason ? `${detail.stageReason}. ` : ''}We won't send it again on our own, because a second send could reach the employer twice.`,
        well: 'Look for a confirmation email, or open your application on the employer’s site.',
      };
    case 'failed':
      return {
        chip: released ? 'Didn’t go · refunded' : 'Didn’t go',
        title: 'We couldn’t finish this form',
        body: `${why ? `${why}. ` : ''}Nothing reached the employer${released ? ', and the hold on your credit was released' : ''}.`,
      };
    case 'closed':
      return {
        chip: 'Posting closed',
        title: 'This posting has closed',
        body: `${company}'s page says it no longer takes applications. Nothing was sent${released ? ', and the hold on your credit was released' : ''}.`,
      };
    case 'cancelled':
      return {
        chip: 'Skipped',
        title: 'You skipped this one',
        body: `Nothing was sent${released ? ', and the hold on your credit was released' : ''}.`,
      };
    case 'sent':
      return {
        chip: STAGE_LABEL[detail.stage],
        title: `${company} has your application`,
        body: '',
      };
  }
}

export function OutcomeView({ detail, navigate, confirmation, onResolve, onAppliedThere, onCancel, onFillAgain, onStage, onReview, onEmail }: OutcomeViewProps) {
  const kind = outcomeKind(detail);
  const [busy, setBusy] = useState<OutcomeAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [showValues, setShowValues] = useState(false);
  const [marking, setMarking] = useState(false);
  const base = useId();
  if (!kind) return null;

  const copy = copyFor(kind, detail);
  const actions = outcomeActions(kind, detail);
  const company = detail.company ?? detail.domain;
  const openUrl = detail.manualUrl ?? detail.applyUrl ?? detail.jobUrl;
  const fields = detail.review?.fields ?? [];
  const tone = stageTone(detail.stage, detail.needs.length);
  const live = kind === 'sending';

  const run = async (what: OutcomeAction, fn: () => Promise<void>, fallback: string) => {
    if (busy) return;
    setBusy(what);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errorText(err, fallback));
    } finally {
      setBusy(null);
    }
  };

  const copyAnswers = async () => {
    try {
      await navigator.clipboard.writeText(answersText(detail));
      setCopied('Copied. Paste them into the employer’s form.');
    } catch {
      setCopied('Your browser did not allow copying. Select the answers below instead.');
      setShowValues(true);
    }
  };

  const button = (a: OutcomeAction, primary: boolean) => {
    const cls = primary ? 'btn btn-primary' : a === 'skip' || a === 'applied_there' || a === 'back' || a === 'mark_reply' ? 'btn btn-quiet' : 'btn';
    const dis = busy ? 'true' : undefined;
    switch (a) {
      case 'open':
      case 'employer_site':
        return (
          <a key={a} className={cls} href={openUrl} target="_blank" rel="noreferrer noopener">
            {a === 'open' ? (kind === 'challenge' ? 'Finish on the employer’s page' : 'Open the form') : 'Apply on the employer’s site'}
          </a>
        );
      case 'copy':
        return (
          <button key={a} type="button" className={cls} onClick={() => void copyAnswers()}>
            Copy my answers
          </button>
        );
      case 'applied_there':
        return (
          <button key={a} type="button" className={cls} aria-disabled={dis} onClick={() => void run(a, onAppliedThere, 'Could not mark it. Try again.')}>
            {busy === a ? 'Marking' : 'I applied there'}
          </button>
        );
      case 'skip':
      case 'dont_send':
        return (
          <button key={a} type="button" className={cls} aria-disabled={dis} onClick={() => void run(a, onCancel, 'Could not stop it. Try again.')}>
            {busy === a ? 'Stopping' : a === 'skip' ? 'Skip this one' : 'Don’t send it'}
          </button>
        );
      case 'review_new':
        return (
          <button key={a} type="button" className={cls} onClick={onReview}>
            Review the new version
          </button>
        );
      case 'went_through':
        return (
          <button key={a} type="button" className={cls} aria-disabled={dis} onClick={() => void run(a, () => onResolve('sent'), 'Could not save that. Try again.')}>
            {busy === a ? 'Saving' : 'It went through'}
          </button>
        );
      case 'refund':
        return (
          <button key={a} type="button" className={cls} aria-disabled={dis} onClick={() => void run(a, () => onResolve('not_sent'), 'Could not save that. Try again.')}>
            {busy === a ? 'Saving' : 'It didn’t: refund me'}
          </button>
        );
      case 'fill_again':
        return (
          <button key={a} type="button" className={cls} aria-disabled={dis} onClick={() => void run(a, onFillAgain, 'Could not start it again. Try again.')}>
            {busy === a ? 'Starting' : 'Fill it again'}
          </button>
        );
      case 'resume':
        return (
          <a key={a} className={cls} href={matchPath({ screen: 'resume', id: detail.matchId ?? '' })}>
            Open the resume
          </a>
        );
      case 'practise':
        return (
          <a key={a} className={cls} href="/practice">
            Practise the interview
          </a>
        );
      case 'mark_reply':
        return (
          <button key={a} type="button" className={cls} aria-expanded={marking} aria-controls={`${base}-mark`} onClick={() => setMarking((m) => !m)}>
            Mark a reply
          </button>
        );
      case 'match':
        return (
          <a key={a} className={cls} href={matchPath({ screen: 'match', id: detail.matchId ?? '' })}>
            See the match
          </a>
        );
      case 'back':
        return (
          <a key={a} className={cls} {...linkTo(LIST_ROUTE, navigate)}>
            Back to Applications
          </a>
        );
    }
  };

  return (
    <div className="ap-stack ap-narrow">
      <p className="kicker">
        <a {...linkTo(LIST_ROUTE, navigate)}>Applications</a> · {[detail.title, company].filter(Boolean).join(' · ')}
      </p>
      <section className={`panel ap-panel ap-stack-s ar-outcome ${tone === 'ok' ? 'm-field' : 'm-quiet'}`} aria-labelledby={`${base}-t`} aria-live={live ? 'polite' : undefined}>
        <span className={`chip ap-chip ap-tone-${tone} ap-self-start`}>{copy.chip}</span>
        <h1 id={`${base}-t`} className="serif ap-title">
          {copy.title}
        </h1>

        {kind === 'check' && detail.submittedAt && (
          <p className="ap-small">
            Sent at <span className="numeral">{whenText(detail.submittedAt)}</span>.
          </p>
        )}
        {copy.body && <p className="ap-small ap-measure">{copy.body}</p>}

        {kind === 'sent' && (
          <dl className="ar-kv">
            <dt className="ap-muted">Sent</dt>
            <dd className="numeral">{whenText(detail.submittedAt ?? detail.approvedAt) || 'Time not recorded'}</dd>
            <dt className="ap-muted">To</dt>
            <dd>{sentTo(detail)}</dd>
            {fields.length > 0 && (
              <>
                <dt className="ap-muted">Values</dt>
                <dd>
                  <span className="numeral">{fields.length}</span>, exactly as you approved them.{' '}
                  <button type="button" className="btn btn-quiet ap-row-btn" aria-expanded={showValues} onClick={() => setShowValues((s) => !s)}>
                    {showValues ? 'Hide them' : 'See them'}
                  </button>
                </dd>
              </>
            )}
            {detail.review?.resume && (
              <>
                <dt className="ap-muted">Resume</dt>
                <dd>{detail.review.resume.name}</dd>
              </>
            )}
            {detail.resumeCopy && (
              <>
                <dt className="ap-muted">Sent with</dt>
                <dd>
                  {sentWithLine(detail.resumeCopy)}.{' '}
                  <CopyLink applicationId={detail.id} /> · <ResumeLink />
                </dd>
              </>
            )}
          </dl>
        )}

        {kind === 'sent' &&
          (confirmation && detail.hasConfirmation ? (
            <img className="ar-shot" src={confirmation} alt={`${company}'s confirmation page, captured after sending`} loading="lazy" />
          ) : (
            <div className="ar-shot ar-shot-empty" role="img" aria-label={detail.hasConfirmation ? `${company}'s confirmation page, captured after sending` : 'No confirmation page was captured'}>
              {detail.hasConfirmation ? 'The employer’s confirmation page, captured after sending' : 'No confirmation page was captured.'}
            </div>
          ))}

        {copy.well && (
          <div className="ap-well-box">
            <p>{copy.well}</p>
            {kind === 'check' && (
              <a href={openUrl} target="_blank" rel="noreferrer noopener">
                Open {company}'s page
              </a>
            )}
          </div>
        )}

        {actions.length > 0 && <div className="ap-actions">{actions.map((a, i) => button(a, i === 0 && a !== 'practise'))}</div>}

        {copied && (
          <p className="ap-muted ap-small" role="status">
            {copied}
          </p>
        )}
        {error && (
          <p className="ap-error" role="alert">
            {error}
          </p>
        )}
        {kind === 'check' && (
          <p className="ap-muted ap-small">
            {detail.creditState === 'held' ? 'Your credit stays on hold until you tell us. ' : ''}If it didn't go through, we release the hold and you can apply on the employer's site.
          </p>
        )}
      </section>

      {marking && kind === 'sent' && (
        <section id={`${base}-mark`} className="panel ap-panel ap-stack-s" aria-labelledby={`${base}-mark-t`}>
          <h2 id={`${base}-mark-t`} className="ap-h3">
            Where it stands now
          </h2>
          <div className="ap-actions">
            {TRACKING_STAGES.map((s) => (
              <button
                key={s}
                type="button"
                className="btn"
                aria-pressed={detail.stage === s}
                aria-disabled={busy || detail.stage === s ? 'true' : undefined}
                onClick={() => {
                  if (detail.stage !== s) void run('mark_reply', () => onStage(s), 'Could not change the stage. Try again.');
                }}
              >
                {STAGE_LABEL[s]}
              </button>
            ))}
          </div>
          {onEmail && <EmailPanel applications={[detail]} onSubmit={onEmail} />}
        </section>
      )}

      {showValues && (fields.length > 0 || (detail.progress?.fields?.length ?? 0) > 0) && (
        <section className="ap-stack-s" aria-label={`The ${plural(fields.length || (detail.progress?.fields?.length ?? 0), 'value')} of this form`}>
          {groupFields(fields.length > 0 ? fields : (detail.progress?.fields ?? [])).map((g) => (
            <ReviewGroup key={g.title} title={g.title} fields={g.fields} facts={detail.review?.facts} resume={detail.review?.resume?.choice} />
          ))}
        </section>
      )}
    </div>
  );
}
