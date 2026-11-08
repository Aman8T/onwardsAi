/**
 * `/apply`: Your applications (canvas 6). Status tabs with counts computed here from the list; a table of the tab's
 * applications (role, resume, where it stands with its reason, since, one action) that becomes stacked cards under
 * 760 px; the pipeline after sending, with "No reply in 3 weeks" computed from the sent time plus 21 days and a one-tap
 * "Heard back?"; the replies panel (paste an email: inline on wide screens, in a sheet on a phone); and the engine metrics
 * for admins only (the route answers 404 to anyone else, and then nothing is shown).
 *
 * The "Ready for you today" stack and the forwarding address are v2 (spec 3.1 Settled, 3.2) and are left out.
 *
 * `ApplicationsView` is presentational (the gallery renders it from fixtures); `ApplicationsScreen` fetches and polls
 * every 3 s while something is still moving on the worker's side.
 */
import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type {
  ApplicationStage,
  ApplicationSummary,
  ApplyMetrics,
  EmailClassification,
  EmailClassifyRequest,
} from '../../shared/apply';
import { Sheet } from '../auth/Sheet';
import { EntitlementChip } from '../billing/EntitlementChip';
import { shortDate } from '../billing/format';
import { NARROW_QUERY, plural, timeAgo, useMediaQuery, useNow } from '../lib/ui';
import { ATS_LABEL, STAGE_LABEL, STOP_REASON_TEXT, applyApi, busy, errorText, isSent, stageTone } from './api';
import { EmailPanel } from './EmailPanel';
import { DETAILS_ROUTE, linkTo } from './route';
import type { Navigate } from './route';

/* ───────────────────────── what code computes ───────────────────────── */

export type ListTab = 'waiting' | 'filling' | 'sent' | 'check' | 'didnt_go' | 'skipped';

export const LIST_TABS: readonly (readonly [ListTab, string])[] = [
  ['waiting', 'Waiting for you'],
  ['filling', 'Filling'],
  ['sent', 'Sent'],
  ['check', 'Check this one'],
  ['didnt_go', "Didn't go"],
  ['skipped', 'Skipped'],
];

const TAB_OF: Record<ApplicationStage, ListTab> = {
  waiting_for_you: 'waiting',
  needs_signin: 'waiting',
  needs_user: 'waiting',
  ready_to_submit: 'waiting',
  queued: 'filling',
  filling: 'filling',
  approved: 'filling',
  submitting: 'filling',
  sent: 'sent',
  viewed: 'sent',
  interviewing: 'sent',
  offer: 'sent',
  rejected: 'sent',
  applied: 'sent',
  check_this_one: 'check',
  failed: 'didnt_go',
  closed: 'didnt_go',
  cancelled: 'skipped',
};

export const tabOf = (a: ApplicationSummary): ListTab => TAB_OF[a.stage];

export function tabCounts(apps: readonly ApplicationSummary[]): Record<ListTab, number> {
  const counts: Record<ListTab, number> = { waiting: 0, filling: 0, sent: 0, check: 0, didnt_go: 0, skipped: 0 };
  for (const a of apps) counts[tabOf(a)] += 1;
  return counts;
}

/** The tab to open on: what waits for the person first, else the first tab with anything in it. */
export function firstTab(counts: Record<ListTab, number>): ListTab {
  return LIST_TABS.find(([id]) => counts[id] > 0)?.[0] ?? 'waiting';
}

/** No reply in 3 weeks: a sent application, unmoved for 21 days. */
export const NO_REPLY_DAYS = 21;
const DAY_MS = 86_400_000;

/**
 * When it was sent. The summary has no send time of its own yet, so for a row still at `sent` its last change is the
 * send (the stage moves only forward, and nothing else touches a sent row).
 */
export const sentAtOf = (a: ApplicationSummary): string => a.updatedAt;

export function noReply(a: ApplicationSummary, now: number): boolean {
  if (a.stage !== 'sent' && a.stage !== 'applied') return false;
  const at = Date.parse(sentAtOf(a));
  return Number.isFinite(at) && now - at >= NO_REPLY_DAYS * DAY_MS;
}

export type PipeColumn = 'sent' | 'viewed' | 'interviewing' | 'offer' | 'rejected' | 'no_reply';

export const PIPE_COLUMNS: readonly (readonly [PipeColumn, string])[] = [
  ['sent', 'Sent'],
  ['viewed', 'Heard back'],
  ['interviewing', 'Interviewing'],
  ['offer', 'Offer'],
  ['rejected', 'Not this time'],
  ['no_reply', 'No reply in 3 weeks'],
];

export function pipeline(apps: readonly ApplicationSummary[], now: number): Record<PipeColumn, ApplicationSummary[]> {
  const cols: Record<PipeColumn, ApplicationSummary[]> = { sent: [], viewed: [], interviewing: [], offer: [], rejected: [], no_reply: [] };
  for (const a of apps) {
    if (!isSent(a.stage)) continue;
    if (noReply(a, now)) cols.no_reply.push(a);
    else if (a.stage === 'sent' || a.stage === 'applied') cols.sent.push(a);
    else if (a.stage === 'viewed' || a.stage === 'interviewing' || a.stage === 'offer' || a.stage === 'rejected') cols[a.stage].push(a);
  }
  return cols;
}

const NUMBER_WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine'];

/** "Three are waiting for you." The serif line of the screen. */
export function waitingTitle(n: number): string {
  if (n === 0) return 'Nothing is waiting for you.';
  const word = NUMBER_WORDS[n] ?? String(n);
  return n === 1 ? 'One is waiting for you.' : `${word} are waiting for you.`;
}

/** The chip and the line under it in "Where it stands". */
export function standing(a: ApplicationSummary): { chip: string; line?: string } {
  const why = a.stageReason ?? (a.stopReason ? STOP_REASON_TEXT[a.stopReason] : undefined);
  switch (a.stage) {
    case 'waiting_for_you':
    case 'needs_user':
    case 'ready_to_submit':
      return a.needsCount > 0
        ? { chip: `${plural(a.needsCount, 'question')} for you`, line: why }
        : { chip: 'Ready to review', line: why ?? 'Every value is filled; nothing is sent until you approve' };
    case 'needs_signin':
      return { chip: a.stopReason === 'verification_code' ? 'Needs a code from you' : 'Needs your sign-in', line: why };
    case 'queued':
      return { chip: 'Waiting to fill', line: why ?? 'Nothing has been sent' };
    case 'filling': {
      const p = a.progress;
      return { chip: 'Filling', line: p && p.total > 0 ? `${p.filled} of ${p.total} fields so far` : 'Nothing has been sent' };
    }
    case 'approved':
      return { chip: 'Approved', line: 'Sending exactly what you approved' };
    case 'submitting':
      return { chip: 'Sending', line: why };
    case 'check_this_one':
      return { chip: 'Check this one', line: why ?? 'We clicked send and saw no confirmation' };
    case 'failed':
      return { chip: "Didn't go", line: [why, refundLine(a)].filter(Boolean).join(' · ') || undefined };
    case 'closed':
      return { chip: 'Posting closed', line: refundLine(a) ?? 'Nothing was sent' };
    case 'cancelled':
      return { chip: 'Skipped', line: refundLine(a) };
    default:
      return { chip: STAGE_LABEL[a.stage], line: why };
  }
}

function refundLine(a: ApplicationSummary): string | undefined {
  if (a.creditState === 'released') return 'Refunded';
  if (a.creditState === 'held') return 'The hold is being released';
  return undefined;
}

export function resumeLine(a: ApplicationSummary): string {
  if (a.resumeChoice === 'tailored') return 'Tailored for this match';
  if (a.resumeChoice === 'saved') return 'Your saved resume';
  return 'Not chosen yet';
}

const roleOf = (a: ApplicationSummary) => a.title ?? a.domain;
const whereOf = (a: ApplicationSummary) => [a.company ?? a.domain, ATS_LABEL[a.ats]].filter(Boolean).join(' · ');

/* ───────────────────────── the view ───────────────────────── */

export interface ApplicationsViewProps {
  applications: readonly ApplicationSummary[];
  navigate: Navigate;
  /** The time "since" and "No reply in 3 weeks" are measured against (fixed in the gallery). */
  now: number;
  onStage: (id: string, stage: ApplicationStage) => Promise<void>;
  onEmail: (body: EmailClassifyRequest) => Promise<EmailClassification>;
  onEmailChanged?: () => void;
  /** The balance chip (the account's entitlement); the gallery passes a static one. */
  balance?: ReactNode;
  /** Admins only; null hides the panel. */
  metrics?: ApplyMetrics | null;
  error?: string | null;
  /** The tab to open on; the first with anything in it when absent. */
  initialTab?: ListTab;
}

export function ApplicationsView(props: ApplicationsViewProps) {
  const { applications, navigate, now, onStage, error } = props;
  const counts = useMemo(() => tabCounts(applications), [applications]);
  const [tab, setTab] = useState<ListTab>(() => props.initialTab ?? firstTab(counts));
  const shown = applications.filter((a) => tabOf(a) === tab);
  const cols = useMemo(() => pipeline(applications, now), [applications, now]);
  const anySent = PIPE_COLUMNS.some(([id]) => cols[id].length > 0);

  return (
    <div className="ap-stack">
      <div className="ap-headrow">
        <div className="ap-head">
          <p className="kicker">Applications</p>
          <h1 className="serif ap-title">{waitingTitle(counts.waiting)}</h1>
        </div>
        <div className="ap-actions">
          <a className="btn" {...linkTo(DETAILS_ROUTE, navigate)}>
            Your details
          </a>
          {props.balance}
        </div>
      </div>

      {error && (
        <p className="ap-error" role="alert">
          {error}
        </p>
      )}

      {applications.length === 0 ? (
        <section className="panel ap-panel ap-empty">
          <p className="ap-strong">No applications yet.</p>
          <p className="ap-muted">
            An application starts from a posting match: open a match, or a role in Discover, and choose Apply for me. Nothing is
            sent until you approve the filled form.
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
      ) : (
        <>
          <div className="ap-tabs" role="group" aria-label="Show applications">
            {LIST_TABS.map(([id, label]) => (
              <button key={id} type="button" className="ap-tab" aria-pressed={tab === id} onClick={() => setTab(id)}>
                {label} <span className="numeral">{counts[id]}</span>
              </button>
            ))}
          </div>

          <section className="panel ap-panel ap-table-panel" aria-label={LIST_TABS.find(([id]) => id === tab)?.[1]}>
            {shown.length === 0 ? (
              <p className="ap-muted">Nothing here.</p>
            ) : (
              <table className="ap-apps">
                <thead>
                  <tr>
                    <th scope="col">Role</th>
                    <th scope="col">Resume</th>
                    <th scope="col">Where it stands</th>
                    <th scope="col">Since</th>
                    <th scope="col">
                      <span className="sr-only">Action</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((a) => (
                    <AppRow key={a.id} app={a} now={now} navigate={navigate} />
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}

      {anySent && <Pipeline cols={cols} now={now} navigate={navigate} onStage={onStage} />}

      <Replies applications={applications.filter((a) => isSent(a.stage) || a.stage === 'check_this_one')} onEmail={props.onEmail} onChanged={props.onEmailChanged} />

      {props.metrics && <MetricsPanel metrics={props.metrics} />}
    </div>
  );
}

function AppRow({ app, now, navigate }: { app: ApplicationSummary; now: number; navigate: Navigate }) {
  const where = standing(app);
  const tone = stageTone(app.stage, app.needsCount);
  const waiting = tabOf(app) === 'waiting';
  const updated = Date.parse(app.updatedAt);
  return (
    <tr>
      <td data-label="Role">
        <p className="ap-strong">{roleOf(app)}</p>
        <p className="ap-muted">{whereOf(app)}</p>
      </td>
      <td data-label="Resume">{resumeLine(app)}</td>
      <td data-label="Where it stands">
        <span className={`chip ap-chip ap-tone-${tone}`}>{where.chip}</span>
        {where.line && <p className="ap-muted ap-small">{where.line}</p>}
      </td>
      <td data-label="Since" className="numeral ap-muted">
        {Number.isFinite(updated) ? timeAgo(updated, now) : ''}
      </td>
      <td className="ap-cell-action">
        <a
          className={`btn${waiting && app.needsCount > 0 ? ' btn-primary' : ''}`}
          {...linkTo({ screen: 'one', id: app.id }, navigate)}
          aria-label={`${waiting && app.stage !== 'needs_signin' ? 'Review' : 'Open'}: ${roleOf(app)}`}
        >
          {waiting && app.stage !== 'needs_signin' ? 'Review' : 'Open'}
        </a>
      </td>
    </tr>
  );
}

const MARKS: readonly (readonly [ApplicationStage, string])[] = [
  ['viewed', 'Heard back'],
  ['interviewing', 'Interviewing'],
  ['rejected', 'Not this time'],
];

function Pipeline({
  cols,
  now,
  navigate,
  onStage,
}: {
  cols: Record<PipeColumn, ApplicationSummary[]>;
  now: number;
  navigate: Navigate;
  onStage: (id: string, stage: ApplicationStage) => Promise<void>;
}) {
  const titleId = useId();
  return (
    <section className="ap-stack-s" aria-labelledby={titleId}>
      <div className="ap-headrow ap-headrow-tight">
        <h2 id={titleId} className="kicker">
          After sending
        </h2>
        <p className="ap-muted ap-small">Moves only forward, from replies you paste, or when you mark it.</p>
      </div>
      <div className="ap-pipe">
        {PIPE_COLUMNS.map(([id, label]) => (
          <div key={id} className="ap-pipe-col">
            <h3 className="kicker">
              {label} <span className="numeral">{cols[id].length}</span>
            </h3>
            <ul className="ap-pipe-list">
              {cols[id].map((a) => (
                <li key={a.id} className="ap-pipe-card">
                  <a className="ap-pipe-link" {...linkTo({ screen: 'one', id: a.id }, navigate)}>
                    <span className="ap-strong">{a.company ?? a.domain}</span>
                    <span className="ap-muted">{a.title ?? ''}</span>
                  </a>
                  {id === 'no_reply' && <HeardBack app={a} now={now} onStage={onStage} />}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function HeardBack({ app, now, onStage }: { app: ApplicationSummary; now: number; onStage: (id: string, stage: ApplicationStage) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [busyMark, setBusyMark] = useState<ApplicationStage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const choicesId = useId();
  const sent = shortDate(sentAtOf(app));
  const mark = async (stage: ApplicationStage) => {
    setBusyMark(stage);
    setErr(null);
    try {
      await onStage(app.id, stage);
      setOpen(false);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusyMark(null);
    }
  };
  const days = Math.floor((now - Date.parse(sentAtOf(app))) / DAY_MS);
  return (
    <div className="ap-heard">
      <span className="ap-muted ap-small">
        Sent {sent} · <span className="numeral">{days}</span> days
      </span>
      <button type="button" className="btn btn-sm ap-btn-sm" aria-expanded={open} aria-controls={choicesId} onClick={() => setOpen(!open)}>
        Heard back?
      </button>
      {open && (
        <div id={choicesId} className="ap-actions" role="group" aria-label={`What happened with ${app.company ?? app.domain}`}>
          {MARKS.map(([stage, label]) => (
            <button key={stage} type="button" className="btn btn-sm ap-btn-sm" disabled={busyMark !== null} onClick={() => void mark(stage)}>
              {label}
            </button>
          ))}
        </div>
      )}
      {err && <p className="ap-error ap-small">{err}</p>}
    </div>
  );
}

function Replies({
  applications,
  onEmail,
  onChanged,
}: {
  applications: readonly ApplicationSummary[];
  onEmail: (body: EmailClassifyRequest) => Promise<EmailClassification>;
  onChanged?: () => void;
}) {
  const narrow = useMediaQuery(NARROW_QUERY);
  const [open, setOpen] = useState(false);
  const formId = useId();
  const sheetTitle = useId();
  return (
    <section className="panel ap-panel ap-stack-s" aria-label="Replies">
      <div className="ap-headrow">
        <div className="ap-measure">
          <p className="ap-strong">Replies come to your own inbox</p>
          <p className="ap-muted">
            Employers write to the email on your form. Paste a reply here and we move its application forward. We read only what
            you paste, and never keep it.
          </p>
        </div>
        <button type="button" className="btn" aria-expanded={open} aria-controls={narrow ? undefined : formId} onClick={() => setOpen(!open)}>
          Paste an email
        </button>
      </div>
      {!narrow && open && (
        <div id={formId} className="ap-email-inline">
          <EmailPanel applications={applications} onSubmit={onEmail} onChanged={onChanged} />
        </div>
      )}
      {narrow && (
        <Sheet open={open} onClose={() => setOpen(false)} labelledBy={sheetTitle} className="ap-sheet">
          <div className="ap-sheet-head">
            <h2 id={sheetTitle} className="ap-h3">
              Paste an email
            </h2>
            <button type="button" className="btn btn-quiet" onClick={() => setOpen(false)}>
              Close
            </button>
          </div>
          <EmailPanel applications={applications} onSubmit={onEmail} onChanged={onChanged} titleId={sheetTitle} />
        </Sheet>
      )}
    </section>
  );
}

/** Engine metrics (admins only): playbook against model runs per ATS. Ratios are measured rates, shown as words and counts. */
export function MetricsPanel({ metrics }: { metrics: ApplyMetrics }) {
  const rows = [...metrics.byAts, metrics.total];
  return (
    <details className="panel ap-panel ap-metrics">
      <summary className="ap-summary">Engine metrics (admin), since {shortDate(metrics.since)}</summary>
      <div className="ap-metrics-scroll scroll">
        <table className="ap-table">
          <thead>
            <tr>
              <th scope="col">ATS</th>
              <th scope="col">Runs</th>
              <th scope="col">Playbook</th>
              <th scope="col">Model</th>
              <th scope="col">Completed</th>
              <th scope="col">Needed the person</th>
              <th scope="col">Failed</th>
              <th scope="col">Cost (USD)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ats}>
                <th scope="row">{r.ats}</th>
                <td className="numeral">{r.runs}</td>
                <td className="numeral">{r.playbookRuns}</td>
                <td className="numeral">{r.aiRuns}</td>
                <td className="numeral">{r.successes}</td>
                <td className="numeral">{r.needsUser}</td>
                <td className="numeral">{r.failures}</td>
                <td className="numeral">{(r.costMicroUsd / 1e6).toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {metrics.failureReasons.length > 0 && (
        <p className="ap-muted ap-small">Why runs failed: {metrics.failureReasons.map((f) => `${f.reason} (${f.count})`).join(', ')}</p>
      )}
    </details>
  );
}

/* ───────────────────────── the container ───────────────────────── */

const POLL_MS = 3000;

export function ApplicationsScreen({ navigate }: { navigate: Navigate }) {
  const [apps, setApps] = useState<ApplicationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<ApplyMetrics | null>(null);
  const now = useNow(60_000);

  const refresh = useCallback(async () => {
    try {
      setApps((await applyApi.list()).applications);
      setError(null);
    } catch (err) {
      setError(errorText(err, 'Could not load your applications. Try again.'));
    }
  }, []);

  useEffect(() => {
    void refresh();
    applyApi
      .metrics()
      .then(setMetrics)
      .catch(() => setMetrics(null));
  }, [refresh]);

  const moving = apps?.some((a) => busy(a.stage)) ?? false;
  useEffect(() => {
    if (!moving) return;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [moving, refresh]);

  if (!apps) {
    return error ? (
      <p className="ap-error" role="alert">
        {error}
      </p>
    ) : (
      <p className="ap-muted" role="status">
        Loading your applications
      </p>
    );
  }

  return (
    <ApplicationsView
      applications={apps}
      navigate={navigate}
      now={now}
      error={error}
      metrics={metrics}
      balance={<EntitlementChip />}
      onStage={async (id, stage) => {
        await applyApi.stage(id, stage);
        await refresh();
      }}
      onEmail={(body) => applyApi.email(body)}
      onEmailChanged={() => void refresh()}
    />
  );
}
