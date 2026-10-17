"""Having the interviewer's next lines ready in Sarvam's voice before the browser asks for them.

A question's voice takes about 5 s to come back from Sarvam and a made one about 1 s, so the practice routes ask the speech
service to make the lines an interview will probably say next, in the background, as soon as they know them. The interview
says which (`InterviewService.speech_upcoming`, code over the stored session); the speech service makes them
(`SpeechService.prefetch`). Neither knows the other: this is where the api layer puts them together.

    prefetch_voice   the line being said now, then what may be said next (a session just started, a reload, an answer's reply)
    voice_after      the same, as a task the response runs after it has been sent (so the person is not kept waiting for it)
    next_turn=True   only what the very next turn may need, asked before the judge reads an answer (it takes about 2 s)

Nothing here ever changes the answer of the route that calls it: no Sarvam, a switched-off prefetch, a budget that is used up, a
session that is gone or any failure at all means nothing is made ahead and the route goes on. The texts stay on the server;
`/api/speech/speak` still reads only a line the interview has said, and by the time the browser asks for one, it has.
"""

from __future__ import annotations

from starlette.background import BackgroundTask

from tayyari.container import Container
from tayyari.contract.models import Profile
from tayyari.core.logging import get_logger

log = get_logger(__name__)


async def prefetch_voice(
    container: Container, profile: Profile, session_id: str, *, next_turn: bool = False
) -> None:
    """Ask the speech service to make the lines this session will probably need. Never raises."""
    speech = container.speech
    if not speech.prefetching:
        return
    try:
        upcoming = await container.interview.speech_upcoming(
            profile, session_id, followups=speech.prefetching_followups
        )
        if (
            upcoming is None
        ):  # not this profile's session, or over: also what keeps a made-up id out of the speech budget
            return
        if not (
            await speech.status(session_id)
        ).tts:  # the same rules as everywhere: a spent budget makes nothing
            return
        lines = upcoming.next_turn if next_turn else (*upcoming.now, *upcoming.soon)
        speech.prefetch(session_id, lines)
    except Exception as exc:  # a voice made ahead is a courtesy: never the reason a route fails
        log.warning("speech_prefetch_failed", reason=type(exc).__name__, stage="upcoming")


def voice_after(container: Container, profile: Profile, session_id: str) -> BackgroundTask | None:
    """`prefetch_voice` as a task for the response to run once it has been sent; None when nothing is made ahead."""
    if not container.speech.prefetching:
        return None
    return BackgroundTask(prefetch_voice, container, profile, session_id)


__all__ = ["prefetch_voice", "voice_after"]
