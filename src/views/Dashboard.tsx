/**
 * The dashboard, "Home": where the app opens once a graph exists, and the person's own space. The page scrolls. On a wide
 * screen the hero (about 46 %) holds still under the header while one column (about 54 %) scrolls beside it.
 *
 *   hero     the greeting and the changing line (views/dashboard/Read.tsx, lines.ts); the skill graph as the biggest
 *            thing, a floating callout per domain (dashboard/HeroRadar.tsx); under it "Share your skill graph" and "Your
 *            wins"; the Ask bar in place
 *   column   in the order a person uses it: pick up where you left off (the one field of colour and the one primary
 *            button), Today (the features as things to do, dashboard/todos.ts), next roles, four small tiles with one
 *            number each (applications, Discover, practice, posting matches), the month as a torn calendar page with the
 *            weeks in a row, your notes (two post-its), and the quote of the day
 *
 * Under 1100 px wide, or on a short screen, nothing holds still and the hero sits above the column; under 760 px it is one
 * straight column and nothing is turned. Nothing here shows a score going down. Every number is counted in code from what
 * the features already store; the read's lines are the one thing a model phrased, and they are stored on the profile.
 * The Weekly and "New analysis" live in the header (graph/GraphHeader.tsx).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Profile } from '../../shared/types';
import type { PracticeHistory } from '../../shared/interview';
import type { PreparePlanSummary } from '../../shared/prepare';
import type { JobSearchRun } from '../../shared/jobsearch';
import type { MatchSummary } from '../match/types';
import { AskDock } from '../ask/AskDock';
import { SaveInvite } from '../auth/SaveInvite';
import { ShareButton } from '../card/ShareButton';
import { useMe } from '../auth/useMe';
import { useTracker } from '../discover/applications/tracker';
import type { TrackedJob } from '../discover/applications/tracker';
import { readArea } from '../discover/area';
import { getLatestRun } from '../discover/jobsearch/api';
import { rankRoles, roleStanding } from '../lib/layout';
import { requirementAction, roleAction } from '../lib/prompts';
import { useNow } from '../lib/ui';
import { listMatches } from '../match/api';
import { readRecent } from '../match/recent';
import { fetchHistory } from '../practice/api';
import { useLoad } from '../practice/hub/useLoad';
import type { Load } from '../practice/hub/useLoad';
import { listPlans } from '../prepare/api';
import { actions, useApp } from '../store';
import { useSubscription } from '../weekly/useSubscription';
import { monthPage } from './dashboard/calendar';
import { PickTile, ShelfLink } from './dashboard/Desk';
import { HeroRadar } from './dashboard/HeroRadar';
import { MonthPage, RunNotes } from './dashboard/Month';
import { Hello, QuoteCard, usePreviousProfile } from './dashboard/Read';
import { NotesTile } from './dashboard/Slips';
import { AppsTile, DiscoverTile, MatchTile, NextTile, PracticeTile, TodayTile } from './dashboard/Tiles';
import { activeDays, askStarters, bestWeekday, dayKey, pickUps, practiceTrend, weekStreak } from './dashboard/deskModel';
import type { ThreadLike } from './dashboard/deskModel';
import { achievements } from './dashboard/model';
import { useTodoMarks } from './dashboard/todoMarks';
import { todayTodos, withMarks } from './dashboard/todos';
import { GraphHeader } from './graph/GraphHeader';
import '../styles/shell.css';
import '../styles/dashboard.css';

/** What the playground hands in so the desk can be looked at without the server. The app never passes it. */
export interface DashboardSeed {
  history?: PracticeHistory;
  plans?: PreparePlanSummary[];
  matches?: MatchSummary[];
  tracked?: TrackedJob[];
  threads?: ThreadLike[];
  /** Discover's last search; `null` is "no search yet". */
  run?: JobSearchRun | null;
}

const NO_PLANS: PreparePlanSummary[] = [];

/** Reopen a plan the way the graph seeded it: the role's question, or the gap's. */
function openPlan(profile: Profile, p: PreparePlanSummary) {
  const role = profile.roles.find((r) => r.id === p.roleId);
  if (!role) return;
  const req = p.requirement ? role.requirements.find((r) => r.name === p.requirement) : undefined;
  const a = req ? requirementAction(req, role) : roleAction(role, roleStanding(profile, role.id));
  // the same seed the graph uses, so an existing thread for this role (or gap) reopens with its stored plan
  actions.openChat(a.origin, a.question, { roleId: role.id, ...(req ? { requirement: req.name } : {}) });
}

export function Dashboard({ profile, previous: forcedPrevious, seed }: { profile: Profile; previous?: Profile | null; seed?: DashboardSeed }) {
  const found = usePreviousProfile(profile);
  // the playground hands a previous analysis in; the app finds its own
  const previous = forcedPrevious === undefined ? found : forcedPrevious;
  const read = useDashboardData(profile, seed);
  const data = useMemo<DashboardData>(
    () => ({
      history: seed?.history ? { status: 'ready', data: seed.history } : read.history,
      plans: seed?.plans ? { status: 'ready', data: seed.plans } : read.plans,
      matches: seed?.matches ?? read.matches,
      run: read.run,
    }),
    [seed, read.history, read.plans, read.matches, read.run],
  );
  const tracker = useTracker(profile.id);
  const tracked = seed?.tracked ?? tracker.items;
  const stored = useApp((s) => s.threads);
  // a graph re-read from /resume moves the person's resumes and notes to the new profile; false says it did not
  const carried = useApp((s) => s.analysis.carried);
  const threads: readonly ThreadLike[] = seed?.threads ?? stored;
  const { user } = useMe();
  const { subscription } = useSubscription(user?.id);
  const now = useNow(60_000);
  const history = data.history.status === 'ready' ? data.history.data : null;
  const plans = data.plans.status === 'ready' ? data.plans.data : NO_PLANS;
  const shelf = useMemo(
    () => achievements({ profile, previous, tracked, history, matches: data.matches, plans, subscribed: !!subscription && subscription.status !== 'off', now }),
    [profile, previous, tracked, history, data.matches, plans, subscription, now],
  );
  // the desk: the last thing touched, the days something was done, the run of weeks
  const desk = useMemo(() => {
    const input = { profile, previous, history, plans, matches: data.matches, tracked, threads };
    const days = activeDays(input);
    return {
      picks: pickUps(input),
      days,
      page: monthPage(now, days),
      streak: weekStreak(days, now),
      weekday: bestWeekday(days),
      trend: practiceTrend(history),
    };
  }, [profile, previous, history, plans, data.matches, tracked, threads, now]);
  const weak = history ? history.claims.filter((c) => c.best === 'shaky' || c.best === 'couldnt').length : 0;
  const reading = data.history.status === 'loading' || data.plans.status === 'loading';
  // what is worth asking now, from what changed most recently on the desk
  const starters = useMemo(() => askStarters({ profile, history, plans, matches: data.matches, tracked, now }), [profile, history, plans, data.matches, tracked, now]);

  // Today: computed from the same records, then the person's own ticks and lines over it
  const run = data.run.status === 'ready' ? data.run.data : null;
  const day = dayKey(now);
  const marks = useTodoMarks(profile.id, day);
  const todos = useMemo(() => {
    const top = rankRoles(profile)[0];
    return todayTodos({
      topRole: top ? { id: top.id, title: top.title, alignment: top.alignment } : null,
      tracked,
      history,
      plans,
      matches: data.matches,
      run: run ? { createdAt: run.createdAt, found: run.jobs.length } : null,
      now,
    });
  }, [profile, tracked, history, plans, data.matches, run, now]);
  const rows = useMemo(() => withMarks(todos, marks.marks), [todos, marks.marks]);
  const lastRun = run ? { createdAt: run.createdAt, found: run.jobs.length, good: run.jobs.filter((j) => j.fit === 'strong' || j.fit === 'good').length } : null;

  // the hero holds still under the app header, so it has to know how tall the header is (it wraps on narrower screens)
  const view = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = view.current;
    const head = el?.querySelector<HTMLElement>('.gv-header');
    if (!el || !head) return;
    const set = () => el.style.setProperty('--db-head', `${head.offsetHeight}px`);
    set();
    const seen = new ResizeObserver(set);
    seen.observe(head);
    return () => seen.disconnect();
  }, []);

  return (
    <div className="gv-view db-view" ref={view}>
      <GraphHeader profile={profile} current="dashboard" extra={<WeeklyLink />} />
      <SaveInvite profile={profile} />

      <main className="os-body">
        {/* ───────── the hero: the greeting, the skill graph and the ask; it holds still while the column scrolls ───────── */}
        <section className="os-hero" aria-label="Your skill graph">
          {carried === false && (
            <p className="fine os-carry" role="status">
              Your graph was updated, but your resumes and notes did not move with it. They are still saved with your earlier analysis.
            </p>
          )}
          <Hello profile={profile} shelf={shelf} history={history} />

          <HeroRadar profile={profile} />

          {/* sharing and the wins sit with the graph they are about; neither is the page's primary button */}
          <div className="os-acts">
            <ShareButton profileId={profile.id} look="slip" className="btn" label="Share your skill graph" />
            <ShelfLink items={shelf} />
          </div>

          <div className="os-ask">
            <AskDock profile={profile} starters={starters} inPlace />
          </div>
        </section>

        {/* ───────── the column: everything else, in the order a person uses it ───────── */}
        <div className="os-col">
          <PickTile profile={profile} items={desk.picks} loading={reading} weak={weak} now={now} onOpenPlan={(p) => openPlan(profile, p)} />
          <TodayTile
            rows={rows}
            onTick={marks.tick}
            onAdd={marks.add}
            onRemove={marks.remove}
            onOpenPlan={(id) => {
              const plan = plans.find((p) => p.id === id);
              if (plan) openPlan(profile, plan);
            }}
          />
          <NextTile profile={profile} />
          <div className="os-stand">
            <AppsTile items={tracked} />
            <DiscoverTile run={lastRun} loading={data.run.status === 'loading'} now={now} />
            <PracticeTile trend={desk.trend} loading={data.history.status === 'loading'} hasSession={!!history && history.sessions.length > 0} />
            <MatchTile matches={data.matches} now={now} />
          </div>
          <section className="os-month" aria-label="The days you showed up this month">
            <MonthPage page={desk.page} />
            <RunNotes streak={desk.streak} weekday={desk.weekday} anyDay={desk.days.size > 0} />
          </section>
          <NotesTile profile={profile} tracked={tracked} />
          <QuoteCard profile={profile} />
        </div>
      </main>
    </div>
  );
}

/* ───────────────────────── the reads the tiles and the shelf share ───────────────────────── */

interface DashboardData {
  history: Load<PracticeHistory>;
  plans: Load<PreparePlanSummary[]>;
  matches: MatchSummary[];
  run: Load<JobSearchRun | null>;
}

const READY_NO_RUN: Load<JobSearchRun | null> = { status: 'ready', data: null };

/** One read each of the practice history, the plans, the posting matches and Discover's last search, shared by the tiles and the celebrations. */
function useDashboardData(profile: Profile, seed?: DashboardSeed): DashboardData {
  const { load: history } = useLoad<PracticeHistory>((signal) => fetchHistory(profile.id, signal), [profile.id]);
  const { load: plans } = useLoad<PreparePlanSummary[]>((signal) => listPlans(profile.id, signal), [profile.id]);
  const area = useMemo(() => readArea(profile.id), [profile.id]);
  const { load: lastRun } = useLoad<JobSearchRun | null>((signal) => getLatestRun(profile.id, area.city, area.remote !== false, signal), [profile.id, area.city, area.remote]);
  const run: Load<JobSearchRun | null> = seed && 'run' in seed ? { status: 'ready', data: seed.run ?? null } : lastRun;
  const { user, loaded } = useMe();
  // an account's matches come from the server; a visitor's last few are kept on this device
  const [matches, setMatches] = useState<MatchSummary[]>(() => readRecent());
  useEffect(() => {
    if (!loaded) return;
    if (!user) {
      setMatches(readRecent());
      return;
    }
    const ctl = new AbortController();
    listMatches(ctl.signal).then(
      (list) => {
        if (!ctl.signal.aborted) setMatches(list);
      },
      () => {
        if (!ctl.signal.aborted) setMatches(readRecent());
      },
    );
    return () => ctl.abort();
  }, [user, loaded]);
  return { history, plans, matches, run: run ?? READY_NO_RUN };
}

/* ───────────────────────── the Weekly, in the header ───────────────────────── */

function WeeklyLink() {
  const { user } = useMe();
  const { subscription } = useSubscription(user?.id);
  const subscribed = !!subscription && subscription.status !== 'off';
  const unread = subscribed ? subscription.unread : 0;
  return (
    <button type="button" className="gv-navbtn" onClick={() => actions.openWeekly()}>
      The Weekly
      {unread > 0 && (
        <span className="stamp gv-stamp">
          {unread} new<span className="sr-only"> in the Weekly</span>
        </span>
      )}
    </button>
  );
}
