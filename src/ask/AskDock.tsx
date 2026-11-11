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
import { actions, useApp } from '../store';
import type { Starter } from './DockPanel';
import { DockView } from './DockView';
import '../styles/ask.css';

/** A free thread each time, like the strip it replaces: the same question asked twice is two threads. */
const DOCK_ORIGIN: ChatOrigin = { kind: 'free', title: 'About you', caption: 'Asked from the dashboard' };

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

  return (
    <DockView
      open={open}
      frameRef={root}
      panel={{
        id: panelId,
        profile,
        starters,
        thread,
        onAsk: ask,
        onClose: close,
        onFull: () => thread && actions.selectThread(thread.id),
        onRetry,
        onEdit,
        closeRef: closeBtn,
      }}
      bar={{
        inputId: `${panelId}-in`,
        panelId,
        open,
        text,
        following: !!thread,
        streaming,
        chips: starters.slice(0, 2),
        inputRef: input,
        onText: setText,
        onSubmit: submit,
        onFocus: () => {
          if (!quiet.current) setOpen(true);
        },
        onOpen: () => setOpen(true),
        onChip: ask,
        onStop: () => thread && actions.stop(thread.id),
      }}
    />
  );
}
