/**
 * The dashboard (board "Dashboard · /dashboard · the entry point"): where the app opens once a graph exists. The skill
 * radar (NowGraph, whole, with its rail and popovers) and the ranked next roles live here, next to a summary from every
 * feature: applications and Apply for me, posting matches, Practice, the running Prepare plans, Discover's last search
 * and the Weekly. A role row opens that role's screen (GraphView); every other card leads into its feature.
 *
 * It opens on the read (views/dashboard/Read.tsx): who you are in three or four sentences written from the graph at
 * analysis time, each cited clause opening its evidence; under it what the resume left out, what moved since the last
 * analysis, and questions worth asking about yourself. Every number here is counted in code from what the features
 * already store; the read is the one thing a model phrased, and it is stored on the profile.
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
import { useMe } from '../auth/useMe';
import { RadarGlyph } from '../components/Radar';
import { STAGES, STAGE_LABEL, useTracker } from '../discover/applications/tracker';
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
import { actions } from '../store';
import { useSubscription } from '../weekly/useSubscription';
import { AskStrip, Celebrations, Hello, NotOnResume, SinceLast, usePreviousProfile } from './dashboard/Read';
import { achievements } from './dashboard/model';
import { GraphHeader } from './graph/GraphHeader';
import { NowGraph } from './graph/NowGraph';
import '../styles/shell.css';
import '../styles/dashboard.css';

/** Auto-apply stages that wait on the person (the Apply screen's "Waiting for you" and "Check this one"). */
const WAITING = new Set(['waiting_for_you', 'needs_signin', 'check_this_one']);

export function Dashboard({ profile, previous: forcedPrevious }: { profile: Profile; previous?: Profile | null }) {
  const found = usePreviousProfile(profile);
  // the playground hands a previous analysis in; the app finds its own
  const previous = forcedPrevious === undefined ? found : forcedPrevious;
  const data = useDashboardData(profile);
  const { items: tracked } = useTracker(profile.id);
  const { user } = useMe();
  const { subscription } = useSubscription(user?.id);
  const now = useNow(60_000);
  const shelf = useMemo(
    () =>
      achievements({
        profile,
        previous,
        tracked,
        history: data.history.status === 'ready' ? data.history.data : null,
        matches: data.matches,
        plans: data.plans.status === 'ready' ? data.plans.data : [],
        subscribed: !!subscription && subscription.status !== 'off',
        now,
      }),
    [profile, previous, tracked, data.history, data.matches, data.plans, subscription, now],
  );
  return (
    <div className="gv-view db-view">
      <GraphHeader profile={profile} current="dashboard" />
      <SaveInvite profile={profile} />

      <main className="db-main">
        {/* the person's own space: the greeting, one line of the read at a time, what is worth celebrating */}
        <Hello profile={profile} />
        <Celebrations items={shelf} />
        <NotOnResume profile={profile} />
        <SinceLast profile={profile} previous={previous} />
        <AskStrip profile={profile} />

        {/* NOW: the skill radar, whole, with its rail and its popovers */}
        <section className="panel db-now" aria-label="Current role: skill radar">
          <NowGraph profile={profile} />
        </section>

        <div className="db-grid">
          <NextRoles profile={profile} />
          <ApplicationsCard profile={profile} />
          <MatchesCard matches={data.matches} />
          <PracticeCard load={data.history} />
          <PrepareCard profile={profile} load={data.plans} />
          <DiscoverCard profile={profile} />
          <WeeklyCard />
        </div>
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

function ApplicationsCard({ profile }: { profile: Profile }) {
  const { items } = useTracker(profile.id);
  const apply = useApplications();
  const sent = applyOn(apply.status) && apply.ready;
  const counts = STAGES.map((stage) => ({ stage, n: items.filter((t) => t.stage === stage).length }));
  // what is moving: an interview first, else the newest application, else the newest saved role
  const moving = items.find((t) => t.stage === 'interviewing') ?? items.find((t) => t.stage === 'applied') ?? items.find((t) => t.stage === 'saved');
  const waiting = sent ? apply.applications.filter((a) => WAITING.has(a.stage)).length : 0;
  const open = items.filter((t) => t.stage !== 'closed').length;

  return (
    <section className="panel db-card" aria-labelledby="db-apps-h">
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
  const shown = matches.slice(0, 3);
  const now = useNow(60_000);

  return (
    <section className="panel db-card" aria-labelledby="db-match-h">
      <div className="db-card-head">
        <h2 className="kicker" id="db-match-h">
          Posting matches
        </h2>
        {matches.length > 3 && (
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
                  <span className="chip">{BAND_LABEL[m.band]}</span>
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

function PracticeCard({ load }: { load: Load<PracticeHistory> }) {
  const history = load.status === 'ready' ? load.data : null;
  // newest first, as the server lists them
  const last = history?.sessions[0];
  const weak = history ? history.claims.filter((c) => c.best === 'shaky' || c.best === 'couldnt').length : 0;
  const lastAt = last ? Date.parse(last.createdAt) : NaN;

  return (
    <section className="panel db-card" aria-labelledby="db-pc-h" aria-busy={load.status === 'loading'}>
      <div className="db-card-head">
        <h2 className="kicker" id="db-pc-h">
          Practice
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

/* ───────────────────────── Prepare ───────────────────────── */

function PrepareCard({ profile, load }: { profile: Profile; load: Load<PreparePlanSummary[]> }) {
  const plans = load.status === 'ready' ? load.data : [];
  const shown = plans.slice(0, 3);

  /** Reopen a plan the way the graph seeded it: the role's question, or the gap's. */
  const open = (p: PreparePlanSummary) => {
    const role = profile.roles.find((r) => r.id === p.roleId);
    if (!role) return;
    const req = p.requirement ? role.requirements.find((r) => r.name === p.requirement) : undefined;
    const a = req ? requirementAction(req, role) : roleAction(role, roleStanding(profile, role.id));
    // the same seed the graph uses, so an existing thread for this role (or gap) reopens with its stored plan
    actions.openChat(a.origin, a.question, { roleId: role.id, ...(req ? { requirement: req.name } : {}) });
  };

  return (
    <section className="panel db-card" aria-labelledby="db-prep-h" aria-busy={load.status === 'loading'}>
      <div className="db-card-head">
        <h2 className="kicker" id="db-prep-h">
          Prepare
        </h2>
        {plans.length > 3 && <span className="db-fine">{plural(plans.length, 'plan')}</span>}
      </div>
      {load.status === 'loading' && <p className="db-muted">Reading your plans…</p>}
      {load.status === 'error' && <p className="db-muted">Your plans could not be read just now.</p>}
      {load.status === 'ready' && shown.length === 0 && (
        <p className="db-muted">Ask how to prepare for a role and the answer becomes a plan: where you stand, the one move, the weeks.</p>
      )}
      {shown.length > 0 && (
        <ul className="db-rows">
          {shown.map((p) => (
            <li key={p.id}>
              <button type="button" className="db-row" onClick={() => open(p)}>
                <span className="db-row-text">
                  <span className="db-small">
                    <b>{p.roleTitle}</b>
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
      {load.status === 'ready' && shown.length === 0 && (
        <div className="db-foot">
          <button type="button" className="btn btn-sm" onClick={() => actions.openRole(null)}>
            Pick a role
          </button>
        </div>
      )}
    </section>
  );
}

/* ───────────────────────── Discover ───────────────────────── */

function DiscoverCard({ profile }: { profile: Profile }) {
  const area = useMemo(() => readArea(profile.id), [profile.id]);
  const { load } = useLoad<JobSearchRun | null>((signal) => getLatestRun(profile.id, area.city, area.remote !== false, signal), [profile.id, area.city, area.remote]);
  const run = load.status === 'ready' ? load.data : null;
  const good = run ? run.jobs.filter((j) => j.fit === 'strong' || j.fit === 'good').length : 0;
  const now = useNow(60_000);
  const at = run ? Date.parse(run.createdAt) : NaN;

  return (
    <section className="panel db-card db-half" aria-labelledby="db-dc-h" aria-busy={load.status === 'loading'}>
      <h2 className="kicker" id="db-dc-h">
        Discover
      </h2>
      {run ? (
        <>
          <p className="db-small">
            <span className="numeral db-big">{run.jobs.length}</span> {plural(run.jobs.length, 'role', 'roles')} found{Number.isFinite(at) ? ` ${timeAgo(at, now)}` : ''} ·{' '}
            <span className="numeral">{good}</span> {FIT_WORD.good.toLowerCase()}
            {good === 1 ? '' : 's'}
          </p>
          <p className="db-fine">
            {[run.city, run.remote ? 'remote in India' : null].filter(Boolean).join(' · ') || 'India'} · employers' own boards only
          </p>
        </>
      ) : (
        <p className="db-muted">
          {load.status === 'loading' ? 'Looking for your last search…' : "Claude searches employers' own job sites for roles like yours and checks each posting against your evidence."}
        </p>
      )}
      <div className="db-foot">
        <button type="button" className="btn btn-sm" onClick={() => actions.openDiscover({ tab: 'roles' })}>
          {run ? 'See the roles' : 'Search with AI'}
        </button>
      </div>
    </section>
  );
}

/* ───────────────────────── The Leafout Weekly ───────────────────────── */

function WeeklyCard() {
  const { user } = useMe();
  const { subscription } = useSubscription(user?.id);
  const subscribed = !!subscription && subscription.status !== 'off';
  const unread = subscribed ? subscription.unread : 0;

  return (
    <section className="panel db-card db-half" aria-labelledby="db-wk-h">
      <div className="db-card-head">
        <h2 className="kicker" id="db-wk-h">
          The Leafout Weekly
        </h2>
        {unread > 0 && <span className="chip">{unread} new</span>}
      </div>
      <p className="db-small">{subscribed ? 'Openings that fit your graph, your week and news from your field.' : 'Openings that fit your graph, your week and news from your field. Every Monday, every line cited.'}</p>
      {subscribed && subscription.email && <p className="db-fine">Every {subscription.day === 'mon' ? 'Monday' : subscription.day} · {subscription.email}</p>}
      <div className="db-foot">
        <button type="button" className={`btn btn-sm${subscribed ? '' : ' btn-primary'}`} onClick={() => actions.openWeekly()}>
          {subscribed ? "Read this week's" : 'Subscribe, free'}
        </button>
      </div>
    </section>
  );
}
