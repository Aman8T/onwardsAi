/**
 * Dev gallery (`/dev/discover`, development builds only): every Discover state on fixtures, with no server and no
 * account. Pick a state in the bar, or link straight to one:
 *
 *   /dev/discover?state=focus
 *
 * States, in the order of the Discover canvas:
 *   start · loading · searching · results · stale · strong · focus · not-for-me · hidden · nothing · limit · paused ·
 *   error · sample · thin
 *   deck · deck-end · sheet · list · phone-searching   the phone's screens, drawn inside a 390 px frame
 *   prepare · share · compare · applications       the other screens of Discover, on the same run
 *   origin                                          the bar above a match that came from Discover
 *
 * The data is the real run of 2026-10-16 (`fixtures.ts`). "Search with AI" never reaches the server here: every state
 * hands the screen a scripted stream (`streams.ts`) that plays through the real reader, and a scripted loader for the
 * last results. In `start` and `results` the button plays a whole run in about twelve seconds; `searching` is held
 * eight seconds in, with Stop working; `limit`, `paused` and `error` play their failure by themselves. Saving and
 * hiding write to this browser's storage under a gallery profile id; "See how you fit" and the buttons that leave
 * Discover are stubbed and say so.
 */
import { useMemo, useState } from 'react';
import type { AiJob, JobSearchRun } from '../../../shared/jobsearch';
import { Brand } from '../../components/Brand';
import { applicationsKey } from '../applications/tracker';
import { DiscoverHeader } from '../DiscoverHeader';
import { DiscoverScreen } from '../DiscoverScreen';
import type { DiscoverScreenProps } from '../DiscoverScreen';
import { seedAvoid } from '../jobsearch/avoid';
import type { LatestLoader } from '../jobsearch/api';
import { OriginBarView } from '../OriginBar';
import { DEFAULT_QUERY, mergeQuery } from '../route';
import type { DiscoverQuery } from '../route';
import type { DiscoverApply } from '../roles/parts';
import { CITIES, DRIVETRAIN, GITLAB, HEADOUT, NOTHING_FUNNEL, OPENFX, PARTICLE, ROLES, SONICWALL, ago, lastRun, trackedFixtures } from './fixtures';
import { FAILS_MIDWAY, FULL_RUN, MIDWAY, refused, scriptedStream } from './streams';
import type { Beat } from './streams';
import { REFERRAL_FIXTURE, ROLE_PREP_FIXTURE } from './roleFixtures';
import '../../styles/discover.css';

const STATES = [
  ['start', 'Start'],
  ['loading', 'Opening'],
  ['searching', 'Searching'],
  ['results', 'Results'],
  ['stale', 'Results · old'],
  ['strong', 'Results · a strong fit'],
  ['focus', 'Focus'],
  ['not-for-me', 'Not for me'],
  ['hidden', 'Hidden'],
  ['nothing', 'Nothing fitted'],
  ['limit', 'Limit (429)'],
  ['paused', 'Paused (503)'],
  ['error', 'Error'],
  ['sample', 'Sample profile'],
  ['thin', 'Thin profile'],
  ['deck', 'Phone · deck'],
  ['deck-end', 'Phone · deck, all seen'],
  ['sheet', 'Phone · sheet'],
  ['list', 'Phone · list'],
  ['phone-searching', 'Phone · searching'],
  ['prepare', 'Prepare'],
  ['share', 'Share'],
  ['compare', 'Compare'],
  ['applications', 'Your applications'],
  ['apply', 'Apply for me'],
  ['origin', 'Match origin bar'],
] as const;
type StateId = (typeof STATES)[number][0];
const isState = (s: string | null): s is StateId => STATES.some(([id]) => id === s);

interface Config {
  /** What `GET /api/jobsearch/latest` answers. */
  latest: 'run' | 'none' | 'old' | 'nothing' | 'pending' | 'strong' | 'apply';
  /** The stream behind the button (and, with `auto`, behind the first paint). */
  script?: Beat[];
  pace?: number;
  auto?: boolean;
  phone?: boolean;
  query?: Partial<DiscoverQuery>;
  initial?: DiscoverScreenProps['initial'];
  sample?: boolean;
  thin?: boolean;
  /** Roles hidden at the start, with their reasons. */
  hidden?: { job: AiJob; reason: 'senior' | 'stack' | 'location' | 'company' | 'applied' | 'other' }[];
}

const CONFIG: Record<Exclude<StateId, 'origin'>, Config> = {
  start: { latest: 'none' },
  loading: { latest: 'pending' },
  searching: { latest: 'run', script: MIDWAY, pace: 0, auto: true },
  results: { latest: 'run' },
  stale: { latest: 'old' },
  strong: { latest: 'strong' },
  focus: { latest: 'run', initial: { focusId: HEADOUT.id } },
  'not-for-me': { latest: 'run', initial: { popoverId: GITLAB.id } },
  hidden: { latest: 'run', hidden: [{ job: GITLAB, reason: 'senior' }] },
  nothing: { latest: 'nothing', script: FULL_RUN, query: { remote: false } },
  limit: { latest: 'run', script: refused(429, "You've used today's 3 searches. Searches reset at midnight UTC.", 42), auto: true },
  paused: { latest: 'run', script: refused(503, 'Search with AI is paused for today.'), auto: true },
  error: { latest: 'run', script: FAILS_MIDWAY, pace: 0, auto: true },
  sample: { latest: 'none', sample: true },
  thin: { latest: 'none', thin: true },
  deck: { latest: 'run', phone: true },
  'deck-end': { latest: 'run', phone: true, initial: { deckAt: 99 } },
  sheet: { latest: 'run', phone: true, initial: { focusId: HEADOUT.id } },
  list: { latest: 'run', phone: true, initial: { list: true } },
  'phone-searching': { latest: 'run', phone: true, script: MIDWAY, pace: 0, auto: true },
  prepare: { latest: 'run', query: { tab: 'prepare', run: lastRun().runId, job: HEADOUT.id } },
  share: { latest: 'run', initial: { share: { jobId: HEADOUT.id } } },
  compare: { latest: 'run', query: { tab: 'compare', ids: [HEADOUT.id, SONICWALL.id, DRIVETRAIN.id] } },
  applications: { latest: 'run', query: { tab: 'applications' } },
  apply: { latest: 'apply' },
};

/** A role on a Workday board: Apply for me says why not, and the employer's link stays the action. */
const HOLLIS: AiJob = {
  ...OPENFX,
  id: 'hollis-data',
  employer: 'Hollis Health',
  applyUrl: 'https://hollis.wd5.myworkdayjobs.com/en-US/careers/job/1',
  applyHost: 'hollis.wd5.myworkdayjobs.com',
};

/** The "Apply for me" state: one role offered, one being checked, one already sent, one on Workday. */
function galleryApply(onNote: (s: string) => void): DiscoverApply {
  return {
    status: { enabled: true, signedIn: true },
    signedIn: true,
    byListing: new Map([
      [PARTICLE.id, {
        id: 'dev-app-1', stage: 'sent', source: 'discover', ats: 'greenhouse', domain: PARTICLE.applyHost,
        jobUrl: PARTICLE.applyUrl, createdAt: ago(4), updatedAt: ago(4), needsCount: 0, hasScreenshot: false,
        company: PARTICLE.employer, title: PARTICLE.title, listingId: PARTICLE.id,
      }],
    ]),
    flows: new Map([[DRIVETRAIN.id, { kind: 'checking', phase: 'match', requirements: 18, step: 'Checked 3 of 18 requirements…' }]]),
    onApply: () => onNote('Gallery: Apply for me runs the start check, then the full match on the card.'),
    onCancel: () => onNote('Gallery: Cancel stops waiting on the card.'),
  };
}

/** What `GET /api/jobsearch/latest` answers for a state. */
function loaderFor(kind: Config['latest']): LatestLoader {
  return (_profile, _city, _remote, signal) => {
    if (kind === 'pending') {
      return new Promise((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true }));
    }
    const run = ((): JobSearchRun | null => {
      switch (kind) {
        case 'none':
          return null;
        case 'old':
          return lastRun({ createdAt: ago(3, 2) });
        case 'nothing':
          return lastRun({ jobs: [], funnel: NOTHING_FUNNEL, note: 'Four design roles are open in Bengaluru today, and all four ask for five or more years.' });
        case 'strong':
          return lastRun({ jobs: [{ ...HEADOUT, fit: 'strong' }, ...ROLES.slice(1)] });
        case 'apply':
          return lastRun({ jobs: [...ROLES.slice(0, 7), HOLLIS] });
        default:
          return lastRun();
      }
    })();
    return Promise.resolve(run);
  };
}

function readState(): StateId {
  const s = new URLSearchParams(window.location.search).get('state');
  return isState(s) ? s : 'results';
}

export function DiscoverGallery() {
  const [state, setState] = useState<StateId>(readState);
  const [note, setNote] = useState('');

  const go = (next: StateId) => {
    setState(next);
    setNote('');
    window.history.replaceState(null, '', `/dev/discover?state=${next}`);
  };

  return (
    <div className="dc-view">
      <header className="dc-header">
        <Brand />
        <span className="kicker">Dev only · Discover on fixtures, no server</span>
        <nav className="dc-nav dc-gallery-nav" aria-label="States">
          {STATES.map(([id, label]) => (
            <button key={id} type="button" className={`dc-navbtn${state === id ? ' is-current' : ''}`} aria-pressed={state === id} onClick={() => go(id)}>
              {label}
            </button>
          ))}
        </nav>
        <p className="dc-small dc-muted dc-devnote" role="status">
          {note}
        </p>
      </header>
      <Stage key={state} id={state} onNote={setNote} />
    </div>
  );
}

function Stage({ id, onNote }: { id: StateId; onNote: (s: string) => void }) {
  if (id === 'origin') {
    return (
      <>
        <OriginBarView
          origin={{
            listingId: HEADOUT.id,
            title: HEADOUT.title,
            employer: HEADOUT.employer,
            applyUrl: HEADOUT.applyUrl,
            applyHost: HEADOUT.applyHost,
            sourceLabel: "Employer's own listing, checked today",
            discoverHref: '/dev/discover?state=results',
          }}
          onBack={() => onNote('Gallery: "← Discover" would go back to the roles with the same place.')}
        />
        <main className="dc-main">
          <p className="dc-lede">This bar sits above a match that was started from a role Discover found. The match underneath is the ordinary posting match; see /dev/match.</p>
        </main>
      </>
    );
  }
  return <Screen id={id} onNote={onNote} />;
}

function Screen({ id, onNote }: { id: Exclude<StateId, 'origin'>; onNote: (s: string) => void }) {
  const cfg = CONFIG[id];
  const profileId = `dev-fixture-${id}`;
  const [frame, setFrame] = useState<HTMLElement | null>(null);

  // each state starts with the boards' four saved roles and what the state hides; nothing is read from an earlier visit
  useState(() => {
    try {
      window.localStorage.setItem(applicationsKey(profileId), JSON.stringify(trackedFixtures()));
    } catch {
      /* storage blocked: the list is empty */
    }
    seedAvoid(profileId, cfg.hidden?.map((h) => ({ job: h.job, reason: h.reason })) ?? []);
    return null;
  });

  const [query, setQuery] = useState<DiscoverQuery>(() => mergeQuery(DEFAULT_QUERY, cfg.query ?? {}));
  const source = useMemo(
    () => ({ stream: scriptedStream(cfg.script ?? FULL_RUN, cfg.pace ?? 1), loadLatest: loaderFor(cfg.latest), autoStart: cfg.auto }),
    [cfg],
  );

  const stub = (text: string) => new Promise<string | undefined>((resolve) => window.setTimeout(() => resolve(text), 700));

  const screen = (phone: boolean) => (
    <DiscoverScreen
      profileId={profileId}
      query={query}
      cities={CITIES}
      resumeCity="Bengaluru"
      resumeLocation="Bengaluru, India"
      signedIn={false}
      sample={!!cfg.sample}
      thin={!!cfg.thin}
      onQuery={(patch) => setQuery((q) => mergeQuery(q, patch))}
      onArea={(choice) =>
        setQuery((q) =>
          mergeQuery(q, {
            ...(choice.city !== undefined ? { city: choice.city ?? undefined } : {}),
            ...(choice.remote !== undefined ? { remote: choice.remote } : {}),
          }),
        )
      }
      onSeeMatch={() => stub("Gallery: no match is started here. In the app this opens the posting match with the role's posting.")}
      matchLocked={false}
      onAnalyse={() => onNote('Gallery: "Analyse my resume" would open the analysis screen.')}
      onAddProject={() => onNote('Gallery: "Add a project" would open the analysis screen.')}
      source={source}
      loadPrep={async () => ROLE_PREP_FIXTURE}
      loadReferral={async () => REFERRAL_FIXTURE}
      layout={phone ? { phone: true, frame } : undefined}
      initial={cfg.initial}
      apply={id === 'apply' ? galleryApply(onNote) : undefined}
    />
  );

  // a phone is shown inside a frame; its sheets go inside the frame too
  if (cfg.phone) {
    return (
      <div className="dc-frame" ref={setFrame}>
        {frame && screen(true)}
      </div>
    );
  }

  return (
    <>
      <DiscoverHeader profileId={profileId} tab={query.tab} onTab={(tab) => setQuery((q) => mergeQuery(q, { tab }))} />
      {screen(false)}
    </>
  );
}

