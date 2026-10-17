/**
 * Speech in the practice room. Two providers sit behind the same two interfaces (`Recognizer`, and `say` for the voice), so
 * the room does not care which one runs:
 *
 *   Sarvam AI   when the server offers it (`GET /api/speech/status`, see `sarvam.ts`). The microphone is recorded in pieces
 *               cut at a pause (about 800 ms of quiet after speech, or two seconds short of the longest piece the server
 *               takes); each piece with speech in it is sent as it is cut, and the words come back in order. The
 *               interviewer's lines come back as audio and are played with the Web Audio API. Tayyari keeps no audio.
 *   the browser the Web Speech API: it turns speech into text (in Chrome that runs on Google's speech service, which the
 *               setup screen says) and reads the lines aloud. Nothing leaves the browser except the text a person confirms.
 *
 * Sarvam is the first choice and the browser is the fallback, and typing always works. If Sarvam stops answering in the
 * middle of an interview (the key is refused, the day's budget is used, a 503), the listener gives it up for the rest of
 * that interview, says why once, and carries on with the browser's recogniser, or with typing when the browser has none.
 *
 *   recognizer   `createListener(plan, …)` picks the provider and does the fallback. The browser's own recogniser is
 *                `createRecognizer`: `SpeechRecognition` / `webkitSpeechRecognition`, continuous with interim results;
 *                Chrome stops a session by itself after a stretch of quiet, and while listening is wanted it starts again.
 *                Errors are mapped to four issues: blocked, no-speech (quiet, not fatal), network, unsupported.
 *   voice        `say(lines, …)`: Sarvam audio when asked for, `speechSynthesis` otherwise or when a line cannot be had.
 *                The lines of one call are requested together and played as they arrive; `warmVoice` starts the
 *                requests the moment a new line is known, before the room has rendered it.
 *   microphone   one `getUserMedia` stream shared by the level meter and the Sarvam recogniser (`leaseMic`), let go when the
 *                last of them is done. About 24 bars are written per frame straight to the elements (never React state).
 *                Under reduced motion nothing is painted: the level is only read.
 *   prefs        how the person answers and the interviewer's voice, kept in this browser.
 *
 * Every API is feature-detected. Nothing is ever uploaded but the pieces of an answer, and only while listening.
 */
import type { SpeechStatus, TranscribeResult } from '../../shared/speech';
import { OFFLINE, PracticeApiError, isOffline } from './api';
import { fetchSpeakAudio, transcribePiece } from './sarvam';
import type { PieceRequest } from './sarvam';
import { PIECE_UNCLEAR, SARVAM_DOWN, SARVAM_DOWN_TYPE } from './words';
import type { SpeechBy } from './words';

export type { SpeechBy } from './words';

export type SpeechIssue = 'blocked' | 'no-speech' | 'network' | 'unsupported';

/* ───────────────────────── support ───────────────────────── */

interface RecognitionAlternative {
  readonly transcript: string;
}
interface RecognitionResult {
  readonly length: number;
  readonly isFinal: boolean;
  readonly [index: number]: RecognitionAlternative | undefined;
}
interface RecognitionEvent {
  readonly resultIndex: number;
  readonly results: { readonly length: number; readonly [index: number]: RecognitionResult | undefined };
}
interface RecognitionErrorEvent {
  readonly error: string;
}
interface RecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: RecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionCtor = new () => RecognitionLike;

function recognitionCtor(): RecognitionCtor | undefined {
  if (typeof window === 'undefined') return undefined;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

type AudioContextCtor = new () => AudioContext;

function audioContextCtor(): AudioContextCtor | undefined {
  if (typeof window === 'undefined') return undefined;
  const w = window as unknown as { AudioContext?: AudioContextCtor; webkitAudioContext?: AudioContextCtor };
  return w.AudioContext ?? w.webkitAudioContext;
}

/** The browser can turn speech into text. */
export const recognitionSupported = (): boolean => recognitionCtor() !== undefined;

/** The browser can read a line aloud. */
export const synthesisSupported = (): boolean => typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined';

/** The microphone can be opened for the level meter. */
export const meterSupported = (): boolean => typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;

/** The first recording format the browser can make that the server takes (webm, ogg or mp4), with its codec. */
const RECORDER_MIMES = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'] as const;

export function recorderMime(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  return RECORDER_MIMES.find((mime) => {
    try {
      return MediaRecorder.isTypeSupported(mime);
    } catch {
      return false;
    }
  });
}

/** The page can record the microphone in pieces and watch its level (what Sarvam's speech-to-text needs). */
export const sarvamListenSupported = (): boolean => meterSupported() && audioContextCtor() !== undefined && recorderMime() !== undefined;

/** The page can decode and play the audio Sarvam sends back. */
export const sarvamVoiceSupported = (): boolean => audioContextCtor() !== undefined && typeof fetch === 'function';

/** `navigator.language` when it is English, else Indian English (the recogniser needs a language it knows). */
export function recognizerLang(): string {
  const nav = typeof navigator !== 'undefined' ? navigator.language : '';
  return /^en(-|$)/i.test(nav) ? nav : 'en-IN';
}

/* ───────────────────────── who does the speech ───────────────────────── */

/** Who writes the person's words down, and who reads the interviewer's lines. `none`: neither can be had here. */
export interface SpeechChoice {
  listen: SpeechBy | 'none';
  voice: SpeechBy | 'none';
}

/** Sarvam when the server offers it and this page can use it, else the browser's own, else nothing (typing, text only). */
export function chooseSpeech(status: SpeechStatus | null): SpeechChoice {
  return {
    listen: status?.stt && sarvamListenSupported() ? 'sarvam' : recognitionSupported() ? 'browser' : 'none',
    voice: status?.tts && sarvamVoiceSupported() ? 'sarvam' : synthesisSupported() ? 'browser' : 'none',
  };
}

/** The choice for one interview, with the limits the server gave for its pieces. */
export interface SpeechPlan extends SpeechChoice {
  profileId: string;
  sessionId: string;
  /** The longest piece of an answer one request takes, in seconds. */
  maxSegmentSeconds: number;
  /** The largest piece, in bytes. */
  maxSegmentBytes: number;
}

const DEFAULT_SEGMENT_SECONDS = 30;
const DEFAULT_SEGMENT_BYTES = 1_500_000;

export function planSpeech(status: SpeechStatus | null, ids: { profileId: string; sessionId: string }): SpeechPlan {
  return {
    ...chooseSpeech(status),
    ...ids,
    maxSegmentSeconds: status?.maxSegmentSeconds ?? DEFAULT_SEGMENT_SECONDS,
    maxSegmentBytes: status?.maxSegmentBytes ?? DEFAULT_SEGMENT_BYTES,
  };
}

/* ───────────────────────── the microphone, shared ───────────────────────── */

export type MeterTrouble = 'denied' | 'busy';

/** The microphone could not be had: `denied` when the browser or the person said no (or there is none), `busy` otherwise. */
export class MicError extends Error {
  readonly why: MeterTrouble;
  constructor(why: MeterTrouble) {
    super(`microphone ${why}`);
    this.name = 'MicError';
    this.why = why;
  }
}

/** The open microphone: its stream, and the audio graph's first node for anything that wants to watch the level. */
export interface MicShare {
  stream: MediaStream;
  context?: AudioContext;
  source?: MediaStreamAudioSourceNode;
}

export interface MicLease {
  /** The open microphone, or a `MicError` when it could not be had. */
  readonly ready: Promise<MicShare>;
  /** Let go. The microphone closes when every lease has. Safe to call at any time, and more than once. */
  release(): void;
}

const DENIED_NAMES = new Set(['NotAllowedError', 'SecurityError', 'NotFoundError', 'PermissionDeniedError']);

async function openMic(): Promise<MicShare> {
  if (!meterSupported()) throw new MicError('busy');
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    throw new MicError(err instanceof DOMException && DENIED_NAMES.has(err.name) ? 'denied' : 'busy');
  }
  const Ctor = audioContextCtor();
  if (!Ctor) return { stream };
  try {
    const context = new Ctor();
    void context.resume().catch(() => undefined);
    return { stream, context, source: context.createMediaStreamSource(stream) };
  } catch {
    // no way to read a level: whatever wants only the stream still has it
    return { stream };
  }
}

function closeMic(share: MicShare): void {
  try {
    share.source?.disconnect();
  } catch {
    /* already apart */
  }
  share.stream.getTracks().forEach((t) => t.stop());
  void share.context?.close().catch(() => undefined);
}

let sharedMic: { users: number; ready: Promise<MicShare> } | null = null;

/**
 * Take a share of the microphone. The first lease opens it (one `getUserMedia`, one audio context); later ones join it, so
 * the level meter and the recogniser never open two. It closes, and the browser's recording indicator goes out, when the
 * last lease is released.
 */
export function leaseMic(): MicLease {
  if (!sharedMic) {
    const entry = { users: 0, ready: openMic() };
    // a refusal is for the leases to hear; the next lease tries afresh
    entry.ready.catch(() => {
      if (sharedMic === entry) sharedMic = null;
    });
    sharedMic = entry;
  }
  const entry = sharedMic;
  entry.users += 1;
  let released = false;
  return {
    ready: entry.ready,
    release() {
      if (released) return;
      released = true;
      entry.users -= 1;
      if (entry.users > 0) return;
      if (sharedMic === entry) sharedMic = null;
      void entry.ready.then(closeMic, () => undefined);
    },
  };
}

/* ───────────────────────── the browser's recognizer ───────────────────────── */

export interface RecognizerOptions {
  lang?: string;
  /** Words the recogniser is sure of, in order. */
  onFinal(text: string): void;
  /** The words still settling; an empty string when there are none. */
  onInterim(text: string): void;
  /** `fatal` issues have stopped listening; `no-speech` has not (it starts again by itself). */
  onIssue(issue: SpeechIssue, fatal: boolean): void;
}

export interface Recognizer {
  /** Start listening, and keep listening (starting again after the browser's own silence stop) until `stop` or `abort`. */
  start(): void;
  /** Stop listening and let what was heard so far arrive. Resolves once it has (or after a short wait). */
  stop(): Promise<void>;
  /** Stop and drop whatever was pending. */
  abort(): void;
  /** Listening is wanted right now. */
  readonly wanted: boolean;
}

/**
 * Phone browsers repeat earlier words as new results in continuous mode (Chrome on Android does), so there a session
 * ends after each phrase and the end handler starts the next. The words are the same; only the seams differ.
 */
const shortSessions = (): boolean => typeof navigator !== 'undefined' && /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

/** How long a stop waits for the browser to hand over what it heard. */
const STOP_WAIT_MS = 900;
/** A session that ends sooner than this after starting counts as a failed start. */
const QUICK_END_MS = 1000;
/** This many failed starts in a row, with nothing heard, and the service is given up on. */
const QUICK_LIMIT = 4;

export function createRecognizer(opts: RecognizerOptions): Recognizer | null {
  const Ctor = recognitionCtor();
  if (!Ctor) return null;

  let rec: RecognitionLike | null = null;
  let wanted = false;
  let restart: number | undefined;
  let startedAt = 0;
  let quick = 0;
  let waiters: Array<() => void> = [];

  const settle = () => {
    const w = waiters;
    waiters = [];
    for (const done of w) done();
  };

  const detach = (r: RecognitionLike) => {
    r.onstart = null;
    r.onresult = null;
    r.onerror = null;
    r.onend = null;
  };

  const fail = (issue: SpeechIssue) => {
    wanted = false;
    opts.onIssue(issue, true);
  };

  function begin() {
    const r = new Ctor!();
    rec = r;
    r.lang = opts.lang ?? recognizerLang();
    r.continuous = !shortSessions();
    r.interimResults = true;
    r.maxAlternatives = 1;
    r.onstart = () => {
      if (rec === r) startedAt = Date.now();
    };
    r.onresult = (e) => {
      if (rec !== r) return;
      quick = 0;
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i];
        const heard = result?.[0]?.transcript;
        if (!result || !heard) continue;
        if (result.isFinal) {
          const words = heard.trim();
          if (words) opts.onFinal(words);
        } else {
          interim += heard;
        }
      }
      opts.onInterim(interim.trim());
    };
    r.onerror = (e) => {
      if (rec !== r) return;
      switch (e.error) {
        case 'no-speech':
          // quiet is not a failure: the session ends, and the end handler starts it again
          opts.onIssue('no-speech', false);
          break;
        case 'aborted':
          break;
        case 'not-allowed':
        case 'service-not-allowed':
        case 'audio-capture':
          fail('blocked');
          break;
        case 'language-not-supported':
          fail('unsupported');
          break;
        default:
          // 'network', and anything the browser invents later: the service cannot be reached
          fail('network');
      }
    };
    r.onend = () => {
      if (rec !== r) return;
      rec = null;
      settle();
      if (!wanted) return;
      // the browser stopped by itself (Chrome does after a stretch of quiet): carry on, unless it keeps failing at once
      quick = Date.now() - startedAt < QUICK_END_MS ? quick + 1 : 0;
      if (quick >= QUICK_LIMIT) {
        fail('network');
        return;
      }
      restart = window.setTimeout(() => {
        if (wanted && !rec) begin();
      }, 200);
    };
    startedAt = Date.now();
    try {
      r.start();
    } catch {
      /* already started: the end handler will start it again if it is still wanted */
    }
  }

  return {
    start() {
      wanted = true;
      quick = 0;
      window.clearTimeout(restart);
      if (!rec) begin();
    },
    stop() {
      wanted = false;
      window.clearTimeout(restart);
      const r = rec;
      if (!r) return Promise.resolve();
      return new Promise<void>((resolve) => {
        waiters.push(resolve);
        window.setTimeout(() => {
          // the browser did not say it had finished: let go of it, so a late result cannot land in the next answer
          if (rec === r) {
            detach(r);
            rec = null;
            // listening was asked for again while this one was ending, and its end never came: start afresh
            if (wanted) begin();
          }
          settle();
        }, STOP_WAIT_MS);
        try {
          r.stop();
        } catch {
          settle();
        }
      });
    },
    abort() {
      wanted = false;
      window.clearTimeout(restart);
      const r = rec;
      rec = null;
      if (r) {
        detach(r);
        try {
          r.abort();
        } catch {
          /* already over */
        }
      }
      settle();
    },
    get wanted() {
      return wanted;
    },
  };
}

/* ───────────────────────── Sarvam: where an answer is cut ───────────────────────── */

/** How often the level is read to decide where a piece ends. */
const TICK_MS = 40;
/** Never voice, however still the room: below this RMS (0 to 1) nothing counts as a voice. */
const MIN_VOICE_RMS = 0.012;
/** The room's noise floor is learned while it is quiet, and a voice is this much above it. */
const FLOOR_RANGE = [0.002, 0.02] as const;
const VOICE_OVER_FLOOR = 3;
/** A piece counts as speech once it holds this much voice (a cough or a knock is shorter). */
const SPEECH_MS = 150;
/** The quiet after speech that ends a piece. */
const PAUSE_MS = 800;
/** Near the longest piece a shorter pause will do, so a long run of words is cut between words and not in one. */
const SHORT_PAUSE_MS = 350;
/** A piece with no speech in it is thrown away and started afresh after this long, once the room is quiet. */
const IDLE_RESTART_MS = 8000;
/** The piece is cut this long before the server's limit. */
const LIMIT_MARGIN_S = 2;
const SHORT_PAUSE_WINDOW_S = 10;

/** What the next reading of the level means for the piece being recorded. */
export type Cut = 'continue' | 'cut' | 'drop';

export interface Segmenter {
  /** A new piece begins at `at` (ms). */
  reset(at: number): void;
  /** One reading: the RMS level (0 to 1) at `at` (ms). `cut` ends the piece here and keeps it; `drop` throws it away. */
  push(rms: number, at: number): Cut;
  /** The piece so far holds speech. */
  readonly heard: boolean;
  /** The last reading was a voice. */
  readonly voiced: boolean;
}

/**
 * Where to cut an answer into pieces: after about 800 ms of quiet that follows speech, and by force two seconds short of
 * `maxSeconds`. A piece that never had speech in it is dropped (it would cost a request for nothing).
 */
export function createSegmenter(maxSeconds: number): Segmenter {
  let floor = 0.005;
  let startedAt = 0;
  let lastAt = 0;
  let lastVoiceAt = 0;
  let voicedMs = 0;
  let heard = false;
  let voiced = false;
  return {
    reset(at) {
      startedAt = at;
      lastAt = at;
      lastVoiceAt = at;
      voicedMs = 0;
      heard = false;
      voiced = false;
    },
    push(rms, at) {
      const dt = Math.max(0, Math.min(250, at - lastAt));
      lastAt = at;
      voiced = rms > Math.max(MIN_VOICE_RMS, floor * VOICE_OVER_FLOOR);
      if (voiced) {
        voicedMs += dt;
        lastVoiceAt = at;
        if (voicedMs >= SPEECH_MS) heard = true;
      } else {
        floor = Math.min(FLOOR_RANGE[1], Math.max(FLOOR_RANGE[0], floor * 0.98 + rms * 0.02));
      }
      const elapsed = at - startedAt;
      if (elapsed >= (maxSeconds - LIMIT_MARGIN_S) * 1000) return heard ? 'cut' : 'drop';
      const quiet = at - lastVoiceAt;
      if (heard) {
        const need = elapsed >= (maxSeconds - SHORT_PAUSE_WINDOW_S) * 1000 ? SHORT_PAUSE_MS : PAUSE_MS;
        return quiet >= need ? 'cut' : 'continue';
      }
      return elapsed >= IDLE_RESTART_MS && quiet >= 300 ? 'drop' : 'continue';
    },
    get heard() {
      return heard;
    },
    get voiced() {
      return voiced;
    },
  };
}

/** Results that arrive in any order and leave in the order the pieces were cut. */
export interface Ordered<T> {
  /** A piece was cut: its number, 1, 2, 3… in cutting order. */
  issue(): number;
  /** Piece `n` is settled. Every settled piece at the front of the line is handed on, in order. */
  settle(n: number, value: T): void;
  /** Pieces cut so far. */
  readonly issued: number;
  /** Pieces handed on so far (they leave in order, so these are pieces 1 to `emitted`). */
  readonly emitted: number;
  /** Cut and not yet handed on. */
  readonly pending: number;
}

export function createOrdered<T>(emit: (value: T, n: number) => void): Ordered<T> {
  let issued = 0;
  let emitted = 0;
  const done = new Map<number, T>();
  return {
    issue() {
      issued += 1;
      return issued;
    },
    settle(n, value) {
      if (n <= emitted || done.has(n)) return;
      done.set(n, value);
      while (done.has(emitted + 1)) {
        const next = emitted + 1;
        const v = done.get(next) as T;
        done.delete(next);
        emitted = next;
        emit(v, next);
      }
    },
    get issued() {
      return issued;
    },
    get emitted() {
      return emitted;
    },
    get pending() {
      return issued - emitted;
    },
  };
}

/* ───────────────────────── Sarvam: the recognizer ───────────────────────── */

/** The part of `MediaRecorder` the recogniser uses. */
export interface RecorderLike {
  ondataavailable: ((e: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
  onerror: (() => void) | null;
  readonly state: string;
  start(): void;
  stop(): void;
}

/** A small steady bitrate: thirty seconds of speech is then about 120 KB, far under the server's cap. */
const RECORDER_BITS = 32_000;

/** What the recogniser needs from the page, so a test can hand it scripted ones. */
export interface SarvamDeps {
  lease(): MicLease;
  mime(): string | undefined;
  recorder(stream: MediaStream, mime: string): RecorderLike;
  transcribe(req: PieceRequest, signal: AbortSignal): Promise<TranscribeResult>;
  now(): number;
}

const realDeps: SarvamDeps = {
  lease: leaseMic,
  mime: recorderMime,
  recorder: (stream, mime) => new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: RECORDER_BITS }) as unknown as RecorderLike,
  transcribe: transcribePiece,
  now: () => Date.now(),
};

export interface SarvamOptions extends RecognizerOptions {
  profileId: string;
  sessionId: string;
  maxSegmentSeconds: number;
  maxSegmentBytes: number;
  /** Pieces are being written down (true), or every piece is (false). */
  onWriting?(pending: boolean): void;
  /** A voice is heard, at most once a second (so the quiet hint knows the person is talking). */
  onVoice?(): void;
  /** One piece could not be made out; the rest carries on. */
  onPieceTrouble?(sentence: string): void;
  /** Sarvam cannot be used any more in this interview. Called once every piece in flight has landed; `sentence` says why. */
  onUnavailable(sentence: string): void;
  deps?: Partial<SarvamDeps>;
}

/** What became of one piece. */
type Outcome =
  | { kind: 'text'; text: string }
  | { kind: 'nothing' }
  | { kind: 'trouble' }
  | { kind: 'fatal'; network: boolean; sentence: string };

type Fatal = Extract<Outcome, { kind: 'fatal' }>;

/** A piece is not worth sending below this (a recorder that wrote a header and nothing else). */
const MIN_PIECE_BYTES = 500;
/** A stop waits at most this long for the pieces in flight. */
const STOP_CAP_MS = 45_000;
/** A recorder that has been stopped and has not said so is let go of after this long. */
const RECORDER_STOP_MS = 1500;

const retryWait = (err: unknown): number | null => {
  if (!(err instanceof PracticeApiError)) return null;
  if (err.status === 0 || err.status === 502 || err.status === 504) return 700;
  if (err.status === 429 && err.retryAfter !== undefined && err.retryAfter >= 1 && err.retryAfter <= 15) return err.retryAfter * 1000 + 200;
  return null;
};

const nap = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const done = () => {
      window.clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = window.setTimeout(done, ms);
    signal.addEventListener('abort', done, { once: true });
  });

/** One request, and one more after a short wait when the failure looks passing (a dropped connection, a short rate limit). */
async function withOneRetry<T>(call: () => Promise<T>, signal: AbortSignal): Promise<T> {
  try {
    return await call();
  } catch (err) {
    const wait = retryWait(err);
    if (wait === null || signal.aborted) throw err;
    await nap(wait, signal);
    return call();
  }
}

/** What a failed request means for the piece: the rest of the answer carries on, or Sarvam is given up on. */
function classify(err: unknown): Outcome {
  if (err instanceof PracticeApiError) {
    const s = err.status;
    if (s === 400 || s === 413 || s === 422) return { kind: 'trouble' };
    // the interview ended or moved on while the piece was on its way: nothing to say about it
    if (s === 404 || s === 409) return { kind: 'nothing' };
    if (isOffline(err)) return { kind: 'fatal', network: true, sentence: err.message };
    // the server's own sentence says why when Sarvam is out of budget or refusing; anything else is said plainly
    return { kind: 'fatal', network: false, sentence: s === 503 || s === 429 ? err.message : SARVAM_DOWN };
  }
  return { kind: 'fatal', network: true, sentence: OFFLINE };
}

interface Level {
  read(): number;
  drop(): void;
}

/** A reader of the microphone's loudness (RMS, 0 to 1) from its own analyser, apart from the meter's. */
function openLevel(share: MicShare): Level {
  if (!share.context || !share.source) throw new Error('no audio graph');
  const source = share.source;
  const analyser = share.context.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0;
  source.connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  return {
    read() {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
      return Math.sqrt(sum / samples.length);
    },
    drop() {
      try {
        source.disconnect(analyser);
      } catch {
        /* already apart */
      }
    },
  };
}

interface Piece {
  /** Its place in the answer; 0 until it is cut and kept. */
  n: number;
  /** The answer it belongs to: a piece cut before an `abort` is never sent. */
  epoch: number;
  recorder: RecorderLike;
  chunks: Blob[];
  startedAt: number;
  endedAt: number;
  done: boolean;
  safety?: number;
  /** Runs once the recorder has handed over its last bytes. */
  after?: () => void;
}

interface Run {
  lease: MicLease;
  seg: Segmenter;
  share?: MicShare;
  level?: Level;
  piece: Piece | null;
  timer?: number;
  voiceNoteAt: number;
  dead: boolean;
}

interface Waiter {
  target: number;
  resolve(): void;
  timer: number;
}

/**
 * The Sarvam recogniser: records the microphone in pieces, sends each piece that has speech in it as it is cut, and hands
 * the words on in the order the pieces were cut. It has the browser recogniser's shape (`start`, `stop`, `abort`,
 * `wanted`, `onFinal`, `onIssue`), plus what a slower recogniser needs to say: `onWriting`, `onVoice`, `onPieceTrouble`
 * and `onUnavailable`. `onInterim` is never called (the words arrive after each pause, not as they are said).
 *
 *   start    open the microphone (shared with the meter), record, and cut at pauses
 *   stop     cut the last piece where it is, let the microphone go, and resolve once every piece so far has landed
 *            (pieces that start after this call are not waited for)
 *   abort    drop everything: the recording, the pieces in flight, whatever has not arrived
 */
export function createSarvamRecognizer(opts: SarvamOptions): Recognizer | null {
  const deps: SarvamDeps = { ...realDeps, ...opts.deps };
  const mime = deps.mime();
  if (!mime) return null;
  const baseType = mime.split(';')[0] ?? mime;

  let wanted = false;
  let epoch = 0;
  let abortCtl = new AbortController();
  let run: Run | null = null;
  let waiters: Waiter[] = [];
  let lastWriting = false;
  let fatal: Fatal | null = null;
  let told = false;
  let queue = newQueue();

  function newQueue(): Ordered<Outcome> {
    return createOrdered<Outcome>((outcome) => {
      if (outcome.kind === 'text') {
        if (outcome.text) opts.onFinal(outcome.text);
      } else if (outcome.kind === 'trouble') {
        opts.onPieceTrouble?.(PIECE_UNCLEAR);
      } else if (outcome.kind === 'fatal' && !fatal) {
        // pieces after this one are already on their way and still land; nothing new is recorded
        fatal = outcome;
        if (run) killRun(run);
      }
    });
  }

  const noteWriting = () => {
    const now = queue.pending > 0;
    if (now === lastWriting) return;
    lastWriting = now;
    opts.onWriting?.(now);
  };

  const settleWaiters = () => {
    const rest: Waiter[] = [];
    for (const w of waiters) {
      if (queue.emitted >= w.target) {
        window.clearTimeout(w.timer);
        w.resolve();
      } else {
        rest.push(w);
      }
    }
    waiters = rest;
  };

  /** After a piece landed: tell the screen, wake the stops that were waiting, and say so once Sarvam is given up on. */
  const afterLanded = () => {
    noteWriting();
    settleWaiters();
    if (!fatal || told || queue.pending > 0) return;
    const f = fatal;
    if (f.network) {
      // a dropped connection is not the end of Sarvam: ask to try again (the next start begins afresh)
      fatal = null;
      wanted = false;
      opts.onIssue('network', true);
      return;
    }
    told = true;
    opts.onUnavailable(f.sentence);
  };

  function detach(r: RecorderLike): void {
    r.ondataavailable = null;
    r.onstop = null;
    r.onerror = null;
  }

  function stopRecorder(r: RecorderLike): void {
    try {
      if (r.state !== 'inactive') r.stop();
    } catch {
      /* already over */
    }
  }

  /** The recording is over and nothing in it is wanted: drop the piece being recorded and let the microphone go. */
  function killRun(r: Run): void {
    r.dead = true;
    window.clearInterval(r.timer);
    r.level?.drop();
    const p = r.piece;
    r.piece = null;
    if (p) {
      detach(p.recorder);
      stopRecorder(p.recorder);
    }
    r.lease.release();
    if (run === r) run = null;
  }

  /** Sarvam cannot be used from this page after all (no recorder, no audio graph): say so as a piece would. */
  function giveUp(r: Run): void {
    killRun(r);
    if (!fatal) fatal = { kind: 'fatal', network: false, sentence: SARVAM_DOWN };
    afterLanded();
  }

  function startPiece(r: Run): void {
    const share = r.share;
    if (!share) throw new Error('no microphone');
    const recorder = deps.recorder(share.stream, mime as string);
    const p: Piece = { n: 0, epoch, recorder, chunks: [], startedAt: deps.now(), endedAt: 0, done: false };
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) p.chunks.push(e.data);
    };
    recorder.onerror = () => {
      if (r.piece === p && !r.dead) giveUp(r);
    };
    recorder.start();
    r.piece = p;
    r.seg.reset(p.startedAt);
  }

  /** The recorder has handed over its last bytes: send the piece. */
  function finishPiece(p: Piece): void {
    if (p.done) return;
    p.done = true;
    window.clearTimeout(p.safety);
    detach(p.recorder);
    p.after?.();
    if (p.epoch !== epoch) return;
    void submit(p, new Blob(p.chunks, { type: baseType }));
  }

  /**
   * End the piece being recorded. With `keep` it is numbered now (so the pieces keep their order whatever order they come
   * back in) and sent once the recorder is done; without it the piece is thrown away. With `next` a new recorder starts
   * first, so no word falls between two pieces.
   */
  function cut(r: Run, keep: boolean, next: boolean, after?: () => void): void {
    const p = r.piece;
    if (!p) {
      after?.();
      return;
    }
    p.endedAt = deps.now();
    r.piece = null;
    let startFailed = false;
    if (next && !r.dead) {
      try {
        startPiece(r);
      } catch {
        startFailed = true;
      }
    }
    if (!keep) {
      detach(p.recorder);
      stopRecorder(p.recorder);
      after?.();
    } else {
      p.n = queue.issue();
      p.after = after;
      noteWriting();
      p.recorder.onstop = () => finishPiece(p);
      p.safety = window.setTimeout(() => finishPiece(p), RECORDER_STOP_MS);
      try {
        if (p.recorder.state === 'inactive') finishPiece(p);
        else p.recorder.stop();
      } catch {
        finishPiece(p);
      }
    }
    // the piece that was just cut is still sent; it is only the next one that cannot be recorded
    if (startFailed) giveUp(r);
  }

  async function submit(p: Piece, blob: Blob): Promise<void> {
    const signal = abortCtl.signal;
    const seconds = Math.min(opts.maxSegmentSeconds, Math.max(1, Math.ceil((p.endedAt - p.startedAt) / 1000)));
    let outcome: Outcome;
    if (blob.size < MIN_PIECE_BYTES) {
      outcome = { kind: 'nothing' };
    } else if (blob.size > opts.maxSegmentBytes) {
      outcome = { kind: 'trouble' };
    } else {
      try {
        const heard = await withOneRetry(() => deps.transcribe({ profileId: opts.profileId, sessionId: opts.sessionId, audio: blob, seconds }, signal), signal);
        outcome = { kind: 'text', text: heard.text.trim() };
      } catch (err) {
        if (p.epoch !== epoch) return;
        outcome = classify(err);
      }
    }
    if (p.epoch !== epoch) return;
    queue.settle(p.n, outcome);
    afterLanded();
  }

  function tick(r: Run): void {
    if (r.dead || !r.piece || !r.level) return;
    const at = deps.now();
    const verdict = r.seg.push(r.level.read(), at);
    if (r.seg.voiced && at - r.voiceNoteAt >= 1000) {
      r.voiceNoteAt = at;
      opts.onVoice?.();
    }
    if (verdict === 'cut') cut(r, true, true);
    else if (verdict === 'drop') cut(r, false, true);
  }

  function begin(): void {
    const r: Run = { lease: deps.lease(), seg: createSegmenter(opts.maxSegmentSeconds), piece: null, voiceNoteAt: 0, dead: false };
    run = r;
    r.lease.ready.then(
      (share) => {
        if (r.dead) return;
        r.share = share;
        try {
          r.level = openLevel(share);
          startPiece(r);
        } catch {
          giveUp(r);
          return;
        }
        r.timer = window.setInterval(() => tick(r), TICK_MS);
      },
      () => {
        // the microphone could not be had: the browser or the person said no, or something else holds it
        if (r.dead) return;
        killRun(r);
        wanted = false;
        opts.onIssue('blocked', true);
      },
    );
  }

  return {
    start() {
      wanted = true;
      if (run || fatal) return;
      begin();
    },
    stop() {
      wanted = false;
      const r = run;
      if (r) {
        // the last piece is cut where it is, and the microphone stays open until its recorder has handed over its bytes
        const keep = !!r.piece && r.seg.heard;
        run = null;
        r.dead = true;
        window.clearInterval(r.timer);
        r.level?.drop();
        cut(r, keep, false, () => r.lease.release());
      }
      const target = queue.issued;
      if (queue.emitted >= target) return Promise.resolve();
      return new Promise<void>((resolve) => {
        const w: Waiter = {
          target,
          resolve,
          timer: window.setTimeout(() => {
            waiters = waiters.filter((x) => x !== w);
            resolve();
          }, STOP_CAP_MS),
        };
        waiters.push(w);
      });
    },
    abort() {
      wanted = false;
      if (run) killRun(run);
      epoch += 1;
      abortCtl.abort();
      abortCtl = new AbortController();
      queue = newQueue();
      fatal = null;
      told = false;
      const w = waiters;
      waiters = [];
      for (const x of w) {
        window.clearTimeout(x.timer);
        x.resolve();
      }
      noteWriting();
    },
    get wanted() {
      return wanted;
    },
  };
}

/* ───────────────────────── the listener: Sarvam first, the browser after ───────────────────────── */

export interface ListenerOptions extends RecognizerOptions {
  /** Pieces of the answer are being written down (Sarvam only). */
  onWriting?(pending: boolean): void;
  /** A voice is heard (Sarvam only; the browser's interim words say the same). */
  onVoice?(): void;
  /** One piece of the answer could not be made out (Sarvam only). */
  onPieceTrouble?(sentence: string): void;
  /**
   * Sarvam was given up on for this interview, and `note` says why (the server's own sentence when it sent one). `to` is
   * what listens now: the browser's recogniser, which has already been started if listening was wanted, or `none`, which
   * means typing.
   */
  onFallback?(note: string, to: 'browser' | 'none'): void;
}

export interface Listener extends Recognizer {
  /** Who is writing the words down now. */
  readonly by: SpeechBy;
}

/**
 * A recogniser for one interview: the plan's choice, and the fallback if Sarvam stops answering. `null` when nothing can
 * listen here (the answer is typed).
 */
export function createListener(plan: SpeechPlan, opts: ListenerOptions, deps?: Partial<SarvamDeps>): Listener | null {
  if (plan.listen === 'none') return null;
  let by: SpeechBy = plan.listen;
  let inner: Recognizer | null = null;
  /** Sarvam was given up on and the browser has no recogniser: nothing listens for the rest of the interview. */
  let down = false;

  const makeSarvam = (): Recognizer | null =>
    createSarvamRecognizer({
      ...opts,
      profileId: plan.profileId,
      sessionId: plan.sessionId,
      maxSegmentSeconds: plan.maxSegmentSeconds,
      maxSegmentBytes: plan.maxSegmentBytes,
      ...(deps ? { deps } : {}),
      onUnavailable: (sentence) => {
        const was = inner;
        const listening = was?.wanted ?? false;
        was?.abort();
        inner = null;
        if (recognitionSupported()) {
          by = 'browser';
          const browser = createRecognizer(opts);
          inner = browser;
          opts.onFallback?.(sentence, 'browser');
          if (listening) browser?.start();
        } else {
          by = 'browser';
          down = true;
          opts.onFallback?.(SARVAM_DOWN_TYPE, 'none');
        }
      },
    });

  const ensure = (): Recognizer | null => {
    if (inner || down) return inner;
    if (by === 'sarvam') {
      inner = makeSarvam();
      if (!inner) by = 'browser'; // this page cannot record after all: the browser's own recogniser, without a word about it
    }
    if (!inner) inner = createRecognizer(opts);
    return inner;
  };

  return {
    start() {
      ensure()?.start();
    },
    stop() {
      return inner ? inner.stop() : Promise.resolve();
    },
    abort() {
      inner?.abort();
    },
    get wanted() {
      return inner?.wanted ?? false;
    },
    get by() {
      return by;
    },
  };
}

/* ───────────────────────── the interviewer's voice ───────────────────────── */

let spoken: SpeechSynthesisUtterance | null = null;
let watchdog: number | undefined;
let token = 0;
let playing: AudioBufferSourceNode[] = [];

/** Stop whatever is being said. Safe to call when nothing is. */
export function cancelSpeech(): void {
  token += 1;
  window.clearTimeout(watchdog);
  spoken = null;
  for (const node of playing) {
    node.onended = null;
    try {
      node.stop();
    } catch {
      /* never started, or already over */
    }
  }
  playing = [];
  if (!synthesisSupported()) return;
  try {
    window.speechSynthesis.cancel();
  } catch {
    /* nothing to cancel */
  }
}

/** The voice that best matches the person's language: an exact match, then any English one, else the browser's own. */
function pickVoice(lang: string): SpeechSynthesisVoice | undefined {
  let voices: SpeechSynthesisVoice[] = [];
  try {
    voices = window.speechSynthesis.getVoices();
  } catch {
    return undefined;
  }
  const same = (v: SpeechSynthesisVoice) => v.lang.replace('_', '-').toLowerCase() === lang.toLowerCase();
  return voices.find(same) ?? voices.find((v) => /^en-in$/i.test(v.lang.replace('_', '-'))) ?? voices.find((v) => /^en(-|_|$)/i.test(v.lang));
}

export interface SpeakOptions {
  /** A slower voice. */
  slow?: boolean;
  /** The browser began to speak. */
  onStart?(): void;
  /** The line is over, or could not be spoken (so the next step is never stuck behind a silent voice). Never called after `cancelSpeech`. */
  onEnd(): void;
  /** The browser would not speak (it wants a click first). `onEnd` follows. */
  onBlocked?(): void;
}

/** Say `line` aloud with the browser's voice. One at a time: a new line cancels the one before. */
export function speak(line: string, opts: SpeakOptions): void {
  if (!synthesisSupported() || !line.trim()) {
    opts.onEnd();
    return;
  }
  cancelSpeech();
  const mine = token;
  const rate = opts.slow ? 0.85 : 1;
  const u = new SpeechSynthesisUtterance(line);
  u.rate = rate;
  const lang = recognizerLang();
  const voice = pickVoice(lang);
  if (voice) {
    u.voice = voice;
    u.lang = voice.lang;
  } else {
    u.lang = lang;
  }
  let over = false;
  const finish = () => {
    if (over || mine !== token) return;
    over = true;
    window.clearTimeout(watchdog);
    spoken = null;
    opts.onEnd();
  };
  u.onstart = () => {
    if (mine === token) opts.onStart?.();
  };
  u.onend = finish;
  u.onerror = (e) => {
    if (mine !== token) return;
    if (e.error === 'not-allowed') opts.onBlocked?.();
    finish();
  };
  // some browsers never say they have finished: about 90 ms a character, and a little more, is longer than any line
  watchdog = window.setTimeout(finish, Math.max(2500, (line.length * 90) / rate + 2000));
  spoken = u; // kept, so the browser cannot collect it before it ends
  try {
    window.speechSynthesis.speak(u);
  } catch {
    finish();
  }
}

/* Sarvam's voice. The page's CSP has no `media-src` for blob URLs, so the audio is fetched, decoded with the Web Audio API
   and played from a buffer, never through an <audio> element. */

/** The Sarvam voice's pace for "On, slower". */
const SLOW_PACE = 0.85;
/** Decoded lines kept so "Say it again" plays without a request (a long decoded line is a megabyte or two). */
const CACHE_LINES = 6;
/** A short gap between the lead and the question that follows it. */
const GAP_S = 0.3;

let playContext: AudioContext | undefined;
const decoded = new Map<string, AudioBuffer>();
const inFlight = new Map<string, Promise<AudioBuffer>>();
/** Interviews whose Sarvam voice failed in a way that will not mend (refused, out of budget): their lines use the browser's voice. */
const voiceDown = new Set<string>();

function audioContextForPlayback(): AudioContext | undefined {
  if (playContext && playContext.state !== 'closed') return playContext;
  const Ctor = audioContextCtor();
  if (!Ctor) return undefined;
  try {
    playContext = new Ctor();
  } catch {
    return undefined;
  }
  return playContext;
}

/** Wake the audio output. Call it from a click (starting the interview): the room's first line is then allowed to play. */
export function primeVoice(): void {
  const ctx = audioContextForPlayback();
  if (ctx && ctx.state === 'suspended') void ctx.resume().catch(() => undefined);
}

/** The context is running, or became so within a moment; a context the browser will not start without a click is not. */
async function awake(ctx: AudioContext): Promise<boolean> {
  const running = () => ctx.state === 'running';
  if (running()) return true;
  await Promise.race([ctx.resume().catch(() => undefined), new Promise<void>((resolve) => window.setTimeout(resolve, 400))]);
  return running();
}

export interface SarvamVoice {
  profileId: string;
  sessionId: string;
}

async function lineAudio(ctx: AudioContext, via: SarvamVoice, text: string, pace: number): Promise<AudioBuffer> {
  const key = `${via.sessionId}|${pace}|${text}`;
  const hit = decoded.get(key);
  if (hit) {
    decoded.delete(key);
    decoded.set(key, hit); // most recently used last
    return hit;
  }
  let pending = inFlight.get(key);
  if (!pending) {
    pending = (async () => {
      const bytes = await fetchSpeakAudio({ profileId: via.profileId, sessionId: via.sessionId, text, ...(pace !== 1 ? { pace } : {}) });
      const buffer = await ctx.decodeAudioData(bytes);
      decoded.set(key, buffer);
      while (decoded.size > CACHE_LINES) {
        const oldest = decoded.keys().next().value;
        if (oldest === undefined) break;
        decoded.delete(oldest);
      }
      return buffer;
    })().finally(() => inFlight.delete(key));
    inFlight.set(key, pending);
  }
  return pending;
}

/** A failure of the Sarvam voice that will not mend within this interview (refused, rate limited, out of budget): its lines use the browser's voice. */
function noteVoiceFailure(via: SarvamVoice, err: unknown): void {
  if (err instanceof PracticeApiError && (err.status === 403 || err.status === 429 || err.status === 503)) voiceDown.add(via.sessionId);
}

/**
 * True once the lines have been handled (played, cancelled, or blocked); false when the browser's voice should take over.
 *
 * Every line is asked for at once, so a question is on its way while its lead is fetched and while the lead plays. The first
 * line is played the moment it is here and each later one follows it as it arrives; a later line Sarvam cannot give is read by
 * the browser's voice after the ones that played.
 */
async function playSarvam(lines: readonly string[], via: SarvamVoice, opts: SpeakOptions, mine: number): Promise<boolean> {
  const ctx = audioContextForPlayback();
  if (!ctx) return false;
  const pace = opts.slow ? SLOW_PACE : 1;
  const pending = lines.map((line) => lineAudio(ctx, via, line, pace));
  for (const p of pending) p.catch(() => undefined); // each failure is handled where its line is waited for
  let first: AudioBuffer;
  try {
    first = await pending[0];
  } catch (err) {
    if (mine !== token) return true;
    noteVoiceFailure(via, err);
    return false;
  }
  if (mine !== token) return true;
  if (!(await awake(ctx))) {
    if (mine !== token) return true;
    // the browser wants a click before it plays anything: "Say it again" is that click
    opts.onBlocked?.();
    opts.onEnd();
    return true;
  }
  if (mine !== token) return true;

  const nodes: AudioBufferSourceNode[] = [];
  playing = nodes;
  let at = ctx.currentTime + 0.04;
  let ended = 0;
  let closed = false;
  let over = false;
  let rest = '';
  const finish = () => {
    if (over || mine !== token) return;
    over = true;
    window.clearTimeout(watchdog);
    playing = [];
    if (rest) speak(rest, opts);
    else opts.onEnd();
  };
  const settle = () => {
    if (closed && ended >= nodes.length) finish();
  };
  const queue = (buffer: AudioBuffer) => {
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(ctx.destination);
    const begin = Math.max(at, ctx.currentTime + 0.02);
    node.start(begin);
    node.onended = () => {
      ended += 1;
      settle();
    };
    nodes.push(node);
    at = begin + buffer.duration + GAP_S;
  };
  queue(first);
  opts.onStart?.();
  for (let i = 1; i < pending.length; i += 1) {
    try {
      const buffer = await pending[i];
      if (mine !== token) return true;
      queue(buffer);
    } catch (err) {
      if (mine !== token) return true;
      noteVoiceFailure(via, err);
      rest = lines.slice(i).join(' ');
      break;
    }
  }
  closed = true;
  // some browsers never say a source has ended: the audio's own length, and a little more, is longer than the lines
  window.clearTimeout(watchdog);
  watchdog = window.setTimeout(finish, Math.max(0, at - ctx.currentTime) * 1000 + 2500);
  settle();
  return true;
}

export interface WarmOptions {
  /** The Sarvam voice of this interview; without it nothing is asked for (the browser's voice needs no request). */
  sarvam?: SarvamVoice;
  slow?: boolean;
}

/**
 * Start fetching the audio of `lines` now, before anything asks for them to be said (a new line has just arrived, and the room
 * has yet to render). The request is the one `say` makes, so `say` finds it in flight or decoded; a failure is left for `say`
 * to meet when it asks, and nothing is played.
 */
export function warmVoice(lines: readonly string[], opts: WarmOptions): void {
  const via = opts.sarvam;
  if (!via || !sarvamVoiceSupported() || voiceDown.has(via.sessionId)) return;
  const ctx = audioContextForPlayback();
  if (!ctx) return;
  const pace = opts.slow ? SLOW_PACE : 1;
  for (const line of lines.map((l) => l.trim()).filter(Boolean)) lineAudio(ctx, via, line, pace).catch(() => undefined);
}

export interface SayOptions extends SpeakOptions {
  /** Read the lines with Sarvam's voice (when this page can play it); the browser's voice reads them otherwise, or when they cannot be had. */
  sarvam?: SarvamVoice;
}

/**
 * Say the interviewer's `lines` aloud: Sarvam's audio when asked for and possible, else the browser's voice, one after the
 * other. Each line is asked for separately, as the server only reads lines that are the interview's own. One at a time: a
 * new call cancels the one before. `onEnd` is called once the last line is over or could not be said, and never after
 * `cancelSpeech`.
 */
export function say(lines: readonly string[], opts: SayOptions): void {
  const parts = lines.map((l) => l.trim()).filter(Boolean);
  if (parts.length === 0) {
    opts.onEnd();
    return;
  }
  const via = opts.sarvam;
  const whole = parts.join(' ');
  if (!via || !sarvamVoiceSupported() || voiceDown.has(via.sessionId)) {
    speak(whole, opts);
    return;
  }
  cancelSpeech();
  const mine = token;
  void playSarvam(parts, via, opts, mine).then((handled) => {
    if (!handled && mine === token) speak(whole, opts);
  });
}

/* ───────────────────────── the level meter ───────────────────────── */

export const METER_BARS = 24;
/** A resting bar is 4 px of 26 px. */
const FLAT = 4 / 26;
/** Speech sits between about 120 Hz and 4 kHz: bins 1 to 45 of a 512-point analysis at 48 kHz. */
const BIN_LO = 1;
const BIN_HI = 45;
const EDGES = Array.from({ length: METER_BARS + 1 }, (_, i) => Math.round(BIN_LO + (BIN_HI - BIN_LO) * (i / METER_BARS) ** 1.6));

export interface MeterOptions {
  /** The bars to draw into (their `transform` is written each frame). */
  bars: readonly HTMLElement[];
  /** Under reduced motion nothing is painted; the level is still read. */
  reduced: boolean;
  /** The loudest bar, 0 to 1: every frame, or about seven times a second under reduced motion. */
  onLevel?(peak: number): void;
  /** The microphone could not be opened: `denied` when the browser or the person said no (or there is none), `busy` when it could not be had. */
  onBlocked?(why: MeterTrouble): void;
}

/** Start reading the microphone. Returns the function that stops it (and lets the microphone go, if nothing else holds it). */
export function startMeter(opts: MeterOptions): () => void {
  let stopped = false;
  let raf = 0;
  let timer: number | undefined;
  let lease: MicLease | undefined;
  let detach: (() => void) | undefined;

  const flat = () => {
    for (const bar of opts.bars) bar.style.transform = '';
  };
  const stop = () => {
    if (stopped) return;
    stopped = true;
    window.cancelAnimationFrame(raf);
    window.clearInterval(timer);
    detach?.();
    lease?.release();
    flat();
  };

  if (!meterSupported()) {
    // nothing to read a level from: the bars stay flat, and the recogniser (if there is one) still works
    opts.onBlocked?.('busy');
    return stop;
  }

  lease = leaseMic();
  lease.ready.then(
    (share) => {
      if (stopped) return;
      const { context, source } = share;
      if (!context || !source) return; // no way to read a level: the bars stay flat, and the recogniser still works
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.65;
      source.connect(analyser);
      detach = () => {
        try {
          source.disconnect(analyser);
        } catch {
          /* already apart */
        }
      };
      const data = new Uint8Array(analyser.frequencyBinCount);

      /** One reading: each bar's 0..1 strength. */
      const read = (): number[] => {
        analyser.getByteFrequencyData(data);
        const out: number[] = [];
        for (let i = 0; i < METER_BARS; i++) {
          const from = EDGES[i];
          const to = Math.max(EDGES[i + 1], from + 1);
          let sum = 0;
          for (let b = from; b < to; b++) sum += data[b] ?? 0;
          const v = sum / (to - from) / 255;
          // a gate under room noise, then a straight ramp
          out.push(Math.min(1, Math.max(0, (v - 0.22) / 0.55)));
        }
        return out;
      };

      if (opts.reduced) {
        timer = window.setInterval(() => opts.onLevel?.(Math.max(...read())), 150);
        return;
      }
      const frame = () => {
        if (stopped) return;
        const levels = read();
        let peak = 0;
        for (let i = 0; i < METER_BARS; i++) {
          const s = levels[i];
          if (s > peak) peak = s;
          const bar = opts.bars[i];
          if (bar) bar.style.transform = `scaleY(${(FLAT + (1 - FLAT) * s).toFixed(3)})`;
        }
        opts.onLevel?.(peak);
        raf = window.requestAnimationFrame(frame);
      };
      raf = window.requestAnimationFrame(frame);
    },
    (err: unknown) => {
      if (stopped) return;
      opts.onBlocked?.(err instanceof MicError ? err.why : 'busy');
    },
  );

  return stop;
}

/* ───────────────────────── what the person chose ───────────────────────── */

export type VoiceSetting = 'normal' | 'slower' | 'off';
export type AnswerPref = 'speech' | 'type';

export interface Prefs {
  answer: AnswerPref;
  voice: VoiceSetting;
}

const PREFS_KEY = 'tayyari.practice.prefs';
const DEFAULT_PREFS: Prefs = { answer: 'speech', voice: 'normal' };

/** How the person chose to answer and hear the interviewer, from this browser (a blocked or full store gives the defaults). */
export function readPrefs(): Prefs {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(PREFS_KEY) ?? 'null');
    if (typeof raw !== 'object' || raw === null) return DEFAULT_PREFS;
    const { answer, voice } = raw as Record<string, unknown>;
    return {
      answer: answer === 'speech' || answer === 'type' ? answer : DEFAULT_PREFS.answer,
      voice: voice === 'normal' || voice === 'slower' || voice === 'off' ? voice : DEFAULT_PREFS.voice,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function writePrefs(prefs: Prefs): void {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* storage is blocked or full: the choice lasts for this page only */
  }
}

/**
 * What the preferences come to here: speech only when something can listen, a voice only when something can speak. Without
 * a `choice` (the server has not been asked yet) the browser's own speech is what counts.
 */
export function effectivePrefs(prefs: Prefs, choice?: SpeechChoice): Prefs {
  const listens = choice ? choice.listen !== 'none' : recognitionSupported();
  const speaks = choice ? choice.voice !== 'none' : synthesisSupported();
  return {
    answer: prefs.answer === 'speech' && listens ? 'speech' : 'type',
    voice: speaks ? prefs.voice : 'off',
  };
}
