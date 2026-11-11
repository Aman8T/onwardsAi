"""`POST /api/analyze` with `fromProfile` through the real app over a real SQLite file: who may start it (another account's
profile is an unknown one, 404; no resume text at all is a 409 before any cost), the inputs built from the profile's files,
own-words lines and links, and the person's resumes, files and lines hanging off the new profile afterwards. Only the network
edges are faked (Claude, the web)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from tayyari.contract.stored import StoredContext, StoredSource
from tayyari.core.settings import Settings
from tayyari.match.facts import user_fact
from tayyari.storage.ports import StoredResume, StoredResumeFile
from tayyari.storage.tables import ResumeFileRow, ResumeRow, UserFactRow
from tests.integration.scenarios import composite_synth
from tests.integration.support import (
    Env,
    FakeWeb,
    RecordingClaude,
    analysis_events,
    done_of,
    reader_output,
    running,
    source_id_of,
)
from tests.unit.match.support import meera

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
OLD = "meera-nair-fixture"
LINE = "Ran the paid-search budget alone for two quarters"
NOW = datetime(2026, 11, 1, tzinfo=UTC)
LINK = "https://meeranair.example/growth-notes"


@dataclass
class Rig:
    env: Env
    claude: RecordingClaude

    async def sign_in(self, email: str = "ana@example.com") -> tuple[str, dict[str, str]]:
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return user.id, {"cookie": f"{SESSION}={token}"}

    async def profile(self, user_id: str, pid: str = OLD, *, context: StoredContext | None = None) -> None:
        persona = meera()
        repos = self.env.container.repos
        await repos.profiles.save(
            pid,
            {**persona.profile.to_dict(), "id": pid},
            context if context is not None else persona.context,
            goal=persona.profile.goal,
            owner_hash=None,
            is_sample=False,
        )
        await repos.users.claim_profile(user_id, pid)

    async def keep(self, pid: str, user_id: str, *, files: int = 2) -> None:
        repos = self.env.container.repos
        for n in range(files):
            text = f"Meera Nair, resume number {n}. Led lifecycle email and paid search."
            await repos.resume_files.add(
                StoredResumeFile(
                    id=f"{n}" * 32, user_id=user_id, profile_id=pid, origin="analysis" if n == 0 else "upload",
                    filename=f"meera-{n}.txt", mime="text/plain", size=len(text), sha256=f"{n}" * 64, text=text,
                    facts={}, merge=None, merged_book=None, created_at=NOW.replace(day=1 + n),
                ),
                text.encode(),
            )  # fmt: skip
        await repos.resumes.create(
            StoredResume(
                id="a" * 32, user_id=user_id, profile_id=pid, kind="upload", role_key="", title="Resume", draft={},
                final=None, facts={}, is_default=True, version=1, created_at=NOW, updated_at=NOW,
            )
        )  # fmt: skip
        await repos.user_facts.add(
            user_id,
            pid,
            user_fact(LINE, key="resume:aaa/b-exp-1.1"),
            question="q",
            answer=LINE,
            match_id=None,
        )

    async def reread(self, pid: str, headers: dict[str, str] | None = None) -> Any:
        return await self.env.client.post(
            "/api/analyze", files=[("fromProfile", (None, pid))], headers=headers or {}
        )


@pytest.fixture
def start(build_settings: MakeSettings, web: FakeWeb):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run() -> AsyncIterator[Rig]:
        claude = RecordingClaude()
        claude.script_structured("reader", lambda call: reader_output(source_id_of(call)), repeat=True)
        claude.script_structured("synth", lambda _call: composite_synth(), repeat=True)
        settings = build_settings(
            auth={"public_url": "https://tayyari.example"}, match={"allow_anonymous": "false"}
        )
        async with running(settings, claude=claude) as env:
            yield Rig(env, claude)

    return run


async def test_another_accounts_profile_is_an_unknown_one_and_costs_nothing(start: Any) -> None:
    async with start() as rig:
        ana, _ = await rig.sign_in()
        _, ben_headers = await rig.sign_in("ben@example.com")
        await rig.profile(ana)
        await rig.keep(OLD, ana)

        theirs = await rig.reread(OLD, ben_headers)
        unknown = await rig.reread("f" * 32, ben_headers)

        assert theirs.status_code == unknown.status_code == 404
        assert theirs.json() == unknown.json()
        assert not rig.claude.calls_for("reader")
        assert await rig.env.spend() == {}


async def test_a_visitor_with_no_sign_in_is_refused(start: Any) -> None:
    async with start() as rig:
        ana, _ = await rig.sign_in()
        await rig.profile(ana)
        assert (await rig.reread(OLD)).status_code == 401


async def test_a_profile_with_no_resume_text_at_all_is_a_409_before_any_cost(start: Any) -> None:
    async with start() as rig:
        ana, headers = await rig.sign_in()
        links_only = StoredContext(
            sources=[StoredSource(id="link-1", label="Notes", kind="link", url=LINK, content="a page")]
        )
        await rig.profile(ana, context=links_only)

        response = await rig.reread(OLD, headers)

        assert response.status_code == 409
        assert response.json()["code"] == "nothing_to_reread"
        assert not rig.claude.calls_for("reader") and await rig.env.spend() == {}


async def test_a_profile_with_files_makes_a_new_profile_that_owns_the_resumes(
    start: Any, web: FakeWeb
) -> None:
    async with start() as rig:
        ana, headers = await rig.sign_in()
        await rig.profile(ana)
        await rig.keep(OLD, ana)

        response = await rig.reread(OLD, headers)
        events = analysis_events(response)
        await rig.env.settle()

        assert response.status_code == 200
        done = events[-1]
        assert done.type == "done" and done.carried is True  # type: ignore[union-attr]
        new_id = done_of(events).profile.id
        assert new_id != OLD
        # the same links were read again
        assert web.web_calls == [LINK]
        # the readers were shown every file under its heading and the own-words section
        resume_calls = [c for c in rig.claude.calls_for("reader") if source_id_of(c) == "resume"]
        shown = resume_calls[0].user_text
        assert "Resume file: meera-0.txt (2026-11-01)" in shown
        assert "Resume file: meera-1.txt (2026-11-02)" in shown
        assert shown.index("meera-0.txt") < shown.index("meera-1.txt")
        assert "In your own words (stated by you, not from a document)" in shown and LINE in shown
        # the person's things moved: the default resume, both files (no new analysis file was made) and the line
        resumes = await rig.env.rows(ResumeRow, ResumeRow.profile_id == new_id)
        assert [(r.id, r.is_default, r.user_id) for r in resumes] == [("a" * 32, True, ana)]
        files = await rig.env.rows(ResumeFileRow, ResumeFileRow.profile_id == new_id)
        assert sorted(f.filename for f in files) == ["meera-0.txt", "meera-1.txt"]
        assert len(await rig.env.rows(UserFactRow, UserFactRow.profile_id == new_id)) == 1
        for table in (ResumeRow, ResumeFileRow, UserFactRow):
            assert await rig.env.rows(table, table.profile_id == OLD) == []
        # the new graph is the account's own, readable by it
        assert (await rig.env.client.get(f"/api/profile/{new_id}", headers=headers)).status_code == 200
        # the stream says what it read
        logs = [
            e.text for e in events if e.type == "log" and e.who == "resume" and e.text.startswith("Reading ")
        ]  # type: ignore[union-attr]
        assert logs == ["Reading 2 resume files and 1 line in your own words"]
        assert json.loads(response.text.strip().splitlines()[-1])["carried"] is True
