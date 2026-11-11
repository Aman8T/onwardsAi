/**
 * The dashboard (board "Home · /dashboard"): where the app opens once a graph exists, and the person's own space.
 * Top to bottom:
 *
 *   the read        the greeting and one line at a time (views/dashboard/Read.tsx, lines.ts)
 *   the profile     the skill radar, whole, with its rail and popovers (NowGraph) and "Share my skill shape";
 *                   beside it the slips (views/dashboard/Slips.tsx): the person's notes as a pile, their card, their resume
 *   ask             questions worth asking about yourself
 *   next            the ranked next roles, and the shelf of what is worth saying out loud
 *   your week       one card: pick up where you left off, the weeks in a row, the last twelve weeks as squares
 *   the features    Applications (with Discover's search), Practice and Prepare, Posting matches; the Weekly as a line
 *
 * Nothing here shows a score going down. Every number is counted in code from what the features already store; the
 * read's lines are the one thing a model phrased, and they are stored on the profile. Cards are a bento of uneven
 * widths; the slips are turned a degree or two; on a phone it is one straight column.
 */
import { useEffect, useMemo, useState } from 'react';
import type { Profile } from '../../shared/types';
import type { PracticeHistory } from '../../shared/interview';
import type { PreparePlanSummary } from '../../shared/prepare';
import type { JobSearchRun } from '../../shared/jobsearch';
import type { MatchSummary } from '../match/types';
import { applyPath } from '../apply/route';
import { applyOn, useApplications } from '../apply/useApplications';
import { SaveInvite } from '../auth/SaveInvite';
import { ShareButton } from '../card/ShareButton';
import { useMe } from '../auth/useMe';
import { RadarGlyph } from '../components/Radar';
import { STAGES, STAGE_LABEL, useTracker } from '../discover/applications/tracker';
import type { TrackedJob } from '../discover/applications/tracker';
import { readArea } from '../discover/area';
import { getLatestRun } from '../discover/jobsearch/api';
import { FIT_WORD } from '../discover/jobsearch/words';
import { dayLabel } from '../discover/prepare/words';
import { orderRequirements, rankRoles, roleStanding } from '../lib/layout';
import { requirementAction, roleAction } from '../lib/prompts';
import { plural, timeAgo, useNow } from '../lib/ui';
import { listMatches } from '../match/api';
import { BAND_LABEL } from '../match/model';
import { readRecent } from '../match/recent';
import { fetchHistory } from '../practice/api';
import { useLoad } from '../practice/hub/useLoad';
import type { Load } from '../practice/hub/useLoad';
import { PipsLine } from '../practice/Pips';
import { listPlans } from '../prepare/api';
import { actions, useApp } from '../store';
import { useSubscription } from '../weekly/useSubscription';
import { Shelf, WeekCard } from './dashboard/Desk';
import { AskStrip, Hello, usePreviousProfile } from './dashboard/Read';
import { activeDays, bestWeekday, pickUps, practiceTrend, weekStreak, weeksStrip } from './dashboard/deskModel';
import type { ThreadLike } from './dashboard/deskModel';
import { achievements } from './dashboard/model';
import { Slips } from './dashboard/Slips';
import { GraphHeader } from './graph/GraphHeader';
import { NowGraph } from './graph/NowGraph';
import '../styles/shell.css';
import '../styles/dashboard.css';

/** Auto-apply stages that wait on the person (the Apply screen's "Waiting for you" and "Check this one"). */
const WAITING = new Set(['waiting_for_you', 'needs_signin', 'check_this_one']);

/** What the playground hands in so the desk can be looked at without the server. The app never passes it. */
export interface DashboardSeed {
  history?: PracticeHistory;
  plans?: PreparePlanSummary[];
  matches?: MatchSummary[];
  tracked?: TrackedJob[];
  threads?: ThreadLike[];
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
  const read = useDashboardData(profile);
  const data = useMemo<DashboardData>(
    () => ({
      history: seed?.history ? { status: 'ready', data: seed.history } : read.history,
      plans: seed?.plans ? { status: 'ready', data: seed.plans } : read.plans,
      matches: seed?.matches ?? read.matches,
    }),
    [seed, read.history, read.plans, read.matches],
  );
  const tracker = useTracker(profile.id);
  const tracked = seed?.tracked ?? tracker.items;
  const stored = useApp((s) => s.threads);
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
      streak: weekStreak(days, now),
      strip: weeksStrip(days, now),
      weekday: bestWeekday(days),
      trend: practiceTrend(history),
    };
  }, [profile, previous, history, plans, data.matches, tracked, threads, now]);
  const weak = history ? history.claims.filter((c) => c.best === 'shaky' || c.best === 'couldnt').length : 0;
  const reading = data.history.status === 'loading' || data.plans.status === 'loading';

  return (
    <div className="gv-view db-view">
      <GraphHeader profile={profile} current="dashboard" />
      <SaveInvite profile={profile} />

      <main className="db-main">
        {/* the person's own space: the greeting and one line at a time (an achievement, a roast, a tip, a quote…) */}
        <Hello profile={profile} shelf={shelf} history={history} />

        {/* the main thing: the profile, with the way to share it; beside it the slips that are the person's own */}
        <div className="db-bento db-top">
          <section className="panel db-now" aria-labelledby="db-now-h">
            <div className="db-card-head db-now-head">
              <h2 className="kicker" id="db-now-h">
                Your profile · the skill graph
              </h2>
              <ShareButton profileId={profile.id} look="primary" className="btn-sm" />
            </div>
            <NowGraph profile={profile} />
          </section>
          <Slips profile={profile} tracked={tracked} />
        </div>

        <AskStrip profile={profile} />

        <div className="db-bento">
          <NextRoles profile={profile} />
          <Shelf items={shelf} />
          <WeekCard
            profile={profile}
            items={desk.picks}
            loading={reading}
            weak={weak}
            now={now}
            onOpenPlan={(p) => openPlan(profile, p)}
            streak={desk.streak}
            strip={desk.strip}
            weekday={desk.weekday}
            trend={desk.trend}
          />
          <ApplicationsCard profile={profile} items={tracked} />
          <PracticeCard profile={profile} load={data.history} plans={data.plans} />
          <MatchesCard matches={data.matches} />
        </div>

        <WeeklyLine />
      </main>
    </div>
  );
}

/* ───────────────────────── the reads the cards and the shelf share ───────────────────────── */

interface DashboardData {
  history: Load<PracticeHistory>;
  plans: Load<PreparePlanSummary[]>;
  matches: MatchSummary[];
}

/** One read each of the practice history, the plans and the posting matches, shared by the cards and the celebrations. */
function useDashboardData(profile: Profile): DashboardData {
  const { load: history } = useLoad<PracticeHistory>((signal) => fetchHistory(profile.id, signal), [profile.id]);
  const { load: plans } = useLoad<PreparePlanSummary[]>((signal) => listPlans(profile.id, signal), [profile.id]);
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
  return { history, plans, matches };
}

/* ───────────────────────── NEXT · the ranked roles ───────────────────────── */

function NextRoles({ profile }: { profile: Profile }) {
  const roles = useMemo(() => rankRoles(profile), [profile]);
  const top = roles[0];
  const prepareTop = () => {
    if (!top) return;
    const a = roleAction(top, roleStanding(profile, top.id));
    actions.openChat(a.origin, a.question, { roleId: top.id });
  };

  return (
    <section className="panel db-card db-next" aria-labelledby="db-next-h">
      <div className="db-card-head">
        <h2 className="db-card-title" id="db-next-h">
          <span className="kicker">Next · {roles.length ? `${plural(roles.length, 'path')} from here` : 'no paths yet'}</span>
          <span className="title db-h2">Most probable next roles</span>
        </h2>
        <span className="kicker">Alignment · time to close</span>
      </div>
      {roles.length === 0 ? (
        <p className="db-muted">Next roles appear here once Leafout has enough evidence.</p>
      ) : (
        <ol className="db-rows" aria-label="Next roles, ranked">
          {roles.map((r, i) => (
            <li key={r.id}>
              <button type="button" className="db-role" onClick={() => actions.openRole(r.id)} aria-label={`Open ${r.title}: ${r.alignment}% aligned, ${r.eta}`}>
                <span className="numeral kicker db-role-rank" aria-hidden="true">
                  {i + 1}
                </span>
                <span className="db-role-text">
                  <span className="db-role-title">{r.title}</span>
                  <span className="kicker">
                    <span className="numeral db-role-pct">{r.alignment}%</span> · {r.eta}
                    {i === 0 && r.why ? ` · ${r.why}` : ''}
                  </span>
                </span>
                <RadarGlyph className="db-role-glyph" values={orderRequirements(r).map((q) => q.current)} />
              </button>
            </li>
          ))}
        </ol>
      )}
      <div className="db-foot">
        {top && (
          <button type="button" className="btn btn-primary" onClick={prepareTop}>
            How do I prepare for {top.title}?
          </button>
        )}
        {top && (
          <button type="button" className="btn btn-quiet" onClick={() => actions.openRole(null)}>
            Open a role →
          </button>
        )}
      </div>
    </section>
  );
}

/* ───────────────────────── Applications ───────────────────────── */

function ApplicationsCard({ profile, items }: { profile: Profile; items: readonly TrackedJob[] }) {
  const apply = useApplications();
  const sent = applyOn(apply.status) && apply.ready;
  const counts = STAGES.map((stage) => ({ stage, n: items.filter((t) => t.stage === stage).length }));
  // what is moving: an interview first, else the newest application, else the newest saved role
  const moving = items.find((t) => t.stage === 'interviewing') ?? items.find((t) => t.stage === 'applied') ?? items.find((t) => t.stage === 'saved');
  const waiting = sent ? apply.applications.filter((a) => WAITING.has(a.stage)).length : 0;
  const open = items.filter((t) => t.stage !== 'closed').length;

  return (
    <section className="panel db-card db-apps" aria-labelledby="db-apps-h">
      <div className="db-card-head">
        <h2 className="kicker" id="db-apps-h">
          Your applications
        </h2>
        <button type="button" className="db-link" onClick={() => actions.openDiscover({ tab: 'applications' })}>
          {items.length > 0 ? `All ${items.length} →` : 'Open →'}
        </button>
      </div>
      {items.length === 0 ? (
        <p className="db-muted">Nothing saved yet. Save a role in Discover and it is kept here, with its stage and a note.</p>
      ) : (
        <ul className="db-stats" aria-label="Applications by stage">
          {counts
            .filter((c) => c.stage !== 'offer' || c.n > 0)
            .map((c) => (
              <li key={c.stage} className={`db-stat${c.stage === 'interviewing' && c.n > 0 ? ' is-on' : ''}`}>
                <span className="numeral db-stat-n">{c.n}</span>
                <span className="db-fine">{STAGE_LABEL[c.stage]}</span>
              </li>
            ))}
        </ul>
      )}
      {moving && (
        <div className="db-line">
          <span className="db-small">
            <b>{moving.job.employer}</b> · {moving.job.title}
          </span>
          <span className="db-fine">
            {STAGE_LABEL[moving.stage]}
            {moving.appliedAt ? ` · applied ${dayLabel(moving.appliedAt)}` : ''}
            {moving.note ? ` · “${moving.note}”` : ''}
          </span>
        </div>
      )}
      {sent && (
        <div className="db-line">
          <span className="db-small">
            <b>Apply for me</b> · {waiting > 0 ? `${waiting} waiting for you` : apply.applications.length > 0 ? plural(apply.applications.length, 'application') : 'nothing in flight'}
          </span>
          <a className="btn btn-sm" href={applyPath({ screen: 'list' })}>
            {waiting > 0 ? `Review the ${waiting}` : 'Open'}
          </a>
        </div>
      )}
      <DiscoverLine profile={profile} />
      {!sent && open > 0 && (
        <div className="db-foot">
          <button type="button" className="btn btn-sm" onClick={() => actions.openPractice({ screen: 'hub' })}>
            Practise for one
          </button>
        </div>
      )}
    </section>
  );
}

/* ───────────────────────── Posting matches ───────────────────────── */

function MatchesCard({ matches }: { matches: MatchSummary[] }) {
  const shown = matches.slice(0, 2);
  const now = useNow(60_000);

  return (
    <section className="panel db-card db-matches" aria-labelledby="db-match-h">
      <div className="db-card-head">
        <h2 className="kicker" id="db-match-h">
          Posting matches
        </h2>
        {matches.length > 2 && (
          <button type="button" className="db-link" onClick={() => actions.openMatch()}>
            All {matches.length} →
          </button>
        )}
      </div>
      {shown.length === 0 ? (
        <p className="db-muted">Paste a posting and see every requirement against your evidence, then a resume and cover note built only from cited facts.</p>
      ) : (
        <ul className="db-rows">
          {shown.map((m) => {
            const at = Date.parse(m.createdAt);
            return (
              <li key={m.id}>
                <button type="button" className="db-row" onClick={() => actions.openMatch({ screen: 'match', id: m.id })}>
                  <span className="db-row-text">
                    <span className="db-small">
                      <b>{m.title}</b>
                    </span>
                    <span className="db-fine">
                      {[m.company, Number.isFinite(at) ? timeAgo(at, now) : null].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  <span className={`stamp${m.band === 'strong' ? ' stamp-ok' : ' stamp-quiet'}`}>{BAND_LABEL[m.band]}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="db-foot">
        <button type="button" className="btn btn-sm" onClick={() => actions.openMatch()}>
          Match a posting
        </button>
      </div>
    </section>
  );
}

/* ───────────────────────── Practice ───────────────────────── */

function PracticeCard({ profile, load, plans: plansLoad }: { profile: Profile; load: Load<PracticeHistory>; plans: Load<PreparePlanSummary[]> }) {
  const history = load.status === 'ready' ? load.data : null;
  // newest first, as the server lists them
  const last = history?.sessions[0];
  const weak = history ? history.claims.filter((c) => c.best === 'shaky' || c.best === 'couldnt').length : 0;
  const lastAt = last ? Date.parse(last.createdAt) : NaN;
  const plans = plansLoad.status === 'ready' ? plansLoad.data.slice(0, 2) : [];

  return (
    <section className="panel db-card db-practice" aria-labelledby="db-pc-h" aria-busy={load.status === 'loading'}>
      <div className="db-card-head">
        <h2 className="kicker" id="db-pc-h">
          Practice and Prepare
        </h2>
        <button type="button" className="db-link" onClick={() => actions.openPractice({ screen: 'hub' })}>
          Open →
        </button>
      </div>
      {load.status === 'loading' && <p className="db-muted">Reading your sessions…</p>}
      {load.status === 'error' && <p className="db-muted">Your past sessions could not be read. The hub has them.</p>}
      {history && !last && <p className="db-muted">Questions about your own work, asked out loud. Credit only for what you can back up.</p>}
      {last && (
        <div className="db-line">
          <span className="db-small">
            <b>Last session</b> · {last.title}
            {Number.isFinite(lastAt) ? ` · ${dayLabel(last.createdAt.slice(0, 10))}` : ''}
          </span>
          <PipsLine verdicts={last.verdicts} counts={last.counts} />
        </div>
      )}
      {weak > 0 && (
        <div className="db-line">
          <span className="db-small">
            <b>{plural(weak, 'claim')} to re-drill</b>
          </span>
          <span className="db-fine">The ones you did not fully back up, each brought back where it wobbled.</span>
        </div>
      )}
      {plans.length > 0 && (
        <ul className="db-rows" aria-label="Your plans">
          {plans.map((p) => (
            <li key={p.id}>
              <button type="button" className="db-row" onClick={() => openPlan(profile, p)}>
                <span className="db-row-text">
                  <span className="db-small">
                    <b>Plan · {p.roleTitle}</b>
                    {p.requirement ? ` · ${p.requirement}` : ''}
                  </span>
                  <span className="db-fine">Started {dayLabel(p.createdAt.slice(0, 10))}</span>
                </span>
                <span className="db-fine">Open →</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {plansLoad.status === 'ready' && plans.length === 0 && last && <p className="db-fine">Ask how to prepare for a role and the answer becomes a plan, kept here.</p>}
      <div className="db-foot">
        <button type="button" className={`btn btn-sm${last ? '' : ' btn-primary'}`} onClick={() => actions.openPractice({ screen: 'hub' })}>
          {last ? 'Practise again' : 'Start practising'}
        </button>
        {weak > 0 && (
          <button type="button" className="btn btn-sm" onClick={() => actions.openPractice({ screen: 'redrill' })}>
            Re-drill
          </button>
        )}
      </div>
    </section>
  );
}

/* ───────────────────────── Discover, as a line of the applications card ───────────────────────── */

function DiscoverLine({ profile }: { profile: Profile }) {
  const area = useMemo(() => readArea(profile.id), [profile.id]);
  const { load } = useLoad<JobSearchRun | null>((signal) => getLatestRun(profile.id, area.city, area.remote !== false, signal), [profile.id, area.city, area.remote]);
  const run = load.status === 'ready' ? load.data : null;
  const good = run ? run.jobs.filter((j) => j.fit === 'strong' || j.fit === 'good').length : 0;
  const now = useNow(60_000);
  const at = run ? Date.parse(run.createdAt) : NaN;

  return (
    <div className="db-line db-line-act" aria-busy={load.status === 'loading'}>
      <span className="db-small">
        <b>Discover</b>
        {run ? (
          <>
            {' · '}
            <span className="numeral">{run.jobs.length}</span> {plural(run.jobs.length, 'role', 'roles')} found{Number.isFinite(at) ? ` ${timeAgo(at, now)}` : ''} · <span className="numeral">{good}</span>{' '}
            {FIT_WORD.good.toLowerCase()}
            {good === 1 ? '' : 's'}
          </>
        ) : (
          <span className="db-fine"> · {load.status === 'loading' ? 'looking for your last search…' : "roles like yours, from employers' own job sites"}</span>
        )}
      </span>
      <button type="button" className="btn btn-sm" onClick={() => actions.openDiscover({ tab: 'roles' })}>
        {run ? 'See the roles' : 'Search with AI'}
      </button>
    </div>
  );
}

/* ───────────────────────── The Leafout Weekly, as one line ───────────────────────── */

function WeeklyLine() {
  const { user } = useMe();
  const { subscription } = useSubscription(user?.id);
  const subscribed = !!subscription && subscription.status !== 'off';
  const unread = subscribed ? subscription.unread : 0;

  return (
    <section className="db-weekly" aria-labelledby="db-wk-h">
      <h2 className="kicker" id="db-wk-h">
        The Leafout Weekly
      </h2>
      <p className="db-small db-weekly-text">
        {subscribed
          ? `Every ${subscription.day === 'mon' ? 'Monday' : subscription.day}${subscription.email ? ` · ${subscription.email}` : ''}`
          : 'Openings that fit your graph, your week and news from your field. Every Monday, every line cited.'}
      </p>
      {unread > 0 && <span className="stamp">{unread} new</span>}
      <button type="button" className={`btn btn-sm${subscribed ? '' : ' btn-primary'}`} onClick={() => actions.openWeekly()}>
        {subscribed ? "Read this week's" : 'Subscribe, free'}
      </button>
    </section>
  );
}
