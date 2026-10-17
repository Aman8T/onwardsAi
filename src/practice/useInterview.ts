/**
 * The interview room's state machine over the API: load (or carry on after a reload), read the interviewer's line aloud,
 * listen, write the words down, send the answer, take the next line. Everything the screen draws comes out of
 * `deriveRoom` (`room/model.ts`); everything it can do goes through `RoomActions`.
 *
 * How an answer goes (speech):
 *   a line arrives → the interviewer reads it (when the voice is on) → listening starts when the voice stops
 *   → words settle into `draft` (the browser's interim words wait in `interim`; with Sarvam they arrive a piece at a time
 *   after each pause, and `writing` says a piece is on its way) → Pause lets the person fix the text
 *   → Done answering stops listening, lets the last words arrive, and sends the text (never audio) with `mode: 'speech'`
 *   → the next line comes back and the cycle starts again.
 * Typing is the same cycle without the microphone. "I don't know" and "Skip" send no text and no model is asked.
 *
 * Who does the speech is decided when the room loads, once: `GET /api/speech/status` for the session says whether Sarvam can
 * write the answer down and read the lines, and `speech.ts` falls back to the browser's own speech (or typing) where it
 * cannot. If Sarvam stops answering mid-interview the listener gives it up for the rest of that interview, `speechNote`
 * says why once, and the next interview asks again.
 *
 * Failures: a request that never got an answer is a dropped connection (the draft is kept, "Carry on" loads the interview
 * again and keeps the draft when nothing moved); the server's other refusals are shown as it wrote them. Closing the page
 * loses nothing the server holds, and loses only the unsent draft.
 *
 * Functions here read the latest state through `live` and the sync refs and call only stable setters, so an effect or a
 * recogniser callback made on one render is still right on the next.
 */
import { useEffect, useRef, useState } from 'react';
import type { AnswerAction, AnswerMode, AssistantReply, InterviewSession } from '../../shared/interview';
import { actions } from '../store';
import type { PracticeQuery } from './route';
import { isOffline, PracticeApiError, realApi, sentenceOf, OFFLINE } from './api';
import type { InterviewApi } from './api';
import { deriveRoom, lineKey, speakParts } from './room/model';
import type { ListenState, MicNote, Problem, RoomActions, RoomModel } from './room/model';
import { fetchSpeechStatus } from './sarvam';
import { cancelSpeech, createListener, effectivePrefs, planSpeech, readPrefs, say, warmVoice, writePrefs } from './speech';
import type { Listener, SpeechPlan } from './speech';

/** Moves the Practice address (the store keeps the address and the screen in step). */
const openPractice = (patch: Partial<PracticeQuery>, opts?: { replace?: boolean }): void => actions.openPractice(patch, opts);

/** The function the screens navigate with: `open({ screen: 'room', session })`. */
export const usePracticeNav = () => openPractice;

export type Load = { status: 'loading' } | { status: 'error'; message: string; gone: boolean } | { status: 'ready' };

export interface WorkControls {
  /** A work sample whose work has not been handed in: the room shows the workspace. */
  active: boolean;
  handing: boolean;
  handError?: string;
  /** Resolves true when the work was taken (the room then asks about it). */
  handIn(work: string, testNote: string): Promise<boolean>;
  ask(message: string, work: string): Promise<AssistantReply>;
  /** Who does the speech in this interview now (the work sample's "Talk it through instead" listens the same way). */
  speech: SpeechPlan;
}

export interface UseInterview {
  load: Load;
  model: RoomModel | null;
  actions: RoomActions;
  work: WorkControls;
  retryLoad(): void;
}

export interface UseInterviewOptions {
  profileId: string;
  sessionId: string;
  /** The debrief is ready (or the interview had already ended): show it. */
  onDebrief: (sessionId: string) => void;
  /** To the Practice page. */
  onLeave: () => void;
  /** The server calls; scripted ones in the dev gallery and in tests. */
  api?: InterviewApi;
  /** `GET /api/speech/status`; a scripted one in tests. */
  speechStatus?: typeof fetchSpeechStatus;
}

/** Quiet this long while listening and the hint appears. There is no countdown. */
const SILENCE_MS = 10_000;

/** The room waits this long for the speech status before it settles for the browser's own speech. */
const STATUS_WAIT_MS = 2500;

const EMPTY_HINT = 'Nothing is written down yet. Say your answer, or type it.';

/** Words joined with one space, without touching either. */
const join = (a: string, b: string) => (a.trim() ? `${a.replace(/\s+$/, '')} ${b}` : b);

export function useInterview({ profileId, sessionId, onDebrief, onLeave, api = realApi, speechStatus = fetchSpeechStatus }: UseInterviewOptions): UseInterview {
  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [session, setSession] = useState<InterviewSession | null>(null);

  // what the person chose on the setup page, as far as this browser can do it (until the server says what Sarvam can do)
  const [chosen] = useState(readPrefs);
  const [prefs] = useState(() => effectivePrefs(chosen));
  const [plan, setPlan] = useState<SpeechPlan>(() => planSpeech(null, { profileId, sessionId }));
  const [answer, setAnswer] = useState<'speech' | 'typed'>(prefs.answer === 'speech' ? 'speech' : 'typed');
  const [listen, setListen] = useState<ListenState>('off');
  const [speaking, setSpeaking] = useState(false);
  const [draft, setDraft] = useState('');
  const [interim, setInterim] = useState('');
  const [writing, setWriting] = useState(false);
  const [silent, setSilent] = useState(false);
  const [micNote, setMicNote] = useState<MicNote | undefined>(() => (chosen.answer === 'speech' && plan.listen === 'none' ? 'unsupported' : undefined));
  const [speechNote, setSpeechNote] = useState<string | undefined>();
  const [voiceOn, setVoiceOn] = useState(prefs.voice !== 'off');
  const [slow] = useState(chosen.voice === 'slower');
  const [voiceBlocked, setVoiceBlocked] = useState(false);
  const [gate, setGate] = useState(false);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<Problem | undefined>();
  const [whyOpen, setWhyOpen] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [finishing, setFinishing] = useState<'no' | 'working' | 'failed'>('no');
  const [finishError, setFinishError] = useState<string | undefined>();
  const [handing, setHanding] = useState(false);
  const [handError, setHandError] = useState<string | undefined>();

  /* Synchronous mirrors: a handler or a recogniser callback reads these, never a stale closure. */
  const draftRef = useRef('');
  const interimRef = useRef('');
  const listenRef = useRef<ListenState>('off');
  const answerRef = useRef(answer);
  const micNoteRef = useRef(micNote);
  const busyRef = useRef(false);
  const finishRef = useRef(false);
  const usedSpeech = useRef(false);
  const recRef = useRef<Listener | null>(null);
  const planRef = useRef(plan);
  const speechAsked = useRef<Promise<void> | null>(null);
  const silenceTimer = useRef<number | undefined>(undefined);
  const listened = useRef({ ms: 0, since: null as number | null });
  const live = useRef({ session, gate, voiceOn, slow, api, speechStatus, profileId, sessionId, onDebrief, onLeave });
  live.current = { session, gate, voiceOn, slow, api, speechStatus, profileId, sessionId, onDebrief, onLeave };

  const setListenSync = (v: ListenState) => {
    listenRef.current = v;
    setListen(v);
  };
  const setAnswerSync = (v: 'speech' | 'typed') => {
    answerRef.current = v;
    setAnswer(v);
  };
  const setMicNoteSync = (v: MicNote | undefined) => {
    micNoteRef.current = v;
    setMicNote(v);
  };
  const setPlanSync = (v: SpeechPlan) => {
    planRef.current = v;
    setPlan(v);
  };
  const updateDraft = (v: string) => {
    draftRef.current = v;
    setDraft(v);
  };
  const clearInterim = () => {
    interimRef.current = '';
    setInterim('');
  };
  /** Words still settling when listening stopped join the draft: nothing said is lost. */
  const flushInterim = () => {
    const rest = interimRef.current.trim();
    if (rest) updateDraft(join(draftRef.current, rest));
    clearInterim();
  };

  /* the clock of this answer: time spent listening, not time spent thinking */
  const markListening = (on: boolean) => {
    const l = listened.current;
    if (on && l.since === null) l.since = Date.now();
    if (!on && l.since !== null) {
      l.ms += Date.now() - l.since;
      l.since = null;
    }
  };
  const clock = () => Math.floor((listened.current.ms + (listened.current.since !== null ? Date.now() - listened.current.since : 0)) / 1000);

  const clearSilence = () => {
    window.clearTimeout(silenceTimer.current);
    silenceTimer.current = undefined;
  };
  const armSilence = () => {
    clearSilence();
    if (listenRef.current !== 'listening') return;
    silenceTimer.current = window.setTimeout(() => setSilent(true), SILENCE_MS);
  };

  /* ───────────────────────── who does the speech ───────────────────────── */

  /** Ask once, when the room first loads, what Sarvam can do for this interview; what cannot be had falls back to the browser. */
  const askSpeech = (): Promise<void> => {
    speechAsked.current ??= (async () => {
      const ctl = new AbortController();
      const timer = window.setTimeout(() => ctl.abort(), STATUS_WAIT_MS);
      let status: Awaited<ReturnType<typeof fetchSpeechStatus>> | null = null;
      try {
        status = await live.current.speechStatus(live.current.profileId, live.current.sessionId, ctl.signal);
      } catch {
        /* no word from the speech service: the browser's own speech is the plan */
      }
      window.clearTimeout(timer);
      const next = planSpeech(status, { profileId: live.current.profileId, sessionId: live.current.sessionId });
      setPlanSync(next);
      const eff = effectivePrefs(chosen, next);
      setAnswerSync(eff.answer === 'speech' ? 'speech' : 'typed');
      setVoiceOn(eff.voice !== 'off');
      setMicNoteSync(chosen.answer === 'speech' && next.listen === 'none' ? 'unsupported' : undefined);
    })();
    return speechAsked.current;
  };

  /** How the interviewer's lines are read: with Sarvam's voice when this interview has it, else the browser's. */
  const voiceVia = () => (planRef.current.voice === 'sarvam' ? { sarvam: { profileId: live.current.profileId, sessionId: live.current.sessionId } } : {});

  /**
   * Ask for a new line's audio the moment it is known, so the request is under way while the room renders. The server has
   * usually made the line ahead already, and `say` finds this request in flight or its audio decoded.
   */
  const warmLine = (next: InterviewSession, voiceWanted: boolean) => {
    const line = next.current;
    const workspace = next.kind === 'work' && !!next.task && next.work === undefined;
    if (!line || !voiceWanted || workspace || next.status === 'done' || planRef.current.voice !== 'sarvam') return;
    warmVoice(speakParts(line, false), { slow: live.current.slow, ...voiceVia() });
  };

  /* ───────────────────────── the recogniser ───────────────────────── */

  const ensureRecognizer = (): Listener | null => {
    if (recRef.current) return recRef.current;
    recRef.current = createListener(planRef.current, {
      onFinal: (t) => {
        usedSpeech.current = true;
        updateDraft(join(draftRef.current, t));
        clearInterim();
        setSilent(false);
        setProblem((p) => (p?.kind === 'empty' ? undefined : p));
        armSilence();
      },
      onInterim: (t) => {
        interimRef.current = t;
        setInterim(t);
        if (t) {
          usedSpeech.current = true;
          setSilent(false);
          armSilence();
        }
      },
      onWriting: (pending) => setWriting(pending),
      onVoice: () => {
        // a voice is heard: the person is answering, even when no words have landed yet
        setSilent(false);
        armSilence();
      },
      onPieceTrouble: (message) => setProblem((p) => (p?.kind === 'dropped' ? p : { kind: 'piece', message })),
      onFallback: (note, to) => {
        setSpeechNote(note);
        setPlanSync({ ...planRef.current, listen: to });
        if (to === 'none') {
          // typing is what is left
          markListening(false);
          clearSilence();
          setSilent(false);
          flushInterim();
          setListenSync('off');
          setAnswerSync('typed');
          recRef.current = null;
        }
      },
      onIssue: (issue, fatal) => {
        if (!fatal) {
          if (issue === 'no-speech') setSilent(true);
          return;
        }
        markListening(false);
        clearSilence();
        setSilent(false);
        flushInterim();
        setListenSync('off');
        if (issue === 'unsupported') {
          setMicNoteSync('unsupported');
          setAnswerSync('typed');
        } else {
          setMicNoteSync(issue === 'blocked' ? 'blocked' : 'network');
        }
      },
    });
    return recRef.current;
  };

  const startListening = () => {
    // nothing is recorded while the interviewer is speaking: a voice still going (Resume during "Say it again") stops first
    cancelSpeech();
    setSpeaking(false);
    const rec = ensureRecognizer();
    if (!rec) {
      setMicNoteSync('unsupported');
      setAnswerSync('typed');
      setListenSync('off');
      return;
    }
    if (micNoteRef.current === 'blocked' || micNoteRef.current === 'network') setMicNoteSync(undefined);
    setListenSync('listening');
    setSilent(false);
    markListening(true);
    armSilence();
    rec.start();
  };

  /** Stop listening for now; what was heard so far joins the draft. */
  const stopListening = async () => {
    markListening(false);
    clearSilence();
    setSilent(false);
    await recRef.current?.stop();
    flushInterim();
  };

  /* ───────────────────────── the line ───────────────────────── */

  const key = session ? lineKey(session) : '';
  const ready = load.status === 'ready';

  useEffect(() => {
    const s = live.current.session;
    const line = s?.current;
    if (!s || !line || !ready) return;
    // a new line: the last answer is over
    recRef.current?.abort();
    clearSilence();
    listened.current = { ms: 0, since: null };
    usedSpeech.current = false;
    updateDraft('');
    clearInterim();
    setWriting(false);
    setSpeechNote(undefined);
    setSilent(false);
    setWhyOpen(false);
    setProblem((p) => (p?.kind === 'dropped' ? p : undefined));

    // the work sample's task is read, not heard: the person is about to work
    const workspace = s.kind === 'work' && !!s.task && s.work === undefined;
    const gated = live.current.gate && !!line.lead && line.kind !== 'done';
    const wantsMic = () => answerRef.current === 'speech' && !gated && line.kind !== 'done' && !workspace;
    const after = () => {
      setSpeaking(false);
      if (wantsMic() && micNoteRef.current !== 'blocked' && micNoteRef.current !== 'network') startListening();
      else setListenSync('off');
    };
    cancelSpeech();
    if (live.current.voiceOn && planRef.current.voice !== 'none' && !workspace) {
      setSpeaking(true);
      setListenSync(wantsMic() ? 'waiting' : 'off');
      say(speakParts(line, gated), {
        slow: live.current.slow,
        ...voiceVia(),
        onStart: () => setVoiceBlocked(false),
        onBlocked: () => setVoiceBlocked(true),
        onEnd: after,
      });
    } else {
      after();
    }
    return () => {
      cancelSpeech();
    };
    // a new line, the gate opening, or the interview becoming ready: nothing else restarts the line
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, gate, ready]);

  useEffect(
    () => () => {
      recRef.current?.abort();
      cancelSpeech();
      window.clearTimeout(silenceTimer.current);
    },
    [],
  );

  /* ───────────────────────── loading ───────────────────────── */

  useEffect(() => {
    const ctl = new AbortController();
    setLoad({ status: 'loading' });
    // the speech status is asked once, alongside the first load: nothing is read or listened to before it is known
    Promise.all([live.current.api.fetchSession(sessionId, profileId, ctl.signal), askSpeech()]).then(
      ([view]) => {
        if (ctl.signal.aborted) return;
        warmLine(view.session, effectivePrefs(chosen, planRef.current).voice !== 'off');
        setSession(view.session);
        setLoad({ status: 'ready' });
        if (view.session.status === 'done' && view.debrief) {
          finishRef.current = true;
          live.current.onDebrief(sessionId);
        }
      },
      (err: unknown) => {
        if (ctl.signal.aborted) return;
        setLoad({ status: 'error', message: sentenceOf(err), gone: err instanceof PracticeApiError && err.status === 404 });
      },
    );
    return () => ctl.abort();
  }, [sessionId, profileId, attempt]);

  /* ───────────────────────── the end ───────────────────────── */

  const finish = async () => {
    if (finishRef.current) return;
    finishRef.current = true;
    setConfirmEnd(false);
    setFinishing('working');
    setFinishError(undefined);
    recRef.current?.abort();
    clearSilence();
    setListenSync('off');
    try {
      await live.current.api.endSession(live.current.sessionId, live.current.profileId);
      cancelSpeech();
      live.current.onDebrief(live.current.sessionId);
    } catch (err) {
      finishRef.current = false;
      setFinishing('failed');
      setFinishError(sentenceOf(err));
    }
  };

  // the last line, or an interview that ended elsewhere: ask for the debrief
  const over = !!session && (session.status === 'done' || !session.current || session.current.kind === 'done');
  useEffect(() => {
    if (over && ready) void finish();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [over, ready]);

  /* ───────────────────────── sending ───────────────────────── */

  const applySession = (next: InterviewSession, action: AnswerAction) => {
    setGate(action === 'dont_know' && next.current?.kind !== 'done' && !!next.current?.lead);
    setProblem(undefined);
    warmLine(next, live.current.voiceOn);
    setSession(next);
  };

  const sendFailed = (err: unknown) => {
    // the person's text stays where it is; speech mode keeps it fixable
    if (answerRef.current === 'speech') setListenSync('paused');
    if (isOffline(err)) {
      setProblem({ kind: 'dropped', message: OFFLINE });
      return;
    }
    const message = sentenceOf(err);
    if (err instanceof PracticeApiError && err.status === 409) {
      if (/ended/i.test(message)) {
        void finish();
        return;
      }
      setProblem({ kind: 'moved', message });
      return;
    }
    if (err instanceof PracticeApiError && err.status === 404) {
      setProblem({ kind: 'gone', message });
      return;
    }
    setProblem({ kind: 'other', message });
  };

  /** One answer (or button) to the server. The caller holds `busyRef`. */
  const transmit = async (text: string, action: AnswerAction) => {
    setSending(true);
    setProblem(undefined);
    const mode: AnswerMode = action === 'answer' ? (usedSpeech.current ? 'speech' : 'typed') : answerRef.current === 'speech' ? 'speech' : 'typed';
    try {
      const next = await live.current.api.sendAnswer(live.current.sessionId, { profileId: live.current.profileId, text, mode, action });
      applySession(next, action);
    } catch (err) {
      sendFailed(err);
    } finally {
      busyRef.current = false;
      setSending(false);
    }
  };

  /** A button that needs no text: "I don't know" and "Skip". */
  const press = async (action: 'dont_know' | 'skip') => {
    if (busyRef.current || finishRef.current) return;
    busyRef.current = true;
    cancelSpeech();
    setSpeaking(false);
    recRef.current?.abort();
    clearSilence();
    markListening(false);
    setListenSync('off');
    await transmit('', action);
  };

  /* ───────────────────────── the actions ───────────────────────── */

  const actions: RoomActions = {
    startAnswering() {
      cancelSpeech();
      setSpeaking(false);
      if (answerRef.current === 'speech') startListening();
    },
    pause() {
      if (listenRef.current !== 'listening') return;
      setListenSync('paused');
      void stopListening();
    },
    resume() {
      if (listenRef.current === 'paused') startListening();
    },
    done() {
      if (busyRef.current || finishRef.current) return;
      busyRef.current = true;
      setSending(true);
      cancelSpeech();
      setSpeaking(false);
      setListenSync('off');
      void (async () => {
        await stopListening();
        const text = draftRef.current.trim();
        if (!text) {
          busyRef.current = false;
          setSending(false);
          // a piece that could not be made out already says why nothing is written
          setProblem((p) => (p?.kind === 'piece' ? p : { kind: 'empty', message: EMPTY_HINT }));
          startListening();
          return;
        }
        await transmit(text, 'answer');
      })();
    },
    send() {
      if (busyRef.current || finishRef.current) return;
      const text = draftRef.current.trim();
      if (!text) return;
      busyRef.current = true;
      cancelSpeech();
      setSpeaking(false);
      void transmit(text, 'answer');
    },
    typeInstead() {
      void (async () => {
        if (listenRef.current === 'listening' || listenRef.current === 'paused') await stopListening();
        cancelSpeech();
        setSpeaking(false);
        setListenSync('off');
        setAnswerSync('typed');
      })();
    },
    speakInstead() {
      if (planRef.current.listen === 'none') return;
      setAnswerSync('speech');
      setMicNoteSync(undefined);
      cancelSpeech();
      setSpeaking(false);
      startListening();
    },
    setDraft(value) {
      updateDraft(value);
      setProblem((p) => (p?.kind === 'empty' ? undefined : p));
    },
    sayAgain() {
      const s = live.current.session;
      const line = s?.current;
      if (!s || !line || planRef.current.voice === 'none') return;
      const resume = listenRef.current === 'listening' || listenRef.current === 'waiting';
      const kept = listenRef.current === 'paused' ? 'paused' : 'off';
      if (listenRef.current === 'listening') void stopListening();
      setSpeaking(true);
      setListenSync(resume ? 'waiting' : kept);
      say(speakParts(line, live.current.gate && !!line.lead && line.kind !== 'done'), {
        slow: live.current.slow,
        ...voiceVia(),
        onStart: () => setVoiceBlocked(false),
        onBlocked: () => setVoiceBlocked(true),
        onEnd: () => {
          setSpeaking(false);
          if (resume && answerRef.current === 'speech' && !live.current.gate) startListening();
          else setListenSync(kept);
        },
      });
    },
    dontKnow() {
      void press('dont_know');
    },
    skip() {
      void press('skip');
    },
    next() {
      setGate(false);
    },
    toggleVoice() {
      const on = !live.current.voiceOn;
      setVoiceOn(on);
      writePrefs({ answer: readPrefs().answer, voice: on ? (live.current.slow ? 'slower' : 'normal') : 'off' });
      if (!on) {
        const waiting = listenRef.current === 'waiting';
        cancelSpeech();
        setSpeaking(false);
        if (waiting && answerRef.current === 'speech') startListening();
        else if (waiting) setListenSync('off');
      }
    },
    toggleWhy() {
      setWhyOpen((v) => !v);
    },
    askEnd() {
      setConfirmEnd(true);
    },
    cancelEnd() {
      setConfirmEnd(false);
    },
    confirmEnd() {
      void finish();
    },
    carryOn() {
      const before = live.current.session;
      setProblem(undefined);
      void (async () => {
        try {
          const view = await live.current.api.fetchSession(live.current.sessionId, live.current.profileId);
          // the answer got through after all: the new line starts a fresh answer. Otherwise the draft is still there to send.
          setSession(view.session);
          if (before && lineKey(view.session) === lineKey(before)) {
            if (answerRef.current === 'speech') setListenSync('paused');
          }
        } catch (err) {
          setProblem(isOffline(err) ? { kind: 'dropped', message: OFFLINE } : { kind: 'other', message: sentenceOf(err) });
        }
      })();
    },
    tryMicAgain() {
      setMicNoteSync(undefined);
      if (answerRef.current !== 'speech') setAnswerSync('speech');
      startListening();
    },
    retryFinish() {
      void finish();
    },
    meterBlocked() {
      if (listenRef.current !== 'listening') return;
      void stopListening();
      recRef.current?.abort();
      setListenSync('off');
      setMicNoteSync('blocked');
    },
    leave() {
      live.current.onLeave();
    },
    reload() {
      setAttempt((n) => n + 1);
    },
  };

  /* ───────────────────────── the work sample ───────────────────────── */

  const work: WorkControls = {
    active: !!session && session.kind === 'work' && !!session.task && session.work === undefined && session.status === 'active',
    handing,
    handError,
    async handIn(text, testNote) {
      if (handing) return false;
      setHanding(true);
      setHandError(undefined);
      try {
        const next = await live.current.api.submitWork(live.current.sessionId, {
          profileId: live.current.profileId,
          work: text,
          ...(testNote.trim() ? { testNote: testNote.trim() } : {}),
        });
        setSession(next);
        return true;
      } catch (err) {
        setHandError(sentenceOf(err));
        return false;
      } finally {
        setHanding(false);
      }
    },
    ask(message, text) {
      return live.current.api.askAssistant(live.current.sessionId, { profileId: live.current.profileId, message, ...(text.trim() ? { work: text } : {}) });
    },
    speech: plan,
  };

  const model = session
    ? deriveRoom(session, {
        gate,
        answer,
        listen,
        speaking,
        draft,
        interim,
        listenBy: plan.listen === 'sarvam' ? 'sarvam' : 'browser',
        writing,
        silent,
        ...(micNote ? { micNote } : {}),
        ...(speechNote ? { speechNote } : {}),
        speechSupported: plan.listen !== 'none',
        voiceSupported: plan.voice !== 'none',
        voiceOn,
        slow,
        voiceBlocked,
        sending,
        ...(problem ? { problem } : {}),
        whyOpen,
        confirmEnd,
        finishing,
        ...(finishError ? { finishError } : {}),
        clock,
      })
    : null;

  return { load, model, actions, work, retryLoad: () => setAttempt((n) => n + 1) };
}
