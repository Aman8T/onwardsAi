"""`HandoffService` with the fake repository: who may hand something over, what is kept, and the one-time take."""

from __future__ import annotations

import base64
from datetime import timedelta
from typing import Any

import pytest

from tayyari.core.errors import NotFound
from tayyari.core.settings import HandoffSettings
from tayyari.handoff import HandoffRejected, HandoffService, ResumeFile, parse_handoff_links
from tayyari.handoff.service import origin_of
from tests.fakes.clock import FakeClock
from tests.fakes.handoff import FakeHandoffRepo

SITE = "https://leafout.in"
PDF = b"%PDF-1.7 bytes \x00\xff"


def make(*, env: Any = "test", **settings: Any) -> tuple[HandoffService, FakeHandoffRepo, FakeClock]:
    repo = FakeHandoffRepo()
    clock = FakeClock()
    return HandoffService(repo, HandoffSettings(**settings), env=env, clock=clock), repo, clock


async def rejected(awaitable: Any) -> str:
    with pytest.raises(HandoffRejected) as caught:
        await awaitable
    return str(caught.value.code)


# ───────────────────────── origin ─────────────────────────


@pytest.mark.parametrize(
    "origin", [SITE, "https://www.leafout.in", "HTTPS://LeafOut.in", "https://leafout.in:443"]
)
def test_the_listed_origins_pass(origin: str) -> None:
    service, _, _ = make()
    assert service.origin_allowed(origin, None)


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "https://leafout.in.evil.example",
        "https://app.leafout.in",
        "http://leafout.in",
        "https://leafout.in:8443",
        "null",
        "",
        "leafout.in",
    ],
)
def test_other_origins_are_refused(origin: str) -> None:
    service, _, _ = make()
    assert not service.origin_allowed(origin, None)
    with pytest.raises(HandoffRejected) as caught:
        service.check_origin(origin, None)
    assert caught.value.code == "origin"


def test_the_referer_counts_only_when_there_is_no_origin() -> None:
    service, _, _ = make()
    assert service.origin_allowed(None, "https://leafout.in/start?x=1")
    assert not service.origin_allowed(None, "https://evil.example/https://leafout.in/")
    assert not service.origin_allowed(None, None)
    assert not service.origin_allowed("null", "https://leafout.in/start"), (
        "a present Origin decides, even null"
    )
    assert not service.origin_allowed("https://evil.example", "https://leafout.in/start")


def test_origins_come_from_settings_and_an_empty_list_refuses_everything() -> None:
    service, _, _ = make(origins=["https://other.example/"])
    assert service.origin_allowed("https://other.example", None)
    assert not service.origin_allowed(SITE, None)
    nobody, _, _ = make(origins=[])
    assert not nobody.origin_allowed(SITE, None)


def test_settings_read_a_comma_separated_list_and_a_json_list() -> None:
    assert HandoffSettings(origins="https://a.example, https://B.example/").origins == [
        "https://a.example",
        "https://b.example",
    ]
    assert HandoffSettings(origins='["https://a.example"]').origins == ["https://a.example"]
    assert HandoffSettings(origins="").origins == []


def test_localhost_only_when_the_app_runs_locally_or_it_is_switched_on() -> None:
    local, _, _ = make(env="local")
    assert local.origin_allowed("http://localhost:5173", None)
    assert not local.origin_allowed("https://localhost:5173", None)
    assert not local.origin_allowed("http://localhost.evil.example", None)
    assert not local.origin_allowed("http://127.0.0.1:5173", None)
    prod, _, _ = make(env="prod")
    assert not prod.origin_allowed("http://localhost:5173", None)
    forced, _, _ = make(env="prod", allow_localhost=True)
    assert forced.origin_allowed("http://localhost:3000", None)
    off, _, _ = make(env="local", allow_localhost=False)
    assert not off.origin_allowed("http://localhost:5173", None)


def test_origin_of_normalises() -> None:
    assert origin_of("https://Leafout.in:443/path?q=1") == "https://leafout.in"
    assert origin_of("http://localhost:5173/x") == "http://localhost:5173"
    assert origin_of("null") is None
    assert origin_of("ftp://leafout.in") is None
    assert origin_of("https://leafout.in:notaport") is None


# ───────────────────────── links ─────────────────────────


def test_links_split_on_newlines_and_spaces_and_keep_only_web_addresses() -> None:
    raw = (
        "https://github.com/you\r\nhttp://you.dev/about  linkedin.com/in/you,\n"
        "javascript:alert(1) mailto:a@b.co ftp://files.example/x file:///etc/passwd data:text/html,x\n"
        "https://user:pw@evil.example/ <https://you.dev/blog> https://github.com/you"
    )
    assert parse_handoff_links([raw]) == [
        "https://github.com/you",
        "http://you.dev/about",
        "https://linkedin.com/in/you",
        "https://you.dev/blog",
    ]


def test_links_are_capped_in_count_and_length_and_a_long_one_is_dropped_not_cut() -> None:
    many = " ".join(f"https://example.com/{i}" for i in range(30))
    assert len(parse_handoff_links([many])) == 10
    prefix = "https://example.com/"
    assert parse_handoff_links([prefix + "a" * 2000]) == []
    fits = prefix + "a" * (2000 - len(prefix))
    assert parse_handoff_links([fits]) == [fits]
    assert parse_handoff_links(["", "   ", "words only"]) == []


def test_a_link_with_a_broken_port_or_no_dotted_host_is_dropped() -> None:
    assert parse_handoff_links(["https://example.com:abc/x http://localhost:3000 https://intranet/x"]) == []


# ───────────────────────── submit ─────────────────────────


async def test_a_resume_alone_is_enough_and_is_stored_with_its_media_type() -> None:
    service, repo, clock = make()
    handoff_id = await service.submit(
        resume=ResumeFile("cv.PDF", "application/octet-stream", PDF), goal="  PM roles "
    )
    stored = repo.rows[handoff_id]
    assert (stored.resume_name, stored.resume_type, stored.resume_bytes) == ("cv.PDF", "application/pdf", PDF)
    assert stored.goal == "PM roles" and stored.links == [] and stored.notes is None
    assert stored.expires_at - stored.created_at == timedelta(seconds=900)
    assert stored.created_at == clock.now()
    assert len(handoff_id) == 32


async def test_links_alone_are_enough_and_notes_and_goal_are_cut_to_their_limits() -> None:
    service, repo, _ = make()
    handoff_id = await service.submit(links=["github.com/you"], goal="g" * 400, notes="n" * 5000)
    stored = repo.rows[handoff_id]
    assert stored.links == ["https://github.com/you"]
    assert stored.goal is not None and len(stored.goal) == 300
    assert stored.notes is not None and len(stored.notes) == 4000
    assert stored.resume_bytes is None


async def test_nothing_usable_is_empty() -> None:
    service, repo, _ = make()
    assert await rejected(service.submit()) == "empty"
    assert await rejected(service.submit(goal="a goal", notes="notes are not enough")) == "empty"
    assert await rejected(service.submit(links=["javascript:alert(1)"])) == "empty"
    assert await rejected(service.submit(resume=ResumeFile("cv.pdf", None, b""))) == "empty"
    assert repo.rows == {}


async def test_a_file_over_the_limit_is_too_large() -> None:
    service, repo, _ = make(max_bytes=2048)
    assert await rejected(service.submit(resume=ResumeFile("cv.pdf", None, b"x" * 2049))) == "too_large"
    await service.submit(resume=ResumeFile("cv.pdf", None, b"x" * 2048))
    assert len(repo.rows) == 1


@pytest.mark.parametrize(
    ("name", "mime"),
    [
        ("cv.exe", "application/pdf"),
        ("cv.png", None),
        ("cv.doc", "application/msword"),
        ("cv", "image/png"),
        ("cv", None),
    ],
)
async def test_other_file_types_are_refused(name: str, mime: str | None) -> None:
    service, repo, _ = make()
    code = await rejected(service.submit(resume=ResumeFile(name, mime, PDF), links=["https://you.dev"]))
    assert code == "type"
    assert repo.rows == {}


async def test_a_file_without_an_extension_is_judged_by_its_label() -> None:
    service, repo, _ = make()
    handoff_id = await service.submit(resume=ResumeFile("resume", "text/plain; charset=utf-8", b"hello"))
    assert repo.rows[handoff_id].resume_type == "text/plain"


# ───────────────────────── take ─────────────────────────


async def test_a_hand_off_is_read_once_and_gone() -> None:
    service, repo, _ = make()
    handoff_id = await service.submit(
        resume=ResumeFile("cv.docx", None, PDF), links=["https://you.dev"], goal="Data roles", notes="n"
    )
    payload = await service.take(handoff_id)
    assert payload.to_dict() == {
        "links": ["https://you.dev"],
        "goal": "Data roles",
        "notes": "n",
        "resume": {
            "name": "cv.docx",
            "type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "dataBase64": base64.b64encode(PDF).decode(),
        },
    }
    assert repo.rows == {}
    with pytest.raises(NotFound):
        await service.take(handoff_id)


async def test_the_payload_leaves_out_what_was_not_given() -> None:
    service, _, _ = make()
    handoff_id = await service.submit(links=["https://you.dev"])
    assert (await service.take(handoff_id)).to_dict() == {"links": ["https://you.dev"]}


async def test_an_expired_hand_off_is_not_found_and_is_deleted_by_the_attempt() -> None:
    service, repo, clock = make(ttl_seconds=60)
    handoff_id = await service.submit(links=["https://you.dev"])
    clock.advance(61)
    with pytest.raises(NotFound):
        await service.take(handoff_id)
    assert repo.rows == {}


async def test_the_last_second_still_counts_and_unknown_or_odd_ids_are_not_found() -> None:
    service, _, clock = make(ttl_seconds=60)
    handoff_id = await service.submit(links=["https://you.dev"])
    clock.advance(59)
    assert (await service.take(handoff_id)).links == ["https://you.dev"]
    for bad in ("", "short", "x" * 33 + "/", "../etc/passwd", "a" * 200, "a b" * 10):
        with pytest.raises(NotFound):
            await service.take(bad)


async def test_a_new_hand_off_deletes_the_expired_ones() -> None:
    service, repo, clock = make(ttl_seconds=60)
    old = await service.submit(links=["https://old.example"])
    clock.advance(120)
    fresh = await service.submit(links=["https://you.dev"])
    assert set(repo.rows) == {fresh} and old not in repo.rows


async def test_ids_are_random_and_urlsafe() -> None:
    service, _, _ = make()
    ids = {await service.submit(links=["https://you.dev"]) for _ in range(20)}
    assert len(ids) == 20
    alphabet = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert all(set(i) <= alphabet for i in ids)
