/**
 * The desk: the dashboard's cards about what the person has been doing here (board "Home · the desk").
 *
 *   PickUp       the last thing touched, large, with the way back into it; under it the newest of the other features
 *   PinnedNote   the person's own newest note on a tracked role, as a pinned slip
 *   ShowingUp    weeks in a row with something done, and the longest run
 *   Momentum     the last twelve weeks as squares, the weekday that leads, claims that held up, what moved since
 *                the previous analysis
 *   Shelf        what is worth saying out loud, and what the resume leaves out as a second slip
 *
 * Every number is counted in code (deskModel.ts, model.ts) from what the features store. The slips and the handwriting are
 * tilted a degree or two on a wide screen and sit straight on a phone.
 */
import { useMemo } from 'react';
import type { Profile } from '../../../shared/types';
import type { PreparePlanSummary } from '../../../shared/prepare';
import type { TrackedJob } from '../../discover/applications/tracker';
import { STAGE_LABEL } from '../../discover/applications/tracker';
import { rankRoles } from '../../lib/layout';
import { sourceAction } from '../../lib/prompts';
import { dayLabel } from '../../discover/prepare/words';
import { plural, timeAgo } from '../../lib/ui';
import { BAND_LABEL } from '../../match/model';
import { PipsLine } from '../../practice/Pips';
import { actions } from '../../store';
import type { DayCell, PickUp, PracticeTrend, Streak } from './deskModel';
import type { Achievement, Delta } from './model';
import { notOnResume } from './model';

/* ───────────────────────── pick up where you left off ───────────────────────── */

const PICK_LABEL: Record<PickUp['kind'], string> = {
  practice: 'Practice',
  plan: 'Prepare',
  thread: 'Conversation',
  match: 'Posting match',
  application: 'Your applications',
};

/** The person's first question in a conversation: their own words, cut to one line's worth. */
function firstQuestion(item: Extract<PickUp, { kind: 'thread' }>): string {
  const text = item.thread.messages.find((m) => m.role === 'user')?.text.trim() ?? '';
  return text.length > 140 ? `${text.slice(0, 139).trimEnd()}…` : text;
}

/** A row's title. A conversation started from the ask strip is named by its question; "About you" says nothing. */
function rowTitle(item: PickUp): string {
  if (item.kind !== 'thread' || (item.thread.origin.kind !== 'free' && item.thread.origin.kind !== 'you')) return pickTitle(item);
  const q = firstQuestion(item);
  if (!q) return pickTitle(item);
  return q.length > 72 ? `${q.slice(0, 71).trimEnd()}…` : q;
}

function pickTitle(item: PickUp): string {
  switch (item.kind) {
    case 'practice':
      return item.session.title;
    case 'plan':
      return item.plan.requirement ? `${item.plan.roleTitle} · ${item.plan.requirement}` : item.plan.roleTitle;
    case 'thread':
      return item.thread.origin.title;
    case 'match':
      return item.match.company ? `${item.match.title} at ${item.match.company}` : item.match.title;
    case 'application':
      return `${item.job.job.title} at ${item.job.job.employer}`;
  }
}

function pickAction(item: PickUp): string {
  switch (item.kind) {
    case 'practice':
      return item.session.status === 'active' ? 'Go back in' : 'Read the debrief';
    case 'plan':
      return 'Open the plan';
    case 'thread':
      return 'Carry on';
    case 'match':
      return 'Open the match';
    case 'application':
      return 'Open your applications';
  }
}

export function PickUpCard({
  profile,
  items,
  loading,
  weak,
  now,
  onOpenPlan,
}: {
  profile: Profile;
  items: PickUp[];
  /** The sessions or the plans are still being read. */
  loading: boolean;
  /** Claims whose best verdict is Shaky or Couldn't defend. */
  weak: number;
  now: number;
  onOpenPlan(plan: PreparePlanSummary): void;
}) {
  const top = useMemo(() => rankRoles(profile)[0], [profile]);
  const open = (item: PickUp) => {
    switch (item.kind) {
      case 'practice':
        return actions.openPractice({ screen: item.session.status === 'active' ? 'room' : 'debrief', session: item.session.id });
      case 'plan':
        return onOpenPlan(item.plan);
      case 'thread':
        return actions.selectThread(item.thread.id);
      case 'match':
        return actions.openMatch({ screen: 'match', id: item.match.id });
      case 'application':
        return actions.openDiscover({ tab: 'applications' });
    }
  };
  const [hero, ...rest] = items;

  return (
    <section className="panel db-card db-pick" aria-labelledby="db-pick-h" aria-busy={loading}>
      <div className="db-card-head">
        <h2 className="kicker" id="db-pick-h">
          Pick up where you left off
        </h2>
        <span className="kicker">{hero ? `${PICK_LABEL[hero.kind]} · ${timeAgo(hero.at, now)}` : loading ? '' : 'nothing yet'}</span>
      </div>

      {!hero && loading && <p className="db-muted">Reading where you left off…</p>}

      {!hero && !loading && (
        <div className="db-pick-hero">
          <p className="serif db-pick-title">Nothing to pick up yet.</p>
          <p className="db-muted">
            This card always holds the last thing you touched: a practice session, a plan, a conversation, a match.
            {top ? ` Your closest role is ${top.title} at ${top.alignment}%, which is a fair place to start.` : ''}
          </p>
          <div className="db-foot">
            {top && (
              <button type="button" className="btn btn-primary" onClick={() => actions.openRole(top.id)}>
                Open {top.title}
              </button>
            )}
            <button type="button" className="btn" onClick={() => actions.openPractice({ screen: 'hub' })}>
              Defend your resume
            </button>
            <button type="button" className="btn btn-quiet" onClick={() => actions.openMatch()}>
              Match a posting →
            </button>
          </div>
        </div>
      )}

      {hero && (
        <div className="db-pick-hero">
          <p className="serif db-pick-title">{pickTitle(hero)}</p>
          {hero.kind === 'practice' && (
            <p className="db-small db-pick-detail">
              <PipsLine verdicts={hero.session.verdicts} counts={hero.session.counts} />
              {hero.session.status === 'active' && <span className="chip">Not finished</span>}
            </p>
          )}
          {hero.kind === 'plan' && <p className="db-muted">Where you stand, the one move, the weeks. Started {dayLabel(hero.plan.createdAt.slice(0, 10))}.</p>}
          {hero.kind === 'thread' && (
            <p className="db-muted">
              You asked: “{firstQuestion(hero)}”
            </p>
          )}
          {hero.kind === 'match' && (
            <p className="db-small db-pick-detail">
              <span className="chip">{BAND_LABEL[hero.match.band]}</span>
              <span className="db-muted">Every requirement against your evidence.</span>
            </p>
          )}
          {hero.kind === 'application' && (
            <p className="db-small db-pick-detail">
              <span className="chip">{STAGE_LABEL[hero.job.stage]}</span>
              {hero.job.appliedAt && <span className="db-muted">Applied {dayLabel(hero.job.appliedAt)}</span>}
            </p>
          )}
          <div className="db-foot">
            <button type="button" className="btn btn-primary" onClick={() => open(hero)}>
              {pickAction(hero)}
            </button>
            {hero.kind === 'practice' && weak > 0 && (
              <button type="button" className="btn" onClick={() => actions.openPractice({ screen: 'redrill' })}>
                Re-drill {plural(weak, 'claim')}
              </button>
            )}
          </div>
        </div>
      )}

      {rest.length > 0 && (
        <ul className="db-rows db-pick-rows" aria-label="Also in progress">
          {rest.map((item) => (
            <li key={`${item.kind}:${item.id}`}>
              <button type="button" className="db-row" onClick={() => open(item)}>
                <span className="db-row-text">
                  <span className="db-small">
                    <b>{rowTitle(item)}</b>
                  </span>
                  <span className="db-fine">
                    {PICK_LABEL[item.kind]} · {timeAgo(item.at, now)}
                  </span>
                </span>
                <span className="db-fine">{pickAction(item)} →</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ───────────────────────── the pinned note ───────────────────────── */

export function PinnedNote({ job }: { job: TrackedJob }) {
  return (
    <button type="button" className="db-note db-note-pin" onClick={() => actions.openDiscover({ tab: 'applications' })} aria-label={`Your note on ${job.job.employer}: ${job.note}. Open your applications`}>
      <span className="kicker">Your note · {job.job.employer}</span>
      <span className="db-hand">{job.note}</span>
      <span className="db-cite">
        {STAGE_LABEL[job.stage].toLowerCase()} · {job.job.title}
      </span>
    </button>
  );
}

/* ───────────────────────── showing up ───────────────────────── */

export function ShowingUp({ streak }: { streak: Streak }) {
  const on = streak.weeks > 0;
  return (
    <section className="panel db-card db-streak" aria-labelledby="db-streak-h">
      <h2 className="kicker" id="db-streak-h">
        Showing up
      </h2>
      {on ? (
        <p className="db-streak-n">
          <span className="numeral serif db-streak-big">{streak.weeks}</span>
          <span className="db-small">{streak.weeks === 1 ? 'week' : 'weeks in a row'}</span>
        </p>
      ) : (
        <p className="serif db-streak-fresh">A fresh week.</p>
      )}
      <p className="db-fine">
        {on && streak.weeks === 1 && streak.longest <= 1
          ? 'A week counts once you do one thing in it.'
          : on && !streak.thisWeek
            ? `Nothing yet this week. One thing keeps it going. Longest: ${streak.longest}.`
            : on
              ? streak.weeks >= streak.longest
                ? 'Your longest run so far.'
                : `Your longest run is ${plural(streak.longest, 'week')}.`
              : `One thing this week starts a run.${streak.longest > 1 ? ` Your longest is ${plural(streak.longest, 'week')}.` : ''}`}
      </p>
    </section>
  );
}

/* ───────────────────────── momentum ───────────────────────── */

function Squares({ strip }: { strip: DayCell[][] }) {
  const active = strip.reduce((n, week) => n + week.filter((d) => d.level > 0).length, 0);
  return (
    <div className="db-days" role="img" aria-label={`${plural(active, 'day')} with something done in the last ${strip.length} weeks`}>
      <span className="db-days-keys" aria-hidden="true">
        <span>M</span>
        <span />
        <span>W</span>
        <span />
        <span>F</span>
        <span />
        <span>S</span>
      </span>
      <span className="db-dots" aria-hidden="true">
        {strip.map((week) =>
          week.map((d) => <span key={d.key} className={`db-dot${d.level === 2 ? ' is-l2' : d.level === 1 ? ' is-l1' : ''}${d.ahead ? ' is-ahead' : ''}${d.today ? ' is-today' : ''}`} />),
        )}
      </span>
    </div>
  );
}

function Spark({ bars }: { bars: number[] }) {
  if (bars.length < 2) return null;
  return (
    <span className="db-spark" role="img" aria-label={`Share of claims defended in your last ${bars.length} sessions: ${bars.map((b) => `${Math.round(b * 100)}%`).join(', ')}`}>
      {bars.map((b, i) => (
        <span key={i} className={`db-spark-bar${i === bars.length - 1 ? ' is-last' : ''}`} style={{ height: `${Math.max(8, Math.round(b * 100))}%` }} />
      ))}
    </span>
  );
}

export function Momentum({
  strip,
  weekday,
  trend,
  loading,
  moved,
  since,
  mastery,
}: {
  strip: DayCell[][];
  weekday: string | null;
  /** Null until there is a session or a marked claim. */
  trend: PracticeTrend | null;
  loading: boolean;
  /** What moved since the previous analysis; empty for a first analysis. */
  moved: Delta[];
  /** "2 November": the day of the previous analysis, when there is one. */
  since: string | null;
  mastery: number;
}) {
  const active = strip.some((week) => week.some((d) => d.level > 0));
  return (
    <section className="panel db-card db-mom" aria-labelledby="db-mom-h">
      <div className="db-card-head">
        <h2 className="kicker" id="db-mom-h">
          Momentum · the last {strip.length} weeks
        </h2>
        <span className="db-fine">a square is a day you did something here</span>
      </div>
      <div className="db-mom-body">
        <div className="db-mom-days">
          <Squares strip={strip} />
          {weekday ? (
            <p className="db-hand db-margin">{weekday} are your day.</p>
          ) : (
            !active && <p className="db-hand db-margin">Your first square goes here.</p>
          )}
        </div>
        <ul className="db-trends" aria-label="Trends">
          <li className="db-trend">
            <span className="db-trend-text">
              <span className="db-small">
                <b>Claims defended</b>
              </span>
              <span className="db-fine">
                {trend && trend.claims > 0
                  ? 'Asked about, out loud, and held up'
                  : loading
                    ? 'Reading your sessions…'
                    : 'Counted after your first practice session.'}
              </span>
            </span>
            {trend && <Spark bars={trend.bars} />}
            <span className="numeral db-trend-n">
              {trend && trend.claims > 0 ? (
                <>
                  {trend.defended} <span className="db-delta-was">of {trend.claims}</span>
                </>
              ) : (
                '0'
              )}
            </span>
          </li>
          {moved.length === 0 && (
            <li className="db-trend">
              <span className="db-trend-text">
                <span className="db-small">
                  <b>Role mastery</b>
                </span>
                <span className="db-fine">One analysis so far. A trend needs a second one.</span>
              </span>
              <span className="numeral db-trend-n">{mastery}</span>
            </li>
          )}
          {moved.map((d) => (
            <li key={d.label} className="db-trend">
              <span className="db-trend-text">
                <span className="db-small">
                  <b>{d.label}</b>
                </span>
                <span className="db-fine">{d.note}</span>
              </span>
              <span className="numeral db-trend-n">
                {d.was ? (
                  <>
                    <span className="db-delta-was">{d.was} →</span> {d.now}
                  </>
                ) : (
                  d.now
                )}
              </span>
            </li>
          ))}
        </ul>
      </div>
      {since && moved.length > 0 && <p className="db-fine db-mom-foot">The last {plural(moved.length, 'line')} compare this analysis with the one from {since}.</p>}
    </section>
  );
}

/* ───────────────────────── the shelf ───────────────────────── */

const KIND_LABEL: Record<Achievement['kind'], string> = {
  graph: 'Your graph',
  moved: 'Moved',
  applications: 'Applications',
  practice: 'Practice',
  match: 'Posting match',
  prepare: 'Prepare',
  weekly: 'The Weekly',
};

const SHELF_WITH_NOTE = 3;

function dayOf(iso: string | undefined): string {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : '';
}

/** What the resume leaves out, as a slip pinned to the shelf: the skill, the proof, the one action. */
function ResumeSlip({ profile }: { profile: Profile }) {
  const found = useMemo(() => notOnResume(profile), [profile]);
  if (!found) return null;
  const { resume, items, role } = found;
  const labels = items.map((it) => it.source.label).filter((v, i, arr) => arr.indexOf(v) === i);
  const add = () => {
    const a = sourceAction(resume);
    actions.openChat(
      a.origin,
      `My resume doesn't mention ${items.map((it) => it.skill.name).join(' or ')}, which ${labels.join(' and ')} prove${labels.length === 1 ? 's' : ''}. Help me add ${items.length === 1 ? 'it' : 'them'} as resume bullets in my own voice, using only the cited facts.`,
    );
  };
  return (
    <div className="db-note db-note-r" role="note" aria-labelledby="db-slip-h">
      <span className="kicker" id="db-slip-h">
        Not on your resume yet
      </span>
      <span className="db-hand">{items.map((it) => it.skill.name).join(' and ')}.</span>
      {items.map((it) => (
        <span key={it.skill.id} className="db-cite">
          {it.source.label} · “{it.evidence.text}”
        </span>
      ))}
      {role && (
        <span className="db-fine">
          {items.length === 1 ? 'It counts' : 'Both count'} towards {role.title}.
        </span>
      )}
      <button type="button" className="btn btn-sm db-note-act" onClick={add}>
        Add {items.length === 1 ? 'it' : 'both'} to my resume
      </button>
    </div>
  );
}

export function Shelf({ profile, items }: { profile: Profile; items: Achievement[] }) {
  const hasSlip = useMemo(() => notOnResume(profile) !== null, [profile]);
  const shown = hasSlip ? items.slice(0, SHELF_WITH_NOTE) : items;
  const fresh = shown.filter((a) => a.fresh).length;
  return (
    <section className="panel db-card db-shelf" aria-labelledby="db-shelf-h">
      <div className="db-card-head">
        <h2 className="kicker" id="db-shelf-h">
          Worth saying out loud
        </h2>
        <span className="kicker">{fresh > 0 ? `${fresh} new this week` : 'counted, not scored'}</span>
      </div>
      {shown.length === 0 ? (
        <p className="db-muted">Practice, applications and plans add to this shelf as they happen.</p>
      ) : (
        <ul className="db-achs">
          {shown.map((a) => (
            <li key={a.id} className="db-ach">
              <span className="db-ach-mark" aria-hidden="true" />
              <span className="db-ach-text">
                <span className="db-ach-title">{a.title}</span>
                <span className="db-fine">{a.detail}</span>
                <span className="db-cite">
                  {KIND_LABEL[a.kind].toLowerCase()}
                  {a.at ? ` · ${dayOf(a.at)}` : ''}
                </span>
              </span>
              {a.fresh && <span className="db-stamp">new</span>}
            </li>
          ))}
        </ul>
      )}
      <ResumeSlip profile={profile} />
    </section>
  );
}
