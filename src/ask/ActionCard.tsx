/**
 * The button the coach proposed, drawn as a card. It never claims anything was done: the line under the label says
 * nothing has started, and the one button opens a screen. See `dispatch.ts` for what each destination opens.
 *
 * It is the ink block of its turn when `ink` is set (the chat passes it for the latest card only); earlier cards, and the
 * card inside the dashboard's Ask panel, are quiet. The ink block is the one thing to press next on a screen.
 */
import { useId, useState } from 'react';
import { CAPABILITIES } from '../../shared/capabilities';
import type { Profile } from '../../shared/types';
import { ShareButton } from '../card/ShareButton';
import type { ChatAction } from '../store';
import '../styles/ask.css';
import { DISPATCH } from './dispatch';

export function ActionCard({ action, profile, ink = false }: { action: ChatAction; profile: Profile; ink?: boolean }) {
  const handler = DISPATCH[action.destination] as (typeof DISPATCH)[keyof typeof DISPATCH] | undefined;
  const capability = CAPABILITIES.find((c) => c.destination === action.destination);
  const [notice, setNotice] = useState('');
  const [asking, setAsking] = useState(false);
  const id = useId();
  // a destination this build does not know (a newer server) shows nothing rather than a dead button
  if (!handler || !capability) return null;

  const text = `Open ${handler.noun}`;
  const ctx = { profile, target: action.target };

  let button;
  if (handler.kind === 'href') {
    button = (
      <a className="btn ak-act-go" href={handler.href(ctx)}>
        {text}
      </a>
    );
  } else if (handler.kind === 'share') {
    button = (
      <ShareButton profileId={profile.id} look="primary" className="ak-act-go">
        {text}
      </ShareButton>
    );
  } else if (handler.kind === 'confirm') {
    button = (
      <button type="button" className="btn ak-act-go" aria-expanded={asking} aria-controls={`${id}-ask`} onClick={() => setAsking((a) => !a)}>
        {text}
      </button>
    );
  } else {
    button = (
      <button
        type="button"
        className="btn ak-act-go"
        onClick={() => {
          setNotice('');
          void Promise.resolve(handler.run(ctx)).then((ok) => {
            if (!ok) setNotice('That item could not be found any more.');
          });
        }}
      >
        {text}
      </button>
    );
  }

  return (
    <div className={`ak-act${ink ? ' is-ink' : ''}`} role="group" aria-label={capability.label}>
      <span className="ak-act-main">
        <span className="ak-act-label">{capability.label}</span>
        {action.title && <span className="ak-act-title">{action.title}</span>}
        <span className="ak-fine">Nothing has started. This button opens it.</span>
        {notice && (
          <span className="ak-fine" role="status">
            {notice}
          </span>
        )}
      </span>
      {button}
      {handler.kind === 'confirm' && asking && (
        <div id={`${id}-ask`} className="ak-act-ask" role="group" aria-label="Confirm New analysis">
          <span className="ak-fine">{handler.warning}</span>
          <span className="ak-act-ask-row">
            <button type="button" className="btn btn-sm" onClick={() => setAsking(false)}>
              Keep graph
            </button>
            <button type="button" className="btn btn-sm" onClick={() => handler.run(ctx)}>
              Start over
            </button>
          </span>
        </div>
      )}
    </div>
  );
}
