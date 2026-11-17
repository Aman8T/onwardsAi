/**
 * Dev page (`/dev/report`, development builds only): every state of the report's owner sheet on fixtures, with no
 * server and no account. Pick a state in the bar, or link straight to one:
 *
 *   /dev/report?state=shared        (`?scheme=dark` for the dark scheme, `?h=120` to pin the hue)
 *
 *   off · shared · copied · new-link     not shared, three roles; shared with the link; after a copy; the question
 *                                        before a new link replaces the old one
 *   making · refused                     "Make a link" pressed and the server has not answered; a change refused with
 *                                        the server's sentence (the checkbox goes back)
 *   thin · none                          one role with the near misses made public; no role at all
 *   hidden · all-off · no-orgs           two quotes hidden; every choice off; no employer name known (the warning)
 *   no-offer                             the paid step's link not configured: no "Get Day One" on the roles
 *   phone · phone-shared                 the sheet in a 390 px frame, the way it is a bottom sheet under 760 px
 *   sample · anonymous                   the graphs that cannot share: a sentence and one action, no switches
 *   loading · error                      the report is being opened; it would not open
 *
 * The checkboxes, the quote switches and the link buttons work (a scripted client keeps the choices). Buttons that
 * leave the sheet say where they would go.
 */
import { useEffect, useId, useMemo, useState } from 'react';
import type { ReportChoices } from '../../../shared/report';
import { Brand } from '../../components/Brand';
import { ReportBody } from '../ReportBody';
import type { ReportBodyProps } from '../ReportBody';
import { ALL_OFF, DEFAULTS, NEAR_MISSES, OFFER_URL, ROLES } from './fixtures';
import type { ViewOptions } from './fixtures';
import { mockClient } from './mock';
import type { MockMode } from './mock';
import '../../styles/report.css';

const STATES = [
  ['off', 'Not shared · three roles'],
  ['shared', 'Shared'],
  ['copied', 'Copied'],
  ['new-link', 'Make a new link'],
  ['making', 'Making the link'],
  ['refused', 'Save refused'],
  ['thin', 'One role · near misses'],
  ['none', 'No roles'],
  ['hidden', 'Two quotes hidden'],
  ['all-off', 'Every choice off'],
  ['no-orgs', 'No employer known'],
  ['no-offer', 'No paid step'],
  ['phone', 'Phone'],
  ['phone-shared', 'Phone · shared'],
  ['sample', 'The sample'],
  ['anonymous', 'Anonymous graph'],
  ['loading', 'Opening'],
  ['error', 'Would not open'],
] as const;
type StateId = (typeof STATES)[number][0];

const isState = (s: string | null): s is StateId => STATES.some(([id]) => id === s);

function readState(): StateId {
  const s = new URLSearchParams(window.location.search).get('state');
  return isState(s) ? s : 'off';
}

interface Scene {
  shared: boolean;
  choices: ReportChoices;
  mode: MockMode;
  opts: ViewOptions;
  narrow: boolean;
  /** A control to press once the report has opened, to reach a state that needs the person to act. */
  press?: string;
  initial?: ReportBodyProps['initial'];
}

function sceneFor(id: StateId): Scene {
  const base: Scene = { shared: false, choices: DEFAULTS, mode: 'stored', opts: { offerUrl: OFFER_URL }, narrow: false };
  const on: Scene = { ...base, shared: true };
  switch (id) {
    case 'off':
      return base;
    case 'shared':
      return on;
    case 'copied':
      return { ...on, initial: { copied: true } };
    case 'new-link':
      return { ...on, initial: { confirmLink: true } };
    case 'making':
      return { ...base, mode: 'hang-put', press: '.rp-actions .btn-primary' };
    case 'refused':
      return { ...on, mode: 'fail-put', press: '.rp-choice input' };
    case 'thin':
      return { ...on, choices: { ...DEFAULTS, showNearMisses: true }, opts: { ...base.opts, roles: ROLES.slice(0, 1) } };
    case 'none':
      return { ...on, choices: { ...DEFAULTS, showNearMisses: true }, opts: { ...base.opts, roles: [] } };
    case 'hidden':
      return { ...on, choices: { ...DEFAULTS, hiddenQuotes: ['q-qa-2', 'q-be-1'] } };
    case 'all-off':
      return { ...on, choices: ALL_OFF };
    case 'no-orgs':
      return { ...base, opts: { ...base.opts, orgsFound: 0 } };
    case 'no-offer':
      return { ...on, opts: {} };
    case 'phone':
      return { ...base, narrow: true };
    case 'phone-shared':
      return { ...on, choices: { ...DEFAULTS, openToWork: true, showNearMisses: true }, narrow: true };
    case 'sample':
      return { ...base, opts: { blocked: 'sample' } };
    case 'anonymous':
      return { ...base, opts: { blocked: 'anonymous' } };
    case 'loading':
      return { ...base, mode: 'hang-load' };
    case 'error':
      return { ...base, mode: 'fail-load' };
  }
}

export function ReportGallery() {
  const [state, setState] = useState<StateId>(readState);
  const [note, setNote] = useState('');

  const go = (next: StateId) => {
    setState(next);
    setNote('');
    window.history.replaceState(null, '', `/dev/report?state=${next}${window.location.search.includes('scheme=dark') ? '&scheme=dark' : ''}`);
  };

  return (
    <div className="rp-dev">
      <header className="rp-dev-header">
        <Brand />
        <span className="kicker">Dev only · the report's owner sheet on fixtures, no server</span>
        <nav className="rp-dev-nav" aria-label="States">
          {STATES.map(([id, label]) => (
            <button key={id} type="button" className={`rp-dev-navbtn${state === id ? ' is-current' : ''}`} aria-pressed={state === id} onClick={() => go(id)}>
              {label}
            </button>
          ))}
        </nav>
        <p className="rp-dev-note" role="status">
          {note}
        </p>
      </header>
      <main className="rp-dev-main">
        <Stage key={state} id={state} onNote={setNote} />
      </main>
    </div>
  );
}

function Stage({ id, onNote }: { id: StateId; onNote: (s: string) => void }) {
  const scene = useMemo(() => sceneFor(id), [id]);
  const client = useMemo(() => mockClient({ shared: scene.shared, choices: scene.choices }, scene.mode, scene.opts), [scene]);
  const titleId = useId();

  // the states that need the person to have acted: press the control once the report has opened
  useEffect(() => {
    if (!scene.press) return;
    const sel = scene.press;
    const timer = window.setTimeout(() => {
      document.querySelector<HTMLElement>(`.rp-dev-main ${sel}`)?.click();
    }, 900);
    return () => window.clearTimeout(timer);
  }, [scene]);

  const body = (
    <ReportBody
      profileId={`ravi-${id}`}
      client={client}
      narrow={scene.narrow}
      titleId={titleId}
      onClose={() => onNote('Gallery: closes the sheet and returns to the graph.')}
      onAnalyse={() => onNote('Gallery: leaves the sample and opens Home, to analyse a resume.')}
      onSignIn={() => onNote('Gallery: closes the sheet and opens the sign-in sheet.')}
      onPrepare={(roleId) => {
        const t = NEAR_MISSES.find((m) => m.id === roleId)?.title ?? roleId;
        onNote(`Gallery: closes the sheet and opens Prepare for ${t}.`);
      }}
      onResume={() => onNote('Gallery: closes the sheet and opens /resume.')}
      initial={scene.initial}
    />
  );

  if (scene.narrow) {
    return (
      <div className="rp-dev-frame" role="dialog" aria-labelledby={titleId}>
        {body}
      </div>
    );
  }
  return (
    <section className="panel rp-dev-sheet" role="dialog" aria-labelledby={titleId}>
      {body}
    </section>
  );
}
