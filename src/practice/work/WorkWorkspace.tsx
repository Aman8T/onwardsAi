/**
 * The work sample's workspace, wired: the person's work and test note (kept in this browser while they write, so a reload
 * loses nothing, and dropped once the work is handed in), the optional assistant, "Talk it through instead", and the hand-in.
 * Handing in moves the interview on: the room then asks about the choices.
 */
import { useEffect, useRef, useState } from 'react';
import type { AssistantTurn } from '../../../shared/interview';
import { useNow } from '../../lib/ui';
import { sentenceOf } from '../api';
import type { RoomActions, RoomModel } from '../room/model';
import type { WorkControls } from '../useInterview';
import { WorkView } from './WorkView';
import { useDictation } from './useDictation';

const key = (sessionId: string) => `tayyari.practice.work.${sessionId}`;

interface Draft {
  work: string;
  note: string;
}

function readDraft(sessionId: string): Draft | null {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(key(sessionId)) ?? 'null');
    if (typeof raw !== 'object' || raw === null) return null;
    const { work, note } = raw as Record<string, unknown>;
    return typeof work === 'string' && typeof note === 'string' && work.length <= 20_000 && note.length <= 2000 ? { work, note } : null;
  } catch {
    return null;
  }
}

function writeDraft(sessionId: string, draft: Draft | null): void {
  try {
    if (draft) window.localStorage.setItem(key(sessionId), JSON.stringify(draft));
    else window.localStorage.removeItem(key(sessionId));
  } catch {
    /* storage is blocked or full: the draft lives for this page only */
  }
}

export function WorkWorkspace({ model, actions, work }: { model: RoomModel; actions: RoomActions; work: WorkControls }) {
  const { session } = model;
  const task = session.task;
  const sessionId = session.id;

  const [text, setText] = useState(() => readDraft(sessionId)?.work ?? (task?.materialKind === 'code' ? task.material : ''));
  const [note, setNote] = useState(() => readDraft(sessionId)?.note ?? '');
  const noteRef = useRef(note);
  const [settling, setSettling] = useState(false);
  const [assistantOn, setAssistantOn] = useState(true);
  const [log, setLog] = useState<AssistantTurn[]>(() => session.assistantTurns ?? []);
  const [message, setMessage] = useState('');
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | undefined>();
  const [left, setLeft] = useState<number | undefined>();
  const now = useNow(30_000);

  // the note is also kept in a ref: the last words of "talk it through" can land after a click, and the hand-in must have them
  const changeNote = (value: string) => {
    noteRef.current = value;
    setNote(value);
  };
  const talk = useDictation((words) => changeNote(`${noteRef.current.trim() ? `${noteRef.current.replace(/\s+$/, '')} ` : ''}${words}`.slice(0, 2000)), work.speech);

  // keep what is written, in this browser only, until it is handed in
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => writeDraft(sessionId, { work: text, note }), 400);
    return () => window.clearTimeout(timer.current);
  }, [sessionId, text, note]);

  if (!task) return null;

  const minutesIn = Math.max(0, Math.floor((now - Date.parse(session.createdAt)) / 60_000));

  const ask = async () => {
    const msg = message.trim();
    if (!msg || asking) return;
    setAsking(true);
    setAskError(undefined);
    try {
      const reply = await work.ask(msg, text);
      setLog((l) => [...l, { you: msg, assistant: reply.reply }]);
      setMessage('');
      setLeft(reply.left);
    } catch (err) {
      setAskError(sentenceOf(err));
    } finally {
      setAsking(false);
    }
  };

  const handIn = async () => {
    // what is still being written down lands in the note first
    setSettling(true);
    await talk.stop();
    setSettling(false);
    if (await work.handIn(text, noteRef.current)) writeDraft(sessionId, null);
  };

  return (
    <WorkView
      model={model}
      actions={actions}
      task={task}
      work={text}
      testNote={note}
      minutesIn={minutesIn}
      handing={work.handing || settling}
      {...(work.handError ? { handError: work.handError } : {})}
      assistant={{ on: assistantOn, log, message, asking, ...(askError ? { error: askError } : {}), ...(left !== undefined ? { left } : {}) }}
      talk={talk}
      onWork={setText}
      onTestNote={changeNote}
      onHandIn={() => void handIn()}
      onTalk={(on) => (on ? talk.start() : talk.stop())}
      assistantHandlers={{ onSwitch: setAssistantOn, onMessage: setMessage, onAsk: () => void ask() }}
    />
  );
}
