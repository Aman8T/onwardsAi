/**
 * The small parts the room is built from: the neutral progress squares, the listening clock, "Why this question", the
 * plan rail, the edge-state notices (microphone blocked, a dropped connection, a send that did not go), the end-early
 * dialog and the quiet row of buttons.
 *
 * The progress squares are never verdicts: done is a solid neutral, now is outlined in the accent, ahead is a hairline.
 * Every string that came from the server or the person is a text node.
 */
import { useEffect, useId, useRef } from 'react';
import type { InterviewSession, PlanSlot, SlotSource } from '../../../shared/interview';
import { NO_SPEECH, SILENCE, SLOT_KIND_LABEL, MIC_BLOCKED, SARVAM_NOTE, SPEECH_NETWORK, END_EARLY, clock as fmtClock, leftLine, privacyLine, quoted } from '../words';
import { useFreeLine } from '../useFreeLine';
import { ChevronIcon, KeyboardIcon, MicOffIcon, RepeatIcon } from './icons';
import type { MicNote, Problem, RoomActions, RoomModel } from './model';

/* ───────────────────────── progress ───────────────────────── */

/** One square per question: done, now, ahead. `answered` is how many are behind the person. */
export function Squares({ total, answered, now, label }: { total: number; answered: number; now: boolean; label: string }) {
  return (
    <span className="rm-squares" role="img" aria-label={label}>
      {Array.from({ length: total }, (_, i) => (
        <span key={i} className={`rm-sq${i < answered ? ' is-done' : i === answered && now ? ' is-now' : ''}`} aria-hidden="true" />
      ))}
    </span>
  );
}

/** "Listening · 0:42": the seconds are written to the text node on a timer, not held in state. */
export function ListenClock({ read }: { read: () => number }) {
  const out = useRef<HTMLSpanElement>(null);
  const latest = useRef(read);
  latest.current = read;
  useEffect(() => {
    const write = () => {
      if (out.current) out.current.textContent = fmtClock(latest.current());
    };
    write();
    const id = window.setInterval(write, 1000);
    return () => window.clearInterval(id);
  }, []);
  return <span className="rm-clock numeral" ref={out} aria-hidden="true" />;
}

/* ───────────────────────── why this question ───────────────────────── */

/** Which source is which: the posting's own words, a graph role's requirement, or a line of the person's evidence with where
 * it is from. */
const sourceLabel = (s: PlanSlot['sources'][number]) => (s.origin === 'posting' ? 'Posting' : s.origin === 'role' ? 'The role' : s.source || 'Your evidence');

/** Why a gap is asked: a posting's ask that is not on the resume, or what a graph role needs that the graph shows too little of. */
function gapNote(slot: PlanSlot): string {
  const has = (origin: SlotSource['origin']) => slot.sources.some((s) => s.origin === origin);
  const nearest = has('evidence') ? ' This is the nearest evidence.' : '';
  if (has('role')) return `The role needs this, and your graph shows less of it than the role expects.${nearest}`;
  return has('posting') && has('evidence') ? "The posting's ask is not on your resume. This is the nearest evidence." : "The posting's ask is not on your resume.";
}

export function WhyBlock({ slot, open, onToggle }: { slot: PlanSlot; open: boolean; onToggle: () => void }) {
  const body = useId();
  return (
    <div className="rm-why">
      <button type="button" className="rm-why-btn" aria-expanded={open} aria-controls={body} onClick={onToggle}>
        <ChevronIcon className={open ? 'is-open' : ''} />
        <span className="rm-why-label">Why this question</span>
        <span className="rm-faint rm-why-hint">{open ? 'Hide' : 'Hidden, as in a real interview · Show'}</span>
      </button>
      {open && (
        <div id={body} className="m-quiet rm-why-body">
          <div className="rm-why-row">
            <span className="kicker">Kind</span>
            <span className={`chip rm-kind rm-kind-${slot.kind}`}>{SLOT_KIND_LABEL[slot.kind]}</span>
          </div>
          {slot.sources.map((s, i) => (
            <div className="rm-why-row rm-why-source" key={i}>
              <span className="rm-cite">{sourceLabel(s)}</span>
              <span className="rm-fact">{quoted(s.text)}</span>
            </div>
          ))}
          {slot.kind === 'gap' && <p className="rm-faint rm-small">{gapNote(slot)}</p>}
          {slot.kind === 'task' && <p className="rm-faint rm-small">This question is about the work you handed in.</p>}
        </div>
      )}
    </div>
  );
}

/* ───────────────────────── the plan ───────────────────────── */

export function PlanRail({ model }: { model: RoomModel }) {
  const { session, position, total, done } = model;
  const freeLine = useFreeLine();
  const askedIds = new Set(session.turns.filter((t) => t.speaker === 'you' && t.slotId).map((t) => t.slotId));
  return (
    <aside className="rm-rail" aria-label="The plan">
      <section className="m-quiet rm-plan">
        <p className="kicker rm-plan-kicker">The plan</p>
        <ol className="rm-plan-list">
          {session.slots.map((slot, i) => {
            const passed = done || i < position - 1;
            const here = !done && i === position - 1;
            return (
              <li key={slot.id} className={`rm-plan-row${here ? ' is-now' : ''}`} aria-current={here ? 'step' : undefined}>
                <span className="numeral rm-plan-n">{i + 1}</span>
                <span className="rm-plan-text">
                  <span className="rm-plan-name">{slot.topic || SLOT_KIND_LABEL[slot.kind]}</span>
                  <span className="rm-faint rm-small">{SLOT_KIND_LABEL[slot.kind]}</span>
                </span>
                {here && <span className="chip rm-chip-now">Now</span>}
                {passed && <span className="rm-faint rm-small">{askedIds.has(slot.id) ? 'Asked' : 'Skipped'}</span>}
              </li>
            );
          })}
        </ol>
        <p className="rm-plan-foot">
          <strong>{done ? `All ${total} questions asked` : leftLine(model.minutesLeft)}</strong>
          {!done && <span className="rm-faint"> · a guide, not a limit</span>}
        </p>
      </section>
      <div className="rm-notes">
        <p className="rm-faint rm-small">{privacyLine(model.listenBy)}</p>
        {model.listenBy === 'sarvam' && <p className="rm-faint rm-small">{SARVAM_NOTE}</p>}
        <p className="rm-faint rm-small">{freeLine}</p>
      </div>
    </aside>
  );
}

/* ───────────────────────── the edges ───────────────────────── */

/** Microphone blocked, or the speech service could not be reached (the States board). */
export function MicNotice({ note, onType, onRetry }: { note: Exclude<MicNote, 'unsupported'>; onType: () => void; onRetry: () => void }) {
  const words = note === 'blocked' ? MIC_BLOCKED : SPEECH_NETWORK;
  return (
    <div className="rm-shown rm-notice" role="alert">
      <div className="rm-notice-row">
        <MicOffIcon size={20} className="rm-notice-icon" />
        <p>
          <b>{words.title}</b> {words.body}
        </p>
      </div>
      <div className="rm-buttons">
        <button type="button" className="btn rm-btn" onClick={onType}>
          Type instead
        </button>
        <button type="button" className="btn rm-btn" onClick={onRetry}>
          Try again
        </button>
      </div>
    </div>
  );
}

/** The browser cannot do speech at all: typing is the way (the States board). */
export function NoSpeechNotice() {
  return (
    <div className="rm-shown rm-notice" role="status">
      <div className="rm-notice-row">
        <KeyboardIcon size={20} className="rm-notice-icon" />
        <p>{NO_SPEECH}</p>
      </div>
    </div>
  );
}

/** Sarvam stopped answering and the browser's speech (or typing) took over: said calmly, once, in the server's own words. */
export function SpeechNote({ note }: { note: string }) {
  return (
    <div className="rm-hint" role="status">
      <p>{note}</p>
    </div>
  );
}

/** About ten seconds of quiet: no countdown, only a way to hear the question again. */
export function SilenceHint({ canSpeak, onAgain }: { canSpeak: boolean; onAgain: () => void }) {
  return (
    <div className="rm-hint" role="status">
      <p>{canSpeak ? SILENCE : 'Take your time. The question is above.'}</p>
      {canSpeak && (
        <button type="button" className="btn btn-sm rm-btn-sm" onClick={onAgain}>
          <RepeatIcon /> Say it again
        </button>
      )}
    </div>
  );
}

/** A send that did not go, or a hint about an answer that is not there yet. The text the person wrote stays where it is. */
export function ProblemNotice({ problem, actions }: { problem: Problem; actions: RoomActions }) {
  if (problem.kind === 'empty' || problem.kind === 'piece') {
    return (
      <p className="rm-muted rm-small" role="status">
        {problem.message}
      </p>
    );
  }
  return (
    <div className="rm-shown rm-notice" role="alert">
      <p>{problem.message}</p>
      <div className="rm-buttons">
        {problem.kind === 'moved' && (
          <button type="button" className="btn rm-btn" onClick={actions.reload}>
            Reload
          </button>
        )}
        {problem.kind === 'gone' && (
          <button type="button" className="btn rm-btn" onClick={actions.leave}>
            Back to Practice
          </button>
        )}
      </div>
    </div>
  );
}

/** The connection dropped: the answers so far are saved on the server (the States board). */
export function DroppedPanel({ model, onCarryOn }: { model: RoomModel; onCarryOn: () => void }) {
  const answered = Math.min(model.session.slotIndex, model.total);
  const next = Math.min(answered + 1, model.total);
  return (
    <section className="m-plain rm-answer rm-dropped" aria-label="Connection dropped">
      <div className="rm-shown">
        <Squares total={model.total} answered={answered} now label={`${model.total} questions: ${answered} answered, question ${next} next`} />
        <p role="alert">
          {answered > 0 ? `The connection dropped after question ${answered}. Your answers so far are saved.` : 'The connection dropped. Your answers so far are saved.'}
        </p>
        <div className="rm-buttons">
          <button type="button" className="btn rm-btn" onClick={onCarryOn}>
            {answered > 0 ? `Carry on from question ${next}` : 'Carry on'}
          </button>
        </div>
      </div>
    </section>
  );
}

/** The last line was said: the debrief is being written, or could not be. */
export function FinishPanel({ model, actions }: { model: RoomModel; actions: RoomActions }) {
  if (model.finishing === 'failed') {
    return (
      <section className="m-plain rm-answer" aria-label="Your debrief">
        <p role="alert">{model.finishError}</p>
        <div className="rm-buttons">
          <button type="button" className="btn rm-btn" onClick={actions.retryFinish}>
            Try again
          </button>
        </div>
      </section>
    );
  }
  return (
    <section className="m-plain rm-answer" aria-label="Your debrief" aria-busy="true">
      <p className="rm-muted" role="status">
        Writing your debrief. It takes a few seconds.
      </p>
    </section>
  );
}

/* ───────────────────────── the quiet row, the end dialog ───────────────────────── */

export function QuietRow({ model, actions, sayAgain = true }: { model: RoomModel; actions: RoomActions; sayAgain?: boolean }) {
  const off = model.sending || model.finishing !== 'no';
  return (
    <div className="rm-quiet">
      {sayAgain && model.voiceSupported && (
        <>
          <button type="button" className="rm-tbtn" onClick={actions.sayAgain} disabled={off}>
            Say it again
          </button>
          <span className="rm-faint" aria-hidden="true">
            ·
          </span>
        </>
      )}
      <button type="button" className="rm-tbtn" onClick={actions.dontKnow} disabled={off}>
        I don&apos;t know
      </button>
      <span className="rm-faint" aria-hidden="true">
        ·
      </span>
      <button type="button" className="rm-tbtn" onClick={actions.skip} disabled={off}>
        Skip
      </button>
    </div>
  );
}

/** "End the interview now?": a real dialog, so focus is held and Escape keeps going. */
export function EndDialog({ open, answered, onEnd, onKeep }: { open: boolean; answered: number; onEnd: () => void; onKeep: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const title = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d || typeof d.showModal !== 'function') return;
    if (open && !d.open) {
      d.showModal();
      // the safe choice holds the focus: Enter keeps going
      d.querySelector<HTMLElement>('[data-first]')?.focus();
    }
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className="rm-dialog"
      aria-labelledby={title}
      onCancel={(e) => {
        e.preventDefault();
        onKeep();
      }}
    >
      <h2 id={title} className="rm-dialog-title">
        {END_EARLY.title}
      </h2>
      <p className="rm-muted">{answered > 0 ? END_EARLY.body : END_EARLY.bodyEmpty}</p>
      <div className="rm-buttons">
        <button type="button" className="btn rm-btn" onClick={onEnd}>
          {END_EARLY.yes}
        </button>
        <button type="button" className="btn rm-btn" onClick={onKeep} data-first>
          {END_EARLY.no}
        </button>
      </div>
    </dialog>
  );
}

/** How many of the person's answers are on the record (the dialog's wording depends on it). */
export const answeredCount = (session: InterviewSession) => session.turns.filter((t) => t.speaker === 'you').length;
