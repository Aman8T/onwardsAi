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
import type { CSSProperties, ReactNode } from 'react';
import type {
  ApplicationStage,
  ApplicationSummary,
  ApplyMetrics,
  EmailClassification,
  EmailClassifyRequest,
} from '../../shared/apply';
import { Sheet } from '../auth/Sheet';
import { Pips } from '../components/materials';
import type { Pip } from '../components/materials';
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
  queued: 'filling',
  filling: 'filling',
  approved: 'filling',
  submitting: 'filling',
  sent: 'sent',
  viewed: 'sent',
  interviewing: 'sent',
  offer: 'sent',
  rejected: 'sent',
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
 * When it was sent: the summary's own `sentAt` (the click that went through, or the person's word on it). A sent row
 * from before `sentAt` existed falls back to its last change, which for a row still at `sent` is the send.
 */
export const sentAtOf = (a: ApplicationSummary): string => a.sentAt ?? a.updatedAt;

export function noReply(a: ApplicationSummary, now: number): boolean {
  if (a.stage !== 'sent') return false;
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
    else if (a.stage === 'sent') cols.sent.push(a);
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
      return a.needsCount > 0
        ? { chip: `${plural(a.needsCount, 'question')} for you`, line: why }
        : { chip: 'Ready to review', line: why ?? 'Every value is filled; nothing is sent until you approve' };
    case 'needs_signin':
      return { chip: a.stopReason === 'verification_code' ? 'Needs a code from you' : a.stopReason === 'challenge' ? 'A check for you' : STAGE_LABEL.needs_signin, line: why };
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


/* ───────── the hero: the application that needs the person now, its stage as pips and in words ───────── */

const RUN_STEPS = ['Queued', 'Filling', 'Your review', 'Approved', 'Sent'] as const;
const STEP_OF: Partial<Record<ApplicationStage, number>> = {
  queued: 0,
  filling: 1,
  waiting_for_you: 2,
  needs_signin: 2,
  approved: 3,
  submitting: 3,
  check_this_one: 3,
  sent: 4,
  viewed: 4,
  interviewing: 4,
  offer: 4,
  rejected: 4,
};

/** The stage as marks and in words, or null for a run that ended without a send (failed, closed, skipped). */
export function stagePips(a: ApplicationSummary): { pips: Pip[]; now: number; words: string } | null {
  const at = STEP_OF[a.stage];
  if (at === undefined) return null;
  const pips: Pip[] = RUN_STEPS.map((_, i) => (i < at ? 'on' : 'no'));
  if (at === RUN_STEPS.length - 1) pips[at] = 'on';
  return { pips, now: at, words: `Step ${at + 1} of ${RUN_STEPS.length}: ${RUN_STEPS[at]}` };
}

/** The application that needs the person now (waiting for an answer or a review), else the most recent. */
export function heroApp(apps: readonly ApplicationSummary[]): ApplicationSummary | undefined {
  const waiting = apps.find((a) => tabOf(a) === 'waiting');
  if (waiting) return waiting;
  return [...apps].sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))[0];
}

/** Uneven tiles on a 6-column mosaic, two to a row (4+2, 3+3, 2+4); a last tile left alone takes the whole row. */
const PAIRS = [['m-c4', 'm-c2'], ['m-c3', 'm-c3'], ['m-c2', 'm-c4']] as const;
const spanOf = (i: number, n: number) => (i === n - 1 && n % 2 === 1 ? 'm-c6' : PAIRS[Math.floor(i / 2) % PAIRS.length][i % 2]);

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

  const hero = heroApp(applications);
  const actionOf = (a: ApplicationSummary) => (tabOf(a) === 'waiting' && a.stage !== 'needs_signin' ? 'Review' : 'Open');
  const tabLabel = LIST_TABS.find(([id]) => id === tab)?.[1];

  return (
    <div className="ap-stack ap-list">
      {error && (
        <p className="ap-error" role="alert">
          {error}
        </p>
      )}

      <div className="m-screen is-wide-hero ap-mscreen">
        <div className="m-hero ap-hero">
          <div className="ap-headrow">
            <p className="kicker">Applications</p>
            <div className="ap-actions">
              <a className="btn" {...linkTo(DETAILS_ROUTE, navigate)}>
                Your details
              </a>
              {props.balance}
            </div>
          </div>
          <h1 className="serif ap-title">{waitingTitle(counts.waiting)}</h1>

          {!hero ? (
            <div className="ap-empty">
              <p className="pen ap-pen">Nothing sent yet, and nothing goes out without you.</p>
              <p className="ap-strong">No applications yet.</p>
              <p className="ap-muted ap-measure">
                An application starts from a posting match: open a match, or a role in Discover, and choose Apply for me. Nothing is
                sent until you approve the filled form.
              </p>
              <div className="ap-actions">
                <a className="btn btn-primary" href="/match">
                  Match a posting
                </a>
                <a className="btn btn-quiet" href="/discover">
                  Find roles in Discover
                </a>
              </div>
            </div>
          ) : (
            <HeroApp app={hero} now={now} navigate={navigate} action={actionOf(hero)} />
          )}
        </div>

        {applications.length > 0 && (
          <div className="m-mosaic ap-mosaic">
            <div className="ap-stages m-c6" role="group" aria-label="Show applications">
              {LIST_TABS.map(([id, label]) => (
                <button key={id} type="button" className={`ap-tab m-tile m-stage ${tab === id ? 'm-field' : 'm-plain'}`} aria-pressed={tab === id} onClick={() => setTab(id)}>
                  <b className="numeral">{counts[id]}</b>
                  <span className="m-fine">{label}</span>
                </button>
              ))}
            </div>

            {shown.length === 0 ? (
              <p className="m-tile m-quiet m-c6 ap-muted" aria-label={tabLabel}>
                Nothing here.
              </p>
            ) : (
              <ul className="ap-tiles m-c6" aria-label={tabLabel}>
                {shown.map((a, i) => (
                  <AppTile key={a.id} app={a} now={now} navigate={navigate} index={i} total={shown.length} action={actionOf(a)} />
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {anySent && <Pipeline cols={cols} now={now} navigate={navigate} onStage={onStage} />}

      <Replies applications={applications.filter((a) => isSent(a.stage) || a.stage === 'check_this_one')} onEmail={props.onEmail} onChanged={props.onEmailChanged} />

      {props.metrics && <MetricsPanel metrics={props.metrics} />}
    </div>
  );
}

function HeroApp({ app, now, navigate, action }: { app: ApplicationSummary; now: number; navigate: Navigate; action: string }) {
  const where = standing(app);
  const tone = stageTone(app.stage, app.needsCount);
  const marks = stagePips(app);
  const updated = Date.parse(app.updatedAt);
  const needs = tabOf(app) === 'waiting';
  return (
    <section className="ap-hero-app m-tile m-field" aria-label={`${needs ? 'Needs you now' : 'Latest'}: ${roleOf(app)}`}>
      <p className="kicker">{needs ? 'Needs you now' : 'Most recent'}</p>
      <h2 className="serif ap-hero-role">{roleOf(app)}</h2>
      <p className="m-fine">{whereOf(app)}</p>
      {marks && (
        <div className="ap-hero-pips" style={{ '--m-ground': 'transparent' } as CSSProperties}>
          <Pips pips={marks.pips} now={marks.now} label={marks.words} r={9} gap={16} />
          <p className="m-fine">{marks.words}</p>
        </div>
      )}
      <div className="ap-hero-where">
        <span className={`chip ap-chip ap-tone-${tone}`}>{where.chip}</span>
        {where.line && <p className="m-fine">{where.line}</p>}
        <p className="m-cite">
          {resumeLine(app)}
          {Number.isFinite(updated) ? ` · ${timeAgo(updated, now)}` : ''}
        </p>
      </div>
      <a className="btn btn-primary ap-hero-go" {...linkTo({ screen: 'one', id: app.id }, navigate)} aria-label={`${action}: ${roleOf(app)}`}>
        {action}
      </a>
    </section>
  );
}

function AppTile({ app, now, navigate, index, total, action }: { app: ApplicationSummary; now: number; navigate: Navigate; index: number; total: number; action: string }) {
  const where = standing(app);
  const tone = stageTone(app.stage, app.needsCount);
  const updated = Date.parse(app.updatedAt);
  const marks = stagePips(app);
  return (
    <li className={`ap-tile m-tile ${index % 3 === 1 ? 'm-quiet' : 'm-plain'} ${spanOf(index, total)}`}>
      <p className="ap-strong ap-tile-role">{roleOf(app)}</p>
      <p className="m-fine">{whereOf(app)}</p>
      <div>
        <span className={`chip ap-chip ap-tone-${tone}`}>{where.chip}</span>
      </div>
      {where.line && <p className="m-fine">{where.line}</p>}
      {marks && (
        <div className="ap-tile-pips">
          <Pips pips={marks.pips} now={marks.now} label={marks.words} r={5} gap={8} />
          <span className="m-cite">{marks.words}</span>
        </div>
      )}
      <div className="m-foot">
        <span className="m-cite">
          {resumeLine(app)}
          {Number.isFinite(updated) ? ` · ${timeAgo(updated, now)}` : ''}
        </span>
        <a className="btn" {...linkTo({ screen: 'one', id: app.id }, navigate)} aria-label={`${action}: ${roleOf(app)}`}>
          {action}
        </a>
      </div>
    </li>
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
      {err && (
        <p className="ap-error ap-small" role="alert">
          {err}
        </p>
      )}
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

/** Engine metrics (admins only): playbook against field-list loop runs per ATS, and how they ended. Counts, not scores. */
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
              <th scope="col">Field-list loop</th>
              <th scope="col">Completed</th>
              <th scope="col">Questions for the person</th>
              <th scope="col">Sign-in</th>
              <th scope="col">Failed</th>
              <th scope="col">Check this one</th>
              <th scope="col">Stopped half way</th>
              <th scope="col">Waited for capacity</th>
              <th scope="col">Cost (USD)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ats}>
                <th scope="row">{r.ats}</th>
                <td className="numeral">{r.runs}</td>
                <td className="numeral">{r.playbookRuns}</td>
                <td className="numeral">{r.loopRuns}</td>
                <td className="numeral">{r.successes}</td>
                <td className="numeral">{r.questions}</td>
                <td className="numeral">{r.needsSignin}</td>
                <td className="numeral">{r.failures}</td>
                <td className="numeral">{r.checkThisOne}</td>
                <td className="numeral">{r.interrupted}</td>
                <td className="numeral">{r.deferred}</td>
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
