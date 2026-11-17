"""The four monthly counts of Just Looking through the real app (roadmap B2, "Where each count is taken"): analyses, searches
with AI, practice interviews and coach questions. For each: taken for a signed-in account, refused as a 402 with the
feature, the limit and the reset moment before anything starts or is spent, never refused on Day One, never counted for the
unlimited list or with no account, and given back in the failure case the table names."""

# ruff: noqa: F811 - the imported fixtures are used by their parameter names
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from tayyari.billing.products import LIFETIME_DAYS
from tayyari.core.settings import Settings
from tayyari.llm.errors import SPEND_CEILING_MESSAGE
from tests.fakes.claude import text_turn
from tests.integration.scenarios import NOTES, script_basic_analysis
from tests.integration.support import Env, RecordingClaude, roomy_guard, running
from tests.integration.test_interview_routes import FRONTEND_ID as PRACTICE_PROFILE
from tests.integration.test_interview_routes import start as practice_start  # noqa: F401 - the fixture
from tests.integration.test_jobsearch_routes import FRONTEND_ID as SEARCH_PROFILE
from tests.integration.test_jobsearch_routes import start as search_start  # noqa: F401 - the fixture

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
LISTED = "tester@example.com"
LOW = {"free_analyses": 1, "free_searches": 1, "free_practice": 1, "free_coach": 1}
"""One of each a month, so the second request is the one at the limit."""


async def sign_in(env: Env, email: str = "ana@example.com") -> tuple[str, dict[str, str]]:
    container = env.container
    user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
    token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
    return user.id, {"cookie": f"{SESSION}={token}"}


async def go_day_one(env: Env, user_id: str) -> None:
    container = env.container
    granted = await container.repos.billing.grant(
        user_id, credits=0, pass_days=LIFETIME_DAYS, ref="test-day-one", now=container.clock.now()
    )
    assert granted
    assert await container.plans.plan_of(user_id) == "day_one"


async def used(env: Env, user_id: str, feature: str) -> int:
    usage = (await env.container.plans.usage(user_id)).to_dict()
    return int(usage[feature]["used"])


def assert_limit_402(response: httpx.Response, feature: str, limit: int = 1) -> None:
    assert response.status_code == 402, response.text
    body = response.json()
    assert body["error"] == "payment_required" and body["reason"] == "limit_reached"
    assert body["feature"] == feature and body["limit"] == limit
    assert datetime.fromisoformat(body["resetsAt"]).tzinfo is not None
    assert body["resetsAt"].endswith("T00:00:00+00:00")
    assert "application/x-ndjson" not in response.headers.get("content-type", "")


def scripted() -> RecordingClaude:
    claude = RecordingClaude()
    script_basic_analysis(claude)
    claude.script_chat(text_turn("Hi."), repeat=True)
    return claude


# ───────────────────────── analyses ─────────────────────────


async def test_an_analysis_is_counted_and_the_one_at_the_limit_is_refused_before_anything_starts(
    build_settings: MakeSettings,
) -> None:
    async with running(build_settings(plans=LOW), claude=scripted()) as env:
        user_id, headers = await sign_in(env)
        first = await env.analyze(notes=NOTES, headers=headers)
        calls = len(env.claude.calls)
        refused = await env.analyze(notes=NOTES, headers=headers)
        assert first.status_code == 200 and b'"type":"done"' in first.content
        assert_limit_402(refused, "analyses")
        assert len(env.claude.calls) == calls  # nothing started
        assert await used(env, user_id, "analyses") == 1
        assert env.container.spend_guard.reserved_micro == 0


async def test_a_bad_upload_is_refused_before_it_is_counted(build_settings: MakeSettings) -> None:
    async with running(build_settings(plans=LOW), claude=scripted()) as env:
        user_id, headers = await sign_in(env)
        bad = await env.analyze(headers=headers)  # nothing to analyse
        assert bad.status_code == 400
        assert await used(env, user_id, "analyses") == 0


async def test_an_analysis_that_ends_without_a_profile_is_given_back(build_settings: MakeSettings) -> None:
    async with running(
        build_settings(plans=LOW), claude=RecordingClaude()
    ) as env:  # nothing scripted: it fails
        user_id, headers = await sign_in(env)
        failed = await env.analyze(notes=NOTES, headers=headers)
        assert failed.status_code == 200 and b'"type":"done"' not in failed.content
        assert b'"type":"error"' in failed.content
        assert await used(env, user_id, "analyses") == 0
        again = await env.analyze(notes=NOTES, headers=headers)  # the use is free again, not refused
        assert again.status_code == 200


async def test_a_refused_analysis_by_the_spend_ceiling_gives_its_use_back(
    build_settings: MakeSettings,
) -> None:
    settings = build_settings(plans=LOW, guard=roomy_guard(daily_ceiling_usd=0.0001))
    async with running(settings, claude=scripted()) as env:
        user_id, headers = await sign_in(env)
        refused = await env.analyze(notes=NOTES, headers=headers)
        assert refused.status_code == 503 and SPEND_CEILING_MESSAGE in refused.text
        assert await used(env, user_id, "analyses") == 0


async def test_day_one_is_never_refused_an_analysis_but_its_uses_are_recorded(
    build_settings: MakeSettings,
) -> None:
    async with running(build_settings(plans=LOW), claude=scripted()) as env:
        user_id, headers = await sign_in(env)
        await go_day_one(env, user_id)
        codes = [(await env.analyze(notes=NOTES, headers=headers)).status_code for _ in range(3)]
        assert codes == [200, 200, 200]
        assert await used(env, user_id, "analyses") == 3


async def test_the_unlimited_list_and_a_visitor_are_not_counted_for_analyses(
    build_settings: MakeSettings,
) -> None:
    settings = build_settings(plans=LOW, guard=roomy_guard(unlimited_emails=LISTED))
    async with running(settings, claude=scripted()) as env:
        listed_id, listed = await sign_in(env, LISTED)
        codes = [(await env.analyze(notes=NOTES, headers=listed)).status_code for _ in range(3)]
        visitors = [(await env.analyze(notes=NOTES)).status_code for _ in range(3)]
        assert codes == [200] * 3 and visitors == [200] * 3
        assert await used(env, listed_id, "analyses") == 0


# ───────────────────────── coach questions ─────────────────────────


async def test_a_coach_question_is_counted_and_the_one_at_the_limit_is_refused_before_the_stream(
    build_settings: MakeSettings,
) -> None:
    async with running(build_settings(plans=LOW), claude=scripted()) as env:
        user_id, headers = await sign_in(env)
        first = await env.chat(headers=headers)
        calls = len(env.claude.calls)
        refused = await env.chat(headers=headers)
        assert first.status_code == 200
        assert_limit_402(refused, "coach")
        assert len(env.claude.calls) == calls
        assert await used(env, user_id, "coach") == 1
        assert env.container.spend_guard.reserved_micro == 0


async def test_a_coach_question_refused_by_the_spend_ceiling_is_given_back(
    build_settings: MakeSettings,
) -> None:
    settings = build_settings(plans=LOW, guard=roomy_guard(daily_ceiling_usd=0.0001))
    async with running(settings, claude=scripted()) as env:
        user_id, headers = await sign_in(env)
        refused = await env.chat(headers=headers)
        assert refused.status_code == 503
        assert await used(env, user_id, "coach") == 0


async def test_a_bad_chat_body_is_refused_before_it_is_counted(build_settings: MakeSettings) -> None:
    async with running(build_settings(plans=LOW), claude=scripted()) as env:
        user_id, headers = await sign_in(env)
        bad = await env.client.post("/api/chat", json={"profileId": env.sample_id}, headers=headers)
        assert bad.status_code == 400
        assert await used(env, user_id, "coach") == 0


async def test_day_one_the_unlimited_list_and_visitors_are_not_refused_a_coach_question(
    build_settings: MakeSettings,
) -> None:
    settings = build_settings(plans=LOW, guard=roomy_guard(unlimited_emails=LISTED))
    async with running(settings, claude=scripted()) as env:
        day_one_id, day_one = await sign_in(env)
        await go_day_one(env, day_one_id)
        listed_id, listed = await sign_in(env, LISTED)
        assert [(await env.chat(headers=day_one)).status_code for _ in range(3)] == [200] * 3
        assert [(await env.chat(headers=listed)).status_code for _ in range(3)] == [200] * 3
        assert [(await env.chat()).status_code for _ in range(3)] == [200] * 3
        assert await used(env, day_one_id, "coach") == 3
        assert await used(env, listed_id, "coach") == 0


# ───────────────────────── searches with AI ─────────────────────────


async def test_a_search_is_counted_and_the_one_at_the_limit_is_refused_before_it_starts(
    search_start: Any,
) -> None:
    async with search_start(plans=LOW) as rig:
        await rig.profile()
        rig.script()
        user_id, headers = await rig.account()
        first = await rig.search(headers=headers)
        calls = len(rig.env.claude.calls)
        refused = await rig.search(headers=headers)
        assert first.status_code == 200 and b'"type":"done"' in first.content
        assert_limit_402(refused, "searches")
        assert len(rig.env.claude.calls) == calls
        assert await used(rig.env, user_id, "searches") == 1


async def test_a_search_that_fails_before_any_role_is_given_back(search_start: Any) -> None:
    async with search_start(plans=LOW) as rig:
        await rig.profile()  # nothing scripted: the scouts fail
        user_id, headers = await rig.account()
        failed = await rig.search(headers=headers)
        assert b'"type":"job"' not in failed.content
        assert await used(rig.env, user_id, "searches") == 0


async def test_a_search_with_a_bad_city_is_refused_before_it_is_counted(search_start: Any) -> None:
    async with search_start(plans=LOW) as rig:
        await rig.profile()
        user_id, headers = await rig.account()
        bad = await rig.search({"profileId": SEARCH_PROFILE, "city": "Atlantis"}, headers=headers)
        assert bad.status_code == 400
        assert await used(rig.env, user_id, "searches") == 0


async def test_day_one_the_unlimited_list_and_visitors_are_not_refused_a_search(search_start: Any) -> None:
    async with search_start(plans=LOW, guard=roomy_guard(unlimited_emails=LISTED)) as rig:
        await rig.profile()
        rig.script()
        day_one_id, day_one = await rig.account("dayone@example.com")
        await go_day_one(rig.env, day_one_id)
        listed_id, listed = await rig.account(LISTED)
        assert [(await rig.search(headers=day_one)).status_code for _ in range(2)] == [200, 200]
        assert [(await rig.search(headers=listed)).status_code for _ in range(2)] == [200, 200]
        assert [(await rig.search()).status_code for _ in range(2)] == [200, 200]
        assert await used(rig.env, day_one_id, "searches") == 2
        assert await used(rig.env, listed_id, "searches") == 0


# ───────────────────────── practice interviews ─────────────────────────


async def test_a_practice_interview_is_counted_and_the_one_at_the_limit_is_refused_before_it_starts(
    practice_start: Any,
) -> None:
    async with practice_start(plans=LOW) as rig:
        await rig.profile()
        user_id, headers = await rig.account()
        first = await rig.start(headers=headers)
        refused = await rig.start(headers=headers)
        assert first.status_code == 200
        assert_limit_402(refused, "practice")
        assert rig.stub.names().count("start") == 1  # the service never ran for the refused one
        assert await used(rig.env, user_id, "practice") == 1


async def test_every_kind_of_interview_counts_but_again_and_the_plan_do_not(practice_start: Any) -> None:
    async with practice_start(plans={**LOW, "free_practice": 10}) as rig:
        await rig.profile()
        user_id, headers = await rig.account()
        for kind in ("role", "resume", "stories", "work", "redrill"):
            body: dict[str, Any] = {"profileId": PRACTICE_PROFILE, "kind": kind}
            response = await rig.start(body, headers=headers)
            assert response.status_code == 200, (kind, response.text)
        session_id = (
            await rig.start({"profileId": PRACTICE_PROFILE, "kind": "resume"}, headers=headers)
        ).json()["id"]
        assert await used(rig.env, user_id, "practice") == 6
        base = "/api/interview"
        await rig.client.post(
            f"{base}/plan", json={"profileId": PRACTICE_PROFILE, "kind": "resume"}, headers=headers
        )
        await rig.client.post(
            f"{base}/sessions/{session_id}/again",
            json={"profileId": PRACTICE_PROFILE, "slotId": "s1"},
            headers=headers,
        )
        assert await used(rig.env, user_id, "practice") == 6


async def test_an_interview_the_service_cannot_start_is_given_back(practice_start: Any) -> None:
    async with practice_start(plans=LOW) as rig:
        await rig.profile()
        user_id, headers = await rig.account()
        rig.stub.fail_with = RuntimeError("no plan")
        failed = await rig.start(headers=headers)
        assert failed.status_code >= 400 and failed.status_code != 402
        assert await used(rig.env, user_id, "practice") == 0
        rig.stub.fail_with = None
        assert (await rig.start(headers=headers)).status_code == 200


async def test_day_one_the_unlimited_list_and_visitors_are_not_refused_an_interview(
    practice_start: Any,
) -> None:
    async with practice_start(plans=LOW, guard=roomy_guard(unlimited_emails=LISTED)) as rig:
        await rig.profile()
        day_one_id, day_one = await rig.account("dayone@example.com")
        await go_day_one(rig.env, day_one_id)
        listed_id, listed = await rig.account(LISTED)
        assert [(await rig.start(headers=day_one)).status_code for _ in range(2)] == [200, 200]
        assert [(await rig.start(headers=listed)).status_code for _ in range(2)] == [200, 200]
        assert [(await rig.start()).status_code for _ in range(2)] == [200, 200]
        assert await used(rig.env, day_one_id, "practice") == 2
        assert await used(rig.env, listed_id, "practice") == 0
