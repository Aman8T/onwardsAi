/**
 * Dev gallery (`/dev/discover`, development builds only): every Discover state on fixtures, with no server and no
 * account. Pick a state in the bar, or link straight to one:
 *
 *   /dev/discover?state=full&open=j1
 *   states: full · nolocality · remote · moving · ai · ai-running · ai-done · ai-empty · ai-error · ai-limit · ai-paused ·
 *           first · first-waited · nocity · empty · weak · weak-shown · thin · coimbatore · loading · error ·
 *           applications · blocked · origin
 *
 * `full`, `nolocality`, `remote` and `moving` are live: the lenses, lanes, order switch and area pickers really
 * filter and re-count the Bengaluru table in fixtures.ts, so the stops and the suggestion move together. Every other
 * state is a fixed answer (its lenses change nothing). Saving a role writes to this browser's storage under a
 * gallery profile id; "See full match", "Paste a posting" and "Add a project or link" are stubbed and say so.
 *
 * "Search with AI" never reaches the server here: every state hands the panel a scripted stream (`jobsearchFixtures.ts`)
 * that plays through the real reader. `ai` is idle (press the button to watch a whole run in about ten seconds); the
 * other `ai-…` states start by themselves and sit in one place: running part-way, done with five roles, done with
 * nothing, stopped by an error, refused for a limit (429), refused because the AI is paused for today (503).
 */
import { useEffect, useMemo, useState } from 'react';
import type { DiscoverResponse } from '../../../shared/discover';
import { Brand } from '../../components/Brand';
import { DiscoverScreen } from '../DiscoverScreen';
import type { DiscoverScreenProps } from '../DiscoverScreen';
import { OriginBarView } from '../OriginBar';
import { Blocked } from '../States';
import { DEFAULT_QUERY, mergeQuery } from '../route';
import type { DiscoverQuery } from '../route';
import { seedTracker } from '../tracker';
import type { JobSearchOptions } from '../jobsearch/useJobSearch';
import { CITIES, allWeakResponse, allWeakShownResponse, coimbatoreResponse, emptyNearResponse, firstRefreshResponse, noCityResponse, riyaResponse, thinResponse, trackedFixtures } from './fixtures';
import { FAILS_MIDWAY, FULL_RUN, MIDWAY, NOTHING_FITTED, refused, scriptedStream } from './jobsearchFixtures';
import '../../styles/discover.css';

const STATES = [
  ['full', 'Bengaluru · full result'],
  ['nolocality', 'Bengaluru · no area yet'],
  ['remote', 'Remote lane'],
  ['moving', 'A city to move to'],
  ['ai', 'Search with AI · idle'],
  ['ai-running', 'Search with AI · running'],
  ['ai-done', 'Search with AI · done'],
  ['ai-empty', 'Search with AI · nothing fitted'],
  ['ai-error', 'Search with AI · error'],
  ['ai-limit', 'Search with AI · limit (429)'],
  ['ai-paused', 'Search with AI · paused (503)'],
  ['first', 'First refresh'],
  ['first-waited', 'First refresh · still reading'],
  ['nocity', 'No city yet'],
  ['empty', 'Empty near lane'],
  ['weak', 'All weak'],
  ['weak-shown', 'All weak · shown'],
  ['thin', 'Thin profile'],
  ['coimbatore', 'Coimbatore · coverage note'],
  ['loading', 'Loading'],
  ['error', 'Fetch error'],
  ['applications', 'Your applications'],
  ['blocked', 'Sample profile'],
  ['origin', 'Match origin bar'],
] as const;
type StateId = (typeof STATES)[number][0];
const isState = (s: string | null): s is StateId => STATES.some(([id]) => id === s);

const GALLERY_PROFILE = 'dev-fixture';
const GALLERY_APPS = 'dev-fixture-applications';
const noop = () => {};

/** The Search-with-AI panel's stand-in for the server, by state. Only `ai` waits for the button. */
function jobSearchFor(id: StateId): JobSearchOptions {
  switch (id) {
    case 'ai-running':
      return { stream: scriptedStream(MIDWAY, 0), autoStart: true };
    case 'ai-done':
      return { stream: scriptedStream(FULL_RUN, 0), autoStart: true };
    case 'ai-empty':
      return { stream: scriptedStream(NOTHING_FITTED, 0), autoStart: true };
    case 'ai-error':
      return { stream: scriptedStream(FAILS_MIDWAY, 0), autoStart: true };
    case 'ai-limit':
      return { stream: scriptedStream(refused(429, 'You have searched a lot in a short while. Please wait a little before the next search.', 42)), autoStart: true };
    case 'ai-paused':
      return { stream: scriptedStream(refused(503, 'Search with AI is paused for today. It starts again tomorrow.')), autoStart: true };
    default:
      return { stream: scriptedStream(FULL_RUN, 1) };
  }
}

function readParams(): { state: StateId; open?: string; query: DiscoverQuery } {
  const p = new URLSearchParams(window.location.search);
  const state = p.get('state');
  const id: StateId = isState(state) ? state : 'full';
  const q = mergeQuery(DEFAULT_QUERY, {
    ...(p.get('reach') ? { reach: p.get('reach') as DiscoverQuery['reach'] } : {}),
    ...(p.get('breadth') ? { breadth: p.get('breadth') as DiscoverQuery['breadth'] } : {}),
    ...(p.get('lane') ? { lane: p.get('lane') as DiscoverQuery['lane'] } : {}),
    ...(p.get('order') ? { order: p.get('order') as DiscoverQuery['order'] } : {}),
  });
  return { state: id, open: p.get('open') ?? undefined, query: q };
}

export function DiscoverGallery() {
  const [{ state, open, query }, setPick] = useState(readParams);
  const [note, setNote] = useState('');

  const go = (next: StateId) => {
    setPick({ state: next, query: DEFAULT_QUERY });
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
      <Stage key={state} id={state} open={open} initial={query} onNote={setNote} />
    </div>
  );
}

function Stage({ id, open, initial, onNote }: { id: StateId; open?: string; initial: DiscoverQuery; onNote: (s: string) => void }) {
  // the live states filter the Bengaluru table; the rest are fixed answers
  const startQuery = (): DiscoverQuery => {
    const base = mergeQuery(initial, { city: 'Bengaluru', ...(id === 'nolocality' ? {} : { locality: 'HSR Layout' }) });
    if (id === 'remote') return mergeQuery(base, { lane: 'remote' });
    if (id === 'moving') return mergeQuery(base, { otherCity: 'Pune', lane: 'city' });
    if (id === 'applications') return mergeQuery(base, { tab: 'applications' });
    return base;
  };
  const [query, setQuery] = useState<DiscoverQuery>(startQuery);
  const jobSearch = useMemo(() => jobSearchFor(id), [id]);
  const [declined, setDeclined] = useState(false);

  useEffect(() => {
    if (id === 'applications') seedTracker(GALLERY_APPS, trackedFixtures());
  }, [id]);

  const data: DiscoverResponse | null = useMemo(() => {
    switch (id) {
      case 'full':
      case 'nolocality':
      case 'remote':
      case 'moving':
      case 'ai':
      case 'ai-running':
      case 'ai-done':
      case 'ai-empty':
      case 'ai-error':
      case 'ai-limit':
      case 'ai-paused':
        return riyaResponse({ breadth: query.breadth, lane: query.lane, order: query.order, reach: query.reach, otherCity: query.otherCity, city: query.city, locality: query.locality });
      case 'first':
      case 'first-waited':
        return firstRefreshResponse();
      case 'nocity':
        return noCityResponse();
      case 'empty':
        return emptyNearResponse();
      case 'weak':
        return allWeakResponse();
      case 'weak-shown':
        return allWeakShownResponse();
      case 'thin':
        return thinResponse();
      case 'coimbatore':
        return coimbatoreResponse();
      default:
        return null;
    }
  }, [id, query]);

  if (id === 'origin') {
    return (
      <>
        <OriginBarView
          origin={{
            listingId: 'j1',
            title: 'Senior Frontend Engineer',
            employer: 'Kestrel Pay',
            applyUrl: 'https://kestrelpay.in/kestrel-pay/j1',
            applyHost: 'kestrelpay.in',
            sourceLabel: "Employer's own listing, checked today",
            discoverHref: '/dev/discover?state=full',
          }}
          onBack={() => onNote('Gallery: "← Discover" would go back to the list with the same lens.')}
        />
        <main className="dc-main">
          <p className="dc-lede">This bar sits above a match that was started from a Discover listing. The match underneath is the ordinary posting match; see /dev/match.</p>
        </main>
      </>
    );
  }

  const live = id === 'full' || id === 'nolocality' || id === 'remote' || id === 'moving' || id.startsWith('ai');
  const status = id === 'loading' ? 'loading' : id === 'error' ? 'error' : 'ready';

  const props: DiscoverScreenProps = {
    profileId: id === 'applications' ? GALLERY_APPS : GALLERY_PROFILE,
    query,
    state: {
      status,
      data,
      error: id === 'error' ? 'Discover is having trouble right now. Please try again in a moment.' : undefined,
      waited: id === 'first-waited',
      reload: () => onNote('Gallery: this would ask the server again.'),
    },
    cities: CITIES,
    localityDeclined: declined,
    onQuery: (patch) => {
      if (!live && !('tab' in patch)) onNote('Gallery: this state is a fixed answer, so the lens changes nothing here. Try "Bengaluru · full result".');
      setQuery((q) => mergeQuery(q, patch));
    },
    onArea: (choice) => {
      if (!live) return onNote('Gallery: this state is a fixed answer, so the area changes nothing here.');
      setQuery((q) => mergeQuery(q, { city: choice.city ?? 'Bengaluru', locality: choice.locality, otherCity: choice.otherCity, reach: choice.locality ? '10' : undefined }));
    },
    onDeclineLocality: () => setDeclined(true),
    onPaste: () => onNote('Gallery: "Paste a posting" would open the paste screen.'),
    onAddProject: () => onNote('Gallery: "Add a project or link" would open the analysis screen.'),
    onSeeMatch: () =>
      new Promise((resolve) => {
        window.setTimeout(() => resolve('Gallery: no match is started here. In the app this opens the posting match.'), 700);
      }),
    onSeeMatchUrl: () =>
      new Promise((resolve) => {
        window.setTimeout(() => resolve("Gallery: no match is started here. In the app this opens the posting match from the role's address."), 700);
      }),
    jobSearch,
    matchLocked: false,
    onOpenMatch: (matchId) => onNote(`Gallery: "Open match" would open /match/${matchId}.`),
    check: id === 'applications' ? async (listing) => (listing === 'd4' ? 'gone' : 'open') : undefined,
    initialOpenId: open,
    blocked:
      id === 'blocked' ? (
        <Blocked title="This is the sample profile." action={<button type="button" className="btn btn-primary dc-btn" onClick={noop}>Analyse my resume</button>}>
          Roles near you are matched to your own evidence, so there is nothing of yours to look for yet. Analyse your resume first.
        </Blocked>
      ) : undefined,
  };

  return <DiscoverScreen {...props} />;
}
