/**
 * Dev page (`/dev/weekly`, development builds only): every state of The Leafout Weekly on fixtures, with no server and no
 * account. Pick a state in the bar, or link straight to one:
 *
 *   /dev/weekly?state=active        (`?scheme=dark` for the dark scheme, `?h=120` to pin the hue)
 *
 *   signed-out    the sample edition and "Sign in to subscribe"
 *   new           signed in, never subscribed: the panel with "Subscribe, free", the sample edition, "Read the latest edition first"
 *   active        subscribed: the latest edition, the panel, the archive with "1 new"
 *   paused        paused: the panel says until when and offers "Resume now"
 *   first-run     subscribed, nothing printed yet
 *   quiet         a week with no openings, no news and no progress
 *   one-job       only the lead story, no tip or quote
 *   progress      "Your week" with every row (layout test values, not a reader's)
 *   news          news items with links and dates
 *   loading · error   the two waiting states
 *   phone         the same screen in a 390 px frame
 *
 * Saving works against a fake that answers after a beat; the buttons that would leave the page say where they go.
 */
import { useMemo, useState } from 'react';
import type { WeeklyEdition, WeeklySubscription, WeeklySubscriptionUpdate } from '../../../shared/weekly';
import { Brand } from '../../components/Brand';
import { FULL_PROGRESS_EDITION, NEWS_LINKED_EDITION, ONE_JOB_EDITION, QUIET_EDITION, SAMPLE_ARCHIVE, SAMPLE_EDITION } from '../fixtures';
import { WeeklyRoom } from '../WeeklyRoom';
import type { WeeklyRoomProps } from '../WeeklyRoom';
import '../../styles/weekly.css';

const STATES = [
  ['signed-out', 'Signed out'],
  ['new', 'Never subscribed'],
  ['active', 'Subscribed'],
  ['paused', 'Paused'],
  ['first-run', 'First run'],
  ['quiet', 'Quiet week'],
  ['one-job', 'One opening'],
  ['progress', 'Full progress'],
  ['news', 'News linked'],
  ['loading', 'Loading'],
  ['error', 'Error'],
  ['phone', 'Phone'],
] as const;
type StateId = (typeof STATES)[number][0];

const isState = (s: string | null): s is StateId => STATES.some(([id]) => id === s);

const ACTIVE: WeeklySubscription = {
  status: 'active',
  email: 'riya@example.com',
  day: 'mon',
  sections: ['jobs', 'progress', 'news', 'tip'],
  unread: 1,
};
const NEW: WeeklySubscription = { ...ACTIVE, status: 'off', unread: 0 };
const PAUSED: WeeklySubscription = {
  ...ACTIVE,
  status: 'paused',
  pausedUntil: '2026-11-25',
  unread: 0,
};

const nap = (ms: number) => new Promise((r) => setTimeout(r, ms));

function readState(): StateId {
  const s = new URLSearchParams(window.location.search).get('state');
  return isState(s) ? s : 'active';
}

export function WeeklyGallery() {
  const [state, setState] = useState<StateId>(readState);
  const [note, setNote] = useState('');

  const go = (next: StateId) => {
    setState(next);
    setNote('');
    window.history.replaceState(null, '', `/dev/weekly?state=${next}${window.location.search.includes('scheme=dark') ? '&scheme=dark' : ''}`);
  };

  return (
    <div className="wk-dev">
      <header className="wk-dev-header">
        <Brand />
        <span className="kicker">Dev only · The Leafout Weekly on fixtures, no server</span>
        <nav className="wk-dev-nav" aria-label="States">
          {STATES.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`wk-dev-navbtn${state === id ? ' is-current' : ''}`}
              aria-pressed={state === id}
              onClick={() => go(id)}
            >
              {label}
            </button>
          ))}
        </nav>
        <p className="wk-dev-note" role="status">
          {note}
        </p>
      </header>
      <main className="wk-main">
        <Stage key={state} id={state} onNote={setNote} />
      </main>
    </div>
  );
}

const EDITIONS: Record<string, WeeklyEdition> = Object.fromEntries(
  SAMPLE_ARCHIVE.map((a) => [
    a.id,
    {
      ...SAMPLE_EDITION,
      id: a.id,
      issue: a.issue,
      weekOf: a.weekOf,
      headline: a.headline.startsWith('[') ? SAMPLE_EDITION.headline : a.headline,
    },
  ]),
);

function Stage({ id, onNote }: { id: StateId; onNote: (s: string) => void }) {
  const [sub, setSub] = useState<WeeklySubscription>(id === 'new' ? NEW : id === 'paused' ? PAUSED : ACTIVE);
  const [edition, setEdition] = useState<WeeklyEdition | undefined>(undefined);
  const [previewed, setPreviewed] = useState(false);

  const save = useMemo(
    () =>
      async (u: WeeklySubscriptionUpdate): Promise<WeeklySubscription> => {
        await nap(400);
        const next: WeeklySubscription = {
          ...sub,
          status: u.status,
          day: u.day,
          sections: u.sections,
          pausedUntil: u.status === 'paused' ? '2026-11-25' : undefined,
          unread: sub.unread,
        };
        return next;
      },
    [sub],
  );

  const archive = SAMPLE_ARCHIVE.map((a) => ({
    ...a,
    read: a.read || edition?.id === a.id,
  }));
  const firstRun = id === 'first-run';
  const latest: WeeklyEdition =
    id === 'quiet'
      ? QUIET_EDITION
      : id === 'one-job'
        ? ONE_JOB_EDITION
        : id === 'progress'
          ? FULL_PROGRESS_EDITION
          : id === 'news'
            ? NEWS_LINKED_EDITION
            : SAMPLE_EDITION;
  const onScreen = edition ?? (id === 'signed-out' || id === 'new' || firstRun || id === 'loading' || id === 'error' ? undefined : latest);

  const base: WeeklyRoomProps = {
    mode: 'ready',
    subscription: sub,
    onSaved: setSub,
    save,
    editions: id === 'signed-out' || id === 'new' || firstRun ? [] : archive,
    edition: previewed && id === 'new' ? latest : onScreen,
    onOpenEdition: (e) => {
      setEdition(EDITIONS[e] ?? latest);
      onNote(`Gallery: opens /weekly?e=${e} (the address follows; here the fixture swaps in place).`);
    },
    onPreview: async () => {
      onNote('Gallery: POST /api/weekly/editions/preview, answered after a beat.');
      await nap(900);
      setPreviewed(true);
    },
  };

  const props: WeeklyRoomProps =
    id === 'signed-out'
      ? {
          ...base,
          mode: 'signed-out',
          subscription: undefined,
          onSignIn: () => onNote('Gallery: opens the sign-in sheet (next: /weekly).'),
        }
      : id === 'loading'
        ? { ...base, mode: 'loading' }
        : id === 'error'
          ? {
              ...base,
              mode: 'error',
              error: "We couldn't reach Leafout. Check your connection and try again.",
              onRetry: () => onNote('Gallery: asks for the subscription again.'),
            }
          : base;

  const room = <WeeklyRoom {...props} />;
  return id === 'phone' ? <div className="wk-dev-frame scroll">{room}</div> : room;
}
