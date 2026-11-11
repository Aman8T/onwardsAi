/**
 * The Ask dock: one opaque bar sticky to the foot of the dashboard. Focusing the input opens a panel that grows upward
 * (starters built from the desk, what the coach can and cannot do); the first question streams its answer in the panel,
 * and a second question, or "Open full view", moves the same thread to the chat with nothing lost.
 *
 * Render it once at the end of the dashboard's content: `<AskDock profile={profile} starters={starters} />`.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { ChatOrigin, Profile } from '../../shared/types';
import { IconStop } from '../components/Icons';
import { actions, useApp } from '../store';
import { DockPanel } from './DockPanel';
import type { Starter } from './DockPanel';
import '../styles/ask.css';

/** A free thread each time, like the strip it replaces: the same question asked twice is two threads. */
const DOCK_ORIGIN: ChatOrigin = { kind: 'free', title: 'About you', caption: 'Asked from the dashboard' };

const PLACEHOLDER = 'Ask about your graph, your roles, or how anything here works';

export function AskDock({ profile, starters }: { profile: Profile; starters: Starter[] }) {
  const threads = useApp((s) => s.threads);
  const dockId = useApp((s) => s.dockThreadId);
  const thread = useMemo(() => threads.find((t) => t.id === dockId) ?? null, [threads, dockId]);
  const streaming = !!thread?.messages.some((m) => m.status === 'streaming');
  const lastUser = useMemo(() => [...(thread?.messages ?? [])].reverse().find((m) => m.role === 'user')?.text, [thread]);

  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);
  /** Set while the input is focused by code (closing the panel), so that focus does not open it again. */
  const quiet = useRef(false);
  const panelId = useId();

  const close = useCallback(() => {
    setOpen(false);
    // a finished answer stays in the chat's list only; the dock starts clean next time
    if (!streaming) actions.clearDockThread();
    quiet.current = true;
    input.current?.focus();
    quiet.current = false;
  }, [streaming]);

  // Escape closes it from anywhere inside; a press outside closes it while no answer is on show
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || !root.current?.contains(document.activeElement)) return;
      e.preventDefault();
      close();
    };
    const onDown = (e: PointerEvent) => {
      if (thread || !root.current || root.current.contains(e.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onDown);
    };
  }, [open, close, thread]);

  const ask = useCallback(
    (raw: string) => {
      const q = raw.trim();
      if (!q || streaming) return;
      setText('');
      if (thread) {
        // a second question: the same thread continues in the chat view
        void actions.send(thread.id, q);
        actions.selectThread(thread.id);
        return;
      }
      setOpen(true);
      actions.openDockThread(DOCK_ORIGIN, q);
    },
    [streaming, thread],
  );

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (streaming) return;
    ask(text);
  };

  const onEdit = () => {
    if (lastUser) setText(lastUser);
    input.current?.focus();
  };

  const onRetry = () => {
    if (thread && lastUser) void actions.send(thread.id, lastUser);
  };

  const chips = starters.slice(0, 2);

  return (
    <div className={`ak-dock${open ? ' is-open' : ''}`} ref={root}>
      {open && (
        <DockPanel
          id={panelId}
          profile={profile}
          starters={starters}
          thread={thread}
          onAsk={ask}
          onClose={close}
          onFull={() => thread && actions.selectThread(thread.id)}
          onRetry={onRetry}
          onEdit={onEdit}
          closeRef={closeBtn}
        />
      )}
      <form className="ak-bar" onSubmit={submit}>
        <svg className="ak-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <path d="M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5m7 7L18 18M6 18l2.5-2.5m7-7L18 6" />
        </svg>
        <label className="sr-only" htmlFor={`${panelId}-in`}>
          {thread ? 'Ask a follow-up' : PLACEHOLDER}
        </label>
        <input
          ref={input}
          id={`${panelId}-in`}
          className="ak-input"
          type="text"
          value={text}
          placeholder={thread ? 'Ask a follow-up' : PLACEHOLDER}
          autoComplete="off"
          enterKeyHint="send"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? panelId : undefined}
          onChange={(e) => setText(e.target.value)}
          onFocus={() => {
            if (!quiet.current) setOpen(true);
          }}
          onClick={() => setOpen(true)}
        />
        {!open && chips.length > 0 && (
          <span className="ak-chips">
            {chips.map((s) => (
              <button key={s.q} type="button" className="ak-chip" onClick={() => ask(s.q)}>
                {s.q}
              </button>
            ))}
          </span>
        )}
        {streaming ? (
          <button type="button" className="btn ak-send ak-stop" onClick={() => thread && actions.stop(thread.id)}>
            <IconStop size={14} strokeWidth={1.5} />
            <span>Stop</span>
          </button>
        ) : (
          <button type="submit" className="btn btn-primary ak-send" aria-label="Send" disabled={!text.trim()}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
              <path d="M12 19V5m0 0-6 6m6-6 6 6" />
            </svg>
          </button>
        )}
      </form>
    </div>
  );
}
