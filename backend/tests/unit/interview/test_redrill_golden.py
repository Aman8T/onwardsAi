"""Golden snapshot of a re-drill debrief (I2): the JSON the Practice screens of wave I2c develop against.

`tests/data/interview/redrill.debrief.json` is the `Debrief` of one scripted re-drill of the Headout interview's claims, as
the contract serialises it (camelCase, unset optionals omitted; the random session id replaced by ``<session>``). The three
claims come back in code's order: the Kite UI claim and the story (Couldn't defend, asked in new words), then the LCP claim
(Shaky, at its push). Kite UI cannot be defended again and gets "Or say less"; the LCP claim moves up. The test replays it and
compares; set ``UPDATE_INTERVIEW_GOLDEN=1`` to rewrite the file after a deliberate change.
"""

from __future__ import annotations

import json
import os
from typing import Any

from tayyari.contract.interview import Debrief
from tayyari.core.paths import REPO_ROOT
from tests.unit.interview.flows import end, say
from tests.unit.interview.support import judged, make_rig
from tests.unit.interview.test_redrill_service import headout_done, start_redrill

GOLDEN = REPO_ROOT / "backend" / "tests" / "data" / "interview" / "redrill.debrief.json"

KITE = "I ran the visual checks on the Kite UI pull requests, but someone else set up the baselines."
KITE_FLOOR = "Someone else on the team owned most of the components."
STORY = "We worked through it with the designer over a few calls and it shipped."
STORY_FLOOR = "I don't remember who I talked to first, honestly."
LCP = "I loaded the payment script only when the pay step came into view, and checked the traces in the pull request."
SOFTER = "Ran the visual checks on the Kite UI pull requests"


async def snapshot() -> dict[str, Any]:
    rig = make_rig()
    await headout_done(rig)
    _, session = await start_redrill(rig, length="full")
    sid = session.id
    await say(
        rig, sid, KITE, verdict=judged(KITE, "couldnt", quote="someone else set up the baselines"), after=40
    )
    await say(rig, sid, KITE_FLOOR, verdict=judged(KITE_FLOOR, "couldnt"), after=30)
    await say(
        rig,
        sid,
        STORY,
        verdict=judged(STORY, "couldnt", why="No part of it is the person's own yet."),
        after=50,
    )
    await say(rig, sid, STORY_FLOOR, verdict=judged(STORY_FLOOR, "couldnt"), after=25)
    await say(
        rig,
        sid,
        LCP,
        verdict=judged(LCP, "defended", quote="checked the traces in the pull request"),
        after=45,
    )
    script = {
        "slots": [
            {
                "id": "s1",
                "say": {"sources": [], "text": ""},
                "avoid": {"quote": "", "advice": ""},
                "fix": {
                    "text": "Write down which parts of the Kite UI visual checks were yours and which were not.",
                    "minutes": 10,
                },
                "softer": SOFTER,
            }
        ]
    }
    debrief = await end(rig, sid, script=script)
    data = debrief.to_dict()
    data["sessionId"] = "<session>"
    return data


async def test_the_snapshot_is_what_a_redrill_produces_and_is_valid_contract_json() -> None:
    got = await snapshot()
    if os.environ.get("UPDATE_INTERVIEW_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert got == want, "the re-drill debrief changed: review it, then run with UPDATE_INTERVIEW_GOLDEN=1"
    assert Debrief.from_dict(want).to_dict() == want


async def test_the_snapshot_moves_one_of_three_and_says_less_only_where_it_may() -> None:
    got = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert got["kind"] == "redrill" and got["redrill"] == {"moved": 1, "of": 3}
    results = got["results"]
    assert [r["verdict"] for r in results] == ["couldnt", "couldnt", "defended"]
    assert [r["previous"]["verdict"] for r in results] == ["couldnt", "couldnt", "shaky"]
    assert results[0]["softer"]["text"] == f"“{SOFTER}”"
    assert "softer" not in results[1] and "softer" not in results[2]
    assert all("score" not in key.lower() for r in results for key in r)
