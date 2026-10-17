"""Speech for practice interviews: Sarvam AI speech-to-text and text-to-speech, metered against a daily budget.

    engine    `SpeechEngine`, its two errors (`SpeechUnavailable`, `SpeechRejected`) and the audio types a piece may be
    sarvam    `SarvamEngine`: the engine over Sarvam's official SDK (imported where it is built, so this package stays light)
    meter     `SpeechMeter`: a soft budget of seconds and characters, per UTC day (in the ``kv`` table) and per session
    service   `SpeechService`: status, transcribe a piece of an answer, speak an interviewer's line (a line being made is
              shared by everyone who asks for it), and make the lines an interview will probably say next before they are
              asked for (`prefetch`)

A self-contained service like discover and billing: it imports only `core`, `contract` and `storage.ports` (a test enforces
it). Which lines may be spoken, and which names bias recognition, come from the interview through the routes. Tayyari keeps no
audio; see shared/speech.ts.
"""

from tayyari.speech.engine import Heard, SpeechEngine, SpeechRejected, SpeechUnavailable
from tayyari.speech.meter import SpeechMeter
from tayyari.speech.service import SpeechService

__all__ = [
    "Heard",
    "SpeechEngine",
    "SpeechMeter",
    "SpeechRejected",
    "SpeechService",
    "SpeechUnavailable",
]
