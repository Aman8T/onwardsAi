"""POST /api/chat with a resume in view, end to end through the real app over a real SQLite file: the coach is shown the
resume's lines only when the caller may open that resume (otherwise the request goes on as if it named none), and a rewrite
it proposes is checked by code against the facts the line cites. Only the network edge is faked (Claude is scripted). The rig
and the `start` fixture are those of `test_resume_routes`."""

from __future__ import annotations

import json
import re
from typing import Any

from tayyari.contract import ChatEvent, DeltaEvent, RewriteEvent
from tests.fakes.claude import ChatCall, text_turn
from tests.integration import test_resume_routes as routes
from tests.integration.support import chat_events
from tests.integration.test_resume_routes import FIGURE_LINE, ROLE, Account, Rig

start = routes.start  # the fixture of the resume routes: Meera, a scripted model, the real app

GOOD = "Ran 38 A/B tests across landing pages and checkout nudges; five winners shipped, adding 6.4% to site conversion."
UNSUPPORTED = "Ran 38 A/B tests on landing pages, lifting conversion by 40%."


def short_id_of(call: ChatCall, needle: str) -> str:
    """The short id the resume block gives the line that holds ``needle``."""
    block = next(b.text for b in call.system if b.text.startswith("## The resume in view"))
    return re.search(rf"- \[(l\d+)\] [^\n]*{re.escape(needle)}", block).group(1)  # type: ignore[union-attr]


def answer_with(words: str, *, needle: str = "A/B tests") -> Any:
    """A coach that reads the resume block it was given and proposes ``words`` for the line holding ``needle``."""

    def respond(call: ChatCall) -> Any:
        return text_turn(
            f"Here is a tighter way to say it.\n[[rewrite: {short_id_of(call, needle)} | {words}]]",
            chunk_chars=9,
        )

    return respond


async def ask(
    rig: Rig,
    who: Account | None,
    resume: dict[str, Any] | None,
    profile_id: str,
    *,
    message: str = "Tighten my testing line",
) -> list[ChatEvent]:
    body: dict[str, Any] = {"profileId": profile_id, "message": message}
    if resume is not None:
        body["resume"] = resume
    response = await rig.client.post("/api/chat", json=body, headers={} if who is None else who.headers)
    assert response.status_code == 200, response.text
    return chat_events(response)


def text_of(events: list[ChatEvent]) -> str:
    return "".join(e.text for e in events if isinstance(e, DeltaEvent))


def rewrites(events: list[ChatEvent]) -> list[RewriteEvent]:
    return [e for e in events if isinstance(e, RewriteEvent)]


async def test_the_coach_proposes_one_rewrite_for_a_line_of_the_resume_in_view(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        rid = (await rig.upload(ana))["id"]
        rig.claude.script_chat(answer_with(GOOD))

        events = await ask(rig, ana, {"id": rid}, ana.profile_id)

        assert text_of(events) == "Here is a tighter way to say it."
        [event] = rewrites(events)
        assert (event.resumeId, event.lineId, event.after) == (rid, FIGURE_LINE, GOOD)
        assert event.before.startswith("Ran 38 A/B tests on landing pages")
        assert [e.type for e in events][-2:] == ["rewrite", "done"]
        wire = next(json.loads(ln) for ln in _wire(events) if '"rewrite"' in ln)
        assert set(wire) == {"type", "resumeId", "lineId", "before", "after"}
        [call] = rig.claude.chat_calls
        assert (
            call.system[-1].text.startswith("## The resume in view")
            and "[[rewrite: <line id>" in call.system[0].text
        )
        assert "[l1] " in call.system[-1].text and "rests on:" in call.system[-1].text

        # accepting is the existing edit op, and Finalise still checks the line
        accepted = await rig.patch(
            ana,
            f"/api/resumes/{rid}",
            json={"ops": [{"op": "edit", "bulletId": event.lineId, "text": event.after}]},
        )
        assert accepted.status_code == 200
        done = await rig.post(ana, f"/api/resumes/{rid}/finalise", json={})
        assert done.json()["report"]["done"] is True


def _wire(events: list[ChatEvent]) -> list[str]:
    return [e.model_dump_json() for e in events]


async def test_a_rewrite_the_facts_do_not_support_is_dropped_and_the_answer_goes_on_without_it(
    start: Any,
) -> None:
    async with start() as rig:
        ana = await rig.account()
        rid = (await rig.upload(ana))["id"]
        rig.claude.script_chat(answer_with(UNSUPPORTED))

        events = await ask(rig, ana, {"id": rid}, ana.profile_id)

        assert rewrites(events) == [] and text_of(events) == "Here is a tighter way to say it."
        assert "40%" not in "".join(_wire(events))
        assert events[-1].type == "done"


async def test_a_path_resume_is_checked_against_the_role_it_is_for(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        await rig.upload(ana)
        path = (
            await rig.post(ana, f"/api/resumes/path?profile={ana.profile_id}", json={"roleId": ROLE})
        ).json()
        rid = path["resume"]["id"]
        rig.claude.script_chat(answer_with(GOOD))

        events = await ask(rig, ana, {"id": rid}, ana.profile_id)

        [event] = rewrites(events)
        assert event.resumeId == rid


async def test_a_resume_the_caller_may_not_open_is_ignored_without_a_word(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        ben = await rig.account("ben@example.com", pid="ben-profile")
        mine = (await rig.upload(ana))["id"]
        other_profile = await rig.upload(ben)
        rig.claude.script_chat(
            *[text_turn("Plain answer.\n[[rewrite: l1 | Something else.]]") for _ in range(4)]
        )

        # Ben names Ana's resume while chatting about his own profile; Ana names Ben's; a resume of another profile of her own account
        # is not the profile in view; an unknown id and a malformed one are nothing
        first = await ask(rig, ben, {"id": mine}, ben.profile_id)
        second = await ask(rig, ana, {"id": other_profile["id"]}, ana.profile_id)
        third = await ask(rig, ana, {"id": "0123456789abcdef0123456789abcdef"}, ana.profile_id)
        fourth = await ask(rig, ana, {"id": "../x"}, ana.profile_id)

    assert (
        rewrites(first) == [] and rewrites(second) == [] and rewrites(third) == [] and rewrites(fourth) == []
    )
    for events in (first, second, third, fourth):
        assert events[-1].type == "done" and "[[" not in text_of(events)
    # none of them was shown a resume block
    assert not any(
        b.text.startswith("## The resume in view") for c in rig.claude.chat_calls[:4] for b in c.system
    )


async def test_a_resume_of_another_profile_than_the_one_in_view_is_ignored(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        rid = (await rig.upload(ana))["id"]
        container = (
            rig.env.container
        )  # a second profile of the same account: its resume is not the one in view
        profile = {**rig.persona.profile.to_dict(), "id": "ana-second"}
        await container.repos.profiles.save(
            "ana-second",
            profile,
            rig.persona.context,
            goal=rig.persona.profile.goal,
            owner_hash=None,
            is_sample=False,
        )
        await container.repos.users.claim_profile(ana.user.id, "ana-second")
        rig.claude.script_chat(text_turn("Plain answer.\n[[rewrite: l1 | Something else.]]"))

        events = await ask(rig, ana, {"id": rid}, "ana-second")

        assert rewrites(events) == []
        assert not any(b.text.startswith("## The resume in view") for b in rig.claude.chat_calls[0].system)


async def test_a_chat_without_a_resume_is_unchanged_and_a_stray_rewrite_line_gives_no_event(
    start: Any,
) -> None:
    async with start() as rig:
        ana = await rig.account()
        rig.claude.script_chat(text_turn("Plain answer.\n[[rewrite: l1 | Something else.]]"))

        events = await ask(rig, ana, None, ana.profile_id)

        assert rewrites(events) == [] and text_of(events) == "Plain answer."
        assert len(rig.claude.chat_calls[0].system) == 3
