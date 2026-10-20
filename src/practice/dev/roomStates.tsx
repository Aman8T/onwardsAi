/**
 * Every state of the room, the work sample and the setup page, drawn from fixtures with no server and no microphone:
 * `ROOM_STATES` maps a state name (the dev page's `?state=`) to a screen. The dev page (`DevPractice`) merges these with
 * its own. The screens are the real components (`RoomView`, `WorkView`, `SetupView`); only the data is made up, and the
 * buttons that change what is drawn (Why this question, Pause, Type instead, the voice, the skips, the assistant's switch)
 * work on the page. The meter shows fixed bars here; in the app it moves only with the voice.
 *
 * Speech by Sarvam AI has its own states: `room-listening-sarvam` (pieces settled, and the quiet "Writing down what you
 * said…" for the one on its way), `room-sarvam-unclear` (one piece could not be made out), `room-sarvam-fallback` (Sarvam
 * stopped answering and the browser's speech took over, said once), `phone-sarvam`, and `setup-sarvam` (the setup page with
 * Sarvam's privacy copy). `setup-graph` is the setup page for a role of the person's graph, which has no posting.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import type { AssistantTurn, InterviewFocus, InterviewLength, InterviewSession } from '../../../shared/interview';
import { HEADOUT } from '../../discover/dev/fixtures';
import { deriveRoom, noopActions } from '../room/model';
import type { RoomActions, RoomUi } from '../room/model';
import { RoomView } from '../room/RoomView';
import { SetupView } from '../setup/SetupView';
import type { Prefs } from '../speech';
import { WorkView } from '../work/WorkView';
import type { Dictation } from '../work/useDictation';
import { GRAPH_ROLE_SUB, PIECE_UNCLEAR, SARVAM_DOWN } from '../words';
import type { SpeechBy } from '../words';
import '../../styles/practice-room.css';
import {
  ANSWER_S2,
  ANSWER_S2_PUSH,
  ASSISTANT_LOG,
  CART_AFTER,
  CART_BEFORE,
  LIVE_BARS,
  LIVE_BARS_2,
  GRAPH_PLAN_FULL,
  GRAPH_PLAN_SHORT,
  GRAPH_ROLE,
  PLAN_FULL,
  PLAN_SHORT,
  PLAN_THIN,
  SESSION_ASKING,
  SESSION_DONE,
  SESSION_DONTKNOW,
  SESSION_FOLLOWUP,
  SESSION_NEXT,
  SESSION_Q4,
  SESSION_WORK,
  SESSION_WORK_TABLE,
  TEST_NOTE,
  UI,
} from './roomFixtures';

const clock42 = () => 42;

/* ───────────────────────── the room ───────────────────────── */

interface RoomPreviewProps {
  session: InterviewSession;
  ui?: Partial<RoomUi>;
  narrow?: boolean;
  /** Fixed bars for the meter (the app has none: it reads the microphone). */
  meter?: readonly number[];
}

/** A room whose buttons change what is drawn, and nothing else: no server, no microphone, no speech. */
function RoomPreview({ session, ui: initial = {}, narrow = false, meter }: RoomPreviewProps) {
  const [ui, setUi] = useState<RoomUi>({ ...UI, ...initial });
  const patch = (p: Partial<RoomUi>) => setUi((cur) => ({ ...cur, ...p }));
  const model = deriveRoom(session, ui);

  const actions: RoomActions = {
    ...noopActions,
    startAnswering: () => patch({ listen: 'listening', speaking: false }),
    // with Sarvam the last piece is still being written down for a moment after Pause or Done
    pause: () => {
      patch({ listen: 'paused' });
      if (ui.writing) window.setTimeout(() => patch({ writing: false }), 1200);
    },
    resume: () => patch({ listen: 'listening' }),
    done: () => {
      patch({ sending: true, listen: 'off', writing: false });
      window.setTimeout(() => patch({ sending: false }), 1200);
    },
    send: () => {
      patch({ sending: true });
      window.setTimeout(() => patch({ sending: false, draft: '' }), 1200);
    },
    typeInstead: () => patch({ answer: 'typed', listen: 'off', speaking: false, micNote: ui.micNote === 'unsupported' ? 'unsupported' : undefined }),
    speakInstead: () => patch({ answer: 'speech', listen: 'listening', micNote: undefined }),
    tryMicAgain: () => patch({ micNote: undefined, listen: 'listening' }),
    setDraft: (draft) => patch({ draft }),
    sayAgain: () => {
      patch({ speaking: true });
      window.setTimeout(() => patch({ speaking: false }), 2500);
    },
    next: () => patch({ gate: false }),
    toggleVoice: () => patch({ voiceOn: !ui.voiceOn }),
    toggleWhy: () => patch({ whyOpen: !ui.whyOpen }),
    askEnd: () => patch({ confirmEnd: true }),
    cancelEnd: () => patch({ confirmEnd: false }),
    confirmEnd: () => patch({ confirmEnd: false, finishing: 'working' }),
    carryOn: () => patch({ problem: undefined, listen: 'paused' }),
  };

  return <RoomView model={model} actions={actions} narrow={narrow} {...(meter ? { meterPreview: meter } : {})} />;
}

const room = (props: RoomPreviewProps) => () => <RoomPreview {...props} />;

const phone = (props: RoomPreviewProps) => () => (
  <div className="rm-phone-frame">
    <RoomPreview {...props} narrow />
  </div>
);

/* ───────────────────────── the work sample ───────────────────────── */

const IDLE_TALK: Dictation = { on: false, interim: '', by: 'browser', writing: false, start() {}, stop: () => Promise.resolve(), clock: () => 0, blocked() {} };

interface WorkPreviewProps {
  session: InterviewSession;
  work: string;
  note: string;
  log?: AssistantTurn[];
  talking?: boolean;
}

function WorkPreview({ session, work: initialWork, note: initialNote, log = [], talking = false }: WorkPreviewProps) {
  const [work, setWork] = useState(initialWork);
  const [note, setNote] = useState(initialNote);
  const [on, setOn] = useState(true);
  const [message, setMessage] = useState('');
  const [talk, setTalk] = useState(talking);
  const [ui, setUi] = useState<RoomUi>({ ...UI });
  const task = session.task;
  if (!task) return null;
  const model = deriveRoom(session, ui);
  const actions: RoomActions = {
    ...noopActions,
    toggleVoice: () => setUi((u) => ({ ...u, voiceOn: !u.voiceOn })),
    askEnd: () => setUi((u) => ({ ...u, confirmEnd: true })),
  };
  return (
    <WorkView
      model={model}
      actions={actions}
      task={task}
      work={work}
      testNote={note}
      minutesIn={6}
      handing={false}
      assistant={{ on, log, message, asking: false, ...(log.length > 0 ? { left: 4 } : {}) }}
      talk={{ ...IDLE_TALK, on: talk, interim: talk ? 'The coupon answer can arrive late, so' : '', clock: clock42 }}
      meterPreview={LIVE_BARS}
      onWork={setWork}
      onTestNote={setNote}
      onHandIn={() => undefined}
      onTalk={setTalk}
      assistantHandlers={{ onSwitch: setOn, onMessage: setMessage, onAsk: () => setMessage('') }}
    />
  );
}

const work = (props: WorkPreviewProps) => () => <WorkPreview {...props} />;

/* ───────────────────────── the setup page ───────────────────────── */

function SetupPreview({ thin = false, by = 'browser', graph = false }: { thin?: boolean; by?: SpeechBy; graph?: boolean }) {
  const [skipped, setSkipped] = useState<ReadonlySet<string>>(new Set());
  const [focus, setFocus] = useState<InterviewFocus | undefined>('claims');
  const [length, setLength] = useState<InterviewLength>('full');
  const [prefs, setPrefs] = useState<Prefs>({ answer: 'speech', voice: 'normal' });
  const [testing, setTesting] = useState(false);
  const [heard, setHeard] = useState(false);
  const [short, full] = graph ? [GRAPH_PLAN_SHORT, GRAPH_PLAN_FULL] : [PLAN_SHORT, PLAN_FULL];
  const plan = thin ? PLAN_THIN : length === 'short' ? short : full;
  return (
    <SetupView
      kind="role"
      sample={false}
      title={plan.title}
      sub={graph ? GRAPH_ROLE_SUB : 'From the posting you applied to on 14 Oct · Apply link stays in Your applications'}
      {...(graph ? { graphRole: GRAPH_ROLE } : { job: HEADOUT })}
      plan={plan}
      loading={false}
      lengths={{ short, full }}
      length={length}
      {...(focus ? { focus } : {})}
      skipped={skipped}
      prefs={prefs}
      speechSupported
      voiceSupported
      listenBy={by}
      mic={{ testing, heard }}
      left={2}
      starting={false}
      onBack={() => undefined}
      onSkip={(id) =>
        setSkipped((prev) => {
          const next = new Set(prev);
          if (next.has(id)) next.delete(id);
          else next.add(id);
          return next;
        })
      }
      onFocus={setFocus}
      onLength={setLength}
      onAnswer={(answer) => setPrefs((p) => ({ ...p, answer }))}
      onVoice={(voice) => setPrefs((p) => ({ ...p, voice }))}
      onTestMic={() => setTesting((t) => !t)}
      onMicLevel={(peak) => peak > 0.25 && setHeard(true)}
      onMicBlocked={() => setTesting(false)}
      onStart={() => undefined}
      onRetry={() => undefined}
      onOtherKind={() => undefined}
      onAnalyse={() => undefined}
      onAddToProfile={() => undefined}
    />
  );
}

/* ───────────────────────── the list ───────────────────────── */

export const ROOM_STATES: Record<string, () => ReactElement> = {
  setup: () => (
    <div className="rm-frame">
      <SetupPreview />
    </div>
  ),
  'setup-thin': () => (
    <div className="rm-frame">
      <SetupPreview thin />
    </div>
  ),
  'setup-sarvam': () => (
    <div className="rm-frame">
      <SetupPreview by="sarvam" />
    </div>
  ),
  'setup-graph': () => (
    <div className="rm-frame">
      <SetupPreview graph />
    </div>
  ),

  // the room, by voice
  'room-asking': room({ session: SESSION_ASKING, ui: { speaking: true, listen: 'waiting' } }),
  'room-listening': room({
    session: SESSION_ASKING,
    ui: { listen: 'listening', draft: 'We, um, did a lot of things. Images, the bundle, some fonts. And it', interim: 'got a lot faster.', clock: clock42 },
    meter: LIVE_BARS,
  }),
  'room-paused': room({ session: SESSION_ASKING, ui: { listen: 'paused', draft: ANSWER_S2, clock: clock42 } }),

  // the room, by voice, with Sarvam AI writing the words down: they arrive after each pause
  'room-listening-sarvam': room({
    session: SESSION_ASKING,
    ui: { listenBy: 'sarvam', listen: 'listening', draft: 'We did a lot of things. Images, the bundle, some fonts.', writing: true, clock: clock42 },
    meter: LIVE_BARS,
  }),
  'room-sarvam-unclear': room({
    session: SESSION_ASKING,
    ui: {
      listenBy: 'sarvam',
      listen: 'listening',
      draft: 'We did a lot of things. Images, the bundle, some fonts.',
      problem: { kind: 'piece', message: PIECE_UNCLEAR },
      clock: clock42,
    },
    meter: LIVE_BARS_2,
  }),
  'room-sarvam-fallback': room({
    session: SESSION_ASKING,
    ui: {
      listenBy: 'browser',
      listen: 'listening',
      speechNote: SARVAM_DOWN,
      draft: 'We did a lot of things. Images, the bundle, some fonts. And it',
      interim: 'got a lot faster.',
      clock: clock42,
    },
    meter: LIVE_BARS,
  }),
  'room-followup': room({ session: SESSION_FOLLOWUP, ui: { speaking: true, listen: 'waiting' } }),
  'room-followup-listening': room({
    session: SESSION_FOLLOWUP,
    ui: { listen: 'listening', draft: "I think the biggest one was the images? I'm not sure how we measured it, the", interim: 'PM had a dashboard.', clock: () => 19 },
    meter: LIVE_BARS_2,
  }),
  'room-dontknow': room({ session: SESSION_DONTKNOW, ui: { gate: true, speaking: true } }),
  'room-next': room({ session: SESSION_NEXT, ui: { speaking: true, listen: 'waiting' } }),
  'room-why': room({ session: SESSION_ASKING, ui: { speaking: true, listen: 'waiting', whyOpen: true } }),
  'room-why-gap': room({ session: SESSION_NEXT, ui: { listen: 'listening', whyOpen: true, clock: () => 3 } }),

  // the room, by typing
  'room-typing': room({ session: SESSION_ASKING, ui: { answer: 'typed', draft: ANSWER_S2_PUSH.slice(0, 54) } }),

  // the edges
  'room-mic-blocked': room({ session: SESSION_ASKING, ui: { listen: 'off', micNote: 'blocked' } }),
  'room-no-speech': room({ session: SESSION_ASKING, ui: { answer: 'typed', micNote: 'unsupported', speechSupported: false } }),
  'room-silence': room({ session: SESSION_ASKING, ui: { listen: 'listening', silent: true, clock: () => 10 } }),
  'room-dropped': room({
    session: SESSION_Q4,
    ui: { answer: 'typed', draft: 'I paired with them twice a week', problem: { kind: 'dropped', message: "We couldn't reach Leafout. Check your connection and try again." } },
  }),
  'room-sending': room({ session: SESSION_ASKING, ui: { listen: 'off', sending: true, draft: ANSWER_S2 } }),
  'room-end': room({ session: SESSION_ASKING, ui: { listen: 'listening', confirmEnd: true, clock: clock42 } }),
  'room-finishing': room({ session: SESSION_DONE, ui: { finishing: 'working' } }),
  'room-finish-failed': room({ session: SESSION_DONE, ui: { finishing: 'failed', finishError: 'Practice is paused for today while we keep costs in check. Your debriefs are still here.' } }),

  // the work sample
  work: work({ session: SESSION_WORK, work: CART_BEFORE, note: '' }),
  'work-assistant': work({ session: SESSION_WORK, work: CART_AFTER, note: TEST_NOTE, log: ASSISTANT_LOG }),
  'work-talk': work({ session: SESSION_WORK, work: CART_AFTER, note: TEST_NOTE, log: ASSISTANT_LOG, talking: true }),
  'work-table': work({ session: SESSION_WORK_TABLE, work: '', note: '' }),

  // the phone
  'phone-room': phone({ session: SESSION_ASKING, ui: { listen: 'listening', draft: 'We, um, did a lot of things. Images, the bundle, some fonts. And it', interim: 'got a lot faster.', clock: clock42 }, meter: LIVE_BARS }),
  'phone-sarvam': phone({
    session: SESSION_ASKING,
    ui: { listenBy: 'sarvam', listen: 'listening', draft: 'We did a lot of things. Images, the bundle, some fonts.', writing: true, clock: clock42 },
    meter: LIVE_BARS,
  }),
  'phone-asking': phone({ session: SESSION_ASKING, ui: { speaking: true, listen: 'waiting' } }),
  'phone-typing': phone({ session: SESSION_ASKING, ui: { answer: 'typed', draft: 'We found the slow part with' } }),
  'phone-dontknow': phone({ session: SESSION_DONTKNOW, ui: { gate: true, speaking: true } }),
};
