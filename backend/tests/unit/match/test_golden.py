"""Golden snapshots of a whole match for both personas: the JSON the frontend wave develops against.

`tests/data/match/<persona>.match.json` holds `{posting, facts, result, variant, cover}` exactly as the contract
serialises them (camelCase, unset optionals omitted). The test replays the scripted pipeline and compares; set
``UPDATE_MATCH_GOLDEN=1`` to rewrite the files after a deliberate change.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from tayyari.contract.match import CoverNote, Fact, MatchResult, Posting, ResumeVariant
from tayyari.match.service import MatchService
from tests.fakes.claude import FakeClaudeClient
from tests.unit.match.support import CTX, DATA, HAIKU, Persona, js, meera, riya, script_pipeline


async def snapshot(persona: Persona) -> dict[str, Any]:
    fake = script_pipeline(FakeClaudeClient(), persona)
    fake.script_structured("match.cover", js(f"{persona.name}.cover.answer.json"))
    service = MatchService(claude=fake, model=HAIKU)
    outcome = await service.analyse(
        profile=persona.profile,
        context=persona.context,
        posting_text=persona.posting_text,
        call_ctx=CTX,
        portfolio_facts=persona.portfolio_facts,
    )
    resume = await service.compose_resume(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    cover = await service.compose_cover(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    exported_resume = await service.export_resume(resume.variant, outcome.book)
    exported_cover = await service.export_cover(cover.note, outcome.book, outcome.posting)
    return {
        "posting": outcome.posting.to_dict(),
        "facts": [f.to_dict() for f in outcome.book.facts],
        "result": outcome.result.to_dict(),
        "variant": exported_resume.variant.to_dict(),
        "cover": exported_cover.note.to_dict(),
    }


@pytest.mark.parametrize("persona", [meera(), riya()], ids=["meera", "riya"])
async def test_the_snapshot_is_what_the_pipeline_produces_and_is_valid_contract_json(
    persona: Persona,
) -> None:
    got = await snapshot(persona)
    path = DATA / f"{persona.name}.match.json"
    if os.environ.get("UPDATE_MATCH_GOLDEN") == "1":
        path.write_text(json.dumps(got, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    want = json.loads(path.read_text(encoding="utf-8"))
    assert got == want, "the match output changed: review it, then run with UPDATE_MATCH_GOLDEN=1"

    # the file is the contract: every part parses back into its model without loss
    assert Posting.from_dict(want["posting"]).to_dict() == want["posting"]
    assert [Fact.from_dict(f).to_dict() for f in want["facts"]] == want["facts"]
    assert MatchResult.from_dict(want["result"]).to_dict() == want["result"]
    assert ResumeVariant.from_dict(want["variant"]).to_dict() == want["variant"]
    assert CoverNote.from_dict(want["cover"]).to_dict() == want["cover"]


@pytest.mark.parametrize("persona", [meera(), riya()], ids=["meera", "riya"])
async def test_every_citation_of_the_snapshot_resolves_to_a_fact(persona: Persona) -> None:
    got = await snapshot(persona)
    known = {f["id"] for f in got["facts"]}
    cited = {i for row in got["result"]["coverage"] for i in row["factIds"]}
    cited |= {i for g in got["result"]["gates"] for i in g["factIds"]}
    for entry in (*got["variant"]["experience"], *got["variant"]["projects"]):
        for bullet in entry["bullets"]:
            cited |= set(bullet["factIds"])
    cited |= {i for g in got["variant"]["skills"] for item in g["items"] for i in item["factIds"]}
    cited |= {i for p in got["cover"]["paragraphs"] for i in p["factIds"]}
    assert cited
    assert cited <= known
    assert {c["requirementId"] for c in got["result"]["coverage"]} == {
        r["id"] for r in got["posting"]["requirements"]
    }
