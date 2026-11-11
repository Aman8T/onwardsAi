/**
 * The panel the Ask dock grows upward: the desk's starters with the reason each is offered, what the coach can and cannot
 * do (from the capability list the coach's prompt is built from), and, once a question is sent, the answer itself.
 */
import type { RefObject } from 'react';
import { CANNOT, CAPABILITIES } from '../../shared/capabilities';
import type { Profile } from '../../shared/types';
import type { Thread } from '../store';
import { AnswerBody } from '../views/chat/Message';
import { statusLines } from '../views/chat/helpers';
import '../styles/chat.css';
import '../styles/ask.css';
import { DISPATCH } from './dispatch';

export interface Starter {
  q: string;
  reason: string;
}

interface PanelProps {
  id: string;
  profile: Profile;
  starters: Starter[];
  /** The thread being answered in place; null shows the starters. */
  thread: Thread | null;
  onAsk: (q: string) => void;
  onClose: () => void;
  onFull: () => void;
  onRetry: () => void;
  onEdit: () => void;
  closeRef: RefObject<HTMLButtonElement | null>;
}

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** The one noun each button's text uses, once each, in the capability list's order. */
const CAN_NOUNS = [...new Set(CAPABILITIES.map((c) => DISPATCH[c.destination].noun))];
const CAN_LINE = `answer from what it can see, and hand you a button that opens ${CAN_NOUNS.slice(0, -1).join(', ')} or ${CAN_NOUNS[CAN_NOUNS.length - 1]}. A button only opens a screen; it does not do anything for you.`;
const CANNOT_LINE = `${CANNOT.map(lower).join(', ')}.`;

export function DockPanel({ id, profile, starters, thread, onAsk, onClose, onFull, onRetry, onEdit, closeRef }: PanelProps) {
  const messages = thread?.messages ?? [];
  const lastUser = [...messages].reverse().find((m) => m.role === 'user');
  const reply = [...messages].reverse().find((m) => m.role === 'assistant');
  const live = !!reply && reply.status === 'streaming';
  const settled = !!reply && reply.status !== 'streaming';

  return (
    <div id={id} className="ak-panel" role="dialog" aria-label="Ask Leafout">
      <div className="ak-panel-head">
        <span className="kicker">Ask Leafout</span>
        <span className="ak-panel-tools">
          {thread && (
            <button type="button" className="btn btn-sm btn-quiet" onClick={onFull}>
              Open full view →
            </button>
          )}
          <button ref={closeRef} type="button" className="btn btn-quiet ak-close" aria-label="Close" onClick={onClose}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </span>
      </div>

      {thread ? (
        <div className="ak-convo">
          {lastUser && <p className="ak-bubble">{lastUser.text}</p>}
          <div className="ak-answer" aria-live="polite" aria-busy={live}>
            {reply && (
              <AnswerBody
                message={reply}
                lines={statusLines(profile)}
                profile={profile}
                onRetry={settled && (reply.status === 'error' || reply.status === 'stopped') ? onRetry : undefined}
                onEdit={settled && reply.status === 'stopped' ? onEdit : undefined}
                question={settled && reply.status === 'error' ? lastUser?.text : undefined}
              />
            )}
          </div>
        </div>
      ) : (
        <>
          <section className="ak-block" aria-labelledby={`${id}-s`}>
            <h2 id={`${id}-s`} className="kicker">
              From your desk today
            </h2>
            <ul className="ak-starters">
              {starters.map((s) => (
                <li key={s.q}>
                  <button type="button" className="ak-starter" onClick={() => onAsk(s.q)}>
                    <span className="ak-starter-q">{s.q}</span>
                    <span className="ak-fine">{s.reason}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <div className="ak-can">
            <p className="ak-fine">
              <b>It can</b> {CAN_LINE}
            </p>
            <p className="ak-fine">
              <b>It cannot</b> {CANNOT_LINE}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
