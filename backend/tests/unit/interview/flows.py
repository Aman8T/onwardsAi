"""Shared steps for the service tests: start a session, answer it, and play the Headout interview of the canvas to its end."""

from __future__ import annotations

from typing import Any

from tayyari.contract.interview import (
    AnswerAction,
    AnswerRequest,
    Debrief,
    InterviewSession,
    StartRequest,
)
from tayyari.contract.models import Profile
from tayyari.llm.errors import ClaudeOverloaded
from tests.unit.interview.support import (
    CTX,
    RUN,
    SAMPLE,
    Rig,
    judged,
    sample_phrasing,
)

A1 = (
    "I merged the three steps into one page with a reducer for the checkout state. The cart updated before the "
    "server answered and rolled back if the payment API said no, with a short message under the button."
)
ROLLBACK = "rolled back if the payment API said no, with a short message under the button"
A2 = "We, um, did a lot of things. Images, the bundle, some fonts. And it got a lot faster."
A2_PUSH = "I think the biggest one was the images? I'm not sure how we measured it, the PM had a dashboard."
A3 = (
    "We test in Chrome, Safari on iPhone and Samsung Internet, because most of our traffic is mobile. Last month the "
    "sticky pay button hid behind Safari's bottom toolbar, and I fixed it with safe-area insets."
)
A3_QUOTE = "the sticky pay button hid behind Safari's bottom toolbar, and I fixed it with safe-area insets"
FACT = (
    "Tests checkout in Chrome, Safari on iPhone and Samsung Internet; fixed the sticky pay button hiding behind "
    "Safari's toolbar with safe-area insets."
)
A5 = "Hmm, I don't remember a specific time. It usually goes fine."
A5_FLOOR = "I'd message the designer."


async def start(
    rig: Rig,
    kind: str = "role",
    *,
    profile: Profile = SAMPLE,
    phrase: Any = "good",
    **over: Any,
) -> InterviewSession:
    """Start an interview. ``phrase`` is the scripted phrasing: ``"good"`` (the Headout plan's), ``"fails"`` (templates), or
    a dict of your own."""
    if kind != "work":
        answer = (
            sample_phrasing() if phrase == "good" else ClaudeOverloaded() if phrase == "fails" else phrase
        )
        rig.claude.script_structured("interview.phrase", answer)
    role = RUN if kind == "role" else over.pop("role", None)
    req = StartRequest.model_validate({"profileId": profile.id, "kind": kind, "role": role, **over})
    return await rig.service.start(profile, req, CTX)


async def say(
    rig: Rig,
    session_id: str,
    text: str = "",
    *,
    action: AnswerAction | None = None,
    verdict: dict[str, Any] | None = None,
    profile: Profile = SAMPLE,
    mode: str = "speech",
    after: float = 0,
) -> InterviewSession:
    """The person's turn ``after`` seconds later. ``verdict`` is the judge's scripted read of it, when it will be judged."""
    rig.clock.advance(after)
    if verdict is not None:
        rig.claude.script_structured("interview.judge", verdict)
    req = AnswerRequest.model_validate(
        {"profileId": profile.id, "text": text, "mode": mode, "action": action}
    )
    return await rig.service.answer(profile, session_id, req, CTX)


async def play_headout(rig: Rig) -> tuple[str, InterviewSession]:
    """The Headout interview of the canvas, answered to the end (not yet ended). Returns the session id and the last session
    state: s1 defended, s2 shaky (after a push), s3 defended with a new fact, s4 "I don't know", s5 couldn't defend (after
    the easier question)."""
    session = await start(rig)
    sid = session.id
    await say(rig, sid, A1, verdict=judged(A1, "defended", quote=ROLLBACK), after=20)
    await say(
        rig,
        sid,
        A2,
        verdict=judged(
            A2,
            "shaky",
            quote="Images, the bundle, some fonts",
            why="Some of it, but not which change was yours.",
        ),
        after=131,
    )
    await say(
        rig, sid, A2_PUSH, verdict=judged(A2_PUSH, "shaky", quote="I'm not sure how we measured it"), after=69
    )
    await say(
        rig,
        sid,
        A3,
        verdict=judged(
            A3,
            "defended",
            quote=A3_QUOTE,
            why="A real bug, the browser it was in, and the fix.",
            new_fact=FACT,
        ),
        after=92,
    )
    await say(rig, sid, action="dont_know", after=88)
    await say(
        rig,
        sid,
        A5,
        verdict=judged(A5, "couldnt", quote="I don't remember a specific time", why="No example yet."),
        after=360,
    )
    last = await say(
        rig, sid, A5_FLOOR, verdict=judged(A5_FLOOR, "couldnt", quote="I'd message the designer"), after=30
    )
    return sid, last


def debrief_script() -> dict[str, Any]:
    """The debrief call of the canvas flow: a good Say, Avoid and Fix for the shaky ask (s2); nothing else."""
    return {
        "slots": [
            {
                "id": "s2",
                "say": {
                    "sources": ["Resume", "Your answer"],
                    "text": (
                        "The payment step's LCP was 4.1s. I found [what was slow] with [the tool you used], and "
                        "[the change] moved it most. It ended at 1.9s."
                    ),
                },
                "avoid": {"quote": "We, um, did a lot of things", "advice": "Say which change was yours."},
                "fix": {
                    "text": "Find the pull request or report behind the LCP change and note the one change that moved it most.",
                    "minutes": 20,
                },
            }
        ]
    }


async def end(rig: Rig, session_id: str, *, profile: Profile = SAMPLE, script: Any = None) -> Debrief:
    rig.claude.script_structured("interview.debrief", debrief_script() if script is None else script)
    return await rig.service.end(profile, session_id, CTX)
