/**
 * Small parts every role view shares: the fit chip, the pips, an Asks / You pair, the apply link, and the apply actions
 * of a role ("Apply for me", spec 2.1).
 *
 * The pips are the signature of a role: one 10 px square per requirement the search checked, filled when your evidence
 * meets it, half filled when it meets part of it, outlined in red when it is not on your resume. They are a picture of
 * counts and never a score; the count line beside them says the same in words, so the pips themselves are hidden from
 * assistive technology. Everything a posting or a profile wrote is a text node, and the apply link renders only when
 * it is a plain web address.
 *
 * Apply for me is worked out here in code (`applyView`) from what is known without a request: auto-apply is on, an
 * earlier application to the listing, the host's ATS (`apply/ats.ts`), who is signed in, and what Discover is doing for
 * the role right now. Liveness and the hard requirements are checked when the button is pressed (the read-only start
 * check, then the full posting match, shown on the card as steps); nothing is charged before that.
 */
import type { ReactNode } from 'react';
import type { ApplicationSummary, ApplyCheck, ApplyStatus } from '../../../shared/apply';
import type { AiFitWord, AiJob, AskCounts, AskMet } from '../../../shared/jobsearch';
import { ApplyForMeButton, appliedLabel, roleApplyFor } from '../../apply/ApplyForMe';
import type { ApplyChoice, RoleApply } from '../../apply/ApplyForMe';
import { hostOf, isFillable, needsOwnAccount } from '../../apply/ats';
import { reopens } from '../../apply/useApplications';
import { FIT_WORD, LEVEL_WORD, countsLine, pipsOf } from '../jobsearch/words';
import { applyLabel, safeHref } from '../util';
import { IconCheck } from './icons';

export function FitChip({ fit }: { fit: AiFitWord }) {
  return <span className={`chip dc-fit dc-fit-${fit}`}>{FIT_WORD[fit]}</span>;
}

/** The fit word and the level word, side by side. */
export function FitLine({ job }: { job: Pick<AiJob, 'fit' | 'level'> }) {
  return (
    <div className="dc-fitline">
      <FitChip fit={job.fit} />
      <span className="dc-lvl">{LEVEL_WORD[job.level]}</span>
    </div>
  );
}

export function Pips({ asks }: { asks: AskCounts }) {
  return (
    <span className="dc-pips" aria-hidden="true">
      {pipsOf(asks).map((kind, i) => (
        <span key={i} className={`dc-pip dc-pip-${kind}`} />
      ))}
    </span>
  );
}

/** The pips and the count line: "3 asks checked · 2 met · 1 not on your resume". */
export function AskLine({ asks }: { asks: AskCounts }) {
  return (
    <div className="dc-asks">
      <Pips asks={asks} />
      <span className="dc-count">{countsLine(asks)}</span>
    </div>
  );
}

/** "Asks “…”" and "You “…” Resume": the posting's own words, and the line of evidence that meets them. */
export function Pair({ met, className }: { met: AskMet; className?: string }) {
  return (
    <div className={['dc-pair', className].filter(Boolean).join(' ')}>
      <p>
        <span className="dc-lbl">Asks </span>“{met.ask}”
      </p>
      <p>
        <span className="dc-lbl">You </span>“{met.cite.text}”
        {met.cite.source && <span className="dc-cite"> {met.cite.source}</span>}
        {met.partly && <span className="dc-partly"> · partly</span>}
      </p>
    </div>
  );
}

/**
 * "Apply on boards.greenhouse.io ↗", opening the employer's own posting in a new tab. Quiet, it is the line under Apply
 * for me: "or apply yourself on boards.greenhouse.io ↗".
 */
export function ApplyLink({ job, className, quiet = false }: { job: Pick<AiJob, 'applyUrl' | 'applyHost' | 'title' | 'employer'>; className: string; quiet?: boolean }) {
  const href = safeHref(job.applyUrl);
  if (!href) return <span className="dc-muted">{quiet ? `or apply yourself on ${job.applyHost}` : `Apply on ${job.applyHost}`}</span>;
  return (
    <a
      className={className}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`${quiet ? 'Apply yourself' : 'Apply'} on ${job.applyHost} for ${job.title} at ${job.employer}, opens in a new tab`}
    >
      {quiet ? `or apply yourself on ${job.applyHost} ↗` : applyLabel(job)}
    </a>
  );
}

/* ───────────────────────── Apply for me on a role ───────────────────────── */

/** Why a Workday-like site is not filled for the person (spec 2.1: the employer's link stays the action). */
export const OWN_ACCOUNT_REASON = 'Apply for me isn’t offered here: this employer’s site asks you to create your own account, and we don’t do that for you.';

/** Why a site whose form we don't fill yet is not offered. */
export const notFillableReason = (host: string): string => `Apply for me isn’t offered here: we don’t fill forms on ${host || 'this site'} yet.`;

/** A posting the start check found closed: nothing is offered and nothing is charged. */
export const CLOSED_REASON = 'this posting is no longer open. Nothing was charged.';

/** A posting the start check found closed (spec 2.1: nothing is offered, not even the employer's link). */
export const CLOSED_NOTE = 'This posting is no longer open. Nothing was charged.';

/** The start check could not be read: nothing was started, so nothing was charged. */
export const CHECK_UNREAD = "We couldn't check this posting just now. Nothing was charged; try again in a moment.";

/** Another posting is being read: one match at a time. */
export const MATCH_BUSY = 'A posting is already being read. Try again in a moment.';

/** Stopped waiting on a card before the match started. */
export const CANCELLED = 'Stopped. Nothing was applied.';

/** Stopped waiting on a card while its match was running: the match itself still finishes. */
export const CANCELLED_MATCH = 'Stopped waiting. The match still finishes and stays in your matches; nothing was applied.';

/** The steps while a card's Apply for me is being checked. */
export interface ApplyProgress {
  kind: 'checking';
  /** The read-only start check, then the full posting match. */
  phase: 'check' | 'match';
  /** How many requirements the posting has, once the match has read it. */
  requirements?: number;
  /** The match's own sentence for what it is doing ("Checked 3 of 18 requirements…"). */
  step?: string;
}

/**
 * Where a card's Apply for me stands while Discover works on it, kept by `DiscoverRoot` per listing:
 *   checking   the start check, then the full posting match (`ApplyProgress`)
 *   refused    the check or the match said no (closed, an earlier application, a hard requirement): that state stays
 *   closed     the start check found the posting closed: only that sentence shows, no button and no employer link
 *   note       a sentence about the last attempt (cancelled, a match that did not finish); the button is offered again
 */
export type CardFlow = ApplyProgress | { kind: 'refused'; apply: RoleApply } | { kind: 'closed' } | { kind: 'note'; text: string };

/** What every Discover surface needs to offer Apply for me. */
export interface DiscoverApply {
  /** The auto-apply status; nothing apply-related renders until it says auto-apply is on. */
  status: ApplyStatus | null;
  signedIn: boolean;
  /** The person's applications by Discover listing (`useApplications().byListing`). */
  byListing: ReadonlyMap<string, ApplicationSummary>;
  /** The listings Discover is working on right now, or was refused for. */
  flows: ReadonlyMap<string, CardFlow>;
  /** Apply for me was pressed: with the resume already chosen (a role's page, Prepare), or not yet (a card). */
  onApply: (job: AiJob, runId: string | undefined, choice?: ApplyChoice) => void;
  /** Stop waiting on the card. */
  onCancel: (job: AiJob) => void;
  /** Where the sign-in sheet returns to; the current page when absent. */
  signInNext?: string;
}

/** Apply for me on one role: the state, the steps while it is being checked, and a sentence about the last attempt. */
export interface RoleApplyView {
  apply: RoleApply;
  progress?: ApplyProgress;
  note?: string;
  /** The posting is closed: nothing is offered (`ApplyFor` renders nothing; the note says why). */
  closed?: boolean;
}

/**
 * Apply for me on one Discover role, or undefined while auto-apply is off (the employer's link stays the action, as
 * before). In this order: an earlier application to this listing (unless a new start would reopen it); a site that
 * needs the person's own account, or one whose form we don't fill (by its host alone); nobody signed in; what Discover
 * is doing for the role right now; else offered.
 */
export function applyView(job: Pick<AiJob, 'id' | 'applyUrl' | 'applyHost'>, input: Pick<DiscoverApply, 'status' | 'signedIn' | 'byListing' | 'flows'>): RoleApplyView | undefined {
  const { status, signedIn, byListing, flows } = input;
  if (!status?.enabled) return undefined;
  const application = byListing.get(job.id);
  if (application && !reopens(application)) return { apply: { kind: 'applied', id: application.id, label: appliedLabel(application) } };
  const where = job.applyUrl || job.applyHost;
  if (needsOwnAccount(where)) return { apply: { kind: 'own_account', reason: OWN_ACCOUNT_REASON } };
  if (!isFillable(where)) return { apply: { kind: 'own_account', reason: notFillableReason(job.applyHost || hostOf(where)) } };
  if (!signedIn || !status.signedIn) return { apply: { kind: 'signed_out' } };
  const flow = flows.get(job.id);
  if (flow?.kind === 'checking') return { apply: { kind: 'checking', ...(flow.step ? { step: flow.step } : {}) }, progress: flow };
  if (flow?.kind === 'refused') return { apply: flow.apply };
  if (flow?.kind === 'closed') return { apply: { kind: 'off', reason: CLOSED_REASON }, note: CLOSED_NOTE, closed: true };
  return { apply: { kind: 'offer' }, ...(flow?.kind === 'note' ? { note: flow.text } : {}) };
}

/**
 * What a start check that said no means on a card: the match's own reading (`roleApplyFor`), except that a closed
 * posting says so here, because the person just pressed the button.
 */
export function refusedApply(check: ApplyCheck, status: ApplyStatus): RoleApply {
  const known = roleApplyFor({ status: { ...status, enabled: true, signedIn: true }, signedIn: true, check: { state: 'ready', check }, knownLink: true });
  return known?.apply ?? { kind: 'off', reason: CLOSED_REASON };
}

export type ApplyStepMood = 'done' | 'now' | 'wait';

export interface ApplyStep {
  key: string;
  label: string;
  text: string;
  mood: ApplyStepMood;
}

/**
 * The card's steps while Apply for me is being checked (canvas 9): the start check, reading the posting, the evidence
 * against each requirement (the match's own sentence), then the choice. Written in code from what has happened.
 */
export function applySteps(progress: ApplyProgress): ApplyStep[] {
  const checked = progress.phase === 'match';
  const read = checked && progress.requirements !== undefined;
  const n = progress.requirements ?? 0;
  return [
    {
      key: 'check',
      label: 'The posting',
      text: checked ? 'Still open, on a form we fill.' : 'Checking it is still open, on a form we fill.',
      mood: checked ? 'done' : 'now',
    },
    {
      key: 'read',
      label: 'Read the posting',
      text: read ? `Read the posting: ${n} ${n === 1 ? 'requirement' : 'requirements'}.` : 'Its requirements and hard limits, on their own.',
      mood: read ? 'done' : checked ? 'now' : 'wait',
    },
    {
      key: 'evidence',
      label: 'Your evidence',
      text: read && progress.step ? progress.step : 'Your evidence against each requirement.',
      mood: read ? 'now' : 'wait',
    },
    { key: 'then', label: 'Then', text: 'How to apply: you choose the resume, and see every value before anything is sent.', mood: 'wait' },
  ];
}

const MOOD_WORD: Record<ApplyStepMood, string> = { done: ', done', now: ', in progress', wait: ', next' };

/** The in-card steps (a polite status region) and Cancel. Static marks, nothing that loops. */
export function ApplyChecking({ progress, onCancel, cancelClass }: { progress: ApplyProgress; onCancel: () => void; cancelClass: string }) {
  const rows = applySteps(progress);
  return (
    <div className="dc-apply-checking" role="group" aria-label="Apply for me">
      <div role="status" aria-live="polite">
        <p className="dc-strong dc-small">Checking the match before applying</p>
        <ol className="dc-trail">
          {rows.map((row, i) => (
            <li key={row.key} className={`dc-trail-row is-${row.mood}`}>
              <div className="dc-trail-mark" aria-hidden="true">
                <span className="dc-trail-dot">{row.mood === 'done' ? <IconCheck size={12} /> : row.mood === 'now' ? <span className="dc-trail-now" /> : null}</span>
                {i < rows.length - 1 && <span className="dc-trail-line" />}
              </div>
              <div className="dc-trail-body">
                <span className="kicker dc-trail-stage">
                  {row.label}
                  <span className="sr-only">{MOOD_WORD[row.mood]}</span>
                </span>
                <p className="dc-trail-text">{row.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
      <button type="button" className={cancelClass} onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

/** What `ApplyFor` reads of a role's controls (`RoleCtl` carries the same fields). */
export interface ApplyCtl {
  /** Apply for me on this role; absent while auto-apply is off. */
  apply?: RoleApply;
  /** The steps while it is being checked. */
  applyProgress?: ApplyProgress;
  onApplyForMe: (choice?: ApplyChoice) => void;
  onCancelApply: () => void;
  applySignInNext?: string;
  /** The posting is closed: no Apply for me and no employer link. */
  applyClosed?: boolean;
}

/** A role's controls when there is no way to apply for it from here (auto-apply off). */
export const NO_APPLY: ApplyCtl = { onApplyForMe: () => {}, onCancelApply: () => {} };

/** The controls `ApplyFor` reads, for one role of a `DiscoverApply` (Compare and Prepare have no `RoleCtl`). */
export function applyCtlOf(job: AiJob, runId: string | undefined, apply: DiscoverApply | undefined): ApplyCtl & { note?: string } {
  const view = apply ? applyView(job, apply) : undefined;
  if (!apply || !view) return NO_APPLY;
  return {
    apply: view.apply,
    ...(view.progress ? { applyProgress: view.progress } : {}),
    ...(view.note ? { note: view.note } : {}),
    ...(view.closed ? { applyClosed: true } : {}),
    onApplyForMe: (choice) => apply.onApply(job, runId, choice),
    onCancelApply: () => apply.onCancel(job),
    ...(apply.signInNext ? { applySignInNext: apply.signInNext } : {}),
  };
}

export interface ApplyForProps {
  job: Pick<AiJob, 'applyUrl' | 'applyHost' | 'title' | 'employer'>;
  ctl: ApplyCtl;
  size?: 'md' | 'sm';
  /** The employer's link while it is the action (auto-apply off, or the employer's own account needed). */
  primaryClass: string;
  /** Extra classes on Apply for me (the surface's sizing). */
  buttonClass?: string;
  /** The quiet "or apply yourself on <host> ↗" under Apply for me. */
  quietClass: string;
  /** What pressing Apply for me does here (a role's page opens its resume choice); else `ctl.onApplyForMe()`. */
  onApply?: () => void;
  controls?: string;
  expanded?: boolean;
  /** Shown between Apply for me and the employer's link (the role page's resume choice). */
  children?: ReactNode;
}

/**
 * The apply actions of a role, the same on a card, the role's page, the deck, Compare and Prepare (spec 2.1):
 *   auto-apply off        the employer's link, as before
 *   own account needed    the employer's link stays the action, with the reason under it
 *   checking              the steps and Cancel, then the quiet employer's link
 *   applied               the earlier application ("Sent 4 Nov · open the application"), and no employer's link
 *   anything else         Apply for me (offered, signed out, or off with its reason), then the quiet "or apply
 *                         yourself on <host> ↗"
 */
export function ApplyFor({ job, ctl, size = 'md', primaryClass, buttonClass, quietClass, onApply, controls, expanded, children }: ApplyForProps) {
  const apply = ctl.apply;
  if (ctl.applyClosed) return null; // a closed posting: nothing is offered (its sentence is the role's note)
  if (!apply) return <ApplyLink job={job} className={primaryClass} />;
  if (apply.kind === 'own_account') {
    return (
      <>
        <ApplyLink job={job} className={primaryClass} />
        <ApplyForMeButton apply={apply} onApply={() => {}} />
      </>
    );
  }
  return (
    <>
      {apply.kind === 'checking' && ctl.applyProgress ? (
        <ApplyChecking progress={ctl.applyProgress} onCancel={ctl.onCancelApply} cancelClass={`btn btn-quiet ${size === 'sm' ? 'btn-sm dc-btn-sm' : 'dc-btn'}`} />
      ) : (
        <ApplyForMeButton
          apply={apply}
          size={size}
          className={buttonClass}
          signInNext={ctl.applySignInNext}
          controls={controls}
          expanded={expanded}
          onApply={onApply ?? (() => ctl.onApplyForMe())}
        />
      )}
      {children}
      {/* an application already stands for this posting: it is the way on, not the employer's form */}
      {apply.kind !== 'applied' && <ApplyLink job={job} className={quietClass} quiet />}
    </>
  );
}
