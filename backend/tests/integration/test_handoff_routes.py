"""The marketing-site hand-off end to end through the real app over a real SQLite file: the form POST from the allowed origin
(303 to the app with an id), the one-time GET, every refusal as a redirect (never JSON), the cross-site guard letting this one
route through for the origin check to decide, expiry, the limits, and the id kept out of the logs and the telemetry.
Nothing here calls the network."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tayyari.api.app import _skip_telemetry
from tayyari.core.settings import Settings
from tayyari.storage.ports import StoredHandoff
from tayyari.storage.tables import HandoffRow
from tests.integration.support import Env, RecordingClaude, roomy_guard, running

MakeSettings = Callable[..., Settings]
SITE = {"origin": "https://leafout.in"}
PDF = b"%PDF-1.7 resume bytes \x00\xff"


def query_of(response: httpx.Response) -> dict[str, list[str]]:
    location = response.headers["location"]
    assert location.startswith("/?"), location  # relative to the app, never an absolute address
    return parse_qs(urlsplit(location).query)


def error_of(response: httpx.Response) -> str:
    assert response.status_code == 303, (response.status_code, response.text)
    (code,) = query_of(response)["handoff_error"]
    return code


async def send(
    env: Env,
    *,
    headers: dict[str, str] | None = None,
    resume: tuple[str, bytes, str] | None = ("cv.pdf", PDF, "application/pdf"),
    **fields: str,
) -> httpx.Response:
    files = [("resume", resume)] if resume is not None else None
    return await env.client.post("/api/handoff", data=fields or None, files=files, headers=headers or SITE)


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**overrides: Any) -> AsyncIterator[Env]:
        overrides.setdefault("guard", roomy_guard())
        async with running(build_settings(**overrides), claude=RecordingClaude()) as env:
            yield env

    return run


# ───────────────────────── the journey ─────────────────────────


async def test_a_post_from_the_site_redirects_to_the_app_and_the_payload_is_read_once(start: Any) -> None:
    async with start() as env:
        response = await send(
            env,
            links="https://github.com/you\nlinkedin.com/in/you javascript:alert(1)",
            goal="Backend roles in Pune",
            notes="Open to relocate",
        )
        assert response.status_code == 303
        assert response.headers["cache-control"] == "no-store"
        (handoff_id,) = query_of(response)["handoff"]
        assert len(handoff_id) == 32
        assert "handoff_error" not in query_of(response)

        got = await env.client.get(f"/api/handoff/{handoff_id}")
        assert got.status_code == 200
        assert got.headers["cache-control"] == "no-store"
        assert got.json() == {
            "links": ["https://github.com/you", "https://linkedin.com/in/you"],
            "goal": "Backend roles in Pune",
            "notes": "Open to relocate",
            "resume": {
                "name": "cv.pdf",
                "type": "application/pdf",
                "dataBase64": base64.b64encode(PDF).decode(),
            },
        }
        assert base64.b64decode(got.json()["resume"]["dataBase64"]) == PDF

        again = await env.client.get(f"/api/handoff/{handoff_id}")
        assert again.status_code == 404 and again.json() == {
            "error": "That hand-off has expired or was already used."
        }
        assert await env.rows(HandoffRow) == []


async def test_links_alone_and_the_referer_instead_of_the_origin(start: Any) -> None:
    async with start() as env:
        response = await send(
            env, resume=None, headers={"referer": "https://www.leafout.in/start?x=1"}, links="https://you.dev"
        )
        (handoff_id,) = query_of(response)["handoff"]
        got = await env.client.get(f"/api/handoff/{handoff_id}")
        assert got.json() == {"links": ["https://you.dev"]}


async def test_the_site_is_let_through_the_cross_site_guard_and_a_stranger_is_not(start: Any) -> None:
    """The site is another origin on purpose; a browser says so with `Sec-Fetch-Site`, and the route decides by origin."""
    async with start() as env:
        site = await send(env, headers={**SITE, "sec-fetch-site": "same-site"})
        assert site.status_code == 303 and "handoff" in query_of(site)
        evil = await send(env, headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"})
        assert error_of(evil) == "origin"
        assert len(await env.rows(HandoffRow)) == 1


# ───────────────────────── refusals are redirects ─────────────────────────


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://evil.example"},
        {"origin": "https://leafout.in.evil.example"},
        {"origin": "http://leafout.in"},
        {"origin": "null"},
        {"referer": "https://evil.example/"},
        {"origin": "https://evil.example", "referer": "https://leafout.in/"},
        {"origin": "null", "referer": "https://leafout.in/"},
        {"x-unrelated": "1"},
    ],
)
async def test_a_wrong_or_missing_origin_is_a_redirect_with_origin(
    start: Any, headers: dict[str, str]
) -> None:
    async with start() as env:
        response = await send(env, headers=headers, links="https://you.dev")
        assert error_of(response) == "origin"
        assert response.headers["cache-control"] == "no-store"
        assert await env.rows(HandoffRow) == []


async def test_the_dev_site_on_localhost_only_while_the_app_runs_locally(start: Any) -> None:
    local = {"origin": "http://localhost:3000"}
    async with start() as env:  # env "test": not local
        assert error_of(await send(env, headers=local, links="https://you.dev")) == "origin"
    async with start(env="local") as env:
        assert "handoff" in query_of(await send(env, headers=local, links="https://you.dev"))


async def test_a_file_of_the_wrong_type_is_type(start: Any) -> None:
    async with start() as env:
        for resume in (("cv.exe", b"MZ", "application/octet-stream"), ("cv.png", b"x", "image/png")):
            assert error_of(await send(env, resume=resume, links="https://you.dev")) == "type"
        assert await env.rows(HandoffRow) == []


async def test_a_file_in_the_wrong_field_is_type(start: Any) -> None:
    async with start() as env:
        response = await env.client.post(
            "/api/handoff", files=[("attachment", ("cv.pdf", PDF, "application/pdf"))], headers=SITE
        )
        assert error_of(response) == "type"


async def test_an_oversized_resume_is_too_large(start: Any) -> None:
    async with start(handoff={"max_bytes": 4096}) as env:
        big = ("cv.pdf", b"x" * 5000, "application/pdf")
        assert error_of(await send(env, resume=big, links="https://you.dev")) == "too_large"
        ok = ("cv.pdf", b"x" * 4096, "application/pdf")
        assert "handoff" in query_of(await send(env, resume=ok))
        assert len(await env.rows(HandoffRow)) == 1


async def test_a_declared_length_over_the_limit_is_refused_before_it_is_read(start: Any) -> None:
    async with start(handoff={"max_bytes": 4096}) as env:
        response = await env.client.post(
            "/api/handoff",
            content=b"x",
            headers={**SITE, "content-type": "multipart/form-data; boundary=b", "content-length": "99999999"},
        )
        assert error_of(response) == "too_large"


async def test_nothing_usable_is_empty(start: Any) -> None:
    async with start() as env:
        assert error_of(await send(env, resume=None, goal="a goal", notes="just notes")) == "empty"
        assert error_of(await send(env, resume=None, links="javascript:alert(1) mailto:a@b.co")) == "empty"
        assert error_of(await send(env, resume=("cv.pdf", b"", "application/pdf"))) == "empty"
        junk = await env.client.post(
            "/api/handoff", content=b"{}", headers={**SITE, "content-type": "application/json"}
        )
        assert error_of(junk) == "empty"
        broken = await env.client.post(
            "/api/handoff",
            content=b"--b\r\nnot a form",
            headers={**SITE, "content-type": "multipart/form-data; boundary=b"},
        )
        assert error_of(broken) == "empty"
        assert await env.rows(HandoffRow) == []


async def test_too_many_hand_offs_from_one_address_is_rate(start: Any) -> None:
    async with start(guard=roomy_guard(handoff_per_minute=1)) as env:
        ip = {**SITE, "cf-connecting-ip": "203.0.113.9"}
        assert "handoff" in query_of(await send(env, headers=ip, links="https://you.dev"))
        assert error_of(await send(env, headers=ip, links="https://you.dev")) == "rate"
        other = {**SITE, "cf-connecting-ip": "203.0.113.10"}
        assert "handoff" in query_of(await send(env, headers=other, links="https://you.dev"))
        assert len(await env.rows(HandoffRow)) == 2


async def test_the_refused_origin_is_not_counted_against_the_limit(start: Any) -> None:
    async with start(guard=roomy_guard(handoff_per_minute=1)) as env:
        for _ in range(3):
            await send(env, headers={"origin": "https://evil.example"}, links="https://you.dev")
        assert "handoff" in query_of(await send(env, links="https://you.dev"))


# ───────────────────────── the take ─────────────────────────


async def test_an_expired_hand_off_is_not_found(start: Any) -> None:
    async with start() as env:
        now = env.container.clock.now()
        await env.container.repos.handoff.create(
            StoredHandoff(
                id="e" * 32,
                created_at=now - timedelta(hours=1),
                expires_at=now - timedelta(seconds=1),
                links=["https://you.dev"],
            )
        )
        gone = await env.client.get(f"/api/handoff/{'e' * 32}")
        assert gone.status_code == 404
        assert await env.rows(HandoffRow) == []


async def test_unknown_and_odd_ids_are_404_json(start: Any) -> None:
    async with start() as env:
        for bad in ("x" * 32, "short", "a" * 300, "..%2Fetc"):
            response = await env.client.get(f"/api/handoff/{bad}")
            assert response.status_code == 404, bad
            assert response.headers["content-type"].startswith("application/json")


async def test_another_sites_page_cannot_use_up_a_hand_off(start: Any) -> None:
    async with start() as env:
        (handoff_id,) = query_of(await send(env, links="https://you.dev"))["handoff"]
        for headers in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
            assert (await env.client.get(f"/api/handoff/{handoff_id}", headers=headers)).status_code == 404
        assert len(await env.rows(HandoffRow)) == 1
        same = await env.client.get(f"/api/handoff/{handoff_id}", headers={"sec-fetch-site": "same-origin"})
        assert same.status_code == 200


async def test_a_new_hand_off_clears_the_expired_ones(start: Any) -> None:
    async with start() as env:
        now = env.container.clock.now()
        await env.container.repos.handoff.create(
            StoredHandoff(
                id="o" * 32,
                created_at=now,
                expires_at=now - timedelta(seconds=5),
                links=["https://old.example"],
            )
        )
        assert "handoff" in query_of(await send(env, links="https://you.dev"))
        ids = [row.id for row in await env.rows(HandoffRow)]
        assert len(ids) == 1 and ids[0] != "o" * 32


# ───────────────────────── the id stays out of logs and traces ─────────────────────────


async def test_the_id_is_not_logged_and_the_traces_leave_the_route_out(
    start: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    async with start() as env:
        (handoff_id,) = query_of(await send(env, links="https://you.dev", goal="a very private goal"))[
            "handoff"
        ]
        await env.client.get(f"/api/handoff/{handoff_id}")
    seen = capsys.readouterr()
    assert handoff_id not in seen.out + seen.err
    assert "a very private goal" not in seen.out + seen.err
    assert _skip_telemetry({"path": f"/api/handoff/{handoff_id}"})
    assert not _skip_telemetry({"path": "/api/handoff"})
