/**
 * The desk: the dashboard's cards about what the person has been doing here (board "Home · the desk").
 *
 *   WeekCard   one card, "Your week": on the left the last thing touched, large, with the way back into it and the
 *              newest of the other features under it; on the right the weeks in a row, the last twelve weeks as
 *              squares, the weekday that leads, and claims that held up once there is a practice session
 *   Shelf      what is worth saying out loud
 *
 * Nothing here compares the person with an earlier, better number: a score that went down is not shown anywhere, and
 * one that went up is an achievement on the shelf. Every number is counted in code (deskModel.ts, model.ts) from what
 * the features store. The handwriting and the stamps come from the kit in global.css.
 */
import { useMemo } from 'react';
import type { Profile } from '../../../shared/types';
import type { PreparePlanSummary } from '../../../shared/prepare';
import { STAGE_LABEL } from '../../discover/applications/tracker';
import { rankRoles } from '../../lib/layout';
import { dayLabel } from '../../discover/prepare/words';
import { plural, timeAgo } from '../../lib/ui';
import { BAND_LABEL } from '../../match/model';
import { PipsLine } from '../../practice/Pips';
import { actions } from '../../store';
import type { DayCell, PickUp, PracticeTrend, Streak } from './deskModel';
import type { Achievement } from './model';

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

const PICK_ROWS = 3;

function PickUpBlock({
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
  const [hero, ...others] = items;
  const rest = others.slice(0, PICK_ROWS);

  return (
    <div className="db-pick" aria-busy={loading}>
      <div className="db-card-head">
        <h2 className="kicker" id="db-week-h">
          Pick up where you left off
        </h2>
        <span className="kicker">{hero ? `${PICK_LABEL[hero.kind]} · ${timeAgo(hero.at, now)}` : loading ? '' : 'nothing yet'}</span>
      </div>

      {!hero && loading && <p className="db-muted">Reading where you left off…</p>}

      {!hero && !loading && (
        <div className="db-pick-hero">
          <p className="serif db-pick-title">Nothing to pick up yet.</p>
          <p className="db-muted">
            The last thing you touched waits here: a practice session, a plan, a conversation, a match.
            {top ? ` Your closest role is ${top.title} at ${top.alignment}%, a fair place to start.` : ''}
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
              {hero.session.status === 'active' && <span className="stamp stamp-quiet">not finished</span>}
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
              <span className="stamp stamp-ok">{BAND_LABEL[hero.match.band]}</span>
              <span className="db-muted">Every requirement against your evidence.</span>
            </p>
          )}
          {hero.kind === 'application' && (
            <p className="db-small db-pick-detail">
              <span className="stamp">{STAGE_LABEL[hero.job.stage]}</span>
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
    </div>
  );
}

/* ───────────────────────── showing up: the weeks and the squares ───────────────────────── */

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

/** A ring drawn by hand around the number of weeks: a win of the person's own. */
function Ring() {
  return (
    <svg viewBox="0 0 100 60" preserveAspectRatio="none" aria-hidden="true">
      <path d="M52 5C24 3 5 15 6 31c1 16 24 25 49 24 24-1 40-11 39-26C93 15 74 4 46 7" />
    </svg>
  );
}

function streakLine(streak: Streak): string {
  const on = streak.weeks > 0;
  if (on && streak.weeks === 1 && streak.longest <= 1) return 'A week counts once you do one thing in it.';
  if (on && !streak.thisWeek) return `Nothing yet this week. One thing keeps it going. Longest: ${streak.longest}.`;
  if (on) return streak.weeks >= streak.longest ? 'Your longest run so far.' : `Your longest run is ${plural(streak.longest, 'week')}.`;
  return `One thing this week starts a run.${streak.longest > 1 ? ` Your longest is ${plural(streak.longest, 'week')}.` : ''}`;
}

function ShowingUpBlock({ streak, strip, weekday, trend }: { streak: Streak; strip: DayCell[][]; weekday: string | null; trend: PracticeTrend | null }) {
  const on = streak.weeks > 0;
  const active = strip.some((week) => week.some((d) => d.level > 0));
  return (
    <div className="db-up" role="group" aria-labelledby="db-up-h">
      <div className="db-card-head">
        <h3 className="kicker" id="db-up-h">
          Showing up · the last {strip.length} weeks
        </h3>
        <span className="db-fine">a square is a day you did something here</span>
      </div>
      <div className="db-up-body">
        <div className="db-up-run">
          {on ? (
            <p className="db-streak-n">
              <span className="scribble">
                <span className="numeral serif db-streak-big">{streak.weeks}</span>
                <Ring />
              </span>
              <span className="db-small">{streak.weeks === 1 ? 'week' : 'weeks in a row'}</span>
            </p>
          ) : (
            <p className="serif db-streak-fresh">A fresh week.</p>
          )}
          <p className="db-fine">{streakLine(streak)}</p>
        </div>
        <div className="db-up-days">
          <Squares strip={strip} />
          {weekday ? <p className="hand hand-margin db-up-hand">{weekday} are your day.</p> : !active && <p className="hand hand-margin db-up-hand">Your first square goes here.</p>}
        </div>
      </div>
      {trend && trend.claims > 0 && (
        <p className="db-up-claims">
          <span className="db-small">
            <b>Claims defended</b> <span className="db-fine">asked about, out loud, and held up</span>
          </span>
          <Spark bars={trend.bars} />
          <span className="numeral db-trend-n">
            {trend.defended} <span className="db-delta-was">of {trend.claims}</span>
          </span>
        </p>
      )}
    </div>
  );
}

/* ───────────────────────── the card ───────────────────────── */

export function WeekCard({
  profile,
  items,
  loading,
  weak,
  now,
  onOpenPlan,
  streak,
  strip,
  weekday,
  trend,
}: {
  profile: Profile;
  items: PickUp[];
  loading: boolean;
  weak: number;
  now: number;
  onOpenPlan(plan: PreparePlanSummary): void;
  streak: Streak;
  strip: DayCell[][];
  weekday: string | null;
  /** Null until there is a session or a marked claim. */
  trend: PracticeTrend | null;
}) {
  return (
    <section className="panel db-card db-week" aria-labelledby="db-week-h">
      <PickUpBlock profile={profile} items={items} loading={loading} weak={weak} now={now} onOpenPlan={onOpenPlan} />
      <ShowingUpBlock streak={streak} strip={strip} weekday={weekday} trend={trend} />
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

const SHELF_MAX = 4;

function dayOf(iso: string | undefined): string {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : '';
}

export function Shelf({ items }: { items: readonly Achievement[] }) {
  const shown = items.slice(0, SHELF_MAX);
  const fresh = shown.filter((a) => a.fresh).length;
  // the newest thing that happened gets the highlighter
  const lead = shown.find((a) => a.fresh)?.id;
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
                <span className="db-ach-title">{a.id === lead ? <span className="hl">{a.title}</span> : a.title}</span>
                <span className="db-fine">{a.detail}</span>
                <span className="db-cite">
                  {KIND_LABEL[a.kind].toLowerCase()}
                  {a.at ? ` · ${dayOf(a.at)}` : ''}
                </span>
              </span>
              {a.fresh && <span className="stamp">new</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
