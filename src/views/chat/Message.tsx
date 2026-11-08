import { memo, useEffect, useState } from 'react';
import type { SourceSummary } from '../../../shared/types';
import type { ChatMessage } from '../../store';
import { IconRefresh } from '../../components/Icons';
import { Markdown } from './MarkdownLazy';
import { toolText } from './helpers';

/* ───────────── user ───────────── */

/** The person's words: a block on the second surface tone, right-aligned. */
export const UserMessage = memo(function UserMessage({ text, caption }: { text: string; caption?: string }) {
  return (
    <div className="ch-msg ch-msg--user ch-in">
      {caption && <span className="kicker ch-from">{caption}</span>}
      <div className="ch-bubble">
        <span className="sr-only">You: </span>
        {text}
      </div>
    </div>
  );
});

/* ───────────── typing indicator ───────────── */

function Typing({ lines }: { lines: string[] }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (lines.length < 2) return;
    const id = window.setInterval(() => setI((n) => (n + 1) % lines.length), 2600);
    return () => window.clearInterval(id);
  }, [lines.length]);
  return (
    <div className="ch-typing" role="status">
      <span className="sr-only">Leafout is thinking</span>
      <span className="ch-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span key={i} className="kicker ch-typing-line" aria-hidden="true">
        {lines[i % lines.length]}
      </span>
    </div>
  );
}

/* ───────────── assistant ───────────── */

interface AssistantProps {
  message: ChatMessage;
  /** Sources that were readable — shown in the "Grounded in" row. */
  sources: SourceSummary[];
  /** Rotating status lines before the first token. */
  lines: string[];
  /** Only passed for the last message in the thread. */
  onRetry?: () => void;
}

/** Tayyari's answer: plain text on the field under a "Tayyari" kicker, with tool chips above it. */
export const AssistantMessage = memo(function AssistantMessage({ message, sources, lines, onRetry }: AssistantProps) {
  const streaming = message.status === 'streaming';
  const failed = message.status === 'error';
  const hasText = message.text.trim().length > 0;
  const tools = message.tools;

  return (
    <div className="ch-msg ch-msg--ai ch-in">
      <span className="kicker ch-who" aria-hidden="true">
        Leafout
      </span>
      <div className="ch-ai-body">
        <span className="sr-only">Leafout: </span>

        {tools.length > 0 && (
          <ul className="ch-tools" aria-label="What Leafout looked up">
            {tools.map((t, i) => {
              const busy = streaming && !hasText && i === tools.length - 1;
              return (
                <li key={i} className={`chip ch-tool${busy ? ' is-busy' : ''}`} title={t.detail || t.name}>
                  <span className="ch-tool-t">{toolText(t)}</span>
                </li>
              );
            })}
          </ul>
        )}

        {streaming && !hasText && <Typing lines={lines} />}

        {hasText && <Markdown text={message.text} streaming={streaming} />}

        {!streaming && !failed && !hasText && <p className="kicker ch-note">Stopped before an answer arrived.</p>}

        {failed && (
          <div className="ch-error" role="alert">
            <div className="ch-error-text">
              <strong>{hasText ? 'This answer was cut short.' : 'Leafout could not finish that answer.'}</strong>
              <span>{(message.error ?? 'No reply came back from the server. Send the message again.').slice(0, 220)}</span>
            </div>
            {onRetry && (
              <button type="button" className="btn btn-sm ch-retry" onClick={onRetry}>
                <IconRefresh size={14} strokeWidth={1.5} />
                <span>Retry</span>
              </button>
            )}
          </div>
        )}

        {message.status === 'done' && hasText && sources.length > 0 && (
          <div className="ch-grounded">
            <span className="kicker">Grounded in</span>
            {sources.slice(0, 5).map((s) => (
              <span key={s.id} className="chip ch-gchip">
                {s.label}
              </span>
            ))}
            {sources.length > 5 && <span className="chip ch-gchip ch-gchip--more">+{sources.length - 5}</span>}
          </div>
        )}
      </div>
    </div>
  );
});
