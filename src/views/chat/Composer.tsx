import { useLayoutEffect, type KeyboardEvent, type RefObject } from 'react';
import { IconStop } from '../../components/Icons';

interface ComposerProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  streaming: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}

/** Keep in step with `.ch-input { max-height }` in chat.css. */
const MAX_H = 168;

/**
 * A panel row: a borderless textarea that grows with the text and a primary Send button
 * (Stop while an answer streams). Enter sends, Shift+Enter adds a line.
 */
export function Composer({ value, onChange, onSubmit, onStop, streaming, textareaRef }: ComposerProps) {
  const canSend = value.trim().length > 0 && !streaming;

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    if (!value) {
      // Empty: one row. (A wrapped placeholder would otherwise inflate scrollHeight.)
      el.style.height = '';
      return;
    }
    el.style.height = 'auto';
    // scrollHeight excludes any border; add it back so one line does not scroll.
    const border = el.offsetHeight - el.clientHeight;
    el.style.height = `${Math.min(el.scrollHeight + border, MAX_H)}px`;
  }, [value, textareaRef]);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (canSend) onSubmit();
  };

  return (
    <form
      className={`ch-composer panel${streaming ? ' is-live' : ''}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSubmit();
      }}
    >
      <label htmlFor="ch-ask" className="sr-only">
        Ask Tayyari
      </label>
      <textarea
        id="ch-ask"
        ref={textareaRef}
        className="ch-input scroll"
        rows={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask anything about your path…"
        enterKeyHint="send"
      />
      {streaming ? (
        <button type="button" className="btn ch-stop" onClick={onStop} aria-label="Stop generating">
          <IconStop size={14} strokeWidth={1.5} />
          <span>Stop</span>
        </button>
      ) : (
        <button type="submit" className="btn btn-primary ch-send" disabled={!canSend} aria-label="Send">
          <span>Send</span>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            focusable="false"
          >
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </button>
      )}
    </form>
  );
}
