"""Golden snapshot of a whole plan for the bundled persona: the JSON the frontend wave develops against.

`tests/data/prepare/riya.prepare.json` holds the `PreparePlan` for Riya's AI Product Engineer role exactly as the contract
serialises it (camelCase, unset optionals omitted), written by the scripted pipeline from the hand-written answers in
`riya.standing.answer.json` and `riya.moves.answer.json`. The test replays the pipeline and compares; set
``UPDATE_PREPARE_GOLDEN=1`` to rewrite the file after a deliberate change.
"""

from __future__ import annotations

import json
import os
from typing import Any

from tayyari.contract.prepare import PrepareDoneEvent, PreparePlan
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.repos import FakeRepositories
from tests.unit.prepare.support import CTX, DATA, make_service, plan_for, riya, script_riya

GOLDEN = DATA / "riya.prepare.json"


async def snapshot() -> dict[str, Any]:
    profile, context = riya()
    repos = FakeRepositories()
    await repos.profiles.save(
        profile.id, profile.to_dict(), context, goal=profile.goal, owner_hash=None, is_sample=True
    )
    service, _, _ = make_service(script_riya(FakeClaudeClient()), repos=repos)
    events = [e async for e in service.run(profile, context, plan_for(), CTX)]
    done = events[-1]
    assert isinstance(done, PrepareDoneEvent), events[-1]
    return done.plan.to_dict()


async def test_the_snapshot_is_what_the_pipeline_produces_and_is_valid_contract_json() -> None:
    got = await snapshot()
    if os.environ.get("UPDATE_PREPARE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert got == want, "the plan changed: review it, then run with UPDATE_PREPARE_GOLDEN=1"
    assert PreparePlan.from_dict(want).to_dict() == want


async def test_every_citation_of_the_snapshot_resolves_and_every_number_is_cited() -> None:
    got = json.loads(GOLDEN.read_text(encoding="utf-8"))
    known = {e["id"]: e["text"] for e in got["evidence"]}
    cited = {i for g in got["gaps"] for i in g.get("evidenceIds", [])}
    cited |= {i for m in got["moves"] for h in m["have"] for i in h["evidenceIds"]}
    assert cited and cited <= set(known)
    assert sum(1 for g in got["gaps"] if g.get("why")) == len(got["gaps"]) == 5
    assert got["headline"] and [m["id"] for m in got["moves"]] == ["m1", "m2", "m3"]
    gap_names = {g["name"] for g in got["gaps"]}
    for move in got["moves"]:
        assert move["works"] and set(move["works"]) <= gap_names
        assert 1 <= len(move["phases"]) <= 5
        for phase in move["phases"]:
            assert 1 <= phase["hours"] <= 40 and 1 <= len(phase["tasks"]) <= 4 and phase["deliverable"]
    assert (
        got["state"] == {"hoursPerWeek": 6, "moveIndex": 0, "done": [], "links": []} and got["version"] == 1
    )
    assert got["rank"] == 5 and got["roleCount"] == 5 and "alignment" not in got
    # no score anywhere the model wrote: the headline, the whys, the moves (an evidence line may quote a real "+9.4%")
    written = [got["headline"], *(g.get("why", "") for g in got["gaps"])]
    for move in got["moves"]:
        written += [move["title"], move["body"], *(h["text"] for h in move["have"])]
    assert all("%" not in text for text in written)
