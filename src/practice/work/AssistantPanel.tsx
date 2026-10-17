/**
 * The work sample's optional assistant: a switch, the conversation so far, and a box to ask it something. Some interviews
 * allow AI; this is for practising with it if the one the person is preparing for does. The interviewer asks afterwards how
 * it was used, and nothing is graded on it.
 *
 * What the assistant says is untrusted text: it is shown as text, with a fenced code block drawn as code and nothing else
 * interpreted.
 */
import { useId } from 'react';
import type { AssistantTurn } from '../../../shared/interview';

export interface AssistantState {
  on: boolean;
  log: readonly AssistantTurn[];
  message: string;
  asking: boolean;
  /** A message that could not be sent, in the server's words. */
  error?: string;
  /** Messages left in this session, once the first reply says so. */
  left?: number;
}

export interface AssistantHandlers {
  onSwitch(on: boolean): void;
  onMessage(value: string): void;
  onAsk(): void;
}

export type Piece = { type: 'text' | 'code'; value: string };

/** A reply split into prose and fenced code blocks (``` fences). An unclosed fence is code to the end. */
export function pieces(reply: string): Piece[] {
  const out: Piece[] = [];
  const parts = reply.split('```');
  parts.forEach((part, i) => {
    if (i % 2 === 0) {
      const value = part.trim();
      if (value) out.push({ type: 'text', value });
    } else {
      // the first line of a fenced block may name the language ("tsx")
      const value = part.replace(/^[A-Za-z0-9_+-]*\r?\n/, '').replace(/\s+$/, '');
      if (value) out.push({ type: 'code', value });
    }
  });
  return out;
}

export function AssistantPanel({ state, handlers }: { state: AssistantState; handlers: AssistantHandlers }) {
  const ids = useId();
  const used = state.left === 0;
  const off = !state.on;
  return (
    <aside className="panel rm-assist" aria-labelledby={`${ids}-h`}>
      <div id={`${ids}-h`} className="kicker">
        Assistant
      </div>

      <label className="rm-switch" htmlFor={`${ids}-on`}>
        <input id={`${ids}-on`} type="checkbox" checked={state.on} onChange={(e) => handlers.onSwitch(e.target.checked)} />
        <span className="rm-switch-text">
          <span className="rm-strong">{state.on ? 'Assistant on' : 'Assistant off'}</span>
          <span className="rm-muted rm-small">Some interviews allow AI. Practise with it if yours does.</span>
        </span>
      </label>

      {state.on ? (
        <div className="rm-log" role="log" aria-label="Conversation with the assistant" aria-live="polite">
          {state.log.length === 0 && !state.asking && <p className="rm-faint rm-small">Nothing asked yet.</p>}
          {state.log.map((turn, i) => (
            <div key={i} className="rm-log-turn">
              <div className="rm-msg">
                <div className="kicker">You</div>
                <p>{turn.you}</p>
              </div>
              <div className="rm-msg rm-msg-ai">
                <div className="kicker">Assistant</div>
                {pieces(turn.assistant).map((piece, j) =>
                  piece.type === 'code' ? (
                    <pre key={j} className="rm-code rm-code-sm" role="region" tabIndex={0} aria-label="The assistant's code">
                      {piece.value}
                    </pre>
                  ) : (
                    <p key={j}>{piece.value}</p>
                  ),
                )}
              </div>
            </div>
          ))}
          {state.asking && (
            <p className="rm-muted rm-small" role="status">
              The assistant is writing…
            </p>
          )}
        </div>
      ) : (
        <p className="rm-muted rm-off">The assistant is off. Turn it on whenever you want it, if the interview you&apos;re practising for allows AI.</p>
      )}

      <form
        className="rm-ask"
        onSubmit={(e) => {
          e.preventDefault();
          handlers.onAsk();
        }}
      >
        <label htmlFor={`${ids}-ask`} className="kicker">
          Ask the assistant
        </label>
        <div className="rm-ask-row">
          <input
            id={`${ids}-ask`}
            type="text"
            className="rm-input"
            placeholder="Ask about the work"
            maxLength={1000}
            autoComplete="off"
            value={state.message}
            disabled={off || used || state.asking}
            onChange={(e) => handlers.onMessage(e.target.value)}
          />
          <button type="submit" className="btn rm-btn" disabled={off || used || state.asking || state.message.trim() === ''}>
            Send
          </button>
        </div>
        {state.error && (
          <p className="rm-danger rm-small" role="alert">
            {state.error}
          </p>
        )}
        {used && <p className="rm-muted rm-small">You&apos;ve used this session&apos;s assistant messages.</p>}
        {!used && state.left !== undefined && state.left > 0 && (
          <p className="rm-faint rm-small">{state.left === 1 ? '1 message left' : `${state.left} messages left`}</p>
        )}
        <p className="rm-faint rm-small">The interviewer will ask how you used it. Nothing is graded on it.</p>
      </form>
    </aside>
  );
}
