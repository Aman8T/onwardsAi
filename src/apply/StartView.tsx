/**
 * `/apply/new?match=<id>&resume=tailored|saved`: Apply for me (canvas 1, spec 1 rule 1, 3.1 step 1).
 *
 * The four checks from `applyApi.check` (read-only, charges nothing), what goes to the employer (your details, the
 * resume choice), the price and the free applications left (from the status only, never written here), what happens
 * next, then "Fill the form" or "Open on <host>'s site instead". When a check fails, one panel per `ApplyBlockReason`
 * says why and offers one thing to do. Before the first application the first check (canvas 11) and the consent step
 * (spec 5) come first.
 *
 * `StartView` is presentational; `StartScreen` runs the check, reads Your details and the match, and starts.
 */
import { useEffect, useId, useState } from 'react';
import type { ReactNode } from 'react';
import type { ApplyBlockReason, ApplyCheck, ApplyStatus, ResumeChoice, StartApplicationRequest, YourDetails } from '../../shared/apply';
import type { MatchBand, WorkMode } from '../../shared/match';
import { signInSheet, useMe } from '../auth/useMe';
import { shortDate } from '../billing/format';
import { plural } from '../lib/ui';
import { getMatch } from '../match/api';
import { BAND_LABEL, WORK_MODE_LABEL } from '../match/model';
import { ATS_LABEL, ApplyError, applyApi, errorText } from './api';
import { Consent } from './Consent';
import { FirstCheck } from './FirstCheck';
import { DETAILS_ROUTE, LIST_ROUTE, applyPath, linkTo } from './route';
import { applyLinkFor, clearApplyIntent } from './useApplications';
import type { ApplyRoute, Navigate } from './route';

/* ───────────────────────── what code computes ───────────────────────── */

/** The posting match the application starts from, as this screen shows it. */
export interface StartMatch {
  id: string;
  title: string;
  company?: string;
  location?: string;
  workMode?: WorkMode;
  band?: MatchBand;
}

export type CheckRowId = 'live' | 'fillable' | 'requirements' | 'not_applied';
export type CheckState = 'ok' | 'fail' | 'unchecked';

export interface CheckRow {
  id: CheckRowId;
  title: string;
  line: string;
  state: CheckState;
}

/** Which check row a block reason belongs to; `signed_out` and `no_credit` are not rows of their own. */
const ROW_OF: Partial<Record<ApplyBlockReason, CheckRowId>> = {
  manual_only: 'fillable',
  own_account_ats: 'fillable',
  not_fillable: 'fillable',
  closed: 'live',
  unknown_liveness: 'live',
  blocked_by_requirement: 'requirements',
  already_applied: 'not_applied',
};

/** The order the server runs the checks in (apply/checks.py): a failure means the later rows were not reached. */
const EVAL_ORDER: readonly CheckRowId[] = ['fillable', 'live', 'requirements', 'not_applied'];
/** The order the screen shows them in (canvas 1). */
const SHOW_ORDER: readonly CheckRowId[] = ['live', 'fillable', 'requirements', 'not_applied'];

const OK_TITLE: Record<CheckRowId, string> = {
  live: 'The posting is open',
  fillable: 'We can fill this form',
  requirements: 'Hard requirements met',
  not_applied: 'Not applied before',
};

const FAIL_TITLE: Record<CheckRowId, string> = {
  live: 'The posting may not be open',
  fillable: "We can't fill this form",
  requirements: "A hard requirement isn't met",
  not_applied: 'Applied before',
};

/** The state of each of the four rows, computed from the one reason the check returned. */
export function checkRows(check: ApplyCheck): CheckRow[] {
  const failing = check.reason ? ROW_OF[check.reason] : undefined;
  const failAt = failing ? EVAL_ORDER.indexOf(failing) : -1;
  const ats = check.ats ? ATS_LABEL[check.ats] : '';
  const stateOf = (id: CheckRowId): CheckState => {
    if (check.offered) return 'ok';
    if (check.reason === 'signed_out') return 'unchecked';
    if (check.reason === 'no_credit') return 'ok';
    const at = EVAL_ORDER.indexOf(id);
    if (failAt < 0) return 'unchecked';
    return at < failAt ? 'ok' : at === failAt ? 'fail' : 'unchecked';
  };
  const okLine: Record<CheckRowId, string> = {
    live: "Read just now on the employer's own page.",
    fillable: `${ats ? `${ats}, ` : ''}${check.host ? `on ${check.host}. ` : ''}We fill it in a browser that cannot send anything.`,
    requirements: "The match's hard requirements did not block.",
    not_applied: 'No earlier application of yours to this posting.',
  };
  return SHOW_ORDER.map((id) => {
    const state = stateOf(id);
    const line = state === 'ok' ? okLine[id] : state === 'fail' ? FAILURE[check.reason!].row : 'Not checked: an earlier check stopped here.';
    return { id, title: state === 'fail' ? FAIL_TITLE[id] : OK_TITLE[id], line, state };
  });
}

type FailureAction = 'signin' | 'pricing' | 'discover' | 'employer' | 'match' | 'application';

export const FAILURE: Record<ApplyBlockReason, { chip: string; row: string; text: string; action: FailureAction; label: string }> = {
  signed_out: {
    row: 'Sign in to run the checks.',
    chip: 'Sign in first',
    text: 'Auto-apply is for signed-in accounts only, so every application and its proof stay with you.',
    action: 'signin',
    label: 'Sign in',
  },
  no_credit: {
    row: 'Every check passed; there is no credit to hold.',
    chip: 'No credits left',
    text: 'Starting holds one credit, charged only when the form is sent. Nothing was charged.',
    action: 'pricing',
    label: 'See plans',
  },
  closed: {
    row: "The employer's page says the role is closed.",
    chip: 'Posting closed',
    text: "The employer's page says this role is no longer open. Nothing was charged.",
    action: 'discover',
    label: 'Find similar roles',
  },
  not_fillable: {
    row: 'Not a form we fill yet.',
    chip: "We can't fill this form",
    text: "This employer's form is not one we fill yet: we fill Greenhouse, Lever and Ashby. Nothing was charged.",
    action: 'employer',
    label: "Open on the employer's site",
  },
  own_account_ats: {
    row: "This form needs your own account on the employer's site.",
    chip: 'Needs your sign-in',
    text: 'This employer asks you to create an account on its site. That step is yours; we never sign in for you.',
    action: 'employer',
    label: "Open on the employer's site",
  },
  blocked_by_requirement: {
    row: "The match's hard requirements blocked it.",
    chip: "A hard requirement isn't met",
    text: "The match found a hard requirement your evidence doesn't meet, so we won't apply for you here. You can still apply yourself.",
    action: 'match',
    label: 'See the gap and how to close it',
  },
  already_applied: {
    row: 'You have applied to this posting before.',
    chip: 'Already applied',
    text: 'You have an application to this posting already. It opens instead of a second one.',
    action: 'application',
    label: 'Open that application',
  },
  manual_only: {
    row: 'This site forbids automated applications.',
    chip: 'Apply on the site yourself',
    text: "This site doesn't allow automated applications, so we never fill it. Nothing was charged.",
    action: 'employer',
    label: "Open on the employer's site",
  },
  unknown_liveness: {
    row: "The employer's page didn't answer.",
    chip: "Couldn't check the posting",
    text: "The employer's page didn't answer, so we can't tell whether the role is still open. Nothing was charged.",
    action: 'employer',
    label: "Open on the employer's site",
  },
};

/** "Name, email, mobile and city, and work authorisation for India." From what Your details holds. */
export function detailsLine(details: Pick<YourDetails, 'answers' | 'workAuthorization'>): string {
  const you = details.answers.filter((a) => a.group === 'you').map((a) => a.question.toLowerCase());
  const parts = you.length > 0 ? [you.slice(0, 5).join(', ')] : [];
  if (details.workAuthorization.some((w) => w.country.toUpperCase() === 'IN')) parts.push('work authorisation for India');
  if (parts.length === 0) return 'Nothing saved yet: the form asks what it needs, and you answer once.';
  const text = parts.join(', and ');
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}

/* ───────────────────────── the view ───────────────────────── */

export interface StartViewProps {
  status: ApplyStatus;
  check: ApplyCheck;
  matchId: string;
  match?: StartMatch;
  details: YourDetails | null;
  resume: ResumeChoice;
  onResume: (r: ResumeChoice) => void;
  onStart: () => Promise<void>;
  navigate: Navigate;
  /** The employer's own page for this posting, when known. */
  employerUrl?: string;
  /** The account has no limits and is never charged. */
  unlimited?: boolean;
  error?: string | null;
  onSignIn?: () => void;
}

export function StartView(props: StartViewProps) {
  const { check, match, details, resume, navigate } = props;
  const [starting, setStarting] = useState(false);
  const titleId = useId();
  const company = match?.company ?? check.host ?? 'the employer';
  const host = check.host ?? match?.company ?? 'the employer';
  const rows = checkRows(check);
  const noSaved = resume === 'saved' && details !== null && !details.hasResume;

  const start = async () => {
    setStarting(true);
    try {
      await props.onStart();
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="ap-stack">
      <div className="ap-head">
        <p className="kicker">
          <a href={`/match/${encodeURIComponent(props.matchId)}`}>Match</a> · Apply for me
        </p>
        <h1 id={titleId} className="serif ap-title">
          {match?.title ?? 'Apply for me'}
        </h1>
        <div className="ap-actions">
          {match && (
            <span className="ap-muted">
              {[match.company, match.location, match.workMode ? WORK_MODE_LABEL[match.workMode] : ''].filter(Boolean).join(' · ')}
            </span>
          )}
          {match?.band && <span className="chip ap-chip">{BAND_LABEL[match.band]}</span>}
          {check.host && <span className="chip ap-chip">Applies on {check.host}</span>}
        </div>
      </div>

      <div className="ap-split">
        <div className="ap-main ap-stack">
          <section className="panel ap-panel" aria-labelledby={`${titleId}-checks`}>
            <h2 id={`${titleId}-checks`} className="kicker">
              Checked before anything starts
            </h2>
            <ul className="ap-checks">
              {rows.map((r) => (
                <li key={r.id} className={`ap-checkrow ap-check-${r.state}`}>
                  <CheckMark state={r.state} />
                  <div>
                    <p className="ap-strong">{r.title}</p>
                    <p className="ap-muted ap-small">
                      {r.line}
                      {r.id === 'requirements' && r.state === 'ok' && (
                        <>
                          {' '}
                          <a href={`/match/${encodeURIComponent(props.matchId)}`}>See the match</a>
                        </>
                      )}
                    </p>
                    {r.state === 'fail' && r.id === 'requirements' && check.gateReasons && check.gateReasons.length > 0 && (
                      <ul className="ap-plain-list ap-small">
                        {check.gateReasons.map((g) => (
                          <li key={g}>{g}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </section>

          {check.offered ? (
            <section className="panel ap-panel ap-stack-s" aria-labelledby={`${titleId}-sent`}>
              <h2 id={`${titleId}-sent`} className="kicker">
                What goes to {company}
              </h2>
              <div className="ap-headrow">
                <div>
                  <p className="ap-strong">Your details</p>
                  <p className="ap-muted ap-small">
                    {details ? detailsLine(details) : 'Reading your details'}
                    {details?.updatedAt ? ` Saved ${shortDate(details.updatedAt)}.` : ''}
                  </p>
                </div>
                <a className="btn" {...linkTo(DETAILS_ROUTE, navigate)}>
                  Check your details
                </a>
              </div>
              <fieldset className="ap-fieldset">
                <legend className="ap-strong">Resume</legend>
                <label className="ap-opt">
                  <input type="radio" name={`${titleId}-resume`} checked={resume === 'tailored'} onChange={() => props.onResume('tailored')} />
                  <span>
                    <span className="ap-block">This match's resume</span>
                    <span className="ap-muted ap-small">One page, built from your cited facts for this role. Attached only when you approve.</span>
                  </span>
                </label>
                <label className="ap-opt">
                  <input type="radio" name={`${titleId}-resume`} checked={resume === 'saved'} onChange={() => props.onResume('saved')} />
                  <span>
                    <span className="ap-block">Your saved resume</span>
                    <span className="ap-muted ap-small">
                      {details?.hasResume
                        ? `${details.resumeName ?? 'Your resume'}, as you uploaded it. Attached only when you approve.`
                        : 'None saved yet: upload one in Your details.'}
                    </span>
                  </span>
                </label>
              </fieldset>
            </section>
          ) : (
            <FailurePanel {...props} />
          )}
        </div>

        {check.offered && (
          <aside className="ap-aside ap-stack">
            <PricePanel status={props.status} unlimited={props.unlimited} />
            <section className="panel ap-panel ap-stack-s" aria-labelledby={`${titleId}-next`}>
              <h2 id={`${titleId}-next`} className="kicker">
                What happens next
              </h2>
              <ol className="ap-steps">
                <li>We fill the form in a browser that cannot send anything.</li>
                <li>You review every value and where it came from.</li>
                <li>You approve, and exactly those values are sent.</li>
                <li>You get the employer's confirmation here.</li>
              </ol>
              {props.error && (
                <p className="ap-error ap-small" role="alert">
                  {props.error}
                </p>
              )}
              <button
                type="button"
                className="btn btn-primary ap-wide-btn"
                aria-disabled={noSaved || starting || undefined}
                aria-describedby={noSaved ? `${titleId}-why` : undefined}
                onClick={() => {
                  if (!noSaved && !starting) void start();
                }}
              >
                {starting ? 'Starting' : 'Fill the form'}
              </button>
              {noSaved && (
                <p id={`${titleId}-why`} className="ap-muted ap-small">
                  Choose this match's resume, or save a resume in Your details first.
                </p>
              )}
              {props.employerUrl && (
                <a className="btn btn-quiet ap-wide-btn" href={props.employerUrl} target="_blank" rel="noreferrer noopener">
                  Open on {host}'s site instead
                </a>
              )}
            </section>
          </aside>
        )}
      </div>
    </div>
  );
}

function CheckMark({ state }: { state: CheckState }) {
  const label = state === 'ok' ? 'Passed' : state === 'fail' ? 'Did not pass' : 'Not checked';
  return (
    <span className={`ap-mark ap-mark-${state}`} role="img" aria-label={label}>
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true" focusable="false">
        {state === 'ok' ? <path d="M2.5 6.2l2.3 2.3 4.7-5" /> : state === 'fail' ? <path d="M3 3l6 6M9 3l-6 6" /> : <path d="M3 6h6" />}
      </svg>
    </span>
  );
}

function FailurePanel(props: StartViewProps) {
  const { check, navigate } = props;
  const reason = check.reason ?? 'unknown_liveness';
  const f = FAILURE[reason];
  const titleId = useId();
  let action: ReactNode = null;
  switch (f.action) {
    case 'signin':
      action = (
        <button type="button" className="btn" onClick={props.onSignIn}>
          {f.label}
        </button>
      );
      break;
    case 'pricing':
      action = (
        <a className="btn" href="/pricing">
          {f.label}
        </a>
      );
      break;
    case 'discover':
      action = (
        <a className="btn" href="/discover">
          {f.label}
        </a>
      );
      break;
    case 'match':
      action = (
        <a className="btn" href={`/match/${encodeURIComponent(props.matchId)}`}>
          {f.label}
        </a>
      );
      break;
    case 'application':
      action = check.applicationId ? (
        <a className="btn" {...linkTo({ screen: 'one', id: check.applicationId }, navigate)}>
          {f.label}
        </a>
      ) : (
        <a className="btn" {...linkTo(LIST_ROUTE, navigate)}>
          Open your applications
        </a>
      );
      break;
    case 'employer':
      action = props.employerUrl ? (
        <a className="btn" href={props.employerUrl} target="_blank" rel="noreferrer noopener">
          {f.label}
        </a>
      ) : (
        <a className="btn" href={`/match/${encodeURIComponent(props.matchId)}`}>
          Back to the match
        </a>
      );
      break;
  }
  const bad = reason === 'closed' || reason === 'blocked_by_requirement';
  return (
    <section className="panel ap-panel ap-stack-s" aria-labelledby={titleId}>
      <span id={titleId} className={`chip ap-chip ${bad ? 'ap-tone-plain' : 'ap-tone-you'} ap-self-start`}>
        {f.chip}
      </span>
      <p>{check.note ?? f.text}</p>
      {reason === 'blocked_by_requirement' && check.gateReasons && check.gateReasons.length > 0 && (
        <p className="ap-muted ap-small">{plural(check.gateReasons.length, 'requirement')} not met: {check.gateReasons.join('; ')}</p>
      )}
      <div className="ap-actions">{action}</div>
    </section>
  );
}

function PricePanel({ status, unlimited }: { status: ApplyStatus; unlimited?: boolean }) {
  return (
    <section className="panel ap-panel ap-stack-s" aria-label="Price">
      <h2 className="kicker">Price</h2>
      {unlimited ? (
        <p>This account has no limits: nothing is charged.</p>
      ) : status.price !== undefined ? (
        <p>
          <span className="numeral ap-price">{status.price}</span> {status.price === 1 ? 'credit' : 'credits'}{' '}
          <span className="ap-muted">if it's sent</span>
        </p>
      ) : (
        <p>No price is set yet, so nothing is charged.</p>
      )}
      <p className="ap-muted ap-small">
        Nothing if it isn't sent. A form we can't finish is refunded; a send we can't confirm waits for your word before anything
        is charged.
      </p>
      {!unlimited && status.freeLeft !== undefined && (
        <p className="ap-small">
          Free applications left: <span className="numeral">{status.freeLeft}</span>
        </p>
      )}
    </section>
  );
}

/* ───────────────────────── the container ───────────────────────── */

export interface StartScreenProps {
  route: Extract<ApplyRoute, { screen: 'new' }>;
  status: ApplyStatus;
  onStatus: (s: ApplyStatus) => void;
  navigate: Navigate;
}

/** What the screen needs before it can show anything, with a first check or consent first when they are due. */
/** The start request: the route's ids and, for a pasted match, the form address the person typed. */
export function startBody(input: { matchId: string; resume: ResumeChoice; listingId?: string; runId?: string; applyUrl?: string }): StartApplicationRequest {
  const { matchId, resume, listingId, runId, applyUrl } = input;
  return { matchId, resume, ...(listingId ? { listingId } : {}), ...(runId ? { runId } : {}), ...(applyUrl ? { applyUrl } : {}) };
}

export function StartScreen({ route, status, onStatus, navigate }: StartScreenProps) {
  const { unlimited } = useMe();
  const [check, setCheck] = useState<ApplyCheck | null>(null);
  const [details, setDetails] = useState<YourDetails | null>(null);
  const [detailsFailed, setDetailsFailed] = useState(false);
  const [detailsTry, setDetailsTry] = useState(0);
  const [match, setMatch] = useState<StartMatch | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const { matchId, listingId, runId } = route;
  // the application form's address the person typed on a pasted match (the match hands it over in this tab)
  const applyUrl = applyLinkFor(matchId);

  useEffect(() => {
    let live = true;
    applyApi
      .details()
      .then((d) => {
        if (!live) return;
        setDetails(d);
        setDetailsFailed(false);
      })
      .catch(() => live && setDetailsFailed(true));
    return () => {
      live = false;
    };
  }, [detailsTry]);

  useEffect(() => {
    if (!matchId) return;
    let live = true;
    const body = { matchId, ...(listingId ? { listingId } : {}), ...(runId ? { runId } : {}), ...(applyUrl ? { applyUrl } : {}) };
    applyApi
      .check(body)
      .then((c) => live && setCheck(c))
      .catch((err) => live && setError(errorText(err, 'Could not check this posting. Try again.')));
    getMatch(matchId)
      .then((m) => {
        if (!live) return;
        setMatch({
          id: m.id,
          title: m.posting.title,
          company: m.posting.company,
          location: m.posting.location,
          workMode: m.posting.workMode,
          band: m.result.band,
        });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [matchId, listingId, runId, applyUrl]);

  if (!matchId) {
    return (
      <section className="panel ap-panel ap-stack-s ap-narrow">
        <h1 className="serif ap-title">Apply for me starts from a match.</h1>
        <p className="ap-muted">
          Every application runs the posting match first, so we only apply where the hard requirements are met. Open a match, or a
          role in Discover, and choose Apply for me there.
        </p>
        <div className="ap-actions">
          <a className="btn" href="/match">
            Match a posting
          </a>
          <a className="btn btn-quiet" href="/discover">
            Find roles in Discover
          </a>
        </div>
      </section>
    );
  }

  if (!status.firstCheckDone) {
    if (!details) {
      return detailsFailed ? (
        <div className="ap-error" role="alert">
          <p>We could not read your details.</p>
          <button type="button" className="btn" onClick={() => setDetailsTry((n) => n + 1)}>
            Try again
          </button>
        </div>
      ) : (
        <p className="ap-muted" role="status">
          Reading your details
        </p>
      );
    }
    return (
      <FirstCheck
        details={details}
        onSubmit={async (body) => {
          setDetails(await applyApi.firstCheck(body));
          onStatus({ ...status, firstCheckDone: true });
        }}
      />
    );
  }

  if (!status.consentAt) {
    return <Consent onAgree={async () => onStatus(await applyApi.consent())} onCancel={() => navigate(LIST_ROUTE)} />;
  }

  if (!check) {
    return error ? (
      <p className="ap-error" role="alert">
        {error}
      </p>
    ) : (
      <p className="ap-muted" role="status">
        Checking the posting. Nothing is charged for this.
      </p>
    );
  }

  const start = async () => {
    setStartError(null);
    try {
      const body = startBody({ matchId, resume: route.resume, listingId, runId, applyUrl });
      const made = await applyApi.start(body);
      clearApplyIntent();
      navigate({ screen: 'one', id: made.id });
    } catch (err) {
      if (err instanceof ApplyError && err.status === 409 && err.existingApplicationId) {
        navigate({ screen: 'one', id: err.existingApplicationId });
      } else if (err instanceof ApplyError && err.status === 402) {
        setCheck({ ...check, offered: false, reason: 'no_credit' });
      } else {
        setStartError(errorText(err, 'Could not start the application. Nothing was charged.'));
      }
    }
  };

  return (
    <StartView
      status={status}
      check={check}
      matchId={matchId}
      match={match}
      details={details}
      resume={route.resume}
      onResume={(resume) => {
        const next = { ...route, resume };
        navigate(next, { replace: true });
      }}
      onStart={start}
      navigate={navigate}
      unlimited={unlimited}
      error={startError}
      onSignIn={() => signInSheet.open('account', undefined, applyPath(route))}
    />
  );
}
