"""The posting match without an account, through the real app over a real SQLite file: a visitor with no sign-in matches a
profile nobody has claimed (or the sample), opens what they made by its id, answers, builds a resume and a cover note,
and downloads them, while no entitlement is taken, given back or even asked about. What stops them: a claimed profile
is a 404, every per-address limit applies, and three matches a day per address. Claiming the profile gives what they
made to the account. Signed-in people behave as they did, whether anonymous matching is on or off.

The rig is the one of `test_match_routes` (Meera's persona, a scripted model), with anonymous matching switched on."""

# ruff: noqa: F401, F811  (``start`` is that module's fixture: imported so pytest finds it, then taken by name)

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import update

from tayyari.llm.errors import ClaudeOverloaded
from tayyari.storage.tables import (
    CreditLedgerRow,
    EntitlementRow,
    MatchRow,
    ProfileRow,
    RateLimitHitRow,
    UserFactRow,
    VariantRow,
)
from tests.integration.support import roomy_guard
from tests.integration.test_match_routes import (
    SESSION,
    Account,
    Rig,
    citing_the_users_words,
    lines,
    rescript,
    start,
)

OPEN_PID = "e5" * 16
OTHER_OPEN_PID = "d4" * 16
ON = {"allow_anonymous": "true"}
UNKNOWN = "f" * 32
EVENTS = ["posting", "coverage", "coverage", "gates", "radar", "questions", "done"]
HOME = {"cf-connecting-ip": "203.0.113.5"}
CAFE = {"cf-connecting-ip": "203.0.113.99"}
BODY = {
    "answers": [{"questionId": "q-g1", "text": "Yes."}],
    "decisions": [{"bulletId": "b", "choice": "keep"}],
}


# ───────────────────────── the rig ─────────────────────────


class Watched:
    """The entitlement service with a record of every question put to it."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[tuple[str, str]] = []

    async def consume(self, user_id: str, ref: str) -> Any:
        self.calls.append(("consume", user_id))
        return await self.inner.consume(user_id, ref)

    async def refund(self, user_id: str, ref: str) -> bool:
        self.calls.append(("refund", user_id))
        return bool(await self.inner.refund(user_id, ref))

    async def grant(self, user_id: str, product: Any, order_id: str) -> bool:
        return bool(await self.inner.grant(user_id, product, order_id))

    async def summary(self, user_id: str) -> Any:
        self.calls.append(("summary", user_id))
        return await self.inner.summary(user_id)


def watch(rig: Rig) -> Watched:
    watched = Watched(rig.env.container.entitlements)
    rig.env.container.entitlements = watched  # type: ignore[assignment]
    return watched


async def open_profile(rig: Rig, pid: str = OPEN_PID, *, sample: bool = False) -> str:
    """A stored copy of Meera's profile that nobody has claimed (``sample``: a sample run, which nobody can claim)."""
    container = rig.env.container
    await container.repos.profiles.save(
        pid,
        {**rig.persona.profile.to_dict(), "id": pid},
        rig.persona.context,
        goal=rig.persona.profile.goal,
        owner_hash=None,
        is_sample=sample,
    )
    return pid


async def sign_in(rig: Rig, email: str = "ana@example.com") -> Account:
    """A signed-in account that owns nothing yet."""
    container = rig.env.container
    user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
    token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
    return Account(user, {"cookie": f"{SESSION}={token}"}, "")


def post_match(rig: Rig, pid: str, *, headers: dict[str, str] | None = None, **body: Any) -> Any:
    payload: dict[str, Any] = {"profileId": pid, "text": rig.persona.posting_text, **body}
    return rig.client.post("/api/match", json=payload, headers=headers or {})


async def match(
    rig: Rig, pid: str, *, headers: dict[str, str] | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """A match with no sign-in that must succeed: ``(match id, the events of its stream)``."""
    response = await post_match(rig, pid, headers=headers)
    assert response.status_code == 200, response.text
    events = lines(response)
    assert events[-1]["type"] == "done", events[-1]
    return str(events[-1]["matchId"]), events


async def untouched(rig: Rig, watched: Watched) -> None:
    """No entitlement was asked for, taken or given back, and no row of the ledger or the balance exists."""
    assert [c for c in watched.calls if c[0] != "summary"] == []
    assert await rig.rows(CreditLedgerRow) == [] and await rig.rows(EntitlementRow) == []


# ───────────────────────── a visitor with no sign-in matches ─────────────────────────


@pytest.mark.parametrize("mode", ["auto", "true"])
async def test_a_visitor_with_no_sign_in_matches_a_profile_nobody_has_claimed_and_nothing_is_charged(
    start: Any, mode: str
) -> None:
    async with start(
        match={"allow_anonymous": mode}
    ) as rig:  # no sign-in method here, so auto lets visitors in
        watched = watch(rig)
        pid = await open_profile(rig)
        response = await post_match(rig, pid)
        events = lines(response)
        match_id = events[-1]["matchId"]
        detail = await rig.client.get(f"/api/match/{match_id}")
        rows = await rig.rows(MatchRow)
        await untouched(rig, watched)

    assert response.status_code == 200 and response.headers["content-type"].startswith("application/x-ndjson")
    assert [e["type"] for e in events] == EVENTS  # the stream is the one the signed-in get
    assert events[-1].keys() == {"type", "matchId", "createdAt"}
    assert detail.status_code == 200 and detail.headers["cache-control"] == "no-store"
    assert detail.json()["id"] == match_id and detail.json()["posting"] == events[0]["posting"]
    [row] = rows
    assert (row.id, row.user_id, row.profile_id, row.band) == (match_id, None, pid, "good")


async def test_the_sample_can_be_matched_without_a_sign_in_and_what_is_said_there_is_not_shared(
    start: Any,
) -> None:
    async with start(match=ON) as rig:
        sample = rig.env.sample_id
        await open_profile(
            rig, sample, sample=True
        )  # Meera's content under the sample's id, flagged as a sample
        first, _ = await match(rig, sample)
        second_run, _ = await match(rig, sample)
        rescript(rig.claude, "match.gates", citing_the_users_words("answers", "pass"))
        rescript(rig.claude, "match.coverage", citing_the_users_words("rows", "covered"))
        answered = await rig.client.post(
            f"/api/match/{first}/answers",
            json={"answers": [{"questionId": "q-g1", "text": "I am authorised to work in India."}]},
        )
        mine = (await rig.client.get(f"/api/match/{first}")).json()
        # another visitor's match works on, and would merge in what was said about the profile if it were kept
        built = await rig.client.post(f"/api/match/{second_run}/resume", json={})
        theirs = (await rig.client.get(f"/api/match/{second_run}")).json()
        stored = await rig.rows(UserFactRow)

    said = "I am authorised to work in India."
    assert answered.status_code == 200 and built.status_code == 200
    assert said in {f["text"] for f in mine["facts"] if f["refs"][0]["source"] == "user"}
    # everyone who holds the sample's id holds this profile: nothing said on one match reaches another
    assert not [f for f in theirs["facts"] if f["refs"][0]["source"] == "user"]
    assert stored == []  # so nothing is kept for the profile, and a later match has nothing of theirs to read


async def test_a_sample_run_with_its_own_id_is_open_to_a_visitor_too(start: Any) -> None:
    async with start(match=ON) as rig:
        pid = await open_profile(rig, "sample-" + "ab" * 16, sample=True)
        match_id, _ = await match(rig, pid)
        [row] = await rig.rows(MatchRow)
    assert (row.id, row.user_id) == (match_id, None)


async def test_what_a_visitor_says_about_their_own_unclaimed_profile_is_kept_for_its_next_match(
    start: Any,
) -> None:
    async with start(match=ON) as rig:
        pid = await open_profile(rig)
        first, _ = await match(rig, pid)
        rescript(rig.claude, "match.gates", citing_the_users_words("answers", "pass"))
        rescript(rig.claude, "match.coverage", citing_the_users_words("rows", "covered"))
        answered = await rig.client.post(
            f"/api/match/{first}/answers",
            json={"answers": [{"questionId": "q-g1", "text": "I am authorised to work in India."}]},
        )
        _, second = await match(rig, pid)
        stored = await rig.rows(UserFactRow)

    assert answered.status_code == 200
    assert [(r.user_id, r.profile_id, r.match_id) for r in stored] == [(None, pid, first)]
    assert [f["text"] for f in second[0]["facts"] if f["refs"][0]["source"] == "user"] == [
        "I am authorised to work in India."
    ]


async def test_a_claimed_profile_is_the_404_of_an_unknown_one_and_nothing_runs(start: Any) -> None:
    async with start(match=ON) as rig:
        watched = watch(rig)
        ana = await rig.account()
        elsewhere = await post_match(rig, ana.profile_id)
        unknown = await post_match(rig, UNKNOWN)
        malformed = await post_match(rig, "../etc")
        seen = [r.status_code for r in (elsewhere, unknown, malformed)]
        bodies = [r.json() for r in (elsewhere, unknown, malformed)]
        await untouched(rig, watched)
        assert rig.claude.structured_calls == [] and await rig.rows(MatchRow) == []
        counted = await rig.rows(RateLimitHitRow)
        assert not [r for r in counted if r.bucket.startswith("match_anon")]  # a refusal costs no allowance

    assert seen == [404, 404, 404]
    assert bodies == [{"error": "Profile not found"}] * 3


async def test_a_visitor_with_a_cookie_that_names_nobody_is_just_a_visitor(start: Any) -> None:
    async with start(match=ON) as rig:
        pid = await open_profile(rig)
        match_id, _ = await match(rig, pid, headers={"cookie": f"{SESSION}=not-a-session"})
        got = await rig.client.get(f"/api/match/{match_id}", headers={"cookie": f"{SESSION}={'x' * 40}"})
    assert got.status_code == 200


async def test_a_posting_that_cannot_be_matched_uses_none_of_the_visitors_allowance(start: Any) -> None:
    async with start(match=ON) as rig:
        pid = await open_profile(rig)
        refused = [await post_match(rig, pid, text="hello", headers=HOME) for _ in range(4)]
        for _ in range(3):
            await match(rig, pid, headers=HOME)
    assert [r.status_code for r in refused] == [400] * 4


async def test_a_model_that_is_down_ends_the_stream_with_an_error_and_there_is_nothing_to_give_back(
    start: Any,
) -> None:
    async with start(match=ON) as rig:
        watched = watch(rig)
        pid = await open_profile(rig)
        rescript(rig.claude, "match.posting", ClaudeOverloaded())
        failed = lines(await post_match(rig, pid))
        assert failed[-1]["type"] == "error" and failed[-1]["code"] == "failed" and failed[-1]["message"]
        assert await rig.rows(MatchRow) == []
        await untouched(rig, watched)

        rescript(rig.claude, "match.posting", rig.persona.posting_answer)
        again = lines(await post_match(rig, pid))
        await untouched(rig, watched)
    assert again[-1]["type"] == "done"


# ───────────────────────── a match made without an account, opened by its id ─────────────────────────


async def test_the_whole_journey_works_by_the_matchs_id_alone_and_costs_no_entitlement(start: Any) -> None:
    async with start(match=ON) as rig:
        watched = watch(rig)
        pid = await open_profile(rig)
        match_id, _ = await match(rig, pid)
        base = f"/api/match/{match_id}"
        rescript(rig.claude, "match.gates", citing_the_users_words("answers", "pass"))
        rescript(rig.claude, "match.coverage", citing_the_users_words("rows", "covered"))

        answered = await rig.client.post(
            f"{base}/answers", json={"answers": [{"questionId": "q-g1", "text": "Yes, I am authorised."}]}
        )
        resume = await rig.client.post(f"{base}/resume", json={})
        stored_resume = await rig.client.get(f"{base}/resume")
        pdf = await rig.client.get(f"{base}/resume.pdf")
        docx = await rig.client.get(f"{base}/resume.docx")
        cover = await rig.client.post(f"{base}/cover", json={})
        stored_cover = await rig.client.get(f"{base}/cover")
        cover_pdf = await rig.client.get(f"{base}/cover.pdf")
        decided = await rig.client.patch(
            f"{base}/resume", json={"decisions": [{"bulletId": "b-zzz", "choice": "keep"}]}
        )
        listing = await rig.client.get("/api/matches")
        [row] = await rig.rows(MatchRow)
        facts = await rig.rows(UserFactRow)
        variants = await rig.rows(VariantRow)
        await untouched(rig, watched)

    assert answered.status_code == 200 and answered.json()["result"]["version"] == 2
    assert [e["type"] for e in lines(resume)] == ["variant", "done"]
    assert stored_resume.status_code == 200 and stored_cover.status_code == 200
    assert pdf.status_code == docx.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert cover.status_code == 200 and cover.json() == stored_cover.json()
    assert cover_pdf.status_code == 200 and cover_pdf.headers["x-robots-tag"] == "noindex"
    assert decided.status_code == 422  # the route is reached, and judges the decision as it always does
    assert (row.user_id, row.version) == (None, 2)
    assert [(f.user_id, f.match_id) for f in facts] == [(None, match_id)]
    assert sorted(v.kind for v in variants) == ["cover", "resume"]
    # there is no identity to list by: the web app keeps its own list of the ids it was given
    assert listing.status_code == 200 and listing.json() == {"matches": []}
    assert listing.headers["cache-control"] == "no-store"


async def test_an_id_that_names_no_match_is_the_404_of_a_made_up_one(start: Any) -> None:
    async with start(match=ON) as rig:
        pid = await open_profile(rig)
        match_id, _ = await match(rig, pid)
        reached = [
            await rig.client.request(method, path.replace("abc", UNKNOWN), json=BODY)
            for method, path in [
                ("GET", "/api/match/abc"),
                ("POST", "/api/match/abc/answers"),
                ("POST", "/api/match/abc/resume"),
                ("GET", "/api/match/abc/cover.pdf"),
            ]
        ]
        unreadable = await rig.client.get("/api/match/not an id")
        real = await rig.client.get(f"/api/match/{match_id}")
    assert [r.status_code for r in reached] == [404] * 4 and unreadable.status_code == 404
    assert real.status_code == 200
    assert {json.dumps(r.json()) for r in [*reached[:1], unreadable]} == {'{"error": "Match not found"}'}


async def test_a_signed_in_persons_match_is_not_found_by_a_visitor_with_no_sign_in(start: Any) -> None:
    async with start(match=ON) as rig:
        ana = await rig.account()
        mine, _ = await rig.match(ana)
        paths = [f"/api/match/{mine}", f"/api/match/{mine}/resume", f"/api/match/{mine}/cover.pdf"]
        visitor = [await rig.client.get(p) for p in paths]
        unknown = await rig.client.get(f"/api/match/{UNKNOWN}")
    assert [r.status_code for r in visitor] == [404] * 3
    assert visitor[0].json() == unknown.json()  # the same words: nothing says it exists


async def test_turning_anonymous_matching_off_closes_every_route_again(start: Any) -> None:
    async with start(match=ON) as rig:
        pid = await open_profile(rig)
        match_id, _ = await match(rig, pid)
        rig.env.container.match_anonymous = False  # what a restart with ALLOW_ANONYMOUS=false does
        base = f"/api/match/{match_id}"
        closed = [
            await rig.client.request(method, path, json={"profileId": pid, "text": "x" * 100})
            for method, path in [
                ("POST", "/api/match"),
                ("GET", "/api/matches"),
                ("GET", base),
                ("POST", f"{base}/answers"),
                ("POST", f"{base}/resume"),
                ("GET", f"{base}/resume.pdf"),
                ("POST", f"{base}/cover"),
                ("GET", f"{base}/cover.pdf"),
            ]
        ]
    assert [r.status_code for r in closed] == [401] * 8
    assert closed[0].json() == {"error": "Sign in to continue."}


# ───────────────────────── the limits ─────────────────────────


async def test_a_visitor_gets_three_matches_a_day_per_address_and_the_fourth_is_a_429(start: Any) -> None:
    async with start(match=ON) as rig:
        watched = watch(rig)
        pid = await open_profile(rig)
        for _ in range(3):
            await match(rig, pid, headers=HOME)
        calls = len(rig.claude.structured_calls)
        fourth = await post_match(rig, pid, headers=HOME)
        asked_for_the_fourth = len(rig.claude.structured_calls) - calls
        elsewhere, _ = await match(rig, pid, headers=CAFE)  # another address has its own three
        mine = (await rig.rows(MatchRow))[0].id
        # nothing but starting a match is counted: the match already made can still be worked on
        rescript(rig.claude, "match.gates", citing_the_users_words("answers", "pass"))
        rescript(rig.claude, "match.coverage", citing_the_users_words("rows", "covered"))
        answered = await rig.client.post(
            f"/api/match/{mine}/answers",
            json={"answers": [{"questionId": "q-g1", "text": "Yes."}]},
            headers=HOME,
        )
        resume = await rig.client.post(f"/api/match/{mine}/resume", json={}, headers=HOME)
        cover = await rig.client.post(f"/api/match/{mine}/cover", json={}, headers=HOME)
        download = await rig.client.get(f"/api/match/{mine}/resume.pdf", headers=HOME)
        matches = await rig.rows(MatchRow)
        await untouched(rig, watched)

    assert fourth.status_code == 429
    assert int(fourth.headers["retry-after"]) >= 1
    assert "today's limit for postings without an account" in fourth.json()["error"]
    assert asked_for_the_fourth == 0  # no model was asked for the refused request
    assert len(matches) == 4  # three from home and one from the cafe: the refused fourth made nothing
    assert elsewhere in {m.id for m in matches}
    assert [answered.status_code, resume.status_code, cover.status_code, download.status_code] == [200] * 4


async def test_the_visitors_daily_allowance_is_a_setting(start: Any) -> None:
    async with start(match=ON, guard=roomy_guard(anon_match_per_day=1)) as rig:
        pid = await open_profile(rig)
        await match(rig, pid, headers=HOME)
        second = await post_match(rig, pid, headers=HOME)
    assert second.status_code == 429


async def test_a_daily_allowance_of_zero_for_visitors_is_not_enforced(start: Any) -> None:
    async with start(
        match=ON, guard=roomy_guard(anon_match_per_day=0, match_per_hour=0, match_per_day=0)
    ) as rig:
        pid = await open_profile(rig)
        for _ in range(5):
            await match(rig, pid, headers=HOME)
        assert len(await rig.rows(MatchRow)) == 5


async def test_the_ordinary_per_address_limits_apply_to_visitors_too(start: Any) -> None:
    async with start(match=ON, guard=roomy_guard(match_per_hour=2)) as rig:
        pid = await open_profile(rig)
        first = await post_match(rig, pid, headers=HOME)
        second = await post_match(rig, pid, headers=HOME)
        third = await post_match(rig, pid, headers=HOME)
    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 429]
    assert "hourly limit" in third.json()["error"]


async def test_the_whole_service_cap_applies_to_visitors_too(start: Any) -> None:
    async with start(match=ON, guard=roomy_guard(match_global_per_hour=1)) as rig:
        pid = await open_profile(rig)
        one = await post_match(rig, pid, headers=HOME)
        other = await post_match(rig, pid, headers=CAFE)
    assert one.status_code == 200 and other.status_code == 429 and "busy" in other.json()["error"]


async def test_the_spend_ceiling_stops_a_visitors_match_before_it_starts(start: Any) -> None:
    async with start(match=ON, guard=roomy_guard(daily_ceiling_usd=0.01)) as rig:
        watched = watch(rig)
        pid = await open_profile(rig)
        response = await post_match(rig, pid)
        await untouched(rig, watched)
        assert response.status_code == 503 and rig.claude.structured_calls == []
        assert await rig.rows(MatchRow) == []


# ───────────────────────── claiming gives it to the account ─────────────────────────


async def test_claiming_the_profile_gives_the_account_its_matches_and_answers_and_closes_them_to_everyone_else(
    start: Any,
) -> None:
    async with start(match=ON) as rig:
        pid, other_pid = await open_profile(rig), await open_profile(rig, OTHER_OPEN_PID)
        mine, _ = await match(rig, pid)
        theirs, _ = await match(rig, other_pid)
        rescript(rig.claude, "match.gates", citing_the_users_words("answers", "pass"))
        rescript(rig.claude, "match.coverage", citing_the_users_words("rows", "covered"))
        await rig.client.post(
            f"/api/match/{mine}/answers", json={"answers": [{"questionId": "q-g1", "text": "Yes."}]}
        )
        await rig.client.post(f"/api/match/{mine}/resume", json={})
        ana = await sign_in(rig)
        before = await rig.client.get("/api/matches", headers=ana.headers)

        claimed = await rig.client.post("/api/me/claim", json={"profileId": pid}, headers=ana.headers)
        listing = await rig.client.get("/api/matches", headers=ana.headers)
        as_owner = await rig.client.get(f"/api/match/{mine}", headers=ana.headers)
        resume = await rig.client.get(f"/api/match/{mine}/resume", headers=ana.headers)
        as_visitor = await rig.client.get(f"/api/match/{mine}")
        new_visitor_match = await post_match(rig, pid)
        other_visitor = await rig.client.get(f"/api/match/{theirs}")
        rows = {m.id: m.user_id for m in await rig.rows(MatchRow)}
        owners = [f.user_id for f in await rig.rows(UserFactRow)]

    assert before.json() == {"matches": []}
    assert claimed.status_code == 200
    assert [m["id"] for m in listing.json()["matches"]] == [mine]
    assert as_owner.status_code == 200 and resume.status_code == 200
    assert as_visitor.status_code == 404  # it is the account's now, and no longer anyone's who holds the id
    assert new_visitor_match.status_code == 404  # and so is the profile
    assert other_visitor.status_code == 200  # a different profile's match is as it was
    assert rows == {mine: ana.user.id, theirs: None}
    assert owners == [ana.user.id]


async def test_a_match_that_slipped_past_the_claim_is_served_to_nobody_and_goes_with_the_account(
    start: Any,
) -> None:
    async with start(match=ON) as rig:
        pid = await open_profile(rig)
        stray, _ = await match(rig, pid)
        ana = await sign_in(rig)
        async with (
            rig.env.container.db.session() as s
        ):  # the profile is claimed while the match is still running
            await s.execute(update(ProfileRow).where(ProfileRow.id == pid).values(user_id=ana.user.id))
        visitor = await rig.client.get(f"/api/match/{stray}")
        owner = await rig.client.get(f"/api/match/{stray}", headers=ana.headers)
        gone = await rig.client.delete("/api/me", headers=ana.headers)
        left = await rig.rows(MatchRow)

    assert visitor.status_code == owner.status_code == 404
    assert gone.status_code == 204 and left == []


# ───────────────────────── signed-in people are unchanged ─────────────────────────


async def test_a_signed_in_person_pays_for_a_match_and_may_use_only_their_own_profile_even_when_visitors_may_match(
    start: Any,
) -> None:
    async with start(match=ON) as rig:
        watched = watch(rig)
        ana = await rig.account()
        pid = await open_profile(rig)
        first, _ = await rig.match(ana)
        ledger = await rig.ledger(ana)
        refused = await rig.post_match(ana)  # the free posting of the month is used
        on_an_open_profile = await rig.client.post(
            "/api/match", json={"profileId": pid, "text": rig.persona.posting_text}, headers=ana.headers
        )
        listing = await rig.get(ana, "/api/matches")
        bob = await rig.account("bob@example.com", "b" * 32)
        bobs_view = await rig.get(bob, f"/api/match/{first}")

    assert ledger == [("free_monthly", 0)]
    assert [c for c in watched.calls if c[0] == "consume"] == [("consume", ana.user.id)] * 2
    assert refused.status_code == 402 and refused.json() == {
        "error": "payment_required",
        "reason": "no_credits",
    }
    assert on_an_open_profile.status_code == 404  # an account matches its own profiles, as before
    assert [m["id"] for m in listing.json()["matches"]] == [first]
    assert bobs_view.status_code == 404


async def test_the_visitors_daily_allowance_does_not_touch_a_signed_in_person(start: Any) -> None:
    async with start(match=ON) as rig:
        ana = await rig.account()
        await rig.env.container.entitlements.grant(ana.user.id, "pass30", "order-1")
        for _ in range(5):
            await rig.match(ana)  # more than the three a visitor gets, from one address
        counted = await rig.rows(RateLimitHitRow)
    assert not [r for r in counted if r.bucket.startswith("match_anon")]


async def test_a_signed_in_person_who_holds_the_id_of_a_visitors_match_may_open_and_answer_it(
    start: Any,
) -> None:
    async with start(match=ON) as rig:
        pid = await open_profile(rig)
        made, _ = await match(rig, pid)
        ana = await sign_in(rig)
        watched = watch(rig)
        rescript(rig.claude, "match.gates", citing_the_users_words("answers", "pass"))
        rescript(rig.claude, "match.coverage", citing_the_users_words("rows", "covered"))
        seen = await rig.client.get(f"/api/match/{made}", headers=ana.headers)
        answered = await rig.client.post(
            f"/api/match/{made}/answers",
            json={"answers": [{"questionId": "q-g1", "text": "Yes."}]},
            headers=ana.headers,
        )
        [row] = await rig.rows(MatchRow)
        facts = await rig.rows(UserFactRow)
        await untouched(rig, watched)
    assert seen.status_code == answered.status_code == 200
    assert row.user_id is None and [f.user_id for f in facts] == [
        None
    ]  # it is still nobody's until the claim


async def test_with_anonymous_matching_off_a_visitor_gets_the_401_it_always_got(start: Any) -> None:
    async with start(match={"allow_anonymous": "false"}) as rig:
        pid = await open_profile(rig)
        response = await post_match(rig, pid)
        listing = await rig.client.get("/api/matches")
        assert await rig.rows(MatchRow) == [] and rig.claude.structured_calls == []
    assert response.status_code == listing.status_code == 401
    assert response.json() == {"error": "Sign in to continue."}
