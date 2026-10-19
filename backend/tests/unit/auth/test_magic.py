"""Emailed sign-in links: issue, deliver, limits, and the single use / expiry rules of the link itself."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from tayyari.auth.magic import LIMIT_WINDOW_S
from tayyari.auth.service import Providers
from tayyari.auth.sessions import hash_token
from tests.fakes.auth import link_in
from tests.unit.auth.conftest import PUBLIC_URL, MakeAuth

EMAIL = "ana@example.com"


def token_of(link: str) -> str:
    return parse_qs(urlsplit(link).query)["token"][0]


async def test_a_link_is_emailed_to_the_address_that_asked(make_auth: MakeAuth) -> None:
    rig = make_auth()
    outcome = await rig.service.request_magic_link(EMAIL, "iphash")

    assert outcome.issued is True and outcome.dev_link is None
    (message,) = rig.mailer.sent
    assert message.to == EMAIL
    link = link_in(message)
    assert link.startswith(f"{PUBLIC_URL}/api/auth/magic/verify?token=")
    assert link in message.html
    assert "15 minutes" in message.text


async def test_only_the_hash_of_the_token_is_stored(make_auth: MakeAuth) -> None:
    rig = make_auth()
    await rig.service.request_magic_link(EMAIL, "iphash")
    token = token_of(link_in(rig.mailer.sent[0]))

    (stored,) = rig.fakes.users.links.values()
    assert stored.token_hash == hash_token(token) and token not in repr(stored)
    assert (stored.email, stored.ip_hash, stored.used_at) == (EMAIL, "iphash", None)
    assert (stored.expires_at - stored.created_at).total_seconds() == 15 * 60


async def test_a_link_signs_its_owner_in_once(make_auth: MakeAuth) -> None:
    rig = make_auth()
    await rig.service.request_magic_link(EMAIL, None)
    token = token_of(link_in(rig.mailer.sent[0]))

    first = await rig.service.verify_magic_link(token, ip_hash=None, user_agent="UA")
    assert first is not None and first.user.email == EMAIL and first.next_path == "/"
    assert (await rig.service.sessions.resolve(first.token)) is not None  # a real session was started

    assert await rig.service.verify_magic_link(token, ip_hash=None, user_agent="UA") is None  # spent


async def test_a_link_creates_the_account_on_first_use_and_finds_it_after(make_auth: MakeAuth) -> None:
    rig = make_auth()
    assert await rig.fakes.users.get_by_email(EMAIL) is None
    ids = set()
    for _ in range(2):
        await rig.service.request_magic_link(EMAIL, None)
        token = token_of(link_in(rig.mailer.sent[-1]))
        signed = await rig.service.verify_magic_link(token, ip_hash=None, user_agent=None)
        assert signed is not None
        ids.add(signed.user.id)
    assert len(ids) == 1 and len(rig.fakes.users.users) == 1


async def test_a_link_expires_after_fifteen_minutes(make_auth: MakeAuth) -> None:
    rig = make_auth()
    await rig.service.request_magic_link(EMAIL, None)
    token = token_of(link_in(rig.mailer.sent[0]))

    rig.clock.advance(15 * 60)  # exactly at the expiry the link is no longer valid
    assert await rig.service.verify_magic_link(token, ip_hash=None, user_agent=None) is None


async def test_a_link_works_until_its_last_second(make_auth: MakeAuth) -> None:
    rig = make_auth()
    await rig.service.request_magic_link(EMAIL, None)
    token = token_of(link_in(rig.mailer.sent[0]))
    rig.clock.advance(15 * 60 - 1)
    assert await rig.service.verify_magic_link(token, ip_hash=None, user_agent=None) is not None


async def test_the_lifetime_follows_the_setting(make_auth: MakeAuth) -> None:
    rig = make_auth(auth={"public_url": PUBLIC_URL, "magic_link_minutes": 5})
    await rig.service.request_magic_link(EMAIL, None)
    token = token_of(link_in(rig.mailer.sent[0]))
    assert "5 minutes" in rig.mailer.sent[0].text
    rig.clock.advance(5 * 60)
    assert await rig.service.verify_magic_link(token, ip_hash=None, user_agent=None) is None


@pytest.mark.parametrize("token", ["", "nope", "A" * 43, "x" * 300])
async def test_a_token_nobody_issued_signs_no_one_in(make_auth: MakeAuth, token: str) -> None:
    rig = make_auth()
    await rig.service.request_magic_link(EMAIL, None)
    assert await rig.service.verify_magic_link(token, ip_hash=None, user_agent=None) is None
    assert len(rig.fakes.users.users) == 0


async def test_one_link_per_request_each_with_its_own_token(make_auth: MakeAuth) -> None:
    rig = make_auth()
    await rig.service.request_magic_link(EMAIL, None)
    await rig.service.request_magic_link(EMAIL, None)
    tokens = {token_of(link_in(m)) for m in rig.mailer.sent}
    assert len(tokens) == 2  # the first link keeps working until it is used or expires


# ───────────────────────── limits ─────────────────────────


async def test_an_address_gets_three_links_per_fifteen_minutes(make_auth: MakeAuth) -> None:
    rig = make_auth()
    results = [
        await rig.service.request_magic_link(EMAIL, f"ip{i}") for i in range(5)
    ]  # a new client each time
    assert [r.issued for r in results] == [True, True, True, False, False]
    assert len(rig.mailer.sent) == 3
    assert len(rig.fakes.users.links) == 3  # a refused request stores nothing


async def test_a_client_gets_three_links_per_fifteen_minutes_whatever_the_addresses(
    make_auth: MakeAuth,
) -> None:
    rig = make_auth()
    results = [await rig.service.request_magic_link(f"person{i}@example.com", "samehash") for i in range(5)]
    assert [r.issued for r in results] == [True, True, True, False, False]
    assert len(rig.mailer.sent) == 3


async def test_the_limits_reset_with_the_window(make_auth: MakeAuth) -> None:
    rig = make_auth()
    for _ in range(4):
        await rig.service.request_magic_link(EMAIL, "iphash")
    assert len(rig.mailer.sent) == 3
    rig.clock.advance(LIMIT_WINDOW_S)
    assert (await rig.service.request_magic_link(EMAIL, "iphash")).issued is True


async def test_the_limits_can_be_turned_off(make_auth: MakeAuth) -> None:
    rig = make_auth(auth={"public_url": PUBLIC_URL, "magic_per_email": 0, "magic_per_ip": 0})
    for _ in range(8):
        await rig.service.request_magic_link(EMAIL, "iphash")
    assert len(rig.mailer.sent) == 8


async def test_the_email_address_never_reaches_the_limit_table(make_auth: MakeAuth) -> None:
    rig = make_auth()
    await rig.service.request_magic_link(EMAIL, "iphash")
    buckets = [bucket for bucket, _start in rig.fakes.limits.hits]
    assert buckets and not any("ana" in b or "example" in b for b in buckets)


# ───────────────────────── delivery ─────────────────────────


async def test_a_failing_mailer_is_not_the_requesters_problem(make_auth: MakeAuth) -> None:
    rig = make_auth()
    rig.mailer.fail_with = "the email provider answered 500"
    outcome = await rig.service.request_magic_link(EMAIL, None)
    assert outcome.issued is True and outcome.dev_link is None  # same answer as a success: no enumeration


async def test_a_local_server_without_a_key_returns_the_link_instead_of_emailing_it(
    make_auth: MakeAuth,
) -> None:
    rig = make_auth(env="local", mailer=False)
    assert rig.service.providers == Providers(True, True, False)
    outcome = await rig.service.request_magic_link(EMAIL, None)

    assert outcome.issued and outcome.dev_link is not None
    assert outcome.dev_link.startswith(f"{PUBLIC_URL}/api/auth/magic/verify?token=")
    signed = await rig.service.verify_magic_link(token_of(outcome.dev_link), ip_hash=None, user_agent=None)
    assert signed is not None and signed.user.email == EMAIL


@pytest.mark.parametrize("env", ["test", "prod"])
async def test_no_other_environment_ever_hands_out_the_link(make_auth: MakeAuth, env: str) -> None:
    rig = make_auth(env=env, mailer=False)
    assert rig.service.providers == Providers(
        True, False, False
    )  # email sign-in is off: nothing could be sent
    outcome = await rig.service.request_magic_link(EMAIL, None)
    assert outcome.issued is False and outcome.dev_link is None
    assert rig.fakes.users.links == {}


async def test_a_local_server_with_an_email_key_sends_instead_of_returning(make_auth: MakeAuth) -> None:
    rig = make_auth(env="local", mailer=False, resend_api_key="re_test_key")
    # No mailer was handed in, but the key is set: the link must never be returned to the requester.
    assert rig.service.magic.dev_mode is False
    assert (await rig.service.request_magic_link(EMAIL, None)).dev_link is None


async def test_a_local_server_with_a_mailer_sends_instead_of_returning(make_auth: MakeAuth) -> None:
    rig = make_auth(env="local")
    outcome = await rig.service.request_magic_link(EMAIL, None)
    assert outcome.dev_link is None and len(rig.mailer.sent) == 1


def test_the_outcome_does_not_print_the_link() -> None:
    from tayyari.auth.magic import MagicOutcome

    assert "token" not in repr(MagicOutcome(issued=True, dev_link="https://x/?token=secret"))
