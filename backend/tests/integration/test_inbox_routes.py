"""The application inbox end to end through the real app over a real SQLite file, with the real `InboxService`, the real
`EmailTracker` (keyword rules: no model key) and a fake mail provider at the network edge: off by default, signed in only,
consent required, the address made once, the Svix-signed webhook (refused, accepted, repeated, too big, not JSON, a
database that is down), the email read in the background and listed, and the tracking address reaching auto-apply's
forms. Nothing here calls the network. The secrets are made up for the test."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from tayyari import container as container_module
from tayyari.container import build_apply_engine
from tayyari.core.settings import Settings
from tayyari.inbox.provider import ReceivedEmail
from tayyari.inbox.svix import sign
from tayyari.storage.ports import StoredApplication
from tayyari.storage.tables import InboundEmailRow
from tests.fakes.inbox_provider import FakeInboundMailProvider
from tests.integration.support import running

MakeSettings = Callable[..., Settings]
SESSION_COOKIE = "tayyari_session"
SECRET = "whsec_" + base64.b64encode(b"integration-test-secret-32-bytes").decode()
DOMAIN = "apply.leafout.in"
ON: dict[str, Any] = {
    "inbound_email_enabled": True,
    "resend_api_key": SecretStr("re_test_not_a_real_key"),
    "resend_webhook_secret": SecretStr(SECRET),
}
BODY = "Dear Riya,\nUnfortunately we will not be moving forward with your application to Lumen.\nRegards"


@pytest.fixture(autouse=True)
def _no_reconcile_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(container_module, "INBOX_FIRST_DELAY_S", 3600.0)


@dataclass
class Rig:
    env: Any
    provider: FakeInboundMailProvider

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    @property
    def container(self) -> Any:
        return self.env.container

    async def account(
        self, email: str = "riya@example.com", name: str = "Riya Shah"
    ) -> tuple[str, dict[str, str]]:
        c = self.container
        user = await c.repos.users.sign_in_email(email=email, now=c.clock.now())
        await c.repos.users.update(user.id, name=name)
        token, _ = await c.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return user.id, {"cookie": f"{SESSION_COOKIE}={token}"}

    async def hook(
        self, event: dict[str, Any] | bytes, *, secret: str = SECRET, **headers: str
    ) -> httpx.Response:
        raw = event if isinstance(event, bytes) else json.dumps(event).encode()
        stamp = str(int(time.time()))
        signed = {
            "svix-id": "msg_1",
            "svix-timestamp": stamp,
            "svix-signature": sign(secret, "msg_1", stamp, raw),
            "content-type": "application/json",
        }
        return await self.client.post("/api/inbound/resend", content=raw, headers={**signed, **headers})


def received(email_id: str, to: str, **data: Any) -> dict[str, Any]:
    return {
        "type": "email.received",
        "created_at": "2026-11-10T09:30:00Z",
        "data": {"email_id": email_id, "from": "Lumen Careers <careers@lumen.com>", "to": [to],
                 "subject": "Your application to Lumen", **data},
    }  # fmt: skip


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**overrides: Any) -> AsyncIterator[Rig]:
        provider = FakeInboundMailProvider()
        extra = {"apply_opener": overrides.pop("apply_opener")} if "apply_opener" in overrides else {}
        async with running(build_settings(**overrides), inbox_provider=provider, **extra) as env:
            yield Rig(env, provider)

    return run


ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/inbox/address", {"consent": True}),
    ("PATCH", "/api/inbox/address", {"active": False}),
    ("GET", "/api/inbox/emails", None),
]


async def test_off_by_default_everything_but_the_status_is_404(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.account()
        assert (await rig.client.get("/api/inbox/status", headers=headers)).json() == {"enabled": False}
        for method, path, body in ROUTES:
            response = await rig.client.request(method, path, json=body, headers=headers)
            assert response.status_code == 404, (method, path)
        assert (await rig.hook(received("e-1", f"a.bcdef@{DOMAIN}"))).status_code == 404
        assert rig.container.inbox_loop is None


async def test_the_flag_alone_is_not_enough(start: Any) -> None:
    async with start(inbound_email_enabled=True, resend_api_key=SecretStr("re_test_key")) as rig:
        assert (await rig.client.get("/api/inbox/status")).json() == {"enabled": False}
        assert (await rig.hook(received("e-1", f"a.bcdef@{DOMAIN}"))).status_code == 404


async def test_signed_out_is_401_and_the_status_has_no_address(start: Any) -> None:
    async with start(**ON) as rig:
        for method, path, body in ROUTES:
            response = await rig.client.request(method, path, json=body)
            assert response.status_code == 401, (method, path)
        assert (await rig.client.get("/api/inbox/status")).json() == {"enabled": True}
        assert rig.container.inbox_loop is not None


async def test_an_address_needs_consent_and_is_made_once(start: Any) -> None:
    async with start(**ON) as rig:
        _, headers = await rig.account()
        for body in ({"consent": False}, {}, {"consent": None}):
            response = await rig.client.post("/api/inbox/address", json=body, headers=headers)
            assert response.status_code == 400, body
        assert (await rig.client.get("/api/inbox/status", headers=headers)).json() == {"enabled": True}
        made = await rig.client.post("/api/inbox/address", json={"consent": True}, headers=headers)
        assert made.status_code == 200 and made.headers["cache-control"] == "no-store"
        address = made.json()
        assert address["email"].startswith("riya.s") and address["email"].endswith("@" + DOMAIN)
        assert address["active"] is True and address["copies"] is True and address["createdAt"]
        again = await rig.client.post("/api/inbox/address", json={"consent": True}, headers=headers)
        assert again.status_code == 200 and again.json() == address
        status = (await rig.client.get("/api/inbox/status", headers=headers)).json()
        assert status == {"enabled": True, "address": address}

        assert (await rig.client.patch("/api/inbox/address", json={}, headers=headers)).status_code == 400
        off = await rig.client.patch(
            "/api/inbox/address", json={"active": False, "copies": False}, headers=headers
        )
        assert off.json()["active"] is False and off.json()["copies"] is False

        _, other = await rig.account("b@example.com")
        response = await rig.client.patch("/api/inbox/address", json={"active": True}, headers=other)
        assert response.status_code == 404


async def test_the_webhook_reads_the_email_in_the_background_and_lists_it(start: Any) -> None:
    async with start(**ON) as rig:
        uid, headers = await rig.account()
        address = (
            await rig.client.post("/api/inbox/address", json={"consent": True}, headers=headers)
        ).json()
        now = rig.container.clock.now()
        await rig.container.repos.apply.create_application(
            StoredApplication(
                id="app-1", owner_key=f"u:{uid}", user_id=uid, source="match", job_url="https://x", domain="x",
                ats="greenhouse", stage="sent", created_at=now, updated_at=now, company="Lumen", title="Growth",
            )
        )  # fmt: skip
        rig.provider.add(ReceivedEmail(id="e-1", sender="careers@lumen.com", subject="Your application to Lumen",
                                       text=BODY))  # fmt: skip

        refused = await rig.hook(
            received("e-1", address["email"]), secret="whsec_" + base64.b64encode(b"x" * 32).decode()
        )
        assert refused.status_code == 401
        assert (await rig.client.get("/api/inbox/emails", headers=headers)).json() == {"emails": []}

        accepted = await rig.hook(received("e-1", address["email"].upper()), origin="https://evil.example")
        assert accepted.status_code == 200 and accepted.json() == {"ok": True}
        assert (await rig.hook(received("e-1", address["email"]))).status_code == 200  # a repeat: nothing new

        listed = (await rig.client.get("/api/inbox/emails?limit=5", headers=headers)).json()["emails"]
        assert len(listed) == 1
        (one,) = listed
        assert (one["status"], one["stage"], one["applicationId"], one["copyStatus"]) == (
            "processed",
            "rejected",
            "app-1",
            "sent",
        )
        assert one["sender"] == "careers@lumen.com" and one["senderDomain"] == "lumen.com"
        assert "not be moving forward" in one["quote"]
        moved = await rig.container.repos.apply.get_application("app-1", f"u:{uid}")
        assert moved.stage == "rejected"
        (copy,) = rig.provider.sent
        assert copy.to == "riya@example.com" and copy.subject == "[Leafout] Lumen: Your application to Lumen"

        assert (await rig.hook({"type": "email.bounced", "data": {}})).status_code == 200
        assert (await rig.hook(b"not json")).status_code == 400
        assert (await rig.hook(b"x" * (256 * 1024 + 1))).status_code == 413


async def test_unknown_addresses_are_quarantined_without_a_fetch(start: Any) -> None:
    async with start(**ON) as rig:
        assert (await rig.hook(received("e-9", f"nobody.zzzzz@{DOMAIN}"))).status_code == 200
        assert rig.provider.fetched == []
        (row,) = await rig.env.rows(InboundEmailRow)
        assert (row.status, row.user_id, row.address_id, row.recipient) == (
            "quarantined",
            None,
            None,
            "nobody.zzzzz",
        )
        assert row.subject == "Your application to Lumen" and row.sender == "careers@lumen.com"


async def test_a_database_that_is_down_is_a_503(start: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async with start(**ON) as rig:

        async def down(item: Any) -> bool:
            raise ConnectionError("database asleep")

        monkeypatch.setattr(rig.container.inbox.repo, "insert_email", down)
        assert (await rig.hook(received("e-1", f"a.bcdef@{DOMAIN}"))).status_code == 503


async def test_the_list_limit_is_clamped(start: Any) -> None:
    async with start(**ON) as rig:
        _, headers = await rig.account()
        await rig.client.post("/api/inbox/address", json={"consent": True}, headers=headers)
        for raw in ("0", "-3", "abc", "100000"):
            response = await rig.client.get(f"/api/inbox/emails?limit={raw}", headers=headers)
            assert response.status_code == 200 and response.json() == {"emails": []}


async def test_auto_apply_forms_get_the_active_tracking_address(start: Any) -> None:
    async with start(**ON, apply_opener=object()) as rig:
        uid, headers = await rig.account()
        engine = build_apply_engine(rig.container)
        assert engine.form_email is not None and await engine.form_email(uid) is None
        address = (
            await rig.client.post("/api/inbox/address", json={"consent": True}, headers=headers)
        ).json()
        assert await engine.form_email(uid) == address["email"]
        await rig.client.patch("/api/inbox/address", json={"active": False}, headers=headers)
        assert await engine.form_email(uid) is None


async def test_with_the_inbox_off_forms_keep_the_persons_email(start: Any) -> None:
    async with start(apply_opener=object()) as rig:
        uid, _ = await rig.account()
        engine = build_apply_engine(rig.container)
        assert engine.form_email is not None and await engine.form_email(uid) is None
