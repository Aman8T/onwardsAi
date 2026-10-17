"""The posting match end to end through the real app over a real SQLite file: the stream of a match and what it stores,
who may read it, what it costs and what is given back when it fails, the questions that sharpen it for free, the resume
and its downloads, the cover note, and what deleting an account leaves behind. Only the network edges are faked (Claude
is scripted, the posting page comes from memory), and a request nobody mocked fails the test."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tayyari.core.settings import Settings
from tayyari.ingest import SourceDoc
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.storage.ports import StoredUser
from tayyari.storage.tables import (
    CreditLedgerRow,
    MatchRow,
    PostingRow,
    ProfileFactsRow,
    RateLimitHitRow,
    UserFactRow,
    VariantRow,
)
from tests.fakes.claude import StructuredCall
from tests.integration.support import (
    AsgiPost,
    Env,
    FakeWeb,
    RecordingClaude,
    assert_tasks_drained,
    eventually,
    roomy_guard,
    running,
)
from tests.unit.match.support import Persona, js, meera, script_pipeline
from tests.unit.portfolio._fixtures_a import scripted_facts

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
PID = "meera-nair-fixture"
FIGURE_LINE = "b-exp-1.3"
INVENTED_FIGURE = {
    FIGURE_LINE: {
        "factIds": ["exp-1.3"],
        "text": "Ran 38 A/B tests on landing pages, lifting site conversion by 40%.",
        "verdict": "exact",
    }
}
"""A phrasing that invents a number: the resume asks the person for the real one before it can be downloaded."""
EXPORT_BLOCKED = "Settle the open questions on your resume before you download it."


# ───────────────────────── the rig ─────────────────────────


@dataclass
class Account:
    user: StoredUser
    headers: dict[str, str]
    profile_id: str


@dataclass
class Rig:
    env: Env
    claude: RecordingClaude
    persona: Persona

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def account(self, email: str = "ana@example.com", pid: str = PID) -> Account:
        """A signed-in account that owns a copy of Meera's profile under ``pid``."""
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        profile = {**self.persona.profile.to_dict(), "id": pid}
        await container.repos.profiles.save(
            pid,
            profile,
            self.persona.context,
            goal=self.persona.profile.goal,
            owner_hash=None,
            is_sample=False,
        )
        await container.repos.users.claim_profile(user.id, pid)
        return Account(user, {"cookie": f"{SESSION}={token}"}, pid)

    async def post_match(
        self, who: Account, *, headers: dict[str, str] | None = None, **body: Any
    ) -> httpx.Response:
        payload: dict[str, Any] = {"profileId": who.profile_id, "text": self.persona.posting_text, **body}
        return await self.client.post("/api/match", json=payload, headers={**who.headers, **(headers or {})})

    async def match(self, who: Account, **body: Any) -> tuple[str, list[dict[str, Any]]]:
        """Start a match that must succeed: ``(match id, the events of its stream)``."""
        response = await self.post_match(who, **body)
        assert response.status_code == 200, response.text
        events = lines(response)
        assert events[-1]["type"] == "done", events[-1]
        return str(events[-1]["matchId"]), events

    async def entitlements(self, who: Account) -> dict[str, Any]:
        response = await self.client.get("/api/me", headers=who.headers)
        return response.json()["entitlements"]  # type: ignore[no-any-return]

    def get(self, who: Account, path: str) -> Any:
        return self.client.get(path, headers=who.headers)

    def post(self, who: Account, path: str, **kwargs: Any) -> Any:
        return self.client.post(path, headers=who.headers, **kwargs)

    async def rows(self, table: Any, *where: Any) -> list[Any]:
        return await self.env.rows(table, *where)

    async def ledger(self, who: Account) -> list[tuple[str, int]]:
        rows = await self.rows(CreditLedgerRow, CreditLedgerRow.user_id == who.user.id)
        return sorted((r.reason, r.delta) for r in rows)

    def calls(self, purpose: str) -> int:
        return len(self.claude.calls_for(purpose))  # type: ignore[arg-type]


def lines(response: httpx.Response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.content.split(b"\n") if line.strip()]


def scripted(
    persona: Persona, *, phrase: dict[str, dict[str, Any]] | None = None, **kwargs: Any
) -> RecordingClaude:
    """A model that answers every call of a match for ``persona`` like a careful Haiku."""
    claude = RecordingClaude()
    script_pipeline(claude, persona, phrase=phrase, **kwargs)
    claude.script_structured("match.facts", lambda call: scripted_facts(persona.context), repeat=True)
    claude.script_structured("match.cover", js("meera.cover.answer.json"), repeat=True)
    return claude


def rescript(claude: RecordingClaude, purpose: str, answer: Any) -> None:
    """Replace what the model says for ``purpose`` from now on."""
    claude._queues.structured[purpose] = []
    claude.script_structured(purpose, answer, repeat=True)  # type: ignore[arg-type]


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    """``async with start(**settings) as rig``: the app with Meera's persona and a scripted model behind it.

    Anonymous matching is off unless a test says otherwise (``match={"allow_anonymous": "auto"}``): the tests here are
    about accounts, and with no sign-in method configured the default would let visitors in. `test_match_anonymous`
    covers the other way."""

    @asynccontextmanager
    async def run(
        *,
        claude: RecordingClaude | None = None,
        phrase: dict[str, dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> AsyncIterator[Rig]:
        persona = meera()
        model = claude or scripted(persona, phrase=phrase)
        match = {"allow_anonymous": "false", **overrides.pop("match", {})}
        values: dict[str, Any] = {"auth": {"public_url": "https://tayyari.example"}, "match": match}
        values.update(overrides)
        async with running(build_settings(**values), claude=model) as env:
            yield Rig(env, model, persona)

    return run


# ───────────────────────── who may ask ─────────────────────────

ROUTES = [
    ("POST", "/api/match"),
    ("GET", "/api/matches"),
    ("GET", "/api/match/abc"),
    ("POST", "/api/match/abc/answers"),
    ("POST", "/api/match/abc/resume"),
    ("GET", "/api/match/abc/resume"),
    ("PATCH", "/api/match/abc/resume"),
    ("GET", "/api/match/abc/resume.pdf"),
    ("GET", "/api/match/abc/resume.docx"),
    ("POST", "/api/match/abc/cover"),
    ("GET", "/api/match/abc/cover"),
    ("GET", "/api/match/abc/cover.pdf"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_every_route_needs_a_sign_in(start: Any, method: str, path: str) -> None:
    async with start() as rig:
        response = await rig.client.request(method, path, json={"profileId": PID, "text": "x" * 100})
        assert response.status_code == 401
        assert response.json() == {"error": "Sign in to continue."}
        assert rig.claude.structured_calls == []


@pytest.mark.parametrize(("method", "path"), ROUTES[2:])
async def test_a_match_that_is_not_yours_is_exactly_an_unknown_one(
    start: Any, method: str, path: str
) -> None:
    async with start() as rig:
        ana, bob = await rig.account("ana@example.com"), await rig.account("bob@example.com", "b" * 32)
        match_id, _ = await rig.match(ana)
        body: dict[str, Any] = {
            "answers": [{"questionId": "q-g1", "text": "Yes."}],
            "decisions": [{"bulletId": "b", "choice": "keep"}],
        }

        theirs = await rig.client.request(
            method, path.replace("abc", match_id), json=body, headers=bob.headers
        )
        unknown = await rig.client.request(method, path, json=body, headers=bob.headers)
    assert theirs.status_code == unknown.status_code == 404
    assert theirs.json() == unknown.json()  # the same words: nothing says it exists


async def test_a_profile_that_is_not_yours_is_a_404_that_charges_nothing(start: Any) -> None:
    async with start() as rig:
        ana, bob = await rig.account("ana@example.com"), await rig.account("bob@example.com", "b" * 32)
        elsewhere = await rig.client.post(
            "/api/match",
            json={"profileId": ana.profile_id, "text": rig.persona.posting_text},
            headers=bob.headers,
        )
        unknown = await rig.client.post(
            "/api/match", json={"profileId": "c" * 32, "text": rig.persona.posting_text}, headers=bob.headers
        )
        sample = await rig.client.post(
            "/api/match",
            json={"profileId": rig.env.sample_id, "text": rig.persona.posting_text},
            headers=bob.headers,
        )
        malformed = await rig.client.post(
            "/api/match", json={"profileId": "../etc", "text": rig.persona.posting_text}, headers=bob.headers
        )
        assert [r.status_code for r in (elsewhere, unknown, sample, malformed)] == [404] * 4
        assert (
            elsewhere.json()
            == unknown.json()
            == sample.json()
            == malformed.json()
            == {"error": "Profile not found"}
        )
        assert await rig.ledger(bob) == [] and await rig.ledger(ana) == []
        assert rig.claude.structured_calls == []
        assert (await rig.entitlements(bob))["freeMatchAvailable"] is True


# ───────────────────────── the stream ─────────────────────────


async def test_a_match_streams_its_events_in_order_and_what_is_stored_is_what_was_streamed(
    start: Any,
) -> None:
    async with start() as rig:
        ana = await rig.account()
        response = await rig.post_match(ana)
        events = lines(response)
        match_id = events[-1]["matchId"]
        detail = (await rig.get(ana, f"/api/match/{match_id}")).json()
        rows = await rig.rows(MatchRow)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["cache-control"] == "no-store"
    assert [e["type"] for e in events] == [
        "posting",
        "coverage",
        "coverage",
        "gates",
        "radar",
        "questions",
        "done",
    ]
    posting, gates, radar, questions, done = events[0], events[3], events[4], events[5], events[6]
    coverage = [row for e in events if e["type"] == "coverage" for row in e["coverage"]]

    assert re.fullmatch(r"[0-9a-f]{32}", match_id) and done.keys() == {"type", "matchId", "createdAt"}
    assert posting["posting"]["title"] == "Senior Growth Marketing Manager"
    assert len(posting["posting"]["requirements"]) == 15 and posting["posting"]["rawText"].startswith(
        "Senior Growth"
    )
    assert {f["id"] for f in posting["facts"]} >= {"exp-1", "exp-1.1", "skill-meta-ads"}
    assert sorted(len(e["coverage"]) for e in events if e["type"] == "coverage") == [
        5,
        10,
    ]  # batches finish in any order
    assert [g["verdict"] for g in gates["gates"]] == ["uncertain", "uncertain", "pass"] and gates[
        "blocked"
    ] is False
    assert len(radar["radar"]["axes"]) >= 3
    assert [q["id"] for q in questions["questions"]] == ["q-g1", "q-g2", "q-r7"]

    # GET gives back what the stream said, and the facts it served are the ones the rows cite
    assert detail["id"] == match_id and detail["createdAt"] == done["createdAt"]
    assert detail["posting"] == posting["posting"]
    assert detail["facts"] == posting["facts"]
    assert detail["result"]["coverage"] == sorted(coverage, key=lambda c: int(c["requirementId"][1:]))
    assert detail["result"]["gates"] == gates["gates"] and detail["result"]["radar"] == radar["radar"]
    assert detail["result"]["questions"] == questions["questions"]
    assert (detail["result"]["band"], detail["result"]["confidence"], detail["result"]["version"]) == (
        "good",
        "low",
        1,
    )
    cited = {fid for row in detail["result"]["coverage"] for fid in row["factIds"]}
    assert cited <= {f["id"] for f in detail["facts"]}

    [row] = rows
    assert (row.id, row.band, row.blocked, row.version, row.profile_id) == (match_id, "good", False, 1, PID)
    assert row.result["band"] == "good" and len(row.facts["facts"]) == len(detail["facts"])


async def test_a_posting_is_read_once_and_a_profiles_facts_are_extracted_once(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        await rig.env.container.entitlements.grant(ana.user.id, "pack3", "order-1")
        first, _ = await rig.match(ana)
        reads, extractions = rig.calls("match.posting"), rig.calls("match.facts")
        second, _ = await rig.match(ana, text="\n\n" + rig.persona.posting_text.replace("\n", "\n\n"))
        postings, cached = await rig.rows(PostingRow), await rig.rows(ProfileFactsRow)
        listing = (await rig.get(ana, "/api/matches")).json()["matches"]

    assert first != second
    assert (reads, extractions) == (1, 2)  # one read of the posting; one reader call per source with text
    assert (
        rig.calls("match.posting") == 1 and rig.calls("match.facts") == 2
    )  # the second match asked for neither
    assert len(postings) == 1 and len(cached) == 1
    assert [m["id"] for m in listing] == [second, first]


async def test_matches_are_listed_newest_first_and_only_for_their_owner(start: Any) -> None:
    async with start() as rig:
        ana, bob = await rig.account("ana@example.com"), await rig.account("bob@example.com", "b" * 32)
        await rig.env.container.entitlements.grant(ana.user.id, "pack3", "order-1")
        mine = [(await rig.match(ana))[0] for _ in range(2)]
        theirs, _ = await rig.match(bob)
        ana_list = await rig.get(ana, "/api/matches")
        bob_list = await rig.get(bob, "/api/matches")

    assert ana_list.status_code == 200 and ana_list.headers["cache-control"] == "no-store"
    items = ana_list.json()["matches"]
    assert [m["id"] for m in items] == list(reversed(mine))
    assert items[0].keys() == {"id", "title", "company", "band", "createdAt"}
    assert (items[0]["title"], items[0]["company"], items[0]["band"]) == (
        "Senior Growth Marketing Manager",
        "Lumen Retail",
        "good",
    )
    assert [m["id"] for m in bob_list.json()["matches"]] == [theirs]


# ───────────────────────── the posting's text ─────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/jobs/view/123456",
        "linkedin.com/jobs/view/1",
        "https://lnkd.in/abc",
        "https://www.naukri.com/job-listings-growth-manager-123",
        "https://recruit.naukri.com/x",
        "https://in.indeed.com/viewjob?jk=1",
    ],
)
async def test_a_link_on_a_board_that_forbids_reading_is_refused_before_anything_is_fetched(
    start: Any, web: FakeWeb, url: str
) -> None:
    async with start() as rig:
        ana = await rig.account()
        response = await rig.client.post(
            "/api/match", json={"profileId": PID, "url": url}, headers=ana.headers
        )
        assert response.status_code == 422
        sentence = response.json()["error"]
        assert "paste it here instead" in sentence and "automated reading" in sentence
        assert web.web_calls == [] and rig.claude.structured_calls == []
        assert await rig.ledger(ana) == []  # nothing was charged


async def test_a_public_link_is_read_through_the_guarded_ingest_and_matched(start: Any, web: FakeWeb) -> None:
    url = "https://careers.lumenretail.example/jobs/sgmm-2291"
    async with start() as rig:
        web.page(url, rig.persona.posting_text, title="Senior Growth Marketing Manager")
        ana = await rig.account()
        response = await rig.client.post(
            "/api/match", json={"profileId": PID, "url": url}, headers=ana.headers
        )
        events = lines(response)
        [posting] = await rig.rows(PostingRow)

    assert response.status_code == 200 and events[-1]["type"] == "done"
    assert web.web_calls == [url]
    assert posting.text == rig.persona.posting_text.strip()


async def test_a_link_that_cannot_be_read_asks_for_the_text_and_costs_nothing(
    start: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreadable(url: str, kind: str, label: str, kind_label: str, log: Any) -> SourceDoc:
        return SourceDoc.failure(
            kind, kind, label, kind_label, url, url, "That page was not found (HTTP 404)."
        )

    monkeypatch.setattr("tayyari.ingest.ingest_web", unreadable)
    async with start() as rig:
        ana = await rig.account()
        gone = await rig.client.post(
            "/api/match", json={"profileId": PID, "url": "https://jobs.example.com/gone"}, headers=ana.headers
        )
        junk = await rig.client.post(
            "/api/match", json={"profileId": PID, "url": "not a link at all"}, headers=ana.headers
        )
        assert await rig.ledger(ana) == []
    assert gone.status_code == 422
    assert gone.json() == {
        "error": "That page was not found (HTTP 404). You can paste the text of the posting instead."
    }
    assert junk.status_code == 400 and "web link" in junk.json()["error"]


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ({"text": "   "}, "Paste the job posting"),
        ({"text": None}, "Paste the job posting"),
        ({"text": "hello"}, "too short"),
        ({"text": "We need a marketer. " * 2_000}, "longer than 20,000 characters"),
    ],
)
async def test_text_that_is_not_a_posting_is_a_400_and_costs_nothing(
    start: Any, body: dict[str, Any], words: str
) -> None:
    async with start() as rig:
        ana = await rig.account()
        response = await rig.post_match(ana, **body)
        assert response.status_code == 400
        assert words in response.json()["error"]
        assert await rig.ledger(ana) == [] and rig.claude.structured_calls == []
        assert (await rig.entitlements(ana))["freeMatchAvailable"] is True


async def test_the_text_limit_is_a_setting(start: Any) -> None:
    async with start(match={"max_posting_chars": 500}) as rig:
        ana = await rig.account()
        response = await rig.post_match(ana)
    assert response.status_code == 400 and "longer than 500 characters" in response.json()["error"]


async def test_a_request_that_is_not_json_or_does_not_say_which_profile_is_refused_early(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        wrong_type = await rig.client.post("/api/match", content=b"profileId=1", headers=ana.headers)
        broken = await rig.client.post(
            "/api/match", content=b"{not json", headers={**ana.headers, "content-type": "application/json"}
        )
        no_profile = await rig.client.post("/api/match", json={"text": "x" * 200}, headers=ana.headers)
    assert (wrong_type.status_code, broken.status_code, no_profile.status_code) == (415, 400, 400)


# ───────────────────────── what it costs ─────────────────────────


async def test_the_first_match_is_free_then_a_402_until_something_is_bought_and_a_402_costs_nothing(
    start: Any,
) -> None:
    async with start() as rig:
        ana = await rig.account()
        await rig.match(ana)
        assert (await rig.entitlements(ana)) == {"credits": 0, "freeMatchAvailable": False}
        calls_before = len(rig.claude.structured_calls)

        refused = await rig.post_match(ana)
        assert refused.status_code == 402
        assert refused.json() == {"error": "payment_required", "reason": "no_credits"}
        assert len(rig.claude.structured_calls) == calls_before  # no model was asked
        assert len(await rig.rows(MatchRow)) == 1

        await rig.env.container.entitlements.grant(ana.user.id, "pack3", "order-1")
        second, _ = await rig.match(ana)
        ledger = await rig.ledger(ana)

        assert (await rig.entitlements(ana))["credits"] == 2
        assert ledger == [("free_monthly", 0), ("match", -1), ("purchase", 3)]
        refs = {r.ref for r in await rig.rows(CreditLedgerRow) if r.reason == "match"}
        assert refs == {second}  # the charge names the match it paid for


async def test_a_pass_pays_for_any_number_of_matches(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        await rig.env.container.entitlements.grant(ana.user.id, "pass30", "order-2")
        for _ in range(3):
            await rig.match(ana)
        ledger = await rig.ledger(ana)
    assert [reason for reason, _ in ledger].count("match") == 3 and all(
        d == 0 for r, d in ledger if r == "match"
    )


async def test_a_posting_with_no_requirements_is_given_back_and_says_why(start: Any) -> None:
    persona = meera()
    empty = dict(persona.posting_answer, requirements=[])
    async with start(claude=scripted(persona, posting=empty)) as rig:
        ana = await rig.account()
        response = await rig.post_match(ana)
        events = lines(response)
        after = await rig.entitlements(ana)
        ledger = await rig.ledger(ana)
        stored = await rig.rows(MatchRow)

    assert response.status_code == 200 and [e["type"] for e in events] == ["error"]
    assert events[0]["code"] == "no_requirements" and "requirements" in events[0]["message"]
    assert after["freeMatchAvailable"] is True  # the free posting is the person's again
    assert ledger == [("free_monthly", 0), ("refund", 0)]
    assert stored == []


async def test_a_model_that_is_down_gives_the_charge_back_and_a_retry_works(start: Any) -> None:
    claude = scripted(meera())
    rescript(claude, "match.posting", ClaudeOverloaded())
    async with start(claude=claude) as rig:
        ana = await rig.account()
        failed = lines(await rig.post_match(ana))
        assert failed[-1]["type"] == "error" and failed[-1]["code"] == "failed" and failed[-1]["message"]
        assert (await rig.entitlements(ana))["freeMatchAvailable"] is True and await rig.rows(MatchRow) == []

        rescript(claude, "match.posting", rig.persona.posting_answer)
        again = lines(await rig.post_match(ana))
    assert again[-1]["type"] == "done"


async def test_a_credit_is_returned_as_well_as_a_free_posting(start: Any) -> None:
    persona = meera()
    empty = dict(persona.posting_answer, requirements=[])
    async with start(claude=scripted(persona, posting=empty)) as rig:
        ana = await rig.account()
        container = rig.env.container
        await container.entitlements.grant(ana.user.id, "pack3", "order-1")
        await container.repos.billing.consume(
            ana.user.id,
            "earlier",
            now=container.clock.now(),
            month_start=container.clock.now().replace(day=1),
            free_allowance=1,
        )  # the month's free posting is used up
        await rig.post_match(ana)
        assert (await rig.entitlements(ana))["credits"] == 3


async def test_a_failure_before_the_analysis_even_starts_still_gives_the_charge_back(
    start: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(profile_id: str) -> list[Any]:
        raise RuntimeError("the database went away")

    async with start() as rig:
        ana = await rig.account()
        monkeypatch.setattr(rig.env.container.repos.user_facts, "list_for_profile", boom)
        events = lines(await rig.post_match(ana))
        assert [e["type"] for e in events] == ["error"] and events[0]["code"] == "failed"
        assert "database" not in json.dumps(events)  # the sentence, never the exception text
        assert (await rig.entitlements(ana))["freeMatchAvailable"] is True
        assert [reason for reason, _ in await rig.ledger(ana)] == ["free_monthly", "refund"]
        assert rig.claude.structured_calls == []


async def test_a_client_that_hangs_up_mid_match_gets_its_posting_back(start: Any) -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def never_answers(call: StructuredCall) -> dict[str, Any]:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    claude = scripted(meera())
    rescript(claude, "match.coverage", never_answers)
    async with start(claude=claude) as rig:
        ana = await rig.account()
        before = set(asyncio.all_tasks())
        post = AsgiPost(
            "/api/match",
            json.dumps({"profileId": PID, "text": rig.persona.posting_text}).encode(),
            "application/json",
            headers=[("cookie", ana.headers["cookie"])],
        )
        request = post.run(rig.env.app)
        await asyncio.wait_for(started.wait(), 10)
        await eventually(lambda: len(post.lines) >= 1, what="the first event")
        assert json.loads(post.lines[0])["type"] == "posting"
        assert (await rig.entitlements(ana))["freeMatchAvailable"] is False  # in use while the match runs

        post.disconnect()
        await asyncio.wait_for(request, 10)
        await asyncio.wait_for(cancelled.wait(), 5)
        await eventually(lambda: True, what="the app to settle")
        await assert_tasks_drained(before)

        assert (await rig.entitlements(ana))["freeMatchAvailable"] is True
        assert await rig.rows(MatchRow) == []
        assert [reason for reason, _ in await rig.ledger(ana)] == ["free_monthly", "refund"]


async def test_the_daily_spend_ceiling_stops_a_match_before_it_is_charged(start: Any) -> None:
    async with start(guard=roomy_guard(daily_ceiling_usd=0.01)) as rig:
        ana = await rig.account()
        response = await rig.post_match(ana)
        assert response.status_code == 503
        assert await rig.ledger(ana) == [] and rig.claude.structured_calls == []


# ───────────────────────── limits ─────────────────────────


async def test_a_match_is_limited_per_account_and_per_address_with_a_retry_after(start: Any) -> None:
    async with start(guard=roomy_guard(match_per_hour=2, match_per_day=0)) as rig:
        ana, bob = await rig.account("ana@example.com"), await rig.account("bob@example.com", "b" * 32)
        await rig.env.container.entitlements.grant(ana.user.id, "pass30", "order-1")
        await rig.env.container.entitlements.grant(bob.user.id, "pass30", "order-2")
        home = {"cf-connecting-ip": "203.0.113.5"}
        cafe = {"cf-connecting-ip": "203.0.113.99"}

        first = await rig.post_match(ana, headers=home)
        second = await rig.post_match(ana, headers=home)
        third = await rig.post_match(ana, headers=home)
        other_address = await rig.post_match(ana, headers=cafe)  # the account is limited wherever it is
        other_account = await rig.post_match(bob, headers=home)  # the address is limited whoever uses it
        fresh = await rig.post_match(bob, headers=cafe)  # a different account at a different address is fine

    assert [first.status_code, second.status_code] == [200, 200]
    for refused in (third, other_address, other_account):
        assert refused.status_code == 429, refused.text
        assert int(refused.headers["retry-after"]) >= 1
        assert "hourly limit" in refused.json()["error"]
    assert fresh.status_code == 200
    assert len(await rig.rows(MatchRow)) == 3  # a refused request made no match


async def test_the_whole_service_has_a_cap_too(start: Any) -> None:
    async with start(guard=roomy_guard(match_global_per_hour=1)) as rig:
        ana, bob = await rig.account("ana@example.com"), await rig.account("bob@example.com", "b" * 32)
        first = await rig.post_match(ana, headers={"cf-connecting-ip": "203.0.113.1"})
        second = await rig.post_match(bob, headers={"cf-connecting-ip": "203.0.113.2"})
    assert first.status_code == 200 and second.status_code == 429
    assert "busy" in second.json()["error"]


# ───────────────────────── an account on the unlimited list ─────────────────────────

LISTED = "tester@example.com"
OTHER = "other@example.com"
UNLIMITED_LIST = f" {LISTED.upper()} , second.listed@example.org"
"""Written the way an owner might: padding, capitals, a second address."""


def listed_guard(**overrides: Any) -> Any:
    return roomy_guard(unlimited_emails=UNLIMITED_LIST, **overrides)


async def test_a_listed_account_is_past_the_free_match_with_no_402_and_nothing_is_charged(start: Any) -> None:
    async with start(guard=listed_guard()) as rig:
        tester = await rig.account(LISTED)
        bob = await rig.account(OTHER, "b" * 32)
        ids = [
            (await rig.match(tester))[0] for _ in range(3)
        ]  # the free match was never used, and there are no credits
        assert len(set(ids)) == 3 and len(await rig.rows(MatchRow, MatchRow.user_id == tester.user.id)) == 3
        assert await rig.ledger(tester) == []  # no free_monthly, no match, no refund: the ledger is untouched
        assert await rig.entitlements(tester) == {"credits": 0, "freeMatchAvailable": True, "unlimited": True}

        # the same setup for an ordinary account: the free match once, then a 402 and nothing else
        await rig.match(bob)
        refused = await rig.post_match(bob)
        assert refused.status_code == 402
        assert refused.json() == {"error": "payment_required", "reason": "no_credits"}
        assert await rig.entitlements(bob) == {"credits": 0, "freeMatchAvailable": False}
        assert await rig.ledger(tester) == []

        # what the listed account made is its own: only it can open it
        opened = await rig.get(tester, f"/api/match/{ids[0]}")
        stranger = await rig.get(bob, f"/api/match/{ids[0]}")
    assert opened.status_code == 200 and stranger.status_code == 404


async def test_a_listed_account_is_not_counted_against_any_match_limit(start: Any) -> None:
    limits = {"match_per_minute": 1, "match_per_hour": 1, "match_per_day": 1, "match_global_per_hour": 1}
    async with start(guard=listed_guard(**limits)) as rig:
        tester = await rig.account(LISTED)
        bob = await rig.account(OTHER, "b" * 32)
        await rig.env.container.entitlements.grant(bob.user.id, "pass30", "order-1")
        home = {"cf-connecting-ip": "203.0.113.5"}
        for _ in range(4):  # not per account, not per address, not for the whole service
            assert (await rig.post_match(tester, headers=home)).status_code == 200
        counted = [h for h in await rig.rows(RateLimitHitRow) if h.bucket.startswith("match")]
        assert counted == []

        # the whole service had nothing counted, so Bob's first goes through, from the very same address; his second does not
        assert (await rig.post_match(bob, headers=home)).status_code == 200
        refused = await rig.post_match(bob, headers=home)
        assert refused.status_code == 429 and int(refused.headers["retry-after"]) >= 1


async def test_the_other_match_routes_never_shed_a_listed_account(start: Any) -> None:
    async with start(guard=listed_guard(chat_per_minute=1)) as rig:
        tester = await rig.account(LISTED)
        bob = await rig.account(OTHER, "b" * 32)
        match_id, _ = await rig.match(tester)
        bob_match_id, _ = await rig.match(bob)
        address = {"cf-connecting-ip": "203.0.113.77"}
        mine = [
            (
                await rig.client.get(f"/api/match/{match_id}", headers={**tester.headers, **address})
            ).status_code
            for _ in range(10)
        ]
        theirs = [
            (
                await rig.client.get(f"/api/match/{bob_match_id}", headers={**bob.headers, **address})
            ).status_code
            for _ in range(6)
        ]
    assert mine == [200] * 10
    assert theirs[:2] == [200, 200] and 429 in theirs  # the listed account never used up the address's share


async def test_a_failed_match_of_a_listed_account_has_nothing_to_give_back(start: Any) -> None:
    claude = scripted(meera())
    rescript(claude, "match.posting", ClaudeOverloaded())
    async with start(claude=claude, guard=listed_guard()) as rig:
        tester = await rig.account(LISTED)
        failed = lines(await rig.post_match(tester))
        assert failed[-1]["type"] == "error" and failed[-1]["code"] == "failed"
        assert await rig.ledger(tester) == []  # no charge, so no refund row either
        assert await rig.rows(MatchRow) == []
        assert (await rig.entitlements(tester))["freeMatchAvailable"] is True


async def test_a_listed_account_still_meets_the_spend_ceiling(start: Any) -> None:
    async with start(guard=listed_guard(daily_ceiling_usd=0.01)) as rig:
        tester = await rig.account(LISTED)
        response = await rig.post_match(tester)
        assert response.status_code == 503
        assert await rig.ledger(tester) == [] and rig.claude.structured_calls == []


async def test_an_empty_list_leaves_a_matching_email_ordinary(start: Any) -> None:
    async with start(guard=roomy_guard(match_per_hour=1, match_per_day=0)) as rig:
        tester = await rig.account(LISTED)
        await rig.env.container.entitlements.grant(tester.user.id, "pass30", "order-1")
        assert (await rig.post_match(tester)).status_code == 200
        assert (await rig.post_match(tester)).status_code == 429
        assert "unlimited" not in await rig.entitlements(tester)


# ───────────────────────── questions ─────────────────────────


def citing_the_users_words(rows_key: str, verdict: str) -> Callable[[StructuredCall], dict[str, Any]]:
    """A model that, asked again after the person answered, points at the facts in their own words."""

    def respond(call: StructuredCall) -> dict[str, Any]:
        user_ids = re.findall(r"\b(user-[0-9a-f]{8})\b", call.user_text)
        assert user_ids, "the prompt should now include the person's own words"
        if rows_key == "answers":
            ids = call.schema["properties"]["answers"]["items"]["properties"]["gateId"]["enum"]
            return {
                "answers": [
                    {"gateId": g, "factIds": [user_ids[0]], "verdict": verdict, "note": "In their words."}
                    for g in ids
                ]
            }
        ids = call.schema["properties"]["rows"]["items"]["properties"]["requirementId"]["enum"]
        return {
            "rows": [
                {
                    "requirementId": r,
                    "factIds": [user_ids[-1]],
                    "status": "covered",
                    "level": 4,
                    "note": "In their words.",
                    "interviewRisk": "",
                    "mitigation": "",
                }
                for r in ids
            ]
        }

    return respond


async def test_answers_become_facts_in_their_own_words_recheck_only_what_they_touch_and_never_cost_anything(
    start: Any,
) -> None:
    async with start() as rig:
        ana = await rig.account()
        match_id, _ = await rig.match(ana)
        ledger, entitlements = await rig.ledger(ana), await rig.entitlements(ana)
        before = (await rig.get(ana, f"/api/match/{match_id}")).json()
        rescript(rig.claude, "match.gates", citing_the_users_words("answers", "pass"))
        rescript(rig.claude, "match.coverage", citing_the_users_words("rows", "covered"))
        calls = len(rig.claude.structured_calls)

        answered = await rig.post(
            ana,
            f"/api/match/{match_id}/answers",
            json={
                "answers": [
                    {"questionId": "q-g1", "text": "  I am authorised to work in India.  "},
                    {"questionId": "q-g2", "text": "I can be in the Mumbai office three days a week."},
                    {"questionId": "q-r7", "text": "I ran a CDP migration at my last agency."},
                ]
            },
        )
        after = (await rig.get(ana, f"/api/match/{match_id}")).json()
        new_calls = rig.claude.structured_calls[calls:]
        stored = await rig.rows(UserFactRow)
        row = (await rig.rows(MatchRow))[0]
        after_ledger, after_entitlements = await rig.ledger(ana), await rig.entitlements(ana)

    assert answered.status_code == 200 and answered.headers["cache-control"] == "no-store"
    assert answered.json() == after  # the answer has the same shape (and content) as GET
    assert after["result"]["version"] == before["result"]["version"] + 1 == 2 and row.version == 2
    assert [g["verdict"] for g in after["result"]["gates"]] == ["pass", "pass", "pass"]
    assert next(c for c in after["result"]["coverage"] if c["requirementId"] == "r7")["status"] == "covered"
    asked = {q["id"] for q in after["result"]["questions"]}
    assert not asked & {"q-g1", "q-g2", "q-r7"}  # what was answered is no longer asked
    assert "q-r6" in asked  # the next most important unknown takes their place

    # exactly the rows the answers touch were asked again: one gates call, one coverage call for r7
    assert sorted(c.ctx.purpose for c in new_calls) == ["match.coverage", "match.gates"]
    assert {
        r
        for c in new_calls
        if c.ctx.purpose == "match.coverage"
        for r in re.findall(r"^(r\d+) \|", c.user_text, re.M)
    } == {"r7"}

    # the answers are facts "from you", kept on the profile with the question they answered
    texts = {f["text"] for f in after["facts"] if f["refs"][0]["source"] == "user"}
    assert texts == {
        "I am authorised to work in India.",
        "I can be in the Mumbai office three days a week.",
        "I ran a CDP migration at my last agency.",
    }
    assert {r.question for r in stored} >= {q["text"] for q in before["result"]["questions"]}
    assert {r.answer for r in stored} == texts and {r.match_id for r in stored} == {match_id}
    assert len(after["facts"]) == len(before["facts"]) + 3

    # a re-check is free
    assert after_ledger == ledger and after_entitlements == entitlements


async def test_answers_to_questions_that_are_not_open_or_not_words_are_refused(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        match_id, _ = await rig.match(ana)
        calls = len(rig.claude.structured_calls)
        path = f"/api/match/{match_id}/answers"
        unknown = await rig.post(ana, path, json={"answers": [{"questionId": "q-zzz", "text": "Yes."}]})
        blank = await rig.post(ana, path, json={"answers": [{"questionId": "q-g1", "text": "   "}]})
        long = await rig.post(ana, path, json={"answers": [{"questionId": "q-g1", "text": "x" * 1501}]})
        many = await rig.post(ana, path, json={"answers": [{"questionId": "q-g1", "text": "a"}] * 4})
        empty = await rig.post(ana, path, json={"answers": []})
    assert [r.status_code for r in (unknown, blank, long, many, empty)] == [400] * 5
    assert "not open" in unknown.json()["error"] and "not open" in blank.json()["error"]
    assert len(rig.claude.structured_calls) == calls and await rig.rows(UserFactRow) == []


# ───────────────────────── the resume ─────────────────────────


async def test_the_resume_is_built_once_stored_and_read_back(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        match_id, _ = await rig.match(ana)
        path = f"/api/match/{match_id}/resume"
        assert (await rig.get(ana, path)).status_code == 404
        assert (await rig.get(ana, path)).json() == {"error": "No resume has been built for this match yet."}
        ledger = await rig.ledger(ana)

        built = await rig.post(ana, path, json={})
        events = lines(built)
        stored = (await rig.get(ana, path)).json()
        calls = len(rig.claude.structured_calls)
        again = lines(await rig.post(ana, path, json={}))
        [variant_row] = await rig.rows(VariantRow)

    assert built.status_code == 200 and built.headers["content-type"].startswith("application/x-ndjson")
    assert [e["type"] for e in events] == ["variant", "done"] and events[1] == {"type": "done"}
    assert events[0]["variant"] == stored["variant"]
    assert stored["variant"]["header"]["name"] == "Meera Nair"
    assert stored["variant"]["summary"]["factIds"] and stored["variant"]["experience"]
    assert again == events  # asking again hands back the stored resume: no model call, nothing rebuilt
    assert len(rig.claude.structured_calls) == calls
    assert (variant_row.kind, variant_row.version, variant_row.status) == ("resume", 1, "ready")
    assert await rig.ledger(ana) == ledger  # a resume never costs a posting
    assert stored["variant"]["lint"]["items"], "nothing waits for the person, so its checks are already there"


async def test_a_resume_with_an_open_question_cannot_be_downloaded_until_the_person_answers(
    start: Any,
) -> None:
    async with start(phrase=INVENTED_FIGURE) as rig:
        ana = await rig.account()
        match_id, _ = await rig.match(ana)
        path = f"/api/match/{match_id}/resume"
        built = lines(await rig.post(ana, path, json={}))[0]["variant"]
        decision = next(d for d in built["decisions"] if d["bulletId"] == FIGURE_LINE)
        line = next(b for e in built["experience"] for b in e["bullets"] if b["id"] == FIGURE_LINE)

        assert decision["kind"] == "number" and decision["options"] == ["fill", "soften", "drop"]
        assert line["status"] == "needs_user" and "[x]%" in line["text"] and "40" not in line["text"]
        assert built.get("lint") is None  # no checklist for text that is about to change

        # the files are refused while a line waits for the person ...
        for extension in ("pdf", "docx"):
            blocked = await rig.get(ana, f"{path}.{extension}")
            assert blocked.status_code == 409 and blocked.json() == {"error": EXPORT_BLOCKED}

        # ... a figure that is not one is refused, and nothing changes ...
        refused = await rig.client.patch(
            path,
            json={"decisions": [{"bulletId": FIGURE_LINE, "choice": "fill", "value": "a lot"}]},
            headers=ana.headers,
        )
        assert refused.status_code == 422 and refused.json() == {"error": "That is not a figure we can use."}
        assert (await rig.get(ana, path)).json()["variant"]["decisions"] == built["decisions"]
        assert await rig.rows(UserFactRow) == []

        # ... the real figure settles it
        settled = await rig.client.patch(
            path,
            json={"decisions": [{"bulletId": FIGURE_LINE, "choice": "fill", "value": "6.4%"}]},
            headers=ana.headers,
        )
        assert settled.status_code == 200
        variant = settled.json()["variant"]
        fixed = next(b for e in variant["experience"] for b in e["bullets"] if b["id"] == FIGURE_LINE)
        assert variant["decisions"] == [] and fixed["status"] == "verified" and "by 6.4%" in fixed["text"]
        assert any(f.startswith("user-") for f in fixed["factIds"])
        assert variant["lint"]["items"] and all(i["ok"] for i in variant["lint"]["items"])

        pdf = await rig.get(ana, f"{path}.pdf")
        docx = await rig.get(ana, f"{path}.docx")
        stored = (await rig.get(ana, path)).json()["variant"]
        facts = (await rig.get(ana, f"/api/match/{match_id}")).json()["facts"]
        users = await rig.rows(UserFactRow)
        [variant_row] = await rig.rows(VariantRow, VariantRow.kind == "resume")

    assert pdf.status_code == docx.status_code == 200
    assert pdf.content.startswith(b"%PDF") and docx.content.startswith(b"PK")
    assert pdf.headers["content-type"] == "application/pdf"
    assert docx.headers["content-type"].endswith("wordprocessingml.document")
    for response, extension in ((pdf, "pdf"), (docx, "docx")):
        assert (
            response.headers["content-disposition"] == f'attachment; filename="meera-nair-resume.{extension}"'
        )
        assert (
            response.headers["x-robots-tag"] == "noindex" and response.headers["cache-control"] == "no-store"
        )
    assert stored["lint"] == variant["lint"]  # the checks of the exported files are on the stored resume
    assert [f["text"] for f in facts if f["refs"][0]["source"] == "user"] == [
        "Figure given by the person: 6.4%"
    ]
    assert [(u.answer, u.match_id) for u in users] == [("6.4%", match_id)]
    assert (variant_row.status, variant_row.version) == (
        "ready",
        2,
    )  # built, then settled; the export found its checks already there


async def test_a_decision_that_does_not_fit_applies_nothing_and_says_so(start: Any) -> None:
    async with start(phrase=INVENTED_FIGURE) as rig:
        ana = await rig.account()
        match_id, _ = await rig.match(ana)
        path = f"/api/match/{match_id}/resume"
        await rig.post(ana, path, json={})
        before = (await rig.get(ana, path)).json()
        mixed = await rig.client.patch(
            path,
            json={
                "decisions": [
                    {"bulletId": FIGURE_LINE, "choice": "drop"},  # fits
                    {"bulletId": "b-zzz", "choice": "keep"},  # does not
                ]
            },
            headers=ana.headers,
        )
        empty = await rig.client.patch(path, json={"decisions": []}, headers=ana.headers)
        no_resume = await rig.client.patch(
            f"/api/match/{match_id[::-1]}/resume",
            json={"decisions": [{"bulletId": "b", "choice": "keep"}]},
            headers=ana.headers,
        )
        after = (await rig.get(ana, path)).json()
    assert mixed.status_code == 422 and mixed.json() == {"error": "That answer does not fit this line."}
    assert empty.status_code == 400 and no_resume.status_code == 404
    assert after == before  # not even the line that fitted was applied


async def test_dropping_a_line_settles_it_too(start: Any) -> None:
    async with start(phrase=INVENTED_FIGURE) as rig:
        ana = await rig.account()
        match_id, _ = await rig.match(ana)
        path = f"/api/match/{match_id}/resume"
        await rig.post(ana, path, json={})
        dropped = await rig.client.patch(
            path, json={"decisions": [{"bulletId": FIGURE_LINE, "choice": "drop"}]}, headers=ana.headers
        )
        file = await rig.get(ana, f"{path}.pdf")
    line = next(
        b for e in dropped.json()["variant"]["experience"] for b in e["bullets"] if b["id"] == FIGURE_LINE
    )
    assert dropped.status_code == 200 and line["status"] == "dropped"
    assert file.status_code == 200


async def test_a_resume_cannot_be_downloaded_before_it_is_built(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        match_id, _ = await rig.match(ana)
        responses = [await rig.get(ana, f"/api/match/{match_id}/resume.{ext}") for ext in ("pdf", "docx")]
    assert [r.status_code for r in responses] == [404, 404]


# ───────────────────────── the cover note ─────────────────────────


async def test_the_cover_note_is_written_once_stored_checked_and_downloadable(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        match_id, _ = await rig.match(ana)
        path = f"/api/match/{match_id}/cover"
        assert (await rig.get(ana, path)).status_code == 404
        assert (await rig.get(ana, f"{path}.pdf")).status_code == 404
        ledger = await rig.ledger(ana)

        written = await rig.post(ana, path, json={})
        stored = await rig.get(ana, path)
        calls = len(rig.claude.structured_calls)
        again = await rig.post(ana, path, json={})
        pdf = await rig.get(ana, f"{path}.pdf")
        [row] = await rig.rows(VariantRow, VariantRow.kind == "cover")

    assert written.status_code == 200 and written.headers["cache-control"] == "no-store"
    cover = written.json()["cover"]
    assert cover["paragraphs"] and 250 <= cover["wordCount"] <= 400
    assert cover["lint"]["items"] and all(item["ok"] for item in cover["lint"]["items"])
    assert stored.json() == written.json() == again.json()
    assert rig.calls("match.cover") == 1 and len(rig.claude.structured_calls) == calls  # written once
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-disposition"] == 'attachment; filename="meera-nair-cover-note.pdf"'
    assert pdf.headers["x-robots-tag"] == "noindex"
    assert (row.status, row.version) == ("ready", 1)
    assert await rig.ledger(ana) == ledger


async def test_a_cover_note_nothing_supports_is_not_written(start: Any) -> None:
    claude = scripted(meera())

    def nothing_covered(call: StructuredCall) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "requirementId": rid,
                    "factIds": [],
                    "status": "missing_gap",
                    "level": 0,
                    "note": "",
                    "interviewRisk": "",
                    "mitigation": "",
                }
                for rid in re.findall(r"^(r\d+) \|", call.user_text, re.M)
            ]
        }

    rescript(claude, "match.coverage", nothing_covered)
    async with start(claude=claude) as rig:
        ana = await rig.account()
        match_id, _ = await rig.match(ana)
        response = await rig.post(ana, f"/api/match/{match_id}/cover", json={})
        stored = await rig.rows(VariantRow)
    assert response.status_code == 422
    assert response.json() == {
        "error": "There is not enough supported evidence yet to write a cover note for this posting."
    }
    assert stored == []


# ───────────────────────── deleting the account ─────────────────────────


async def test_deleting_the_account_removes_its_matches_resumes_cover_notes_and_words(start: Any) -> None:
    async with start(phrase=INVENTED_FIGURE) as rig:
        ana, bob = await rig.account("ana@example.com"), await rig.account("bob@example.com", "b" * 32)
        mine, _ = await rig.match(ana)
        theirs, _ = await rig.match(bob)
        for who, match_id in ((ana, mine), (bob, theirs)):
            await rig.post(who, f"/api/match/{match_id}/resume", json={})
            await rig.post(who, f"/api/match/{match_id}/cover", json={})
            await rig.post(
                who,
                f"/api/match/{match_id}/answers",
                json={"answers": [{"questionId": "q-g1", "text": "Yes, I am."}]},
            )
        assert len(await rig.rows(MatchRow)) == 2 and len(await rig.rows(VariantRow)) == 4
        assert len(await rig.rows(UserFactRow)) == 2 and len(await rig.rows(ProfileFactsRow)) == 2

        gone = await rig.client.delete("/api/me", headers=ana.headers)
        left_matches = await rig.rows(MatchRow)
        left_variants = await rig.rows(VariantRow)
        left_words = await rig.rows(UserFactRow)
        left_cache = await rig.rows(ProfileFactsRow)
        postings = await rig.rows(PostingRow)
        old_cookie = await rig.get(ana, "/api/matches")
        bobs = await rig.get(bob, f"/api/match/{theirs}")

    assert gone.status_code == 204
    assert [m.id for m in left_matches] == [theirs]
    assert {v.match_id for v in left_variants} == {theirs} and len(left_variants) == 2
    assert [u.user_id for u in left_words] == [bob.user.id]
    assert [c.profile_id for c in left_cache] == ["b" * 32]
    assert len(postings) == 1  # the posting is shared and stays
    assert old_cookie.status_code == 401
    assert bobs.status_code == 200 and bobs.json()["id"] == theirs
