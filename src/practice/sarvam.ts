/**
 * The client for speech in practice interviews: `/api/speech/*` (types in `shared/speech.ts`). The server holds Sarvam AI's
 * key and a soft budget; the browser only ever sends a piece of the person's answer (a few seconds of audio, cut at a
 * pause) and gets text back, and asks for an interviewer line to be read aloud and gets audio back. Nothing is kept: the
 * server sends a piece to Sarvam and drops it.
 *
 *   status      GET  /api/speech/status?profileId=[&sessionId=]   which of the two Sarvam can do now (without a session it
 *                                                                ignores the session's own share: the setup page uses that)
 *   transcribe  POST /api/speech/transcribe?profileId=&sessionId=&seconds=   the raw piece -> its words
 *   speak       POST /api/speech/speak                            one of the interview's own lines -> audio/mpeg
 *
 * Failures are the same `PracticeApiError` the practice calls throw (`api.ts`), with the server's sentence as written: a
 * 503 carries the reason Sarvam could not be used, so the room can say why the browser's speech took over. A request that
 * never reached the server is status 0. A body that is not what was asked for is a 502, never trusted.
 */
import type { SpeakRequest, SpeechStatus, TranscribeResult } from '../../shared/speech';
import { OFFLINE, PracticeApiError, TROUBLE } from './api';
import { isObj } from './read';
import { SARVAM_DOWN } from './words';

const BASE = '/api/speech';

/** A machine code such as `speech_off`, as opposed to a sentence. */
const looksLikeCode = (s: string) => /^[a-z][a-z0-9_]*$/.test(s);

const FALLBACK: Record<number, string> = {
  400: "We couldn't make out that piece. Say it again, or type it.",
  404: "That line isn't part of this interview.",
  409: 'This interview has moved on.',
  413: "That piece was too long. Say it again in shorter parts, or type it.",
  429: 'Too many speech requests at once. Please wait a moment.',
  503: SARVAM_DOWN,
};

/** The error for a non-2xx response, with the server's sentence as given. */
async function failure(res: Response): Promise<PracticeApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const sentence = isObj(body) && typeof body.error === 'string' ? body.error.trim() : '';
  const message = sentence && !looksLikeCode(sentence) ? sentence : (FALLBACK[res.status] ?? TROUBLE);
  const header = res.headers.get('retry-after')?.trim();
  const seconds = header ? Number(header) : NaN;
  return new PracticeApiError(message, res.status, Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined);
}

const isAbort = (err: unknown) => err instanceof DOMException && err.name === 'AbortError';

/** A request that never got an answer is status 0; an abort (the caller's own) is left as it is. */
async function send(path: string, init: RequestInit, signal?: AbortSignal): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(path, { ...init, signal });
  } catch (err) {
    if (isAbort(err) || signal?.aborted) throw err;
    throw new PracticeApiError(OFFLINE, 0);
  }
  if (!res.ok) throw await failure(res);
  return res;
}

/** A positive whole number from the server, else nothing. */
const count = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) && v >= 1 ? Math.floor(v) : undefined);

function readStatus(raw: unknown): SpeechStatus | null {
  if (!isObj(raw)) return null;
  const { stt, tts, maxSegmentSeconds, maxSegmentBytes } = raw;
  const seconds = count(maxSegmentSeconds);
  const bytes = count(maxSegmentBytes);
  if (typeof stt !== 'boolean' || typeof tts !== 'boolean' || seconds === undefined || bytes === undefined) return null;
  return { stt, tts, ...(raw.provider === 'sarvam' ? { provider: 'sarvam' as const } : {}), maxSegmentSeconds: seconds, maxSegmentBytes: bytes };
}

function readTranscript(raw: unknown): TranscribeResult | null {
  if (!isObj(raw) || typeof raw.text !== 'string') return null;
  const seconds = typeof raw.seconds === 'number' && Number.isFinite(raw.seconds) ? Math.max(0, Math.round(raw.seconds)) : 0;
  return { text: raw.text.slice(0, 4000), seconds, ...(typeof raw.languageCode === 'string' ? { languageCode: raw.languageCode } : {}) };
}

/**
 * `GET /api/speech/status`: what Sarvam can do now. Without a `sessionId` the session's own share is ignored, which is what
 * the setup page wants before a session exists.
 */
export async function fetchSpeechStatus(profileId: string, sessionId?: string, signal?: AbortSignal): Promise<SpeechStatus> {
  const query = `profileId=${encodeURIComponent(profileId)}${sessionId ? `&sessionId=${encodeURIComponent(sessionId)}` : ''}`;
  const res = await send(`${BASE}/status?${query}`, { method: 'GET', cache: 'no-store' }, signal);
  let raw: unknown;
  try {
    raw = await res.json();
  } catch {
    throw new PracticeApiError(TROUBLE, 502);
  }
  const status = readStatus(raw);
  if (!status) throw new PracticeApiError(TROUBLE, 502);
  return status;
}

export interface PieceRequest {
  profileId: string;
  sessionId: string;
  /** The piece, as the recorder wrote it (webm, ogg or mp4). */
  audio: Blob;
  /** About how long it is, rounded up to a whole second; the server bills the larger of this and what the size says. */
  seconds: number;
}

/** A piece that has had this long without an answer is given up on: Sarvam is too slow to write an answer down. */
const PIECE_WAIT_MS = 30_000;

/** `POST /api/speech/transcribe`: the words in one piece of an answer. The audio is sent as the body, nothing else. */
export async function transcribePiece(req: PieceRequest, signal?: AbortSignal): Promise<TranscribeResult> {
  const ctl = new AbortController();
  const stop = () => ctl.abort();
  let timedOut = false;
  if (signal?.aborted) ctl.abort();
  else signal?.addEventListener('abort', stop, { once: true });
  const timer = window.setTimeout(() => {
    timedOut = true;
    ctl.abort();
  }, PIECE_WAIT_MS);
  try {
    const query = `profileId=${encodeURIComponent(req.profileId)}&sessionId=${encodeURIComponent(req.sessionId)}&seconds=${Math.max(1, Math.round(req.seconds))}`;
    const type = req.audio.type.split(';')[0]?.trim() || 'audio/webm';
    let res: Response;
    try {
      res = await send(`${BASE}/transcribe?${query}`, { method: 'POST', headers: { 'Content-Type': type }, body: req.audio, cache: 'no-store' }, ctl.signal);
    } catch (err) {
      // the wait ran out (a 408, so it is not tried again; the caller's own abort stays an abort)
      if (timedOut && !signal?.aborted) throw new PracticeApiError(SARVAM_DOWN, 408);
      throw err;
    }
    let raw: unknown;
    try {
      raw = await res.json();
    } catch {
      throw new PracticeApiError(TROUBLE, 502);
    }
    const result = readTranscript(raw);
    if (!result) throw new PracticeApiError(TROUBLE, 502);
    return result;
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', stop);
  }
}

/** `POST /api/speech/speak`: one of the interview's own lines as audio (mp3), ready for the Web Audio API to decode. */
export async function fetchSpeakAudio(req: SpeakRequest, signal?: AbortSignal): Promise<ArrayBuffer> {
  const res = await send(`${BASE}/speak`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req), cache: 'no-store' }, signal);
  const type = res.headers.get('content-type') ?? '';
  if (!/^audio\//i.test(type)) throw new PracticeApiError(TROUBLE, 502);
  let bytes: ArrayBuffer;
  try {
    bytes = await res.arrayBuffer();
  } catch {
    throw new PracticeApiError(OFFLINE, 0);
  }
  if (bytes.byteLength === 0) throw new PracticeApiError(TROUBLE, 502);
  return bytes;
}
