"""The resume routes added in version 2, end to end through the real app over a real SQLite file: the grammar check at
Finalise, a wording finalised in one resume offered to the others, and a match's tailored resume saved into the list.
Only the network edges are faked (Claude is scripted). The rig, the `start` fixture and the helpers are those of
`test_resume_routes`."""

from __future__ import annotations

import json
from typing import Any

from tayyari.llm.errors import ClaudeNotConfigured, ClaudeOverloaded
from tayyari.llm.types import CallContext
from tayyari.resume.service import Holder
from tests.integration import test_resume_routes as routes
from tests.integration.test_resume_routes import FIGURE_LINE, ROLE, Account, Rig, lines

start = routes.start  # the fixture of the resume routes: Meera, a scripted model, the real app

TYPO = "Ran 38 A/B tests on landing pages and checkout nudges; five winners shipped and added 6.4% to site conversion"
"""The line of the block `exp-1.3` with its full stop missing."""
FIXED = TYPO + "."
REWORDED = "Ran 38 A/B tests across landing pages and checkout nudges; five winners shipped, adding 6.4% to site conversion."


def edit(text: str, line_id: str = FIGURE_LINE) -> dict[str, Any]:
    return {"ops": [{"op": "edit", "bulletId": line_id, "text": text}]}


# ───────────────────────── the grammar check ─────────────────────────


async def test_the_grammar_check_proposes_only_what_changes_no_fact_and_stores_nothing(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        rid = (await rig.upload(ana))["id"]
        typed = await rig.patch(ana, f"/api/resumes/{rid}", json=edit(TYPO))
        assert typed.status_code == 200
        stamp = typed.json()["resume"]["updatedAt"]
        rig.claude.script_structured(
            "resume.grammar",
            {
                "fixes": [
                    {"bulletId": FIGURE_LINE, "after": FIXED, "why": "Missing full stop"},
                    {
                        "bulletId": "b-exp-1.1",
                        "after": "Led the programme for 4,000 subscribers.",
                        "why": "x",
                    },
                ]
            },
            repeat=True,
        )

        response = await rig.post(ana, f"/api/resumes/{rid}/grammar")

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "proposals": [
                {"bulletId": FIGURE_LINE, "before": TYPO, "after": FIXED, "why": "Missing full stop"}
            ],
            "ran": True,
        }
        assert rig.calls("resume.grammar") == 1  # one call for the whole draft
        assert (await rig.get(ana, f"/api/resumes/{rid}")).json()["resume"]["updatedAt"] == stamp

        # the client accepts a proposal with the ordinary edit op; Finalise checks the line like any typed line
        accepted = await rig.patch(ana, f"/api/resumes/{rid}", json=edit(FIXED))
        assert accepted.status_code == 200
        done = await rig.post(ana, f"/api/resumes/{rid}/finalise", json={})
        assert done.status_code == 200 and done.json()["report"]["done"] is True


async def test_without_a_model_key_or_when_the_model_fails_the_check_answers_empty_and_not_a_500(
    start: Any,
) -> None:
    async with start() as rig:
        ana = await rig.account()
        rid = (await rig.upload(ana))["id"]
        for failure in (ClaudeNotConfigured(), ClaudeOverloaded()):
            rig.claude._queues.structured["resume.grammar"] = []
            rig.claude.script_structured("resume.grammar", failure, repeat=True)
            response = await rig.post(ana, f"/api/resumes/{rid}/grammar")
            assert response.status_code == 200, response.text
            assert response.json() == {"proposals": [], "ran": False}


async def test_the_grammar_check_reserves_spend_before_it_asks_the_model(start: Any) -> None:
    async with start(guard={"daily_ceiling_usd": 0.05, "est_match_usd": 0.06}) as rig:
        ana = await rig.account()
        # the uploaded resume exists already (the estimate is above the ceiling: seed it directly, which reserves nothing)
        stored = await rig.env.container.resume.ensure_upload(
            Holder(ana.profile_id, ana.user.id),
            rig.persona.profile,
            rig.persona.context,
            CallContext(purpose="match.facts"),
        )

        response = await rig.post(ana, f"/api/resumes/{stored.id}/grammar")

        assert response.status_code == 503
        assert rig.calls("resume.grammar") == 0


# ───────────────────────── a wording from another resume ─────────────────────────


async def test_a_wording_finalised_in_one_resume_is_offered_by_the_others(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        upload = await rig.upload(ana)
        path = (
            await rig.post(ana, f"/api/resumes/path?profile={ana.profile_id}", json={"roleId": ROLE})
        ).json()
        pid = path["resume"]["id"]
        assert (await rig.get(ana, f"/api/resumes/{upload['id']}/wordings")).json() == {"wordings": []}

        await rig.patch(ana, f"/api/resumes/{pid}", json=edit(REWORDED))
        assert (await rig.get(ana, f"/api/resumes/{upload['id']}/wordings")).json() == {
            "wordings": []
        }  # a draft
        done = await rig.post(ana, f"/api/resumes/{pid}/finalise", json={})
        assert done.json()["report"]["done"] is True

        response = await rig.get(ana, f"/api/resumes/{upload['id']}/wordings")

        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "wordings": [
                {
                    "lineId": FIGURE_LINE,
                    "text": REWORDED,
                    "fromResumeId": pid,
                    "fromTitle": path["resume"]["title"],
                }
            ]
        }
        assert (await rig.get(ana, f"/api/resumes/{pid}/wordings")).json() == {
            "wordings": []
        }  # never its own text
        taken = await rig.patch(
            ana, f"/api/resumes/{upload['id']}", json=edit(REWORDED)
        )  # taking one is the edit op
        assert taken.status_code == 200 and taken.json()["resume"]["state"] == "draft"


# ───────────────────────── saving a match's resume ─────────────────────────


async def build_match_resume(rig: Rig, who: Account, *, build: bool = True) -> str:
    """A match for Meera's posting, with its resume built unless ``build`` is False; the match id."""
    response = await rig.client.post(
        "/api/match",
        json={"profileId": who.profile_id, "text": rig.persona.posting_text},
        headers=who.headers,
    )
    assert response.status_code == 200, response.text
    match_id = str(
        [json.loads(line) for line in response.content.split(b"\n") if line.strip()][-1]["matchId"]
    )
    if build:
        built = await rig.post(who, f"/api/match/{match_id}/resume", json={})
        assert built.status_code == 200, built.text
    return match_id


async def test_a_match_resume_saved_into_the_list_is_a_posting_resume_and_saving_again_is_the_same_one(
    start: Any,
) -> None:
    async with start() as rig:
        ana = await rig.account()
        match_id = await build_match_resume(rig, ana)
        before = await rig.listing(ana)
        assert [r["kind"] for r in before["resumes"]] == [
            "upload"
        ]  # not in the list unless the person saves it

        saved = await rig.post(ana, "/api/resumes/from-match", json={"matchId": match_id})

        assert saved.status_code == 200, saved.text
        detail = saved.json()
        assert detail["resume"]["kind"] == "posting" and detail["resume"]["matchId"] == match_id
        assert detail["resume"]["isDefault"] is False and "roleId" not in detail["resume"]
        assert (
            detail["resume"]["state"] == "final" and detail["canDownload"] is True
        )  # the match had exported it
        assert lines(detail)
        again = await rig.post(ana, "/api/resumes/from-match", json={"matchId": match_id})
        assert again.json()["resume"]["id"] == detail["resume"]["id"]
        listing = await rig.listing(ana)
        assert [(r["kind"], r.get("matchId")) for r in listing["resumes"]] == [
            ("upload", None),
            ("posting", match_id),
        ]
        pdf = await rig.get(ana, f"/api/resumes/{detail['resume']['id']}/resume.pdf")
        assert pdf.status_code == 200
        made = await rig.post(ana, f"/api/resumes/{detail['resume']['id']}/default")
        assert made.status_code == 200 and made.json()["resume"]["isDefault"] is True


async def test_a_match_resume_with_an_open_figure_is_saved_as_a_draft_without_the_slot(start: Any) -> None:
    phrase = {
        FIGURE_LINE: {
            "factIds": ["exp-1.3"],
            "text": "Ran 38 A/B tests on landing pages, lifting site conversion by 40%.",
            "verdict": "exact",
        }
    }
    async with start(phrase=phrase) as rig:
        ana = await rig.account()
        match_id = await build_match_resume(rig, ana)

        saved = await rig.post(ana, "/api/resumes/from-match", json={"matchId": match_id})

        assert saved.status_code == 200, saved.text
        detail = saved.json()
        assert detail["resume"]["state"] == "draft" and detail["canDownload"] is False
        assert not any("[x]" in line["text"] for line in lines(detail))
        done = await rig.post(ana, f"/api/resumes/{detail['resume']['id']}/finalise", json={})
        assert done.status_code == 200 and done.json()["report"]["done"] is True


async def test_saving_a_match_that_is_not_the_callers_is_a_404(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        ben = await rig.account("ben@example.com", pid="ben-profile")
        match_id = await build_match_resume(rig, ana)

        theirs = await rig.post(ben, "/api/resumes/from-match", json={"matchId": match_id})
        unknown = await rig.post(ana, "/api/resumes/from-match", json={"matchId": "e" * 32})
        malformed = await rig.post(ana, "/api/resumes/from-match", json={"matchId": "../x"})
        empty = await rig.post(ana, "/api/resumes/from-match", json={})

        assert (theirs.status_code, unknown.status_code, malformed.status_code) == (404, 404, 404)
        assert theirs.json() == unknown.json()  # another account's match is exactly an unknown one
        assert empty.status_code == 400


async def test_a_match_whose_resume_was_never_built_has_nothing_to_save(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        bare = await build_match_resume(rig, ana, build=False)

        none = await rig.post(ana, "/api/resumes/from-match", json={"matchId": bare})

        assert none.status_code == 404 and "no resume" in none.json()["error"].lower()
        assert [r["kind"] for r in (await rig.listing(ana))["resumes"]] == ["upload"]
