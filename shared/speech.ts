/**
 * Speech for practice interviews: the contract between `/api/speech/*` and the web app.
 *
 * When the server has a Sarvam AI key and budget left, the room records each spoken answer in pieces cut at a pause (at
 * most `maxSegmentSeconds` each) and sends them to `POST /api/speech/transcribe`, which turns them into text with Sarvam's
 * speech-to-text (Indian English, with the interview's own names and terms as hints). The interviewer's lines are read
 * aloud by `POST /api/speech/speak` in an Indian English voice. Tayyari keeps no audio: a piece is sent to Sarvam and
 * dropped; only the text reaches the interview.
 *
 * When Sarvam is not available (no key, the day's budget used, the session's share used, or switched off) the room uses
 * the browser's own speech, and typing always works.
 *
 *   GET  /api/speech/status?profileId=&sessionId=                     SpeechStatus
 *   POST /api/speech/transcribe?profileId=&sessionId=&seconds=        raw audio body (audio/webm, audio/ogg, audio/mp4,
 *                                                                     audio/mpeg, audio/wav) -> TranscribeResult
 *   POST /api/speech/speak                                            SpeakRequest -> audio bytes (audio/mpeg)
 *
 * Mirrored by backend/tayyari/contract/speech.py; tests/unit/contract/test_speech_parity.py reads this file. Same subset
 * as the other contracts: interfaces, literal unions, `x?:` optionals, `T[]`.
 */

export type SpeechProvider = 'sarvam';

/** Why Sarvam speech is not available right now (the room falls back to the browser's speech). */
export type SpeechOffReason = 'not_configured' | 'switched_off' | 'daily_budget' | 'session_budget';

export interface SpeechStatus {
  /** Sarvam turns spoken answers into text for this session. */
  stt: boolean;
  /** Sarvam reads the interviewer's lines aloud for this session. */
  tts: boolean;
  provider?: SpeechProvider;
  /** Present when `stt` or `tts` is false. */
  sttReason?: SpeechOffReason;
  ttsReason?: SpeechOffReason;
  /** The longest piece of an answer one transcribe request takes. */
  maxSegmentSeconds: number;
  /** The largest piece, in bytes. */
  maxSegmentBytes: number;
}

export interface TranscribeResult {
  /** The words heard, as Sarvam wrote them ('' when it heard nothing). */
  text: string;
  /** The seconds of audio counted against the budget. */
  seconds: number;
  /** The language Sarvam reports, when it does. */
  languageCode?: string;
}

/** Read one of the session's interviewer lines aloud. The text must be a line the interviewer said or is saying. */
export interface SpeakRequest {
  profileId: string;
  sessionId: string;
  text: string;
  /** 0.5 to 2.0 (1 = normal). */
  pace?: number;
}
