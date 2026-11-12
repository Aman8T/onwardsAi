/**
 * Review every value (canvas 4): the form as data, not a browser. Everything that will be sent, in the form's own groups
 * and order, each value with its source; the resume on its own line, attached only on approval; the screenshot of the
 * filled form. Approving signs the review's digest, so the person approves exactly what they saw.
 *
 *   - A breadcrumb, and Prev and Next across the applications waiting for the person ("2 of 5 ready"); the arrow keys
 *     do the same, except while focus is in a field or the tabs.
 *   - Tabs (Form, Resume, Cover note, Posting) as a roving-tabindex tablist.
 *   - "Approve and send" stays aria-disabled, with the reason, until no required need is left, the ones typed on the
 *     employer's page included (those can never be sent from here: the person opens the form instead).
 *   - Not now, Don't apply (cancel, the hold is released), the match and the emails about this application.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import type { AnswerRequest, ApplicationDetail, EmailClassification, EmailClassifyRequest, QuestionAnswer } from '../../shared/apply';
import type { MatchBand } from '../../shared/match';
import { plural } from '../lib/ui';
import { BAND_LABEL } from '../match/model';
import { matchPath } from '../match/route';
import { APPROVED_LINE, errorText, stageTone } from './api';
import { EmailPanel } from './EmailPanel';
import { NeedCard, answerBatch, answerable, initialDrafts, keyOf, missingRequired } from './NeedCard';
import type { NeedDraft } from './NeedCard';
import { LockIcon, ReviewGroup, groupFields, sourceSummary } from './ReviewGroup';
import type { ReviewField } from './ReviewGroup';
import { DefaultStamp, CopyLink, ResumeLink, defaultLine } from './ResumeNamed';
import { LIST_ROUTE, linkTo } from './route';
import type { Navigate } from './route';

/* ───────────────────────── what code computes ───────────────────────── */

/** The posting match beside the review: its band and the sentence code wrote about it (`bandNote`). */
export interface ReviewMatch {
  id: string;
  band: MatchBand;
  note: string;
}

/**
 * The person said an unclear send did not go through ("It didn't: refund me"): the review it was sent from is never
 * approved again (a fresh fill and approval, never a resend).
 */
export function afterUnclearSend(detail: { events?: ApplicationDetail['events'] }): boolean {
  const last = [...(detail.events ?? [])].reverse().find((e) => e.toStage);
  return last?.fromStage === 'check_this_one' && last.toStage === 'waiting_for_you';
}

/** Why "Approve and send" is off, or null when it is on. */
export function approveBlock(detail: Pick<ApplicationDetail, 'review' | 'needs' | 'company' | 'domain'> & { events?: ApplicationDetail['events'] }): string | null {
  if (!detail.review) return 'The review is not ready yet.';
  if (afterUnclearSend(detail)) return 'This review was already sent once without a clear answer. The form is filled again before you can approve it.';
  const required = detail.needs.filter((n) => n.required);
  const onSite = required.filter((n) => n.onSite);
  if (onSite.length > 0) {
    const what = onSite.length === 1 ? `“${onSite[0].label}” is` : `${plural(onSite.length, 'field')} are`;
    return `${what} yours to type on ${detail.company ?? detail.domain}'s page, so this form can't be sent from here. Open the form instead.`;
  }
  const files = required.filter((n) => n.kind === 'file');
  if (files.length > 0) {
    const what = files.length === 1 ? `“${files[0].label}”` : plural(files.length, 'file');
    return `This form needs ${what}. Add it in Your details first; the form is then filled again.`;
  }
  if (required.length > 0) return `Answer the ${required.length === 1 ? 'question' : `${required.length} questions`} marked for you first.`;
  return null;
}

export interface ReadyPosition {
  k: number;
  n: number;
  prev?: string;
  next?: string;
}

/** Where this application sits among the ones waiting for the person, or null when it is not one of them. */
export function readyPosition(queue: readonly string[], id: string): ReadyPosition | null {
  const at = queue.indexOf(id);
  if (at < 0) return null;
  return { k: at + 1, n: queue.length, prev: queue[at - 1], next: queue[at + 1] };
}

/** Arrow keys belong to a field, a select, the tabs or a radio group while focus is there; Review's Prev/Next ignores them. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!target || typeof (target as Element).closest !== 'function') return false;
  const el = target as HTMLElement;
  if (el.isContentEditable) return true;
  return el.closest('input, textarea, select, [contenteditable="true"], [role="tablist"], [role="radiogroup"], [role="listbox"]') !== null;
}

export type ReviewTab = 'form' | 'resume' | 'cover' | 'posting';

const TAB_LABEL: Record<ReviewTab, string> = { form: 'Form', resume: 'Resume', cover: 'Cover note', posting: 'Posting' };
const TABS: readonly ReviewTab[] = ['form', 'resume', 'cover', 'posting'];

const isCover = (f: ReviewField) => f.questionKey === 'coverLetter' || f.profileKey === 'coverLetter' || /cover (letter|note)/i.test(f.label);

/* ───────────────────────── the view ───────────────────────── */

export interface ReviewViewProps {
  detail: ApplicationDetail;
  navigate: Navigate;
  /** The ids of the applications waiting for the person, in the list's order. */
  queue?: readonly string[];
  match?: ReviewMatch;
  /** The screenshot of the filled form; absent in the gallery. */
  shot?: string;
  onApprove: (digest: string) => Promise<void>;
  onAnswer: (body: AnswerRequest) => Promise<void>;
  onCancel: () => Promise<void>;
  onEmail?: (body: EmailClassifyRequest) => Promise<EmailClassification>;
  initialTab?: ReviewTab;
}

export function ReviewView({ detail, navigate, queue = [], match, shot, onApprove, onAnswer, onCancel, onEmail, initialTab = 'form' }: ReviewViewProps) {
  const review = detail.review;
  const fields: ReviewField[] = review?.fields ?? [];
  const [tab, setTab] = useState<ReviewTab>(initialTab);
  const [drafts, setDrafts] = useState<Record<string, NeedDraft>>(() => initialDrafts(detail.needs));
  const [busy, setBusy] = useState<'approve' | 'answer' | 'cancel' | null>(null);
  const [approved, setApproved] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pasting, setPasting] = useState(false);
  const tabRefs = useRef<Partial<Record<ReviewTab, HTMLButtonElement | null>>>({});
  const base = useId();
  const reasonId = `${base}-reason`;
  const company = detail.company ?? detail.domain;
  const block = approveBlock(detail);
  // every value the approval signs is on screen: a skipped field the page set itself shows with its value
  const preset = (review?.skipped ?? []).filter((f) => Boolean(f.value));
  const empty = (review?.skipped ?? []).filter((f) => !f.value);
  const pos = readyPosition(queue, detail.id);
  const summary = sourceSummary(fields, review?.resume?.choice);
  const groups = groupFields(fields);
  const cover = fields.filter(isCover);
  const toAnswer = detail.needs.filter(answerable);
  const batch = answerBatch(detail.needs, drafts);
  const missing = missingRequired(detail.needs, drafts);
  const emails = detail.events.filter((e) => e.source === 'email');
  const openUrl = detail.manualUrl ?? detail.applyUrl ?? detail.jobUrl;

  // Prev and Next with the arrow keys, never while the person is typing or in the tabs
  const prev = pos?.prev;
  const next = pos?.next;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || isTypingTarget(e.target)) return;
      if (e.key === 'ArrowLeft' && prev) {
        e.preventDefault();
        navigate({ screen: 'one', id: prev });
      } else if (e.key === 'ArrowRight' && next) {
        e.preventDefault();
        navigate({ screen: 'one', id: next });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [prev, next, navigate]);

  const run = async (what: 'approve' | 'answer' | 'cancel', fn: () => Promise<void>, fallback: string) => {
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

  const approve = () => {
    if (block || !review) return;
    void run(
      'approve',
      async () => {
        await onApprove(review.digest);
        setApproved(true);
      },
      'Could not approve it. Nothing was sent. Try again.',
    );
  };
  const change = (answer: QuestionAnswer) => run('answer', () => onAnswer({ answers: [answer] }), 'Could not change that value. Try again.');

  const onTabKey = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    const at = TABS.indexOf(tab);
    let to: ReviewTab | null = null;
    if (e.key === 'ArrowRight') to = TABS[(at + 1) % TABS.length];
    else if (e.key === 'ArrowLeft') to = TABS[(at - 1 + TABS.length) % TABS.length];
    else if (e.key === 'Home') to = TABS[0];
    else if (e.key === 'End') to = TABS[TABS.length - 1];
    if (!to) return;
    e.preventDefault();
    setTab(to);
    tabRefs.current[to]?.focus();
  };

  const tabLabel = (t: ReviewTab) => (t === 'form' ? `Form · ${plural(fields.length, 'value')}` : TAB_LABEL[t]);
  const needsText = detail.needs.length > 0 ? `Waiting for you · ${detail.needs.length} to answer` : 'Ready for you';

  const resumeLine = review?.resume ? (
    <div className="ar-row">
      <span className="ap-q">Resume</span>
      <span className="ap-v">
        {review.resume.name}
        <span className="ap-muted ap-small">
          {' '}
          · {review.resume.choice === 'tailored' ? "this match's resume" : detail.resumeCopy ? defaultLine(detail.resumeCopy) : 'your saved resume'}
        </span>
        {detail.resumeCopy && review.resume.choice === 'saved' && (
          <>
            {' '}
            <DefaultStamp />{' '}
            <span className="ap-small">
              <ResumeLink>Open /resume</ResumeLink>
            </span>
          </>
        )}
      </span>
      <span className="ap-src">
        <span className="ar-lock">
          <LockIcon />
          Attached only when you approve
        </span>
      </span>
    </div>
  ) : null;

  return (
    <div className="ap-stack">
      <div className="ap-headrow ap-headrow-tight">
        <p className="kicker">
          <a {...linkTo(LIST_ROUTE, navigate)}>Applications</a> · Ready to review
        </p>
        {pos && (
          <nav className="ar-nav" aria-label="Applications waiting for you">
            {pos.prev ? (
              <a className="btn" aria-label="Previous application" {...linkTo({ screen: 'one', id: pos.prev }, navigate)}>
                ←
              </a>
            ) : (
              <span className="btn" role="link" aria-disabled="true" aria-label="Previous application">
                ←
              </span>
            )}
            <span className="numeral ap-muted ap-small">
              {pos.k} of {pos.n} ready
            </span>
            {pos.next ? (
              <a className="btn" aria-label="Next application" {...linkTo({ screen: 'one', id: pos.next }, navigate)}>
                →
              </a>
            ) : (
              <span className="btn" role="link" aria-disabled="true" aria-label="Next application">
                →
              </span>
            )}
          </nav>
        )}
      </div>

      <div className="ap-head">
        <h1 className="serif ap-title">{detail.title ?? company}</h1>
        <div className="ap-actions">
          <span className="ap-muted">{[company, detail.location].filter(Boolean).join(' · ')}</span>
          <span className={`chip ap-chip ap-tone-${stageTone(detail.stage, detail.needs.length)}`}>{needsText}</span>
          {match && <span className="chip ap-chip">{BAND_LABEL[match.band]}</span>}
        </div>
      </div>

      <div className="ar-tabs" role="tablist" aria-label="Application parts">
        {TABS.map((t) => (
          <button
            key={t}
            ref={(el) => {
              tabRefs.current[t] = el;
            }}
            type="button"
            role="tab"
            id={`${base}-tab-${t}`}
            aria-selected={tab === t}
            aria-controls={`${base}-panel-${t}`}
            tabIndex={tab === t ? 0 : -1}
            className="ar-tab"
            onClick={() => setTab(t)}
            onKeyDown={onTabKey}
          >
            {tabLabel(t)}
          </button>
        ))}
      </div>

      <div className="ap-split">
        <div className="ap-main ap-stack" role="tabpanel" id={`${base}-panel-${tab}`} aria-labelledby={`${base}-tab-${tab}`} tabIndex={0}>
          {tab === 'form' && (
            <>
              {fields.length > 0 && (
                <div className="panel ar-summary m-quiet">
                  <span className="ap-strong">Where these {plural(fields.length, 'value')} come from:</span>
                  {summary.map((s) => (
                    <span key={s.source} className="chip ap-chip">
                      {s.text}
                    </span>
                  ))}
                  {detail.needs.length > 0 && <span className="chip ap-chip ap-tone-you">{detail.needs.length} need you</span>}
                </div>
              )}

              {(review?.warnings.length ?? 0) > 0 && (
                <div className="ap-well-box" role="note">
                  <p className="ap-strong">Before you approve</p>
                  <ul className="ap-plain-list">
                    {review?.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}

              {detail.needs.length > 0 && (
                <section className="ap-stack-s" aria-labelledby={`${base}-needs`}>
                  <h2 id={`${base}-needs`} className="ap-h3">
                    For you to answer
                  </h2>
                  {detail.needs.map((need) => (
                    <NeedCard
                      key={keyOf(need)}
                      need={need}
                      company={company}
                      draft={drafts[keyOf(need)]}
                      disabled={busy !== null}
                      onChange={(d) => setDrafts((all) => ({ ...all, [keyOf(need)]: d }))}
                    />
                  ))}
                  {toAnswer.length > 0 && (
                    <div className="ap-actions">
                      <button
                        type="button"
                        className="btn"
                        aria-disabled={busy || batch.length === 0 || missing.length > 0 ? 'true' : undefined}
                        onClick={() => {
                          if (busy || batch.length === 0 || missing.length > 0) return;
                          void run('answer', () => onAnswer({ answers: batch }), 'Could not add your answers. Try again.');
                        }}
                      >
                        {busy === 'answer' ? 'Adding' : 'Add to the form'}
                      </button>
                      <span className="ap-muted ap-small">The form is filled again with your answers, and you review it once more.</span>
                    </div>
                  )}
                </section>
              )}

              {resumeLine && (
                <section className="panel ap-panel ar-group m-graph" aria-label="Resume">
                  {resumeLine}
                </section>
              )}

              {groups.map((g) => (
                <ReviewGroup key={g.title} title={g.title} fields={g.fields} facts={review?.facts} resume={review?.resume?.choice} onChange={change} busy={busy !== null} />
              ))}

              {preset.length > 0 && (
                <section className="panel ap-panel ap-stack-s" aria-labelledby={`${detail.id}-preset`}>
                  <h2 id={`${detail.id}-preset`} className="kicker">
                    Set by the employer's form
                  </h2>
                  <p className="ap-muted ap-small">We left these alone; the form had them set already, and they are sent as they are.</p>
                  <ul className="ap-plain-list ap-small">
                    {preset.map((f) => (
                      <li key={f.key}>
                        {f.label}: <span className="ap-strong">{f.value}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {empty.length > 0 && (
                <details className="panel ap-panel">
                  <summary className="ap-summary">Left empty: {plural(empty.length, 'optional field')}</summary>
                  <ul className="ap-plain-list ap-small">
                    {empty.map((f) => (
                      <li key={f.key}>{f.label}</li>
                    ))}
                  </ul>
                </details>
              )}

              <figure className="ar-figure">
                {shot && detail.hasScreenshot ? (
                  <img className="ar-shot" src={shot} alt={`The ${company} form, filled, as it will be sent`} loading="lazy" />
                ) : (
                  <div className="ar-shot ar-shot-empty" role="img" aria-label="No screenshot of the filled form yet">
                    The screenshot of the filled form shows here.
                  </div>
                )}
                <figcaption className="ap-muted ap-small">The filled form, captured before anything was sent. The resume input is empty on purpose.</figcaption>
              </figure>
            </>
          )}

          {tab === 'resume' && (
            <section className="panel ap-panel ap-stack-s">
              {review?.resume ? (
                <>
                  <p className="ap-strong">{review.resume.name}</p>
                  <p className="ap-small">
                    {review.resume.choice === 'tailored'
                      ? 'The resume this match built, every line from your facts.'
                      : detail.resumeCopy
                        ? `${defaultLine(detail.resumeCopy)}. Copied when these values were prepared: a later edit or a new default does not change this application, and a changed default stops the send until you review again.`
                        : 'The resume you saved in Your details, as it is.'}
                  </p>
                  <p className="ap-src">
                    <span className="ar-lock">
                      <LockIcon />
                      Attached only when you approve. The preview never uploads it.
                    </span>
                  </p>
                  {review.resume.choice === 'tailored' && detail.matchId && (
                    <a className="ap-small" href={matchPath({ screen: 'resume', id: detail.matchId })}>
                      Open the resume
                    </a>
                  )}
                  {detail.resumeCopy && review.resume.choice === 'saved' && (
                    <p className="ap-small">
                      <CopyLink applicationId={detail.id} /> · <ResumeLink />
                    </p>
                  )}
                </>
              ) : (
                <p className="ap-muted">This form does not take a resume.</p>
              )}
            </section>
          )}

          {tab === 'cover' && (cover.length > 0 ? <ReviewGroup title="Cover note" fields={cover} facts={review?.facts} resume={review?.resume?.choice} onChange={change} busy={busy !== null} /> : <p className="ap-muted">This form does not ask for a cover note.</p>)}

          {tab === 'posting' && (
            <section className="panel ap-panel ap-stack-s">
              {detail.description ? <p className="ar-posting">{detail.description}</p> : <p className="ap-muted">The posting's text is on the employer's site.</p>}
              <a className="ap-small" href={detail.jobUrl} target="_blank" rel="noreferrer noopener">
                The posting on {detail.domain}
              </a>
            </section>
          )}
        </div>

        <aside className="ap-aside ap-stack-s">
          <section className="panel ap-panel ap-stack-s m-ink ap-ink" aria-labelledby={`${base}-approve`}>
            <h2 id={`${base}-approve`} className="kicker">
              Approve and send
            </h2>
            <p className="ap-small">
              You approve exactly these <span className="numeral">{fields.length}</span> values{review?.resume ? ' and this resume' : ''}. If the form changes before we
              send, we stop and show it to you again.
            </p>
            <button
              type="button"
              className="btn btn-primary ap-wide-btn"
              aria-disabled={block || busy ? 'true' : undefined}
              aria-describedby={block ? reasonId : undefined}
              onClick={approve}
            >
              {busy === 'approve' ? 'Approving' : 'Approve and send'}
            </button>
            <p className="sr-only" role="status" aria-live="polite">
              {busy === 'approve' ? 'Approving' : approved ? APPROVED_LINE : ''}
            </p>
            {block && (
              <p id={reasonId} className="ap-muted ap-small">
                {block}
              </p>
            )}
            {block && detail.needs.some((n) => n.required && n.onSite) && openUrl && (
              <a className="btn ap-wide-btn" href={openUrl} target="_blank" rel="noreferrer noopener">
                Open the form
              </a>
            )}
            {error && (
              <p className="ap-error" role="alert">
                {error}
              </p>
            )}
            <a className="btn btn-quiet ap-wide-btn" {...linkTo(LIST_ROUTE, navigate)}>
              Not now
            </a>
            {confirmCancel ? (
              <div className="ap-stack-s">
                <p className="ap-small">Skip this application? Nothing is sent, and the hold on your credit is released.</p>
                <div className="ap-actions">
                  <button type="button" className="btn" aria-disabled={busy ? 'true' : undefined} onClick={() => void run('cancel', onCancel, 'Could not skip it. Try again.')}>
                    {busy === 'cancel' ? 'Skipping' : "Yes, don't apply"}
                  </button>
                  <button type="button" className="btn btn-quiet" onClick={() => setConfirmCancel(false)}>
                    Keep it
                  </button>
                </div>
              </div>
            ) : (
              <button type="button" className="btn btn-quiet ap-wide-btn" onClick={() => setConfirmCancel(true)}>
                Don't apply
              </button>
            )}
          </section>

          <section className="panel ap-panel ap-stack-s" aria-labelledby={`${base}-match`}>
            <h2 id={`${base}-match`} className="kicker">
              The match
            </h2>
            {match ? (
              <>
                <p className="ap-small">
                  <span className="ap-strong">{BAND_LABEL[match.band]}.</span> {match.note}
                </p>
                <a className="ap-small" href={matchPath({ screen: 'match', id: match.id })}>
                  See the full match
                </a>
              </>
            ) : detail.matchId ? (
              <a className="ap-small" href={matchPath({ screen: 'match', id: detail.matchId })}>
                See the match
              </a>
            ) : (
              <p className="ap-muted ap-small">No match is linked to this application.</p>
            )}
          </section>

          <section className="panel ap-panel ap-stack-s" aria-labelledby={`${base}-emails`}>
            <h2 id={`${base}-emails`} className="kicker">
              Emails about this application
            </h2>
            {emails.length === 0 ? (
              <p className="ap-muted ap-small">None yet. Replies you forward or paste show up here.</p>
            ) : (
              <ul className="ap-plain-list ap-small">
                {emails.map((e) => (
                  <li key={`${e.at}-${e.kind}`}>{e.note ?? e.kind}</li>
                ))}
              </ul>
            )}
            {onEmail &&
              (pasting ? (
                <EmailPanel applications={[detail]} onSubmit={onEmail} />
              ) : (
                <button type="button" className="btn btn-quiet ap-self-start" onClick={() => setPasting(true)}>
                  Paste an email
                </button>
              ))}
          </section>
        </aside>
      </div>
    </div>
  );
}
