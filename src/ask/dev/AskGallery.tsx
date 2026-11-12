/**
 * Dev gallery (`/dev/ask`, development builds only): the Ask dock in every state, on fixtures, with no server, no account
 * and no store. Pick a state in the bar, or link straight to one:
 *
 *   /dev/ask?state=done
 *
 *   resting · small · open · waiting · streaming · done · stopped · failed · rate · ceiling
 *
 * It renders `DockView`, the same component the dashboard's `AskDock` renders, so nothing here is a copy of the markup.
 * The buttons do nothing but say what they would have done.
 */
import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Brand } from '../../components/Brand';
import { DockView } from '../DockView';
import { PROFILE, QUESTION, STARTERS, STATES } from './fixtures';
import type { DockState, StateId } from './fixtures';
import '../../styles/ask.css';

const isState = (s: string | null): s is StateId => STATES.some((d) => d.id === s);
const readState = (): StateId => {
  const s = new URLSearchParams(window.location.search).get('state');
  return isState(s) ? s : 'open';
};

export function AskGallery() {
  const [id, setId] = useState<StateId>(readState);
  const [said, setSaid] = useState('');
  const [text, setText] = useState('');
  const closeRef = useRef<HTMLButtonElement>(null);
  const state: DockState = STATES.find((d) => d.id === id) ?? STATES[1];

  const go = (next: StateId) => {
    setId(next);
    setSaid('');
    setText('');
    const p = new URLSearchParams(window.location.search);
    p.set('state', next);
    window.history.replaceState(null, '', `/dev/ask?${p.toString()}`);
  };
  const say = (what: string) => setSaid(what);

  return (
    <div className="ak-dev">
      <header className="ak-dev-head">
        <Brand />
        <span className="kicker">Dev only · the dock on fixtures, no network</span>
        <nav className="ak-dev-nav" aria-label="States">
          {STATES.map((d) => (
            <button key={d.id} type="button" className="ak-dev-btn" aria-pressed={id === d.id} onClick={() => go(d.id)}>
              {d.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="ak-dev-stage" key={id}>
        <DockView
          flow
          open={state.open}
          panel={{
            id: 'ak-dev-panel',
            profile: PROFILE,
            starters: STARTERS,
            thread: state.thread,
            onAsk: (q) => say(`Would ask: ${q}`),
            onClose: () => say('Would close the panel'),
            onFull: () => say('Would open the thread in the chat'),
            onRetry: () => say(`Would send again: ${QUESTION}`),
            onEdit: () => say('Would put the question back in the bar'),
            closeRef,
          }}
          bar={{
            inputId: 'ak-dev-in',
            panelId: 'ak-dev-panel',
            open: state.open,
            compact: state.compact,
            text,
            following: !!state.thread,
            streaming: state.streaming,
            chips: STARTERS.slice(0, 2),
            onText: setText,
            onSubmit: (e: FormEvent) => {
              e.preventDefault();
              say(`Would ask: ${text}`);
              setText('');
            },
            onFocus: () => undefined,
            onOpen: () => undefined,
            onChip: (q) => say(`Would ask: ${q}`),
            onStop: () => say('Would stop the answer'),
          }}
        />
        <p className="ak-fine ak-dev-said" role="status">
          {said}
        </p>
      </main>
    </div>
  );
}
