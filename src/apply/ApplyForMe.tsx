/**
 * "Apply for me" wherever a posting can be applied to (spec 2.1, canvas 8 and 9): the button and the one choice it
 * opens. Both are presentational; the screen that renders them (MatchRoot, Discover's cards) works out the state.
 *
 *   ApplyForMeButton   the primary action in each of its states (`RoleApply`): offered, signed out (opens the sign-in
 *                      sheet), checking, already applied (opens that application), off with the reason, or the
 *                      employer's own account needed (the reason only; the employer's link stays the primary)
 *   ApplyForMeChoice   "How should we apply?": the match's tailored resume or the saved one, the application link
 *                      for a pasted match, the Stretch note, the first-time note, Continue and Cancel
 *
 * `roleApplyFor` turns what is known (the status, the match's gates, an earlier application, the start check) into
 * the state, in code, in the start checks' order (`apply/checks.py`). Nothing here starts, charges or fetches: the
 * start screen runs the checks again before anything is filled. No percentage, no spinner: a static mark and words.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { ApplicationSummary, ApplyStatus, ResumeChoice } from '../../shared/apply';
import { signInSheet } from '../auth/useMe';
import { shortDate } from '../billing/format';
import { STAGE_LABEL, isSent } from './api';
import { cleanApplyUrl } from './ats';
import { applyPath } from './route';
import { reopens } from './useApplications';
import type { CheckState, SavedResume } from './useApplications';
import '../styles/apply-entry.css';

/* ───────────────────────── the state ───────────────────────── */

/** Where "Apply for me" stands for one posting. */
export type RoleApply =
  | { kind: 'offer' }
  | { kind: 'signed_out' }
  | { kind: 'checking'; step?: string }
  | { kind: 'applied'; id: string; label: string }
  | { kind: 'own_account'; reason: string }
  | { kind: 'off'; reason: string };

/** The sentence above the sign-in form when Apply for me is pressed signed out (rule 9: signed-in only). */
export const SIGN_IN_NOTE = 'Sign in to apply from here. Auto-apply is for signed-in accounts only.';

/** "Sent 4 Nov" for a sent application (and every later stage), else the stage in words ("Waiting for you"). */
export function appliedLabel(a: Pick<ApplicationSummary, 'stage' | 'updatedAt'>): string {
  if (!isSent(a.stage)) return STAGE_LABEL[a.stage];
  const day = shortDate(a.updatedAt);
  return day ? `Sent ${day}` : 'Sent';
}

export interface RoleApplyInput {
  /** The auto-apply status; null while it is not known (nothing is offered then). */
  status: ApplyStatus | null;
  /** Someone is signed in, by the account (`useMe`). */
  signedIn: boolean;
  /** The match blocked on a hard requirement: the failing gate's text. */
  blockedReason?: string;
  /** The application that already stands for this posting, if any. */
  application?: ApplicationSummary;
  /** The read-only start check, when one was asked. */
  check?: CheckState;
  /** The posting's form address is known (a Discover listing's link), so the choice never asks for one. */
  knownLink: boolean;
}

export interface RoleApplyResult {
  apply: RoleApply;
  /** The choice must ask for the application form's link (a pasted match, no posting address known). */
  needsLink: boolean;
}

/**
 * The state of Apply for me, or null when auto-apply is off (nothing apply-related renders). Order: off, an earlier
 * application, signed out, the match's own gates, then the server's check. A check that could not be read offers the
 * button anyway: the start screen runs the same checks before anything starts.
 */
export function roleApplyFor(input: RoleApplyInput): RoleApplyResult | null {
  const { status, signedIn, blockedReason, application, check, knownLink } = input;
  if (!status?.enabled) return null;
  const result = (apply: RoleApply, needsLink = false): RoleApplyResult => ({ apply, needsLink });
  if (application && !reopens(application)) return result({ kind: 'applied', id: application.id, label: appliedLabel(application) });
  if (!signedIn || !status.signedIn) return result({ kind: 'signed_out' });
  if (blockedReason) return result({ kind: 'off', reason: blockedReason });
  if (!check || check.state === 'idle' || check.state === 'error' || !check.check) {
    if (check?.state === 'loading') return result({ kind: 'checking' });
    return result({ kind: 'offer' }, !knownLink);
  }
  const c = check.check;
  if (c.offered) return result({ kind: 'offer' });
  const note = c.note ?? 'Apply on the employer’s site.';
  switch (c.reason) {
    case 'signed_out':
      return result({ kind: 'signed_out' });
    case 'already_applied':
      return c.applicationId ? result({ kind: 'applied', id: c.applicationId, label: 'Already applied' }) : result({ kind: 'off', reason: note });
    case 'own_account_ats':
    case 'unknown_liveness':
    case 'manual_only':
      // the employer's own site is the way (spec 2.1; an unknown liveness is never shown as closed)
      return result({ kind: 'own_account', reason: note });
    case 'closed':
      return null; // a closed posting offers nothing
    case 'blocked_by_requirement':
      return result({ kind: 'off', reason: c.gateReasons?.[0] ?? note });
    case 'not_fillable':
      // no form address at all (pasted text): ask for the link; a known address on an ATS we don't fill: say so
      return !c.host && !knownLink ? result({ kind: 'offer' }, true) : result({ kind: 'off', reason: note });
    default:
      return result({ kind: 'off', reason: note });
  }
}

/** The shapes `blockedReasonOf` reads (shared/match `Gate` and `GateAnswer`). */
interface GateLike {
  id: string;
  question: string;
  severity: number;
}
interface GateAnswerLike {
  gateId: string;
  verdict: string;
  note: string;
}

export const BLOCKED_FALLBACK = 'a hard requirement of this role isn’t met yet.';

/**
 * Why a blocked match is not offered: the failing gate with the highest severity, by its own note, else by what the
 * posting asks. Only a `fail` blocks (a "can't tell" never does), so only a fail is named.
 */
export function blockedReasonOf(gates: readonly GateLike[], answers: readonly GateAnswerLike[]): string {
  const byId = new Map(gates.map((g) => [g.id, g]));
  const failing = answers
    .filter((a) => a.verdict === 'fail')
    .map((a) => ({ a, g: byId.get(a.gateId) }))
    .sort((x, y) => (y.g?.severity ?? 0) - (x.g?.severity ?? 0));
  const top = failing[0];
  if (!top) return BLOCKED_FALLBACK;
  const note = top.a.note.trim();
  if (note) return note;
  return top.g ? `the posting asks: ${top.g.question}` : BLOCKED_FALLBACK;
}

/** What a match screen needs to render Apply for me (MatchView's `apply` prop). */
export interface ApplyEntry extends RoleApplyResult {
  /** The person has not done the first check yet: the choice says a 30-second check comes first. */
  firstTime: boolean;
  /** The saved resume, when known. */
  savedResume?: SavedResume | null;
  /** The choice is open on first paint (a Discover card's Apply for me sent the person here). */
  open?: boolean;
  /** Bumped when something else (the origin bar) asks for the choice; the screen opens it and calls `onAsked`. */
  asks?: number;
  onAsked?: () => void;
  /** Where the sign-in returns to. */
  signInNext?: string;
  onContinue: (choice: ApplyChoice) => void;
}

/* ───────────────────────── the button ───────────────────────── */

export interface ApplyForMeButtonProps {
  apply: RoleApply;
  /** Pressed while offered (open the choice, or start the card's match first). */
  onApply: () => void;
  size?: 'md' | 'sm';
  /** The id of the inline choice this button opens, and whether it is open (aria-controls, aria-expanded). */
  controls?: string;
  expanded?: boolean;
  /** Where the sign-in returns to; the current page when absent. */
  signInNext?: string;
  /** Extra classes on the button (the screen's own button sizing). */
  className?: string;
  /** Announce the checking line (default). Off for a second copy of the button on the same screen (the origin bar). */
  live?: boolean;
}

function Chevron() {
  return (
    <svg className="ae-chev" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" focusable="false">
      <path d="M3 4.5L6 7.5 9 4.5" />
    </svg>
  );
}

/** The primary "Apply for me" in each state; off and checking say why beside the button. */
export function ApplyForMeButton({ apply, onApply, size = 'md', controls, expanded, signInNext, className, live = true }: ApplyForMeButtonProps) {
  const why = useId();
  const cls = ['btn', 'ae-btn', size === 'sm' ? 'ae-btn-sm' : '', className ?? ''].filter(Boolean).join(' ');
  const primary = `${cls} btn-primary`;
  switch (apply.kind) {
    case 'offer':
      return (
        <button type="button" className={primary} aria-controls={controls} aria-expanded={controls ? !!expanded : undefined} onClick={onApply}>
          Apply for me
          {controls && <Chevron />}
        </button>
      );
    case 'signed_out':
      return (
        <button type="button" className={primary} onClick={() => signInSheet.open('account', SIGN_IN_NOTE, signInNext)}>
          Apply for me
        </button>
      );
    case 'checking':
      return (
        <span className="ae-entry">
          <button type="button" className={primary} aria-disabled="true" aria-describedby={why}>
            Apply for me
          </button>
          <span id={why} className="ae-why" role={live ? 'status' : undefined}>
            {apply.step ?? 'Checking the posting before applying…'}
          </span>
        </span>
      );
    case 'applied':
      return (
        <a className={cls} href={applyPath({ screen: 'one', id: apply.id })}>
          {apply.label} · open the application
        </a>
      );
    case 'off':
      return (
        <span className="ae-entry">
          <button type="button" className={primary} aria-disabled="true" aria-describedby={why}>
            Apply for me
          </button>
          <span id={why} className="ae-why">
            Off: {apply.reason}
          </span>
        </span>
      );
    case 'own_account':
      return <span className="ae-why">{apply.reason}</span>;
  }
}

/* ───────────────────────── the choice ───────────────────────── */

export interface ApplyChoice {
  resume: ResumeChoice;
  /** The application form's link, for a pasted match. */
  applyUrl?: string;
}

export interface ApplyForMeChoiceProps {
  /** The section's id (the button's aria-controls). */
  id: string;
  /** The employer, for "then we fill <company>'s form". */
  company?: string;
  /** The saved resume, when known (null: not read yet). */
  savedResume?: SavedResume | null;
  /** Ask for the application form's link (a pasted match). */
  needsLink: boolean;
  /** On a Stretch: how many open questions, and how to get to them. The note never blocks. */
  stretchQuestions?: number;
  onQuestions?: () => void;
  /** The person has not done the 30-second first check yet. */
  firstTime: boolean;
  initial?: ResumeChoice;
  /** The resume radios only (a Discover role page): no heading, no notes. */
  compact?: boolean;
  onContinue: (choice: ApplyChoice) => void;
  onCancel?: () => void;
  /** Focus lands on the heading when the choice opens. */
  focusOnMount?: boolean;
}

function savedLine(saved: SavedResume | null | undefined): string {
  if (saved && !saved.hasResume) return 'You haven’t saved a resume yet. You can add one in Your details before the form is filled.';
  return `${saved?.name ?? 'Your saved resume'}, as you uploaded it. Quicker; nothing is tailored.`;
}

function Option({ name, value, checked, onPick, title, children }: { name: string; value: ResumeChoice; checked: boolean; onPick: (v: ResumeChoice) => void; title: string; children: ReactNode }) {
  return (
    <label className="ae-opt">
      <input type="radio" name={name} value={value} checked={checked} onChange={() => onPick(value)} />
      <span className="ae-opt-text">
        <span className="ae-opt-title">{title}</span>
        <span className="ae-muted">{children}</span>
      </span>
    </label>
  );
}

/** "How should we apply?": two resumes, the link for a pasted match, the Stretch note, Continue and Cancel. */
export function ApplyForMeChoice(props: ApplyForMeChoiceProps) {
  const { id, company, savedResume, needsLink, stretchQuestions = 0, onQuestions, firstTime, initial = 'tailored', compact = false, onContinue, onCancel, focusOnMount = false } = props;
  const [resume, setResume] = useState<ResumeChoice>(initial);
  const [link, setLink] = useState('');
  const [linkError, setLinkError] = useState<string | null>(null);
  const uid = useId();
  const heading = useRef<HTMLHeadingElement | null>(null);

  useEffect(() => {
    if (!focusOnMount) return;
    const node = heading.current;
    if (!node) return;
    const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    node.scrollIntoView?.({ block: 'center', behavior: reduce ? 'auto' : 'smooth' });
    node.focus({ preventScroll: true });
  }, [focusOnMount]);

  const employer = company ? `${company}’s` : 'the employer’s';
  const submit = () => {
    if (!needsLink) return onContinue({ resume });
    const clean = cleanApplyUrl(link);
    if (!clean) {
      setLinkError('Paste the link to the employer’s job page. It starts with https://');
      return;
    }
    setLinkError(null);
    onContinue({ resume, applyUrl: clean });
  };

  const options = (
    <fieldset className="ae-fieldset">
      <legend className={compact ? 'ae-legend' : 'sr-only'}>{compact ? 'With which resume' : 'Which resume goes with the application'}</legend>
      <div className="ae-options">
        <Option name={`${uid}-resume`} value="tailored" checked={resume === 'tailored'} onPick={setResume} title="Fit my resume to this role, then apply">
          We build this role’s resume from your cited facts, the same one “Only build the resume” makes. You check it, then we fill {employer} form.
        </Option>
        <Option name={`${uid}-resume`} value="saved" checked={resume === 'saved'} onPick={setResume} title="Apply with my current resume">
          {savedLine(savedResume)}
        </Option>
      </div>
    </fieldset>
  );

  const linkField = needsLink && (
    <div className="ae-field">
      <label htmlFor={`${uid}-link`} className="ae-label">
        Link to the application form
      </label>
      <input
        id={`${uid}-link`}
        className="ae-input"
        type="url"
        inputMode="url"
        autoComplete="off"
        spellCheck={false}
        placeholder="https://"
        value={link}
        aria-invalid={linkError ? true : undefined}
        aria-describedby={`${uid}-link-hint${linkError ? ` ${uid}-link-error` : ''}`}
        onChange={(e) => setLink(e.target.value)}
      />
      <span id={`${uid}-link-hint`} className="ae-muted ae-small">
        This match came from pasted text, so we need the employer’s job page. We check it’s the same posting before filling anything.
      </span>
      {linkError && (
        <span id={`${uid}-link-error`} className="ae-error" role="alert">
          {linkError}
        </span>
      )}
    </div>
  );

  if (compact) {
    return (
      <div id={id} className="ae-choice ae-choice-compact">
        {options}
        {linkField}
        <div className="ae-actions">
          <button type="button" className="btn btn-primary ae-btn" onClick={submit}>
            Continue
          </button>
        </div>
      </div>
    );
  }

  const stretch = stretchQuestions > 0;
  return (
    <section id={id} className="ae-choice" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`} className="ae-h" tabIndex={-1} ref={heading}>
        How should we apply?
      </h3>
      {options}
      {linkField}
      {stretch && (
        <div className="ae-well">
          <p className="ae-strong">This one is a Stretch</p>
          <p className="ae-muted">
            Answering the <span className="numeral">{stretchQuestions}</span> {stretchQuestions === 1 ? 'question' : 'questions'} first lets your resume and answers cite
            them. You can still apply now.
          </p>
        </div>
      )}
      {firstTime && (
        <p className="ae-muted ae-small">
          The first time only, a 30-second check of what we read from your resume comes before the form, plus two questions. After that it goes straight to the form.
        </p>
      )}
      <div className="ae-actions">
        <button type="button" className="btn btn-primary ae-btn" onClick={submit}>
          Continue
        </button>
        {stretch && onQuestions && (
          <button type="button" className="btn ae-btn" onClick={onQuestions}>
            Answer the <span className="numeral">{stretchQuestions}</span> {stretchQuestions === 1 ? 'question' : 'questions'} first
          </button>
        )}
        {onCancel && (
          <button type="button" className="btn btn-quiet ae-btn" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </section>
  );
}
