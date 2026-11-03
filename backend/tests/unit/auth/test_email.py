"""Sign-in emails through Resend's HTTP API (against a fake network), and the message itself."""

from __future__ import annotations

import httpx2
import pytest

from tayyari.auth.email import (
    DEFAULT_SENDER,
    RESEND_URL,
    EmailError,
    ResendEmailSender,
    sign_in_message,
)
from tests.fakes.auth import FakeNetwork, FakeResend

LINK = "https://tayyari.example/api/auth/magic/verify?token=abc-DEF_123"


def sender_on(
    net: FakeNetwork, *, sender: str | None = "Tayyari <login@tayyari.example>"
) -> ResendEmailSender:
    return ResendEmailSender(
        api_key="re_secret_key", sender=sender, client=httpx2.AsyncClient(transport=net.transport)
    )


async def test_the_message_is_posted_to_resend_with_the_key_as_a_bearer_token() -> None:
    net, resend = FakeNetwork(), FakeResend()
    resend.install(net)
    message = sign_in_message("ana@example.com", LINK, 15)

    await sender_on(net).send(message)

    (posted,) = resend.posted
    assert posted == {
        "from": "Tayyari <login@tayyari.example>",
        "to": ["ana@example.com"],
        "subject": "Your Leafout sign-in link",
        "text": message.text,
        "html": message.html,
    }
    assert resend.headers[0]["authorization"] == "Bearer re_secret_key"
    assert [str(r.url) for r in net.seen] == [RESEND_URL]


async def test_without_a_configured_sender_resends_test_sender_is_used() -> None:
    net, resend = FakeNetwork(), FakeResend()
    resend.install(net)
    await sender_on(net, sender=None).send(sign_in_message("ana@example.com", LINK, 15))
    assert resend.posted[0]["from"] == DEFAULT_SENDER


async def test_a_refusal_raises_with_the_status_and_error_name_only() -> None:
    net, resend = FakeNetwork(), FakeResend(status=422)
    resend.install(net)
    with pytest.raises(EmailError) as failed:
        await sender_on(net).send(sign_in_message("ana@example.com", LINK, 15))
    text = str(failed.value)
    assert "422" in text and "validation_error" in text
    assert "ana@example.com" not in text and "abc-DEF_123" not in text and "re_secret_key" not in text


async def test_an_unreachable_provider_raises_email_error() -> None:
    net = FakeNetwork()

    def down(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to host")

    net.on("POST", RESEND_URL, down)
    with pytest.raises(EmailError, match="could not be reached"):
        await sender_on(net).send(sign_in_message("ana@example.com", LINK, 15))


async def test_a_non_json_error_body_is_survivable() -> None:
    net = FakeNetwork()
    net.on("POST", RESEND_URL, lambda _r: httpx2.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(EmailError, match="502"):
        await sender_on(net).send(sign_in_message("ana@example.com", LINK, 15))


def test_the_message_is_plain_words_and_one_link() -> None:
    message = sign_in_message("ana@example.com", LINK, 15)
    assert message.subject == "Your Leafout sign-in link"
    assert LINK in message.text and "15 minutes" in message.text and "ignore this email" in message.text
    assert f'href="{LINK}"' in message.html
    assert "<img" not in message.html and "<script" not in message.html  # no images, no tracking


def test_a_link_with_markup_characters_is_escaped_in_the_html_part() -> None:
    message = sign_in_message("ana@example.com", 'https://x.example/?a=1&b="2"><script>', 15)
    assert "<script>" not in message.html and "&amp;" in message.html and "&quot;" in message.html


def test_the_message_does_not_print_the_address_or_the_link() -> None:
    message = sign_in_message("ana@example.com", LINK, 15)
    assert "ana@example.com" not in repr(message) and "abc-DEF_123" not in repr(message)
