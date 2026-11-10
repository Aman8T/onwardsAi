"""The application inbox's repository, run against every backend (the fake, a SQLite file and memory, and Postgres when
``TEST_DATABASE_URL`` is set), so `FakeInboxRepo` cannot drift from `SqlInboxRepo`: one address per account and per local
part, a repeated Resend id writing nothing, the claim's compare-and-set, newest-first lists, the daily cap's count, codes
used once and purged, and (on a real database) what deleting the account does."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from tayyari.storage.ports import StoredInboundEmail, StoredInboxAddress, StoredVerificationCode
from tayyari.storage.tables import InboundEmailRow, InboxAddressRow, VerificationCodeRow
from tests.unit.storage.conftest import T0, Env


async def account(env: Env, email: str = "riya@example.com") -> str:
    user = await env.repos.users.sign_in_email(email=email, now=T0)
    return user.id


def address(user_id: str | None, local: str = "riya.sk7m4q", **over: Any) -> StoredInboxAddress:
    values: dict[str, Any] = {
        "id": f"a-{local}", "user_id": user_id, "local_part": local, "status": "active", "copies": True,
        "consent_at": T0, "consent_version": "inbox-v1", "created_at": T0,
    }  # fmt: skip
    values.update(over)
    return StoredInboxAddress(**values)


def email(row_id: str, provider_id: str, **over: Any) -> StoredInboundEmail:
    values: dict[str, Any] = {
        "id": row_id, "provider_email_id": provider_id, "received_at": T0, "status": "pending",
        "sender": "talent@acme.com", "sender_domain": "acme.com", "subject": "Hello", "created_at": T0,
    }  # fmt: skip
    values.update(over)
    return StoredInboundEmail(**values)


def code(code_id: str, user_id: str, **over: Any) -> StoredVerificationCode:
    values: dict[str, Any] = {
        "id": code_id, "user_id": user_id, "sender_domain": "greenhouse.io", "received_at": T0,
        "expires_at": T0 + timedelta(minutes=15), "code": "482913",
    }  # fmt: skip
    values.update(over)
    return StoredVerificationCode(**values)


async def test_one_address_per_account_and_per_local_part(env: Env) -> None:
    uid, other = await account(env), await account(env, "b@example.com")
    repo = env.repos.inbox
    made = await repo.create_address(address(uid, "Riya.SK7M4Q", id="a-riya.sk7m4q"))
    assert made == address(uid)  # stored lower-case
    assert await repo.create_address(address(uid, "riya.sother", id="a-2")) is None  # the account has one
    assert (
        await repo.create_address(address(other, "riya.sk7m4q", id="a-3")) is None
    )  # the local part is taken
    assert await repo.get_address_for_user(uid) == made
    assert await repo.get_address_by_local_part("RIYA.SK7M4Q") == made
    assert await repo.get_address("a-riya.sk7m4q") == made
    assert await repo.get_address_for_user(other) is None


async def test_an_address_is_turned_off_and_copies_switched(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.inbox
    await repo.create_address(address(uid))
    off = await repo.update_address("a-riya.sk7m4q", status="off")
    assert off is not None and off.status == "off" and off.copies and not off.active
    quiet = await repo.update_address("a-riya.sk7m4q", copies=False)
    assert quiet is not None and quiet.status == "off" and quiet.copies is False
    assert await repo.update_address("nope", status="off") is None


async def test_a_repeated_resend_id_writes_nothing(env: Env) -> None:
    repo = env.repos.inbox
    assert await repo.insert_email(email("r1", "p1", status="quarantined", recipient="nobody.zzzzz")) is True
    assert await repo.insert_email(email("r2", "p1")) is False
    assert await repo.get_email("r2") is None
    found = await repo.get_email("r1")
    assert found is not None and found.status == "quarantined" and found.recipient == "nobody.zzzzz"
    assert await repo.known_provider_ids(["p1", "p2", "p1"]) == {"p1"}
    assert await repo.known_provider_ids([]) == set()


async def test_the_claim_is_a_compare_and_set_with_a_lease(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.inbox
    await repo.insert_email(email("r1", "p1", user_id=uid))
    lease = T0 + timedelta(minutes=2)
    first = await repo.claim_email("r1", now=T0, until=lease)
    assert first is not None and first.attempts == 1 and first.claimed_until == lease
    assert await repo.claim_email("r1", now=T0 + timedelta(minutes=1), until=lease) is None  # still leased
    again = await repo.claim_email("r1", now=lease + timedelta(seconds=1), until=lease + timedelta(minutes=2))
    assert again is not None and again.attempts == 2
    await repo.update_email(
        "r1", {"status": "processed", "claimed_until": None, "stage": "viewed", "quote": "q"}
    )
    assert await repo.claim_email("r1", now=lease + timedelta(hours=1), until=lease) is None  # not pending
    done = await repo.get_email("r1")
    assert done is not None and (done.status, done.stage, done.quote, done.claimed_until) == (
        "processed",
        "viewed",
        "q",
        None,
    )
    with pytest.raises(ValueError, match="not an inbound email column"):
        await repo.update_email("r1", {"user_id": "someone-else"})


async def test_lists_pending_and_the_daily_count(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.inbox
    await repo.create_address(address(uid))
    aid = "a-riya.sk7m4q"
    for i, (status, minutes) in enumerate(
        [
            ("processed", 0),
            ("unmatched", 1),
            ("verification", 2),
            ("quarantined", 3),
            ("pending", 4),
            ("failed", 5),
        ]
    ):
        await repo.insert_email(
            email(f"r{i}", f"p{i}", status=status, received_at=T0 + timedelta(minutes=minutes), user_id=uid,
                  address_id=aid)
        )  # fmt: skip
    listed = await repo.list_emails(uid, 3)
    assert [e.id for e in listed] == ["r5", "r4", "r3"]
    assert await repo.count_fetched_since(aid, T0) == 4
    assert await repo.count_fetched_since(aid, T0 + timedelta(minutes=2)) == 2
    now = T0 + timedelta(minutes=10)
    assert [e.id for e in await repo.list_pending(before=now, now=now, limit=10)] == ["r4"]
    assert await repo.list_pending(before=T0 + timedelta(minutes=4), now=now, limit=10) == []
    await repo.claim_email("r4", now=now, until=now + timedelta(minutes=2))
    assert await repo.list_pending(before=now, now=now, limit=10) == []
    # a claimed pending row is being read: it counts towards the cap, except for the worker that holds it
    assert await repo.count_fetched_since(aid, T0) == 5
    assert await repo.count_fetched_since(aid, T0, exclude_id="r4") == 4


async def test_codes_are_listed_used_once_and_purged(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.inbox
    await repo.add_code(code("c1", uid))
    await repo.add_code(code("c2", uid, code=None, link="https://app.greenhouse.io/verify?t=1",
                             received_at=T0 + timedelta(minutes=1), expires_at=T0 + timedelta(minutes=16)))  # fmt: skip
    found = await repo.list_codes(uid, since=T0, now=T0 + timedelta(minutes=2))
    assert [c.id for c in found] == ["c2", "c1"] and found[1].code == "482913" and found[0].link
    assert "482913" not in repr(found)
    assert [c.id for c in await repo.list_codes(uid, since=T0 + timedelta(seconds=30), now=T0)] == ["c2"]
    assert await repo.use_code("c2", now=T0) is True
    assert await repo.use_code("c2", now=T0) is False
    assert await repo.use_code("nope", now=T0) is False
    assert [c.id for c in await repo.list_codes(uid, since=T0, now=T0)] == ["c1"]
    assert await repo.list_codes(uid, since=T0, now=T0 + timedelta(minutes=15)) == []
    assert await repo.purge_codes(T0 + timedelta(minutes=5)) == 1  # the used one
    assert await repo.purge_codes(T0 + timedelta(minutes=15)) == 1  # the expired one
    assert await repo.list_codes(uid, since=T0, now=T0) == []


async def test_deleting_the_account_keeps_the_local_part_taken(env: Env) -> None:
    if env.db is None:
        pytest.skip("the foreign keys are a database's")
    uid = await account(env)
    repo = env.repos.inbox
    await repo.create_address(address(uid))
    await repo.insert_email(email("r1", "p1", user_id=uid, address_id="a-riya.sk7m4q"))
    await repo.insert_email(email("r2", "p2", status="quarantined"))
    await repo.add_code(code("c1", uid))
    assert await env.repos.users.delete(uid) is True
    async with env.db.session() as s:
        kept = (await s.execute(select(InboxAddressRow))).scalars().one()
        emails = [r.id for r in (await s.execute(select(InboundEmailRow))).scalars()]
        codes = (await s.execute(select(VerificationCodeRow))).scalars().all()
    assert kept.user_id is None and kept.local_part == "riya.sk7m4q"
    assert emails == ["r2"] and codes == []
    other = await account(env, "new@example.com")
    assert await repo.create_address(address(other, "riya.sk7m4q", id="a-new")) is None  # never reused
