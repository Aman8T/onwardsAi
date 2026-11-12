/**
 * The interview room, drawn (boards `Main` and `PhoneRoom` of the Interview canvas). Presentational: it draws a
 * `RoomModel` and calls `RoomActions`; the state machine is `../useInterview.ts`, and the dev gallery draws the same
 * component from fixtures.
 *
 * The question is the visual centre: large serif, one at a time, always as text even when it is also spoken. Around it
 * only what is needed: how far along (neutral squares, never verdicts), "Why this question" closed by default (a real
 * interview does not explain itself), the answer panel, and on a wide screen the plan and the privacy line. The mic meter
 * is the one thing that moves, and only with the voice (`MicMeter`). With `narrow` the same model is drawn as the phone
 * room: a top bar, the question, the words as they are written down, and large round buttons.
 */
import { useEffect, useId, useRef } from 'react';
import type { RefObject } from 'react';
import { Brand } from '../../components/Brand';
import '../../styles/practice-room.css';
import { SAID_PAUSED, WRITING_WORDS, clip, clock, keptLine, listenHint, progressLine, saidLabel } from '../words';
import { KeyboardIcon, MicIcon, PauseIcon, PlayIcon, RepeatIcon, SendIcon, SpeakerIcon, SpeakerOffIcon } from './icons';
import { MicMeter } from './MicMeter';
import { canSend } from './model';
import type { RoomActions, RoomModel } from './model';
import {
  DroppedPanel,
  EndDialog,
  FinishPanel,
  ListenClock,
  MicNotice,
  NoSpeechNotice,
  PlanRail,
  ProblemNotice,
  QuietRow,
  SilenceHint,
  SpeechNote,
  Squares,
  WhyBlock,
  answeredCount,
} from './parts';

export interface RoomViewProps {
  model: RoomModel;
  actions: RoomActions;
  /** The phone room. */
  narrow?: boolean;
  /** Dev gallery: fixed meter bars (4 to 26 px) instead of the microphone. */
  meterPreview?: readonly number[];
}

/** "Headout" from "Frontend Engineer at Headout"; the kind otherwise. */
function shortName(title: string, fallback: string): string {
  const at = title.lastIndexOf(' at ');
  return at > 0 ? title.slice(at + 4).trim() || fallback : fallback;
}

export function RoomView({ model, actions, narrow = false, meterPreview }: RoomViewProps) {
  const m = model;
  const answered = m.done ? m.total : m.position - 1;
  const progressText = m.done ? `All ${m.total} questions asked` : progressLine(m.position, m.total, m.slot);
  const progressLabel = m.done ? `All ${m.total} questions asked` : `Question ${m.position} of ${m.total}`;
  const heading = useRef<HTMLHeadingElement>(null);

  // a screen reader hears each new question once; the heading itself is not a live region
  const announce = m.done ? m.shown.text : `${progressLabel}. ${m.shown.lead && !m.gate ? `${m.shown.lead} ` : ''}${m.shown.text}`;

  // the question is where focus sits when a line is on its own (so Space, and a screen reader, start from it)
  useEffect(() => {
    if (m.gate) heading.current?.focus({ preventScroll: true });
  }, [m.gate]);

  const showAnswer = m.finishing === 'no' && !m.gate && !m.done;
  const dropped = m.problem?.kind === 'dropped';

  return (
    <div className={`rm-room${narrow ? ' is-phone' : ''}`}>
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>

      {narrow ? <PhoneBar model={m} actions={actions} progressLabel={progressLabel} answered={answered} /> : <FocusBar model={m} actions={actions} />}

      <main className="rm-main">
        <div className="rm-cols">
          <div className="rm-col">
            {!narrow && (
              <div className="rm-progress">
                <span className="rm-rung">{progressText}</span>
                <Squares total={m.total} answered={answered} now={!m.done} label={progressLabel} />
                <span className="rm-faint rm-small">No verdicts until the end</span>
              </div>
            )}

            <Interviewer model={m} actions={actions} heading={heading} />

            {m.slot && !m.gate && !m.done && <WhyBlock slot={m.slot} open={m.whyOpen} onToggle={actions.toggleWhy} />}

            {m.gate && (
              <div className="rm-gate">
                <button type="button" className="btn btn-primary rm-btn" onClick={actions.next}>
                  Next question
                </button>
              </div>
            )}

            {(m.finishing !== 'no' || m.done) && <FinishPanel model={m} actions={actions} />}

            {showAnswer && dropped && <DroppedPanel model={m} onCarryOn={actions.carryOn} />}
            {showAnswer && !dropped && (m.answer === 'speech' ? <SpeechPanel model={m} actions={actions} narrow={narrow} meterPreview={meterPreview} /> : <TypePanel model={m} actions={actions} narrow={narrow} />)}
          </div>

          {!narrow && <PlanRail model={m} />}
        </div>
      </main>

      {narrow && <p className="rm-faint rm-phone-foot">{keptLine(m.listenBy)}</p>}

      <EndDialog open={m.confirmEnd} answered={answeredCount(m.session)} onEnd={actions.confirmEnd} onKeep={actions.cancelEnd} />
    </div>
  );
}

/* ───────────────────────── the bars ───────────────────────── */

function VoiceToggle({ model, actions, iconOnly }: { model: RoomModel; actions: RoomActions; iconOnly?: boolean }) {
  if (!model.voiceSupported) return null;
  const word = model.voiceOn ? 'on' : 'off';
  return (
    <button
      type="button"
      className={`btn btn-quiet btn-sm rm-voice${iconOnly ? ' rm-voice-icon' : ''}`}
      aria-pressed={model.voiceOn}
      aria-label={iconOnly ? `Interviewer's voice: ${word}` : undefined}
      onClick={actions.toggleVoice}
    >
      {model.voiceOn ? <SpeakerIcon /> : <SpeakerOffIcon />}
      {!iconOnly && <>Interviewer&apos;s voice: {word}</>}
    </button>
  );
}

/** The wide room's one thin bar: no navigation, only where you are and how to leave. */
export function FocusBar({ model, actions }: { model: RoomModel; actions: RoomActions }) {
  return (
    <header className="rm-bar">
      <Brand onClick={actions.leave} />
      <span className="rm-faint" aria-hidden="true">
        ·
      </span>
      <span className="rm-bar-title">Practice · {model.session.title}</span>
      <span className="rm-bar-fill" />
      <span className="rm-faint rm-small">{keptLine(model.listenBy)}</span>
      <VoiceToggle model={model} actions={actions} />
      <button type="button" className="btn btn-quiet btn-sm rm-btn-sm" onClick={actions.askEnd} disabled={model.finishing !== 'no'}>
        End interview
      </button>
    </header>
  );
}

function PhoneBar({ model, actions, progressLabel, answered }: { model: RoomModel; actions: RoomActions; progressLabel: string; answered: number }) {
  const m = model;
  return (
    <header className="rm-phonebar">
      <span className="rm-phone-name">{shortName(m.session.title, m.session.title)}</span>
      <span className="numeral rm-muted rm-small">{m.done ? 'Done' : `${m.position} of ${m.total}`}</span>
      <Squares total={m.total} answered={answered} now={!m.done} label={`${progressLabel}, no verdicts until the end`} />
      <span className="rm-bar-fill" />
      <VoiceToggle model={m} actions={actions} iconOnly />
      <button type="button" className="rm-tbtn rm-tbtn-end" onClick={actions.askEnd} disabled={m.finishing !== 'no'}>
        End
      </button>
    </header>
  );
}

/* ───────────────────────── the interviewer ───────────────────────── */

function Interviewer({ model, actions, heading }: { model: RoomModel; actions: RoomActions; heading: RefObject<HTMLHeadingElement | null> }) {
  const m = model;
  const said = m.youSaid;
  return (
    <section className="rm-says" aria-label={m.gate ? 'The interviewer, replying' : m.shown.kind === 'question' ? 'The interviewer' : 'The interviewer, follow-up'}>
      {said && (
        <div className="rm-youbox m-paper is-loose">
          <div className="rm-rung">{m.gate ? 'You said' : `You said · ${clock(said.at)}`}</div>
          <p className="rm-muted">{said.text.trim() ? `“${clip(said.text, 320)}”` : ''}</p>
        </div>
      )}
      {m.shown.back && (
        <div className="rm-youbox rm-back m-paper is-loose">
          <div className="rm-rung">{m.shown.back.line}</div>
          <p className="rm-muted">
            “{clip(m.shown.back.claim, 240)}”{m.shown.back.source ? <span className="rm-small"> · {m.shown.back.source}</span> : null}
          </p>
        </div>
      )}
      <div className="rm-line">
        <div className="rm-rung">{m.shown.label}</div>
        {m.shown.lead && !m.gate && <p className="rm-lead">{m.shown.lead}</p>}
        <h1 className="rm-q" ref={heading} tabIndex={-1}>
          {m.shown.text}
        </h1>
        <div className="rm-speaking" aria-live="polite">
          {m.speaking && (
            <>
              <SpeakerIcon />
              <span>Speaking</span>
              <span aria-hidden="true">·</span>
              <button type="button" className="rm-tbtn rm-tbtn-inline" onClick={actions.sayAgain}>
                Say it again
              </button>
            </>
          )}
          {!m.speaking && m.voiceBlocked && (
            <>
              <span>Your browser wants a click before it speaks.</span>
              <button type="button" className="rm-tbtn rm-tbtn-inline" onClick={actions.sayAgain}>
                Say it again
              </button>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

/* ───────────────────────── answering by voice ───────────────────────── */

function Transcript({ model, actions, narrow }: { model: RoomModel; actions: RoomActions; narrow: boolean }) {
  const m = model;
  const id = useId();
  // the words can be fixed whenever they are not still being written (with Sarvam, a piece may still be on its way)
  const editable = (m.listen === 'paused' || (m.listen === 'off' && !m.sending)) && !m.writing;
  const label = m.listen === 'paused' ? SAID_PAUSED : saidLabel(m.listenBy);
  const empty = m.draft.trim() === '' && m.interim.trim() === '' && !m.writing;
  const hint = listenHint(m.listenBy);
  // the browser's words still settling, or the quiet note that a piece is being written down (never part of the answer)
  const tail = m.interim || (m.writing ? WRITING_WORDS : '');
  return (
    <div className="rm-write">
      {editable ? (
        <label className="kicker" htmlFor={id}>
          {label}
        </label>
      ) : (
        <span className="kicker" id={`${id}-l`}>
          {label}
        </span>
      )}
      {editable ? (
        <textarea id={id} className="rm-textarea rm-textarea-said" rows={narrow ? 5 : 4} value={m.draft} onChange={(e) => actions.setDraft(e.target.value)} placeholder={hint} />
      ) : (
        <div className="rm-said-box m-paper is-loose" role="group" aria-labelledby={`${id}-l`}>
          {empty ? (
            <p className="rm-faint">{hint}</p>
          ) : (
            <p className="rm-said">
              {/* settled words are announced as they arrive; the few still settling are not, they change too fast to read out */}
              <span aria-live="polite">{m.draft}</span>
              {m.draft && tail ? ' ' : ''}
              <span className="rm-interim" aria-hidden="true">
                {tail}
              </span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function SpeechPanel({ model, actions, narrow, meterPreview }: { model: RoomModel; actions: RoomActions; narrow: boolean; meterPreview?: readonly number[] }) {
  const m = model;
  const live = m.listen === 'listening';
  const paused = m.listen === 'paused';
  const heard = live || paused;
  const blocked = m.micNote === 'blocked' || m.micNote === 'network';
  const meterOn = live && !meterPreview;

  if (blocked && m.draft.trim() === '') {
    return (
      <section className="m-plain rm-answer" aria-label="Your answer">
        {m.speechNote && <SpeechNote note={m.speechNote} />}
        <MicNotice note={m.micNote === 'network' ? 'network' : 'blocked'} onType={actions.typeInstead} onRetry={actions.tryMicAgain} />
        <QuietRow model={m} actions={actions} sayAgain={!narrow} />
      </section>
    );
  }

  return (
    <section className="m-plain rm-answer" aria-label="Your answer" aria-busy={m.sending}>
      {m.speechNote && <SpeechNote note={m.speechNote} />}
      {blocked && <MicNotice note={m.micNote === 'network' ? 'network' : 'blocked'} onType={actions.typeInstead} onRetry={actions.tryMicAgain} />}

      <div className="rm-listenrow">
        <MicMeter active={meterOn} preview={live || paused ? meterPreview : undefined} onBlocked={(why) => why === 'denied' && actions.meterBlocked()} />
        {m.sending ? (
          <span className="rm-listenlabel" role="status">
            Sending your answer…
          </span>
        ) : heard ? (
          <span className="rm-listenlabel">
            <span aria-live="polite">{paused ? 'Paused' : 'Listening'}</span> · <ListenClock read={m.clock} />
          </span>
        ) : (
          <>
            <span className="rm-muted rm-small rm-wait">Listening starts when the interviewer stops.</span>
            {!blocked && (
              <button type="button" className="btn btn-quiet btn-sm rm-btn-sm" onClick={actions.startAnswering} disabled={m.sending}>
                Start answering
              </button>
            )}
          </>
        )}
      </div>

      <Transcript model={m} actions={actions} narrow={narrow} />

      {live && m.silent && <SilenceHint canSpeak={m.voiceSupported} onAgain={actions.sayAgain} />}
      {m.problem && m.problem.kind !== 'dropped' && <ProblemNotice problem={m.problem} actions={actions} />}

      {narrow ? <PhoneControls model={m} actions={actions} /> : <WideControls model={m} actions={actions} />}
      {!narrow && !paused && <p className="rm-faint rm-small rm-fixhint">Wrong word? Press Pause to fix it.</p>}
      <QuietRow model={m} actions={actions} sayAgain={!narrow} />
    </section>
  );
}

function WideControls({ model, actions }: { model: RoomModel; actions: RoomActions }) {
  const m = model;
  const heard = m.listen === 'listening' || m.listen === 'paused';
  return (
    <div className="rm-buttons">
      <button type="button" className="btn btn-primary rm-btn" disabled={!heard || m.sending} onClick={actions.done}>
        Done answering <kbd className="rm-kbd">Space</kbd>
      </button>
      <button type="button" className="btn rm-btn" disabled={!heard || m.sending} onClick={m.listen === 'paused' ? actions.resume : actions.pause}>
        {m.listen === 'paused' ? <PlayIcon /> : <PauseIcon />}
        {m.listen === 'paused' ? 'Resume' : 'Pause'}
      </button>
      <button type="button" className="btn rm-btn" onClick={actions.typeInstead} disabled={m.sending}>
        <KeyboardIcon />
        Type instead
      </button>
    </div>
  );
}

/** The phone's thumb row: Type, the large Done button, Say again. Pause sits in the quiet row beside it. */
function PhoneControls({ model, actions }: { model: RoomModel; actions: RoomActions }) {
  const m = model;
  const heard = m.listen === 'listening' || m.listen === 'paused';
  return (
    <>
      <div className="rm-round-row">
        <div className="rm-round-cell">
          <button type="button" className="rm-round rm-round-sm" aria-label="Type instead" onClick={actions.typeInstead} disabled={m.sending}>
            <KeyboardIcon size={22} />
          </button>
          <span className="rm-muted rm-round-label">Type</span>
        </div>
        <div className="rm-round-cell rm-round-main">
          <button
            type="button"
            className="rm-round rm-round-lg"
            aria-label={heard ? 'Done answering' : 'Start answering'}
            onClick={heard ? actions.done : actions.startAnswering}
            disabled={m.sending}
          >
            <MicIcon size={28} />
          </button>
          <span className="rm-round-label rm-strong">{heard ? 'Tap when done' : 'Tap to answer'}</span>
        </div>
        <div className="rm-round-cell">
          <button type="button" className="rm-round rm-round-sm" aria-label="Say the question again" onClick={actions.sayAgain} disabled={m.sending || !m.voiceSupported}>
            <RepeatIcon size={22} />
          </button>
          <span className="rm-muted rm-round-label">Say again</span>
        </div>
      </div>
      {heard && (
        <div className="rm-pause-row">
          <button type="button" className="rm-tbtn" onClick={m.listen === 'paused' ? actions.resume : actions.pause} disabled={m.sending}>
            {m.listen === 'paused' ? 'Resume' : 'Pause'}
          </button>
        </div>
      )}
    </>
  );
}

/* ───────────────────────── answering by typing ───────────────────────── */

function TypePanel({ model, actions, narrow }: { model: RoomModel; actions: RoomActions; narrow: boolean }) {
  const m = model;
  const id = useId();
  const box = useRef<HTMLTextAreaElement>(null);
  const line = m.session.current?.text ?? '';
  // each new question starts with the cursor in the box
  useEffect(() => {
    if (!m.sending) box.current?.focus({ preventScroll: true });
  }, [line, m.session.turns.length, m.sending]);
  return (
    <section className="m-plain rm-answer" aria-label="Your answer" aria-busy={m.sending}>
      {m.speechNote && <SpeechNote note={m.speechNote} />}
      {m.micNote === 'unsupported' && <NoSpeechNotice />}
      <div className="rm-write">
        <label htmlFor={id} className="kicker">
          Your answer
        </label>
        <textarea
          id={id}
          ref={box}
          className="rm-textarea"
          rows={narrow ? 6 : 7}
          value={m.draft}
          onChange={(e) => actions.setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Ctrl or Command + Enter sends; Enter alone is a new line
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && canSend(m)) {
              e.preventDefault();
              actions.send();
            }
          }}
          placeholder="Type your answer in your own words."
          disabled={m.sending}
        />
      </div>
      {m.problem && m.problem.kind !== 'dropped' && <ProblemNotice problem={m.problem} actions={actions} />}
      {narrow ? (
        <div className="rm-round-row">
          <div className="rm-round-cell">
            <button type="button" className="rm-round rm-round-sm" aria-label="Speak instead" onClick={actions.speakInstead} disabled={m.sending || !m.speechSupported}>
              <MicIcon size={22} />
            </button>
            <span className="rm-muted rm-round-label">Speak</span>
          </div>
          <div className="rm-round-cell rm-round-main">
            <button type="button" className="rm-round rm-round-lg" aria-label="Send answer" onClick={actions.send} disabled={!canSend(m)}>
              <SendIcon size={28} />
            </button>
            <span className="rm-round-label rm-strong">{m.sending ? 'Sending…' : 'Send answer'}</span>
          </div>
          <div className="rm-round-cell">
            <button type="button" className="rm-round rm-round-sm" aria-label="Say the question again" onClick={actions.sayAgain} disabled={m.sending || !m.voiceSupported}>
              <RepeatIcon size={22} />
            </button>
            <span className="rm-muted rm-round-label">Say again</span>
          </div>
        </div>
      ) : (
        <div className="rm-buttons">
          <button type="button" className="btn btn-primary rm-btn" disabled={!canSend(m)} onClick={actions.send}>
            {m.sending ? 'Sending…' : 'Send answer'}
          </button>
          {m.speechSupported && (
            <button type="button" className="btn rm-btn" onClick={actions.speakInstead} disabled={m.sending}>
              <MicIcon />
              Speak instead
            </button>
          )}
        </div>
      )}
      <QuietRow model={m} actions={actions} sayAgain={!narrow} />
    </section>
  );
}

