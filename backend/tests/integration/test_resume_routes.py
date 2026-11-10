"""The resume routes end to end through the real app over a real SQLite file: who may open a profile's resumes, the
uploaded resume made on the first call, a path resume made once, a draft edited, Finalise with a line nobody can vouch
for but the person, the default moving between resumes, and the files only a finalised resume has. Only the network
edges are faked (Claude is scripted), and a request nobody mocked fails the test."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tayyari.core.settings import Settings
from tayyari.storage.ports import StoredUser
from tayyari.storage.tables import UserFactRow
from tests.integration.support import RecordingClaude, running
from tests.unit.match.support import Persona, meera, script_pipeline
from tests.unit.portfolio._fixtures_a import scripted_facts

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
PID = "meera-nair-fixture"
ROLE = "senior-growth"
FIGURE_LINE = "b-exp-1.3"
INVENTED_FIGURE = "Ran 38 A/B tests on landing pages, lifting conversion by 40%"
"""A typed line whose 40% is in no fact: Finalise must stop on it and ask the person."""


@dataclass
class Account:
    user: StoredUser
    headers: dict[str, str]
    profile_id: str


@dataclass
class Rig:
    env: Any
    claude: RecordingClaude
    persona: Persona

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client  # type: ignore[no-any-return]

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

    def get(self, who: Account, path: str) -> Any:
        return self.client.get(path, headers=who.headers)

    def post(self, who: Account, path: str, **kwargs: Any) -> Any:
        return self.client.post(path, headers=who.headers, **kwargs)

    def patch(self, who: Account, path: str, **kwargs: Any) -> Any:
        return self.client.patch(path, headers=who.headers, **kwargs)

    async def listing(self, who: Account) -> dict[str, Any]:
        response = await self.get(who, f"/api/resumes?profile={who.profile_id}")
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    async def upload(self, who: Account) -> dict[str, Any]:
        """The uploaded resume's summary (made by the first listing)."""
        listing = await self.listing(who)
        return next(r for r in listing["resumes"] if r["kind"] == "upload")

    def calls(self, purpose: str) -> int:
        return len(self.claude.calls_for(purpose))  # type: ignore[arg-type]


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    """``async with start() as rig``: the app with Meera's persona and a scripted model behind it."""

    @asynccontextmanager
    async def run(**overrides: Any) -> AsyncIterator[Rig]:
        persona = meera()
        claude = RecordingClaude()
        script_pipeline(claude, persona)
        claude.script_structured("match.facts", lambda call: scripted_facts(persona.context), repeat=True)
        match = {"allow_anonymous": "false", **overrides.pop("match", {})}
        values: dict[str, Any] = {
            "auth": {"public_url": "https://tayyari.example"},
            "match": match,
            **overrides,
        }
        async with running(build_settings(**values), claude=claude) as env:
            yield Rig(env, claude, persona)

    return run


def lines(detail: dict[str, Any]) -> list[dict[str, Any]]:
    variant = detail["variant"]
    return [b for entry in [*variant["experience"], *variant["projects"]] for b in entry["bullets"]]


# ───────────────────────── who may ask ─────────────────────────

ROUTES = [
    ("GET", f"/api/resumes?profile={PID}"),
    ("GET", f"/api/resumes/blocks?profile={PID}"),
    ("POST", f"/api/resumes/path?profile={PID}"),
    ("GET", "/api/resumes/abc"),
    ("PATCH", "/api/resumes/abc"),
    ("POST", "/api/resumes/abc/finalise"),
    ("POST", "/api/resumes/abc/default"),
    ("GET", "/api/resumes/abc/resume.pdf"),
    ("GET", "/api/resumes/abc/resume.docx"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_every_route_needs_a_sign_in(start: Any, method: str, path: str) -> None:
    async with start() as rig:
        response = await rig.client.request(method, path, json={})
        assert response.status_code == 401, response.text


async def test_another_accounts_profile_and_resume_are_exactly_unknown_ones(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        ben = await rig.account("ben@example.com", pid="ben-profile")
        mine = await rig.upload(ana)

        listing = await rig.get(ben, f"/api/resumes?profile={ana.profile_id}")
        assert listing.status_code == 404
        for method, path in [
            ("GET", f"/api/resumes/{mine['id']}"),
            ("POST", f"/api/resumes/{mine['id']}/default"),
            ("GET", f"/api/resumes/{mine['id']}/resume.pdf"),
        ]:
            response = await rig.client.request(method, path, headers=ben.headers)
            assert response.status_code == 404, (path, response.text)
        unknown = await rig.get(ana, "/api/resumes/0123456789abcdef0123456789abcdef")
        assert unknown.status_code == 404


async def test_a_listing_without_a_profile_says_which_is_missing(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        response = await rig.get(ana, "/api/resumes")
        assert response.status_code == 400
        assert "profile" in response.json()["error"]


# ───────────────────────── the uploaded resume ─────────────────────────


async def test_the_first_listing_makes_the_uploaded_resume_final_and_default_and_the_next_reuses_it(
    start: Any,
) -> None:
    async with start() as rig:
        ana = await rig.account()
        first = await rig.listing(ana)
        assert [(r["kind"], r["state"], r["isDefault"]) for r in first["resumes"]] == [
            ("upload", "final", True)
        ]
        assert [p["roleId"] for p in first["paths"]] == [ROLE]
        assert first["blocks"] > 0
        facts_calls = rig.calls("match.facts")

        again = await rig.listing(ana)
        assert again["resumes"] == first["resumes"]
        assert rig.calls("match.facts") == facts_calls  # the fact book is read once

        detail = (await rig.get(ana, f"/api/resumes/{first['resumes'][0]['id']}")).json()
        assert detail["canDownload"] is True
        assert detail["marks"] == []
        assert all(line["status"] == "verified" for line in lines(detail))


async def test_the_blocks_list_every_block_and_how_many_resumes_use_it(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        await rig.listing(ana)
        response = await rig.get(ana, f"/api/resumes/blocks?profile={ana.profile_id}")
        assert response.status_code == 200, response.text
        blocks = response.json()
        assert blocks["resumes"] == 1
        assert blocks["entries"] and blocks["blocks"]
        assert {b["usedIn"] for b in blocks["blocks"]} <= {0, 1}


# ───────────────────────── a resume for a next role ─────────────────────────


async def test_a_path_resume_is_made_once_with_one_relevance_call(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        await rig.listing(ana)
        before = rig.calls("match.relevance")

        made = await rig.post(ana, f"/api/resumes/path?profile={ana.profile_id}", json={"roleId": ROLE})
        assert made.status_code == 200, made.text
        detail = made.json()
        assert detail["resume"]["kind"] == "path" and detail["resume"]["roleId"] == ROLE
        assert detail["resume"]["state"] == "draft" and detail["canDownload"] is False
        assert rig.calls("match.relevance") == before + 1

        again = await rig.post(ana, f"/api/resumes/path?profile={ana.profile_id}", json={"roleId": ROLE})
        assert again.json()["resume"]["id"] == detail["resume"]["id"]
        assert rig.calls("match.relevance") == before + 1

        listing = await rig.listing(ana)
        assert listing["paths"][0]["resumeId"] == detail["resume"]["id"]


async def test_a_role_the_graph_does_not_have_is_a_404(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        response = await rig.post(
            ana, f"/api/resumes/path?profile={ana.profile_id}", json={"roleId": "astronaut"}
        )
        assert response.status_code == 404


# ───────────────────────── a draft, Finalise and the files ─────────────────────────


async def test_a_draft_is_never_downloaded_and_a_typed_figure_waits_for_the_person(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        upload = await rig.upload(ana)
        rid = upload["id"]

        edited = await rig.patch(
            ana,
            f"/api/resumes/{rid}",
            json={"ops": [{"op": "edit", "bulletId": FIGURE_LINE, "text": INVENTED_FIGURE}]},
        )
        assert edited.status_code == 200, edited.text
        detail = edited.json()
        assert detail["resume"]["state"] == "draft"
        assert {"bulletId": FIGURE_LINE, "kind": "edited"} in detail["marks"]

        # the finalised version from before the edit is still the one that downloads
        pdf = await rig.get(ana, f"/api/resumes/{rid}/resume.pdf")
        assert pdf.status_code == 200 and pdf.headers["content-type"].startswith("application/pdf")

        checked = await rig.post(ana, f"/api/resumes/{rid}/finalise", json={})
        assert checked.status_code == 200, checked.text
        report = checked.json()["report"]
        assert report["done"] is False
        assert [(f["bulletId"], f["kind"], f["what"]) for f in report["findings"]] == [
            (FIGURE_LINE, "number", "40%")
        ]

        settled = await rig.post(
            ana,
            f"/api/resumes/{rid}/finalise",
            json={"answers": [{"bulletId": FIGURE_LINE, "choice": "mine"}]},
        )
        assert settled.status_code == 200, settled.text
        body = settled.json()
        assert body["report"]["done"] is True and body["report"]["lint"]["items"]
        assert body["detail"]["resume"]["state"] == "final"
        said = await rig.env.rows(UserFactRow, UserFactRow.profile_id == ana.profile_id)
        assert [row.answer for row in said] == [INVENTED_FIGURE]


async def test_an_operation_that_does_not_fit_changes_nothing(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        rid = (await rig.upload(ana))["id"]
        response = await rig.patch(
            ana,
            f"/api/resumes/{rid}",
            json={
                "ops": [
                    {"op": "leave_out", "bulletId": FIGURE_LINE},
                    {"op": "move", "bulletId": "nope", "dir": "up"},
                ]
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "op_refused"
        detail = (await rig.get(ana, f"/api/resumes/{rid}")).json()
        assert FIGURE_LINE in [line["id"] for line in lines(detail)]


async def test_a_path_draft_has_no_files_until_it_is_finalised_and_then_the_default_moves_to_it(
    start: Any,
) -> None:
    async with start() as rig:
        ana = await rig.account()
        upload = await rig.upload(ana)
        path = (
            await rig.post(ana, f"/api/resumes/path?profile={ana.profile_id}", json={"roleId": ROLE})
        ).json()["resume"]

        for suffix in ("resume.pdf", "resume.docx"):
            response = await rig.get(ana, f"/api/resumes/{path['id']}/{suffix}")
            assert response.status_code == 409 and response.json()["code"] == "not_finalised"
        early = await rig.post(ana, f"/api/resumes/{path['id']}/default")
        assert early.status_code == 409 and early.json()["code"] == "not_finalised"

        done = await rig.post(ana, f"/api/resumes/{path['id']}/finalise", json={"makeDefault": True})
        assert done.status_code == 200, done.text
        assert done.json()["report"]["done"] is True
        assert done.json()["detail"]["resume"]["isDefault"] is True

        listing = await rig.listing(ana)
        defaults = {r["id"]: r["isDefault"] for r in listing["resumes"]}
        assert defaults == {upload["id"]: False, path["id"]: True}

        docx = await rig.get(ana, f"/api/resumes/{path['id']}/resume.docx")
        assert docx.status_code == 200 and "attachment" in docx.headers["content-disposition"]

        back = await rig.post(ana, f"/api/resumes/{upload['id']}/default")
        assert back.status_code == 200 and back.json()["resume"]["isDefault"] is True
        listing = await rig.listing(ana)
        assert {r["id"]: r["isDefault"] for r in listing["resumes"]} == {
            upload["id"]: True,
            path["id"]: False,
        }
