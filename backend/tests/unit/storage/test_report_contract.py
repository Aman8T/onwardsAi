"""Reports (N1), run against every backend (the fake, a SQLite file and memory, and Postgres when configured), so
`FakeReportRepo` cannot drift from `SqlReportRepo`: one report per account that keeps when it began, a slug found only while
shared, a slug never held by two reports, copies in and out, and (on a real database) the report going with its account."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from tayyari.contract.stored import StoredContext
from tayyari.storage.ports import StoredReportSnapshot
from tests.unit.storage.conftest import T0, Env

P1, P2 = "1" * 32, "2" * 32
CHOICES = {
    "showName": True,
    "showOrgs": False,
    "showQuotes": True,
    "openToWork": False,
    "showNearMisses": False,
    "hiddenQuotes": ["q1"],
}
CONTENT = {
    "person": {"name": "Ravi K.", "line": "Senior Software Engineer · 7 years · Pune"},
    "areas": [{"name": "Java backend", "short": "Java", "score": 80}],
    "roles": [
        {
            "id": "qa-lead",
            "title": "QA Automation Lead",
            "have": 5,
            "total": 6,
            "ready": True,
            "eta": "now",
            "quotes": [
                {"id": "a1", "text": "Led the move to Playwright.", "source": "Resume", "hidden": False}
            ],
            "missing": ["Performance testing"],
        }
    ],
    "nearMisses": [],
    "openToWork": False,
}


async def account(env: Env, email: str = "ana@example.com", pid: str = P1) -> str:
    user = await env.repos.users.sign_in_email(email=email, now=T0)
    await env.repos.profiles.save(
        pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
    )
    return user.id


def report(user_id: str, profile_id: str = P1, **over: Any) -> StoredReportSnapshot:
    values: dict[str, Any] = {
        "user_id": user_id,
        "profile_id": profile_id,
        "slug": "abcdefgh1234",
        "shared": True,
        "choices": CHOICES,
        "content": CONTENT,
        "version": "0a1b2c3d",
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(over)
    return StoredReportSnapshot(**values)


async def test_a_report_comes_back_whole_by_account_and_by_slug(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.report
    assert await repo.get(uid) is None
    await repo.save(report(uid))
    assert await repo.get(uid) == report(uid)
    assert await repo.get_by_slug("abcdefgh1234") == report(uid)
    assert await repo.get_by_slug("zzzzzzzzzzzz") is None


async def test_a_slug_on_a_report_turned_off_is_not_found(env: Env) -> None:
    uid = await account(env)
    await env.repos.report.save(report(uid, shared=False))
    assert await env.repos.report.get_by_slug("abcdefgh1234") is None


async def test_saving_again_replaces_the_report_and_keeps_when_it_began(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.report
    await repo.save(report(uid))
    later = T0 + timedelta(days=2)
    await repo.save(report(uid, slug="newslug98765", version="ffffffff", created_at=later, updated_at=later))
    found = await repo.get(uid)
    assert found == report(uid, slug="newslug98765", version="ffffffff", updated_at=later)
    assert await repo.get_by_slug("abcdefgh1234") is None
    assert await repo.get_by_slug("newslug98765") == found


async def test_two_reports_never_share_a_slug(env: Env) -> None:
    ana, ben = await account(env), await account(env, "ben@example.com", P2)
    await env.repos.report.save(report(ana))
    with pytest.raises(Exception):  # noqa: B017  (the fake raises ValueError, SQL an IntegrityError)
        await env.repos.report.save(report(ben, P2))
    await env.repos.report.save(report(ben, P2, slug=None, shared=False))
    await env.repos.report.save(report(ana, slug=None, shared=False))
    assert (await env.repos.report.get(ben)) is not None


async def test_reads_are_copies(env: Env) -> None:
    uid = await account(env)
    await env.repos.report.save(report(uid))
    got = await env.repos.report.get(uid)
    assert got is not None
    got.content["roles"].clear()
    got.choices["hiddenQuotes"].append("zz")
    assert await env.repos.report.get(uid) == report(uid)


async def test_deleting_the_account_takes_its_report_with_it(env: Env) -> None:
    if env.kind == "fake":
        pytest.skip("the foreign key cascade is the database's")
    uid = await account(env)
    await env.repos.report.save(report(uid))
    assert await env.repos.users.delete(uid)
    assert await env.repos.report.get(uid) is None
    assert await env.repos.report.get_by_slug("abcdefgh1234") is None
