/**
 * What the interview room draws, and what it can be asked to do. The state machine (`../useInterview.ts`) produces a
 * `RoomUi` from its state and the session from the server; `deriveRoom` joins them into a `RoomModel` that the
 * presentational components (`RoomView`, `PhoneRoomView`) draw. The dev states build a `RoomModel` the same way, from
 * fixtures, so what is drawn there is what is drawn in the app.
 *
 * Nothing here knows about the network, the speech services or the DOM.
 */
import type { InterviewLine, InterviewSession, LineKind, PlanSlot, Turn } from '../../../shared/interview';
import { LINE_LABEL, minutesLeft } from '../words';
import type { SpeechBy } from '../words';

/** Where listening stands. `waiting` is the interviewer still speaking; `paused` is the person's, or a send that did not go. */
export type ListenState = 'off' | 'waiting' | 'listening' | 'paused';

/** Why speech cannot be used just now. */
export type MicNote = 'blocked' | 'network' | 'unsupported';

/** `empty` and `piece` are quiet hints, not failures: nothing is written yet, or one piece of a spoken answer could not be made out. */
export type ProblemKind = 'dropped' | 'moved' | 'gone' | 'empty' | 'piece' | 'other';

/** Something that went wrong with the last send, in a sentence the person can read. */
export interface Problem {
  kind: ProblemKind;
  message: string;
}

export interface RoomUi {
  /** After "I don't know": the interviewer's reply is shown alone until "Next question". */
  gate: boolean;
  answer: 'speech' | 'typed';
  listen: ListenState;
  /** The interviewer is speaking now. */
  speaking: boolean;
  /** The words written down so far (what the person can fix and what is sent). */
  draft: string;
  /** The few words still settling. */
  interim: string;
  /** Who is writing the words down: Sarvam AI (after each pause) or the browser (as they are said). */
  listenBy: SpeechBy;
  /** Pieces of the answer are being written down (Sarvam): the box says so until they land. */
  writing: boolean;
  /** About ten seconds of quiet while listening. */
  silent: boolean;
  micNote?: MicNote;
  /** Why the speech changed hands in this interview (Sarvam stopped answering): shown calmly, once. */
  speechNote?: string;
  speechSupported: boolean;
  voiceSupported: boolean;
  voiceOn: boolean;
  slow: boolean;
  /** The browser would not speak without a click first. */
  voiceBlocked: boolean;
  sending: boolean;
  problem?: Problem;
  whyOpen: boolean;
  confirmEnd: boolean;
  finishing: 'no' | 'working' | 'failed';
  finishError?: string;
  /** Seconds spent listening to this answer (read on a timer, not held in state). */
  clock: () => number;
}

/** The interviewer's line as it is drawn. */
export interface Shown {
  kind: LineKind;
  label: string;
  /** Said before the question, e.g. "I didn't catch that." */
  lead?: string;
  text: string;
}

export interface RoomModel extends RoomUi {
  session: InterviewSession;
  /** The slot being asked (none while the reply to "I don't know" is on its own). */
  slot?: PlanSlot;
  /** 1-based number of the question the progress shows. */
  position: number;
  total: number;
  /** Every question has been asked. */
  done: boolean;
  shown: Shown;
  /** What was said on this question before, for a follow-up (and for the reply to "I don't know"). */
  youSaid?: Turn;
  minutesLeft: number;
  /** The text read aloud for this line. */
  spoken: string;
}

/** The last thing the person said on a slot (or, with no slot, anywhere). */
export function lastYouTurn(session: InterviewSession, slotId?: string): Turn | undefined {
  for (let i = session.turns.length - 1; i >= 0; i--) {
    const t = session.turns[i];
    if (t.speaker === 'you' && (slotId === undefined || t.slotId === slotId)) return t;
  }
  return undefined;
}

/** What identifies a line: when it changes, the answer starts afresh (and the line is read aloud). */
export function lineKey(session: InterviewSession): string {
  const c = session.current;
  return [session.turns.length, session.slotIndex, c?.kind ?? '', c?.slotId ?? '', c?.lead ?? '', c?.text ?? ''].join('|');
}

/** What is spoken: the lead (if any) and the line; with the gate shut, only the lead. */
export function spokenText(line: InterviewLine, gate: boolean): string {
  if (gate && line.lead) return line.lead;
  return line.lead ? `${line.lead} ${line.text}` : line.text;
}

/**
 * What is read aloud, line by line. The speech service reads only lines that are the interview's own, so the lead and the
 * question are asked for one at a time (the browser's voice is handed them joined).
 */
export function speakParts(line: InterviewLine, gate: boolean): string[] {
  if (gate && line.lead) return [line.lead];
  return line.lead ? [line.lead, line.text] : [line.text];
}

const FALLBACK_LINE: InterviewLine = { kind: 'done', text: "That's the last question. Thank you. Your debrief is ready." };

export function deriveRoom(session: InterviewSession, ui: RoomUi): RoomModel {
  const line = session.current ?? FALLBACK_LINE;
  const total = session.slots.length;
  const done = line.kind === 'done' || session.status === 'done';
  const gated = ui.gate && !!line.lead && !done;
  // while the reply to "I don't know" is on its own, the progress stays on the question just answered
  const index = done ? total - 1 : gated ? Math.max(0, session.slotIndex - 1) : Math.min(session.slotIndex, total - 1);
  const slot = gated || done ? undefined : (session.slots.find((s) => s.id === line.slotId) ?? session.slots[index]);
  const shown: Shown = gated
    ? { kind: line.kind, label: LINE_LABEL.question, text: line.lead ?? line.text }
    : { kind: line.kind, label: LINE_LABEL[line.kind], ...(line.lead ? { lead: line.lead } : {}), text: line.text };
  const youSaid = gated ? lastYouTurn(session) : line.kind === 'followup' || line.kind === 'floor' ? lastYouTurn(session, line.slotId) : undefined;
  return {
    ...ui,
    gate: gated,
    session,
    ...(slot ? { slot } : {}),
    position: index + 1,
    total,
    done,
    shown,
    ...(youSaid ? { youSaid } : {}),
    minutesLeft: minutesLeft(session.minutes, done ? total - 1 : index, total),
    spoken: spokenText(line, gated),
  };
}

/** Everything the room can be asked to do. */
export interface RoomActions {
  /** Begin listening now, without waiting for the interviewer to stop. */
  startAnswering(): void;
  pause(): void;
  resume(): void;
  /** Done answering (speech): stop listening and send what is written. */
  done(): void;
  /** Send answer (typing). */
  send(): void;
  typeInstead(): void;
  speakInstead(): void;
  setDraft(value: string): void;
  sayAgain(): void;
  dontKnow(): void;
  skip(): void;
  /** After "I don't know": the next question. */
  next(): void;
  toggleVoice(): void;
  toggleWhy(): void;
  askEnd(): void;
  cancelEnd(): void;
  confirmEnd(): void;
  /** After a dropped connection: load the interview again and carry on. */
  carryOn(): void;
  /** Try the microphone again after it was blocked or the speech service failed. */
  tryMicAgain(): void;
  /** The debrief could not be made: try again. */
  retryFinish(): void;
  /** The level meter could not open the microphone. */
  meterBlocked(): void;
  /** To the Practice page. */
  leave(): void;
  /** Reload the interview from the server (another tab moved it on). */
  reload(): void;
}

export const noopActions: RoomActions = {
  startAnswering() {},
  pause() {},
  resume() {},
  done() {},
  send() {},
  typeInstead() {},
  speakInstead() {},
  setDraft() {},
  sayAgain() {},
  dontKnow() {},
  skip() {},
  next() {},
  toggleVoice() {},
  toggleWhy() {},
  askEnd() {},
  cancelEnd() {},
  confirmEnd() {},
  carryOn() {},
  tryMicAgain() {},
  retryFinish() {},
  meterBlocked() {},
  leave() {},
  reload() {},
};

/** Can the person press "Done answering" or "Send answer" now? */
export function canSend(m: Pick<RoomModel, 'sending' | 'finishing' | 'draft' | 'interim'>): boolean {
  return !m.sending && m.finishing === 'no' && (m.draft + m.interim).trim().length > 0;
}
