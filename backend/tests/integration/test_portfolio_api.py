"""End-to-end portfolios through the real app: the generation stream, the reads and edits, the rewrite, the public
page, and the guards in front of them, over a real SQLite file. Only the network edges are faked (Claude and Jev)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import httpx

from tayyari.api.routes.portfolio import BODY_MAX_BYTES
from tayyari.contract import encode_event
from tayyari.contract.portfolio import (
    Portfolio,
    PortfolioDoneEvent,
    PortfolioErrorEvent,
    PortfolioEvent,
    PortfolioEventAdapter,
    spec_problems,
)
from tayyari.core.paths import PKG_DIR
from tayyari.core.settings import Settings
from tayyari.llm.errors import SPEND_CEILING_MESSAGE, SpendCeilingReached
from tayyari.portfolio.service import PAUSED_MESSAGE, PORTFOLIO_NOT_FOUND, PROFILE_NOT_FOUND
from tayyari.sample.provider import SampleProvider
from tayyari.storage.tables import LlmCallRow, PortfolioRow, RateLimitHitRow
from tests.fakes.jev import FakeJev
from tests.integration.support import Env, JevScript, RecordingClaude, ndjson_lines, roomy_guard, running
from tests.unit.portfolio._fixtures_c import good_copy, is_copy_call, script_copy, script_facts
from tests.unit.test_layering import scan

MakeSettings = Callable[..., Settings]
UNKNOWN_ID = "f" * 32


def scripted_claude() -> RecordingClaude:
    """A Claude that extracts the sample's facts and writes the copy of the sample persona (a careful Sonnet)."""
    claude = RecordingClaude()
    script_facts(claude, SampleProvider().sample_context)
    script_copy(claude, good_copy("riya"))
    return claude


def portfolio_events(response: httpx.Response) -> list[PortfolioEvent]:
    """Every line of a /api/portfolio body parsed through the adapter, in canonical wire form."""
    events: list[PortfolioEvent] = []
    for line in ndjson_lines(response):
        event = PortfolioEventAdapter.validate_json(line)
        assert encode_event(event) == line + b"\n", line[:120]
        events.append(event)
    return events


def done_page(events: list[PortfolioEvent]) -> Portfolio:
    last = events[-1]
    assert isinstance(last, PortfolioDoneEvent), last
    return last.portfolio


async def generate(env: Env, **body: Any) -> httpx.Response:
    return await env.client.post("/api/portfolio", json={"profileId": env.sample_id, **body})


def error_of(response: httpx.Response) -> str:
    assert response.headers["content-type"].startswith("application/json")
    body: dict[str, str] = response.json()
    assert set(body) == {"error"}
    return body["error"]


def jev() -> FakeJev:
    return FakeJev(JevScript())


# ───────────────────────── generate and read ─────────────────────────


async def test_post_streams_the_generation_and_the_page_is_saved_and_readable(
    build_settings: MakeSettings,
) -> None:
    claude = scripted_claude()
    async with running(build_settings(), claude=claude, jev=jev()) as env:
        response = await generate(env)
        events = portfolio_events(response)
        rows = await env.rows(PortfolioRow)
        usage = await env.rows(LlmCallRow)
        read = await env.client.get(f"/api/portfolio/{env.sample_id}")
        sample_id = env.sample_id

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson; charset=utf-8"
    assert [e.type for e in events] == ["progress", "progress", "draft", "progress", "progress", "done"]
    page = done_page(events)
    assert page.profileId == sample_id and page.publicId != sample_id
    assert {s.origin for s in page.content.slots} == {"model"}
    assert all(spec_problems(look) == [] for look in page.looks)

    (row,) = rows
    assert (row.profile_id, row.public_id, row.version, row.published) == (sample_id, page.publicId, 1, False)
    assert row.facts_hash and row.doc["publicId"] == page.publicId
    assert [g["id"] for g in row.generated] == [s.id for s in page.content.slots]

    # one Haiku call for the facts and one Sonnet call for the copy, both recorded for this client
    assert sorted(r.purpose for r in usage) == ["portfolio", "portfolio"]
    assert {r.model for r in usage} == {claude.structured_calls[0].model, claude.structured_calls[1].model}
    assert all(r.profile_id == sample_id and r.ip_hash for r in usage)

    assert read.status_code == 200 and read.headers["cache-control"] == "no-store"
    assert read.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert read.json() == page.to_dict()


async def test_jev_is_asked_about_every_sentence_and_a_contradiction_sends_the_slot_to_the_fallback(
    build_settings: MakeSettings,
) -> None:
    claude = scripted_claude()
    script = JevScript(evidence=lambda claim: "contradicts")
    async with running(build_settings(), claude=claude, jev=FakeJev(script)) as env:
        response = await generate(env)
        await env.settle()

    page = done_page(portfolio_events(response))
    assert {s.origin for s in page.content.slots} == {"resume"}  # nothing the model wrote survived
    copy_calls = [c for c in claude.structured_calls if is_copy_call(c)]
    assert len(copy_calls) == 2  # the page, then ONE re-ask
    assert "is not supported" in copy_calls[1].user_text


async def test_an_existing_page_is_returned_at_once_and_does_not_count_against_the_limit(
    build_settings: MakeSettings,
) -> None:
    claude = scripted_claude()
    async with running(
        build_settings(guard=roomy_guard(portfolio_per_minute=5)), claude=claude, jev=jev()
    ) as env:
        first = done_page(portfolio_events(await generate(env)))
        calls = len(claude.structured_calls)
        second_response = await generate(env)
        hits = await env.rows(RateLimitHitRow)

    second = portfolio_events(second_response)
    assert [e.type for e in second] == ["done"]
    assert done_page(second).to_dict() == first.to_dict()
    assert len(claude.structured_calls) == calls
    counted = [h.count for h in hits if h.bucket.startswith("portfolio:min:")]
    assert counted == [1]  # only the generation was counted


async def test_regenerate_makes_version_two_and_keeps_the_public_id(build_settings: MakeSettings) -> None:
    claude = scripted_claude()
    async with running(build_settings(), claude=claude, jev=jev()) as env:
        first = done_page(portfolio_events(await generate(env)))
        second = done_page(portfolio_events(await generate(env, regenerate=True)))
        rows = await env.rows(PortfolioRow)

    assert (second.version, second.publicId) == (2, first.publicId)
    assert len(rows) == 1 and rows[0].version == 2
    # the facts were read once: the second run reused them
    assert len([c for c in claude.structured_calls if not is_copy_call(c)]) == 1


async def test_an_unknown_profile_is_an_error_event_in_the_stream(build_settings: MakeSettings) -> None:
    claude = scripted_claude()
    async with running(build_settings(), claude=claude, jev=jev()) as env:
        response = await env.client.post("/api/portfolio", json={"profileId": UNKNOWN_ID})

    events = portfolio_events(response)
    assert response.status_code == 200 and len(events) == 1
    assert isinstance(events[0], PortfolioErrorEvent) and events[0].message == PROFILE_NOT_FOUND
    assert claude.calls == []


async def test_reads_answer_404_json_until_there_is_a_page(build_settings: MakeSettings) -> None:
    async with running(build_settings(), claude=scripted_claude(), jev=jev()) as env:
        sample_id = env.sample_id
        missing = await env.client.get(f"/api/portfolio/{sample_id}")
        preview = await env.client.get(f"/api/portfolio/{sample_id}/public-preview")
        odd_shape = await env.client.get("/api/portfolio/not%20an%20id")
        public = await env.client.get("/api/p/0123456789ab")

    for response in (missing, preview, odd_shape, public):
        assert response.status_code == 404
        assert error_of(response) == PORTFOLIO_NOT_FOUND


# ───────────────────────── edit and rewrite ─────────────────────────


async def test_patch_edits_a_slot_and_the_look_and_returns_the_page(build_settings: MakeSettings) -> None:
    async with running(build_settings(), claude=scripted_claude(), jev=jev()) as env:
        first = done_page(portfolio_events(await generate(env)))
        url = f"/api/portfolio/{env.sample_id}"
        edited = await env.client.patch(url, json={"slots": [{"id": "tagline", "text": "Hello, world."}]})
        looked = await env.client.patch(url, json={"lookIndex": len(first.looks) - 1, "defaultView": "press"})
        read = await env.client.get(url)
        rows = await env.rows(PortfolioRow)

    assert edited.status_code == 200 and edited.headers["cache-control"] == "no-store"
    tagline = next(s for s in Portfolio.model_validate(edited.json()).content.slots if s.id == "tagline")
    assert (tagline.text, tagline.origin, tagline.factIds) == ("Hello, world.", "user", [])
    assert edited.json()["version"] == 2 and edited.json()["publicId"] == first.publicId

    assert looked.status_code == 200
    page = Portfolio.model_validate(looked.json())
    assert page.lookIndex == len(first.looks) - 1 and page.publish.defaultView == "press"
    assert spec_problems(page.spec) == [] and page.version == 3
    assert read.json() == looked.json()
    assert (
        rows[0].version == 3
        and next(g for g in rows[0].generated if g["id"] == "tagline")["origin"] == "model"
    )


async def test_patch_refuses_what_the_kit_does_not_allow(build_settings: MakeSettings) -> None:
    async with running(build_settings(), claude=scripted_claude(), jev=jev()) as env:
        await generate(env)
        url = f"/api/portfolio/{env.sample_id}"
        html = await env.client.patch(url, json={"slots": [{"id": "tagline", "text": "<script>x</script>"}]})
        unknown = await env.client.patch(url, json={"slots": [{"id": "headline", "text": "x"}]})
        too_long = await env.client.patch(url, json={"slots": [{"id": "tagline", "text": "x" * 91}]})
        bad_shape = await env.client.patch(url, json={"lookIndex": 9})
        no_page = await env.client.patch(f"/api/portfolio/{UNKNOWN_ID}", json={"defaultView": "press"})
        odd_id = await env.client.patch("/api/portfolio/not%20an%20id", json={})
        read = await env.client.get(url)

    assert [r.status_code for r in (html, unknown, too_long, bad_shape)] == [400, 400, 400, 400]
    assert "plain text" in error_of(html)
    assert "not a piece of text you can edit" in error_of(unknown)
    assert "limited to 90 characters" in error_of(too_long)
    assert error_of(bad_shape).startswith("Invalid portfolio request: lookIndex")
    assert (no_page.status_code, odd_id.status_code) == (404, 404)
    assert read.json()["version"] == 1  # nothing was saved


async def test_refresh_graph_takes_a_new_snapshot_of_the_profile(build_settings: MakeSettings) -> None:
    async with running(build_settings(), claude=scripted_claude(), jev=jev()) as env:
        first = done_page(portfolio_events(await generate(env)))
        url = f"/api/portfolio/{env.sample_id}"
        refreshed = await env.client.patch(url, json={"refreshGraph": True})

    assert refreshed.status_code == 200
    page = Portfolio.model_validate(refreshed.json())
    assert page.graph.domains == first.graph.domains and page.graphOutdated is False


async def test_rewrite_writes_one_slot_again_and_is_rate_limited_like_a_generation(
    build_settings: MakeSettings,
) -> None:
    claude = scripted_claude()
    async with running(
        build_settings(guard=roomy_guard(portfolio_per_minute=3)), claude=claude, jev=jev()
    ) as env:
        await generate(env)  # 1
        url = f"/api/portfolio/{env.sample_id}"
        await env.client.patch(url, json={"slots": [{"id": "tagline", "text": "Edited by hand."}]})
        calls = len(claude.structured_calls)
        rewritten = await env.client.post(f"{url}/rewrite", json={"slotId": "tagline"})  # 2
        asked = claude.structured_calls[calls:]
        refused = await env.client.post(f"{url}/rewrite", json={"slotId": "nonsense"})  # 3
        missing = await env.client.post(
            f"{url}/rewrite", json={}
        )  # 4: invalid body, refused before it is counted
        hits = await env.rows(RateLimitHitRow)

    assert rewritten.status_code == 200
    tagline = next(s for s in Portfolio.model_validate(rewritten.json()).content.slots if s.id == "tagline")
    assert tagline.origin == "model" and tagline.text.startswith("I care about motion")
    assert len(asked) == 1 and is_copy_call(asked[0])
    assert refused.status_code == 400 and "not a piece of text that can be rewritten" in error_of(refused)
    assert missing.status_code == 400 and error_of(missing).startswith("Invalid portfolio request: slotId")
    # the generation, the rewrite and the refused slot were counted; the malformed body was not
    assert [h.count for h in hits if h.bucket.startswith("portfolio:min:")] == [3]


async def test_rewrite_of_a_missing_page_is_404_and_a_bad_body_is_400(build_settings: MakeSettings) -> None:
    async with running(build_settings(), claude=scripted_claude(), jev=jev()) as env:
        no_page = await env.client.post(f"/api/portfolio/{UNKNOWN_ID}/rewrite", json={"slotId": "tagline"})
        empty = await env.client.post(f"/api/portfolio/{env.sample_id}/rewrite", json={"slotId": ""})
        wrong_type = await env.client.post(
            f"/api/portfolio/{env.sample_id}/rewrite",
            content=b'{"slotId":"tagline"}',
            headers={"content-type": "text/plain"},
        )

    assert no_page.status_code == 404 and error_of(no_page) == PORTFOLIO_NOT_FOUND
    assert empty.status_code == 400 and error_of(empty).startswith("Invalid portfolio request: slotId")
    assert wrong_type.status_code == 415


# ───────────────────────── preview and the public page ─────────────────────────


async def test_the_preview_and_the_public_page_never_carry_the_profile_id(
    build_settings: MakeSettings,
) -> None:
    async with running(build_settings(), claude=scripted_claude(), jev=jev()) as env:
        page = done_page(portfolio_events(await generate(env)))
        sample_id = env.sample_id
        preview = await env.client.get(f"/api/portfolio/{sample_id}/public-preview")
        unpublished = await env.client.get(f"/api/p/{page.publicId}")

        stored = await env.container.repos.portfolios.get(sample_id)
        assert stored is not None
        await env.container.repos.portfolios.put(
            dataclasses.replace(stored, published=True), expected_version=stored.version
        )
        published = await env.client.get(f"/api/p/{page.publicId}")
        rows = await env.rows(PortfolioRow)

    assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store"
    assert unpublished.status_code == 404 and error_of(unpublished) == PORTFOLIO_NOT_FOUND

    assert published.status_code == 200
    body = published.json()
    assert body["publicId"] == page.publicId
    assert set(body) <= {"publicId", "kitVersion", "spec", "content", "graph", "publishedAt", "defaultView"}
    for text in (preview.text, published.text):
        assert sample_id not in text and "profileId" not in text
    assert body["content"]["person"]["name"] == page.content.person.name
    assert all(s["score"] >= 45 for d in body["graph"]["domains"] for s in d["skills"])
    assert rows[0].last_viewed_at is not None  # a public view touches the row


# ───────────────────────── the guards ─────────────────────────


async def test_a_cross_site_request_is_refused_before_anything_runs(build_settings: MakeSettings) -> None:
    claude = scripted_claude()
    async with running(build_settings(), claude=claude, jev=jev()) as env:
        posts = await env.client.post(
            "/api/portfolio", json={"profileId": env.sample_id}, headers={"Origin": "https://evil.example"}
        )
        patches = await env.client.patch(
            f"/api/portfolio/{env.sample_id}", json={}, headers={"Sec-Fetch-Site": "cross-site"}
        )
        rows = await env.rows(PortfolioRow)

    assert posts.status_code == 403 and patches.status_code == 403
    assert claude.calls == [] and rows == []


async def test_the_body_is_checked_before_any_work(build_settings: MakeSettings) -> None:
    claude = scripted_claude()
    async with running(build_settings(), claude=claude, jev=jev()) as env:
        not_json = await env.client.post(
            "/api/portfolio", content=b'{"profileId": "x"}', headers={"content-type": "text/plain"}
        )
        broken = await env.client.post(
            "/api/portfolio", content=b"{nope", headers={"content-type": "application/json"}
        )
        empty = await env.client.post("/api/portfolio", json={})
        long_id = await env.client.post("/api/portfolio", json={"profileId": "a" * 200})
        odd_id = await env.client.post("/api/portfolio", json={"profileId": "../etc/passwd"})
        huge = await env.client.post(
            "/api/portfolio",
            content=b" " * (BODY_MAX_BYTES + 10),
            headers={"content-type": "application/json"},
        )
        hits = await env.rows(RateLimitHitRow)

    assert not_json.status_code == 415
    assert broken.status_code == 400 and error_of(broken) == "Invalid JSON body"
    assert empty.status_code == 400 and error_of(empty).startswith("Invalid portfolio request: profileId")
    assert long_id.status_code == 400
    assert odd_id.status_code == 404 and error_of(odd_id) == "Profile not found"
    assert huge.status_code == 413
    assert claude.calls == []
    assert [h for h in hits if h.bucket.startswith("portfolio:")] == []  # none of them cost a count


async def test_the_per_ip_limit_answers_429_json_with_retry_after(build_settings: MakeSettings) -> None:
    claude = scripted_claude()
    settings = build_settings(guard=roomy_guard(portfolio_per_minute=2))
    async with running(settings, claude=claude, jev=jev()) as env:
        first = await generate(env, regenerate=True)
        second = await generate(env, regenerate=True)
        limited = await generate(env, regenerate=True)
        other_client = await env.client.post(
            "/api/portfolio",
            json={"profileId": env.sample_id, "regenerate": True},
            headers={"cf-connecting-ip": "198.51.100.9"},
        )
        hits = await env.rows(RateLimitHitRow)

    assert [first.status_code, second.status_code] == [200, 200]
    assert limited.status_code == 429
    assert "portfolios" in error_of(limited)
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert other_client.status_code == 200
    counted = sorted(h.count for h in hits if h.bucket.startswith("portfolio:min:"))
    assert counted[-1] >= 2


async def test_the_global_hourly_cap_stops_everyone(build_settings: MakeSettings) -> None:
    settings = build_settings(guard=roomy_guard(portfolio_global_per_hour=1))
    async with running(settings, claude=scripted_claude(), jev=jev()) as env:
        first = await generate(env, regenerate=True)
        capped = await env.client.post(
            "/api/portfolio",
            json={"profileId": env.sample_id, "regenerate": True},
            headers={"cf-connecting-ip": "198.51.100.77"},
        )

    assert first.status_code == 200
    assert capped.status_code == 429 and "very busy" in error_of(capped)


async def test_a_zero_ceiling_answers_503_before_any_work(build_settings: MakeSettings) -> None:
    claude = scripted_claude()
    async with running(
        build_settings(guard=roomy_guard(daily_ceiling_usd=0.0)), claude=claude, jev=jev()
    ) as env:
        paused = await generate(env)
        rewrite = await env.client.post(f"/api/portfolio/{env.sample_id}/rewrite", json={"slotId": "tagline"})
        read = await env.client.get(f"/api/portfolio/{env.sample_id}")

    for response in (paused, rewrite):
        assert response.status_code == 503
        assert error_of(response) == SPEND_CEILING_MESSAGE
        assert 1 <= int(response.headers["retry-after"]) <= 86_400
    assert claude.calls == []
    assert read.status_code == 404  # reads never need the ceiling


async def test_a_ceiling_reached_during_the_run_ends_the_stream_with_a_calm_error(
    build_settings: MakeSettings,
) -> None:
    claude = RecordingClaude()
    claude.script_structured("portfolio", SpendCeilingReached(60.0), repeat=True)
    async with running(build_settings(), claude=claude, jev=jev()) as env:
        response = await generate(env)
        rows = await env.rows(PortfolioRow)

    events = portfolio_events(response)
    assert response.status_code == 200 and [e.type for e in events] == ["progress", "error"]
    assert isinstance(events[-1], PortfolioErrorEvent) and events[-1].message == PAUSED_MESSAGE
    assert rows == []


def test_the_portfolio_package_respects_the_layering_rule() -> None:
    mine = [v for v in scan(PKG_DIR) if "portfolio" in v.file or "portfolio" in v.imported]
    assert mine == [], "\n".join(str(v) for v in mine)
