/**
 * Dev page (`/dev/card`, development builds only): every state of the radar card's share sheet on fixtures, with no
 * server and no account. Pick a state in the bar, or link straight to one:
 *
 *   /dev/card?state=github        (`?scheme=dark` for the dark scheme, `?h=120` to pin the hue)
 *
 *   off · making · refused    sharing off; the switch just turned on and the server has not answered; a save refused with
 *                             the server's sentence (the switch goes back)
 *   github · github-match     the GitHub README tab; with "Match the page" the snippet is a `<picture>` element
 *   linkedin · link · copied  the other two tabs; the copy button after a copy
 *   light · dark · transparent · match    the theme menu on each theme
 *   role                      three skills named and a role as the lighter outline
 *   bars · many               two areas (bars, no leaf); eight areas with long names
 *   new-link                  the question before a new link replaces the old one
 *   image-failed              the real image does not load (this page has no server for /r/…): the card is drawn here
 *   phone · phone-off         the sheet in a 390 px frame, the way it is a bottom sheet under 760 px
 *   sample · anonymous        the graphs that cannot share: a sentence and one action, no switch
 *   loading · error           the card is being opened; it would not open
 *
 * The switch, the checkboxes and the menus work (a scripted client keeps the settings). Outside `image-failed` the card is
 * drawn here in the page's colours, because the real one is an image the server draws: the theme choice shows in the menu
 * and in the snippet, not in the picture. Buttons that leave the sheet say where they would go.
 */
import { useEffect, useId, useMemo, useState } from 'react';
import type { CardSettings } from '../../../shared/card';
import { Brand } from '../../components/Brand';
import { ShareBody } from '../ShareBody';
import type { ShareBodyProps } from '../ShareBody';
import { OFF, MANY_AREAS, THIN_AREAS } from './fixtures';
import type { ViewOptions } from './fixtures';
import { mockClient } from './mock';
import type { MockMode } from './mock';
import '../../styles/card.css';

const NAMED: CardSettings = { shared: true, skills: ['motion', 'react'], theme: 'light' };
const ROLE: CardSettings = { shared: true, skills: ['motion', 'react', 'design-systems'], roleId: 'senior-frontend-engineer', theme: 'light' };

const STATES = [
  ['off', 'Sharing off'],
  ['making', 'Saving · making the link'],
  ['refused', 'Save refused'],
  ['github', 'GitHub README'],
  ['github-match', 'GitHub · match the page'],
  ['linkedin', 'LinkedIn'],
  ['link', 'Just the link'],
  ['copied', 'Copied'],
  ['light', 'Theme · light'],
  ['dark', 'Theme · dark'],
  ['transparent', 'Theme · transparent'],
  ['match', 'Theme · match the page'],
  ['role', 'Skills and a role'],
  ['bars', 'Two areas · bars'],
  ['many', 'Eight areas'],
  ['new-link', 'Make a new link'],
  ['image-failed', 'Image did not load'],
  ['phone', 'Phone'],
  ['phone-off', 'Phone · off'],
  ['sample', 'The sample'],
  ['anonymous', 'Anonymous graph'],
  ['loading', 'Opening'],
  ['error', 'Would not open'],
] as const;
type StateId = (typeof STATES)[number][0];

const isState = (s: string | null): s is StateId => STATES.some(([id]) => id === s);

function readState(): StateId {
  const s = new URLSearchParams(window.location.search).get('state');
  return isState(s) ? s : 'github';
}

interface Scene {
  start: CardSettings;
  mode: MockMode;
  opts: ViewOptions;
  narrow: boolean;
  /** Draw the real image's address (it fails here): only the `image-failed` state. */
  live: boolean;
  /** Press the switch once the card has opened, to reach a state that needs the person to act. */
  press: boolean;
  initial?: ShareBodyProps['initial'];
}

function sceneFor(id: StateId): Scene {
  const base: Scene = { start: NAMED, mode: 'stored', opts: {}, narrow: false, live: false, press: false };
  switch (id) {
    case 'off':
      return { ...base, start: OFF };
    case 'making':
      return { ...base, start: OFF, mode: 'hang-put', press: true };
    case 'refused':
      return { ...base, mode: 'fail-put', press: true };
    case 'github':
      return base;
    case 'github-match':
      return { ...base, start: { ...NAMED, theme: 'match' } };
    case 'linkedin':
      return { ...base, initial: { tab: 'linkedin' } };
    case 'link':
      return { ...base, initial: { tab: 'link' } };
    case 'copied':
      return { ...base, initial: { tab: 'github', copied: true } };
    case 'light':
      return base;
    case 'dark':
      return { ...base, start: { ...NAMED, theme: 'dark' } };
    case 'transparent':
      return { ...base, start: { ...NAMED, theme: 'transparent' } };
    case 'match':
      return { ...base, start: { ...NAMED, theme: 'match' } };
    case 'role':
      return { ...base, start: ROLE };
    case 'bars':
      return { ...base, start: { ...NAMED, skills: [] }, opts: { areas: THIN_AREAS } };
    case 'many':
      return { ...base, start: ROLE, opts: { areas: MANY_AREAS } };
    case 'new-link':
      return { ...base, initial: { confirmLink: true } };
    case 'image-failed':
      return { ...base, live: true };
    case 'phone':
      return { ...base, narrow: true };
    case 'phone-off':
      return { ...base, start: OFF, narrow: true };
    case 'sample':
      return { ...base, start: OFF, opts: { blocked: 'sample' } };
    case 'anonymous':
      return { ...base, start: OFF, opts: { blocked: 'anonymous' } };
    case 'loading':
      return { ...base, mode: 'hang-load' };
    case 'error':
      return { ...base, mode: 'fail-load' };
  }
}

export function CardGallery() {
  const [state, setState] = useState<StateId>(readState);
  const [note, setNote] = useState('');

  const go = (next: StateId) => {
    setState(next);
    setNote('');
    window.history.replaceState(null, '', `/dev/card?state=${next}${window.location.search.includes('scheme=dark') ? '&scheme=dark' : ''}`);
  };

  return (
    <div className="cs-dev">
      <header className="cs-dev-header">
        <Brand />
        <span className="kicker">Dev only · the share sheet on fixtures, no server</span>
        <nav className="cs-dev-nav" aria-label="States">
          {STATES.map(([id, label]) => (
            <button key={id} type="button" className={`cs-dev-navbtn${state === id ? ' is-current' : ''}`} aria-pressed={state === id} onClick={() => go(id)}>
              {label}
            </button>
          ))}
        </nav>
        <p className="cs-dev-note" role="status">
          {note}
        </p>
      </header>
      <main className="cs-dev-main">
        <Stage key={state} id={state} onNote={setNote} />
      </main>
    </div>
  );
}

function Stage({ id, onNote }: { id: StateId; onNote: (s: string) => void }) {
  const scene = useMemo(() => sceneFor(id), [id]);
  const client = useMemo(() => mockClient(scene.start, scene.mode, scene.opts), [scene]);
  const titleId = useId();

  // the states that need the person to have acted: press the switch once the card has opened
  useEffect(() => {
    if (!scene.press) return;
    const timer = window.setTimeout(() => {
      document.querySelector<HTMLButtonElement>('.cs-dev-main [role="switch"]')?.click();
    }, 900);
    return () => window.clearTimeout(timer);
  }, [scene]);

  const body = (
    <ShareBody
      profileId={`riya-${id}`}
      client={client}
      narrow={scene.narrow}
      titleId={titleId}
      onClose={() => onNote('Gallery: closes the sheet and returns to the graph.')}
      onAnalyse={() => onNote('Gallery: leaves the sample and opens Home, to analyse a resume.')}
      onSignIn={() => onNote('Gallery: closes the sheet and opens the sign-in sheet.')}
      liveImage={scene.live}
      initial={scene.initial}
    />
  );

  if (scene.narrow) {
    return (
      <div className="cs-dev-frame" role="dialog" aria-labelledby={titleId}>
        {body}
      </div>
    );
  }
  return (
    <>
      <section className="cs-dev-behind" aria-label="Your graph, behind the sheet">
        <p>[Your graph on Now, as today. The sheet opens over it; under 760 px it is a bottom sheet.]</p>
      </section>
      <section className="panel cs-dev-sheet" role="dialog" aria-labelledby={titleId}>
        {body}
      </section>
    </>
  );
}
