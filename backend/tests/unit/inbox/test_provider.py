"""The Resend implementation of the mail provider over an ``httpx2.MockTransport`` (nothing reaches Resend): the paths and
the Bearer key, defensive parsing of received mail (sender shapes, HTML fallback, the Message-ID header, wrapped answers),
the list, the copy's payload, and errors that carry only a status and an error name. The key is made up."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from tayyari.inbox.provider import (
    CopyMessage,
    InboundMailError,
    ResendInboundProvider,
    parse_list,
    parse_received,
    parse_time,
)

KEY = "re_test_not_a_real_key"


def provider(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> tuple[ResendInboundProvider, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(record))
    return ResendInboundProvider(api_key=KEY, client=client), seen


RECEIVED: dict[str, Any] = {
    "object": "email",
    "id": "e-1",
    "to": ["riya.s23456@apply.leafout.in"],
    "from": "Acme Talent <talent@acme.com>",
    "created_at": "2026-11-10 09:30:00.123+00",
    "subject": "Thanks for applying",
    "html": "<p>Thank you for applying to <b>Acme</b>.</p>",
    "text": None,
    "headers": {"Message-ID": "<abc@acme.com>"},
}


async def test_get_received_reads_the_email_with_the_key() -> None:
    p, seen = provider(lambda r: httpx2.Response(200, json=RECEIVED))
    got = await p.get_received("e-1")
    assert seen[0].method == "GET" and str(seen[0].url) == "https://api.resend.com/emails/receiving/e-1"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert got.id == "e-1" and got.sender == "talent@acme.com" and got.to == ["riya.s23456@apply.leafout.in"]
    assert got.text == "Thank you for applying to Acme."
    assert got.message_id == "<abc@acme.com>"
    assert got.received_at == datetime(2026, 11, 10, 9, 30, 0, 123000, tzinfo=UTC)
    assert "Thank you" not in repr(got)


def test_parse_received_tolerates_other_shapes() -> None:
    got = parse_received(
        {
            "data": {
                "email_id": "e-2",
                "from": {"email": "Jobs@Acme.com"},
                "text": "Hello",
                "message_id": "<m>",
            }
        }
    )
    assert (got.id, got.sender, got.text, got.message_id, got.subject) == (
        "e-2",
        "jobs@acme.com",
        "Hello",
        "<m>",
        "",
    )
    empty = parse_received({}, fallback_id="e-3")
    assert empty.id == "e-3" and empty.sender == "" and empty.text == "" and empty.received_at is None
    listed_headers = parse_received({"headers": [{"name": "Subject", "value": "From headers"}]})
    assert listed_headers.subject == "From headers"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-10T09:30:00Z", datetime(2026, 11, 10, 9, 30, tzinfo=UTC)),
        ("2026-11-10T15:00:00+05:30", datetime(2026, 11, 10, 9, 30, tzinfo=UTC)),
        ("2026-11-10 09:30:00", datetime(2026, 11, 10, 9, 30, tzinfo=UTC)),
        ("yesterday", None),
        (None, None),
    ],
)
def test_parse_time(raw: Any, expected: datetime | None) -> None:
    assert parse_time(raw) == expected


async def test_list_received_caps_the_limit() -> None:
    body = {
        "object": "list",
        "data": [{"id": "e-1", "from": "a@b.co", "to": ["x@apply.leafout.in"]}, {"nope": 1}],
    }
    p, seen = provider(lambda r: httpx2.Response(200, json=body))
    got = await p.list_received(500)
    assert seen[0].url.params["limit"] == "100" and seen[0].url.path == "/emails/receiving"
    assert [s.id for s in got] == ["e-1"] and got[0].to == ["x@apply.leafout.in"]
    assert parse_list({"emails": [{"id": "e-9"}]})[0].id == "e-9"
    assert parse_list("junk") == []


async def test_send_copy_posts_plain_text_with_reply_to() -> None:
    p, seen = provider(lambda r: httpx2.Response(200, json={"id": "sent-1"}))
    await p.send_copy(
        CopyMessage(sender="Leafout <inbox@apply.leafout.in>", to="riya@gmail.com", reply_to="talent@acme.com",
                    subject="[Leafout] Acme: Thanks", text="Hello")
    )  # fmt: skip
    sent = json.loads(seen[0].content)
    assert seen[0].method == "POST" and seen[0].url.path == "/emails"
    assert sent == {
        "from": "Leafout <inbox@apply.leafout.in>",
        "to": ["riya@gmail.com"],
        "subject": "[Leafout] Acme: Thanks",
        "text": "Hello",
        "headers": {"X-Leafout-Copy": "1"},
        "reply_to": ["talent@acme.com"],
    }


async def test_errors_carry_only_the_status_and_the_error_name() -> None:
    p, _ = provider(lambda r: httpx2.Response(404, json={"name": "not_found", "message": "secret details"}))
    with pytest.raises(InboundMailError) as caught:
        await p.get_received("e-1")
    assert str(caught.value) == "the mail provider answered 404: not_found"

    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    p2, _ = provider(down)
    with pytest.raises(InboundMailError, match="could not be reached"):
        await p2.send_copy(CopyMessage(sender="s", to="t@x.co", reply_to=None, subject="s", text="t"))
