import { memo, useEffect, useState } from 'react';
import type { Profile, SourceSummary } from '../../../shared/types';
import type { ChatMessage } from '../../store';
import { ActionCard } from '../../ask/ActionCard';
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

/** The sentence under a failed answer: what went wrong, said in words (a missing code reads as a plain failure). */
function failureText(message: ChatMessage): string {
  if (message.failure === 'rate') {
    return message.retryAfter
      ? `Too many questions in a short time. Try again in ${message.retryAfter} seconds.`
      : 'Too many questions in a short time. Try again in a moment.';
  }
  if (message.failure === 'ceiling') return "The coach has reached today's limit. It is back tomorrow.";
  return (message.error ?? 'No reply came back from the server. Send the message again.').slice(0, 220);
}

interface AnswerProps {
  message: ChatMessage;
  /** Rotating status lines before the first token. */
  lines: string[];
  /** The graph, for the action card's button. */
  profile?: Profile;
  /** Only passed for the last message in the thread. */
  onRetry?: () => void;
  /** Stopped: put the question back in the composer. Only for the last message. */
  onEdit?: () => void;
  /** Failed: the question that was asked, offered for copying. Only for the last message. */
  question?: string;
}

/**
 * What an answer is made of, shared by the chat view and the Ask dock: tool chips, the status line before the first
 * token, the text, the stopped and failed states, and the action card.
 */
export function AnswerBody({ message, lines, profile, onRetry, onEdit, question }: AnswerProps) {
  const streaming = message.status === 'streaming';
  const failed = message.status === 'error';
  const stopped = message.status === 'stopped';
  const hasText = message.text.trim().length > 0;
  const tools = message.tools;
  const [copied, setCopied] = useState(false);

  const copy = () => {
    if (!question) return;
    void navigator.clipboard?.writeText(question).then(
      () => setCopied(true),
      () => setCopied(false),
    );
  };

  return (
    <>
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

      {message.status === 'done' && !hasText && <p className="kicker ch-note">Stopped before an answer arrived.</p>}

      {stopped && (
        <div className="ch-error" role="status">
          <div className="ch-error-text">
            <span>{hasText ? 'Stopped. What arrived is kept.' : 'Stopped before an answer arrived.'}</span>
          </div>
          {(onRetry || onEdit) && (
            <div className="ch-error-acts">
              {onRetry && (
                <button type="button" className="btn btn-sm ch-retry" onClick={onRetry}>
                  <IconRefresh size={14} strokeWidth={1.5} />
                  <span>Retry</span>
                </button>
              )}
              {onEdit && (
                <button type="button" className="btn btn-sm btn-quiet" onClick={onEdit}>
                  Edit the question
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {failed && (
        <div className="ch-error" role="alert">
          <div className="ch-error-text">
            <strong>The answer did not arrive</strong>
            <span>{failureText(message)}</span>
          </div>
          {(onRetry || question) && (
            <div className="ch-error-acts">
              {onRetry && (
                <button type="button" className="btn btn-sm ch-retry" onClick={onRetry}>
                  <IconRefresh size={14} strokeWidth={1.5} />
                  <span>Retry</span>
                </button>
              )}
              {question && (
                <button type="button" className="btn btn-sm btn-quiet" onClick={copy}>
                  {copied ? 'Copied' : 'Copy my question'}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {message.action && profile && !streaming && <ActionCard action={message.action} profile={profile} />}
    </>
  );
}

interface AssistantProps extends Omit<AnswerProps, 'lines'> {
  /** Sources that were readable — shown in the "Grounded in" row. */
  sources: SourceSummary[];
  lines: string[];
}

/** Tayyari's answer: plain text on the field under a "Tayyari" kicker, with tool chips above it. */
export const AssistantMessage = memo(function AssistantMessage({ sources, ...answer }: AssistantProps) {
  const { message } = answer;
  const hasText = message.text.trim().length > 0;
  return (
    <div className="ch-msg ch-msg--ai ch-in">
      <span className="kicker ch-who" aria-hidden="true">
        Leafout
      </span>
      <div className="ch-ai-body">
        <span className="sr-only">Leafout: </span>
        <AnswerBody {...answer} />

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
