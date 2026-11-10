"""tayyari.contract.inbox must match shared/inbox.ts exactly (interfaces, optionality, the unions, the constants), and the
request models must refuse what the inbox routes must refuse.

Reuses the TypeScript reader and the shape comparison of test_ts_parity.py. `EmailStage` is owned by shared/apply.ts, so
the two files are read together.
"""

from __future__ import annotations

import re
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import apply as ap
from tayyari.contract import inbox
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

APPLY_TS = REPO_ROOT / "shared" / "apply.ts"
INBOX_TS = REPO_ROOT / "shared" / "inbox.ts"

if not (APPLY_TS.is_file() and INBOX_TS.is_file()):
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)

INTERFACES: dict[str, type[BaseModel]] = {
    name: getattr(inbox, name)
    for name in ["InboxAddress", "InboxStatus", "CreateInboxAddressRequest", "UpdateInboxAddressRequest", "InboxEmail",
                 "InboxEmailList"]
}  # fmt: skip
UNIONS = ["InboxEmailStatus", "CopyStatus"]
LIMITS = ["INBOX_SUBJECT_MAX_CHARS", "INBOX_EMAILS_DEFAULT_LIMIT", "INBOX_EMAILS_MAX_LIMIT"]


def _text() -> str:
    return INBOX_TS.read_text(encoding="utf-8")


def _ts() -> TsTypes:
    return TsTypes(APPLY_TS.read_text(encoding="utf-8") + "\n" + _text())


def test_every_interface_has_a_mirror() -> None:
    assert set(TsTypes(_text()).interfaces) == set(INTERFACES)


def test_every_union_is_listed() -> None:
    """A literal alias exported by inbox.ts but missing from UNIONS would never be compared."""
    assert set(TsTypes(_text()).aliases) == set(UNIONS)


@pytest.mark.parametrize("name", sorted(INTERFACES))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACES[name])) == []


@pytest.mark.parametrize("name", UNIONS)
def test_unions_match(name: str) -> None:
    assert _ts().shape(name) == ("lit", frozenset(get_args(getattr(inbox, name))))


def test_email_stage_is_the_apply_one() -> None:
    assert inbox.EmailStage is ap.EmailStage
    assert "import type { EmailStage } from './apply';" in _text()
    assert "export type EmailStage =" not in _text()


@pytest.mark.parametrize("name", LIMITS)
def test_limits_match(name: str) -> None:
    found = re.search(rf"export const {name} = (\d+);", _text())
    assert found is not None and int(found[1]) == getattr(inbox, name)


def test_every_limit_is_listed() -> None:
    ts_ints = set(re.findall(r"export const (\w+) = \d+;", _text()))
    py_ints = {n for n in inbox.__all__ if n.startswith("INBOX_") and isinstance(getattr(inbox, n), int)}
    assert ts_ints == py_ints == set(LIMITS)


def test_the_consent_version_matches() -> None:
    found = re.search(r"export const INBOX_CONSENT_VERSION = '([^']*)';", _text())
    assert found is not None and found[1] == inbox.INBOX_CONSENT_VERSION == "inbox-v1"


def test_the_limits_are_sane() -> None:
    assert 0 < inbox.INBOX_EMAILS_DEFAULT_LIMIT <= inbox.INBOX_EMAILS_MAX_LIMIT
    assert inbox.INBOX_SUBJECT_MAX_CHARS == 200


# ───────────────────────── requests refuse what the routes refuse ─────────────────────────


def test_an_address_needs_consent() -> None:
    assert inbox.CreateInboxAddressRequest.from_dict({"consent": True}).consent is True
    for body in ({"consent": False}, {}, {"consent": None}):
        with pytest.raises(ValidationError):
            inbox.CreateInboxAddressRequest.from_dict(body)


def test_an_update_changes_something() -> None:
    assert inbox.UpdateInboxAddressRequest.from_dict({"active": False}).to_dict() == {"active": False}
    assert inbox.UpdateInboxAddressRequest.from_dict({"copies": True}).to_dict() == {"copies": True}
    both = inbox.UpdateInboxAddressRequest.from_dict({"active": True, "copies": False})
    assert both.to_dict() == {"active": True, "copies": False}
    for body in ({}, {"active": None, "copies": None}, {"unknown": True}):
        with pytest.raises(ValidationError):
            inbox.UpdateInboxAddressRequest.from_dict(body)


# ───────────────────────── wire shape ─────────────────────────


def test_no_address_is_a_missing_key_not_null() -> None:
    assert inbox.InboxStatus(enabled=False).to_dict() == {"enabled": False}
    address = inbox.InboxAddress(
        email="jordan.k7m4q@apply.leafout.in", active=True, copies=True, createdAt="2026-11-10T09:00:00Z"
    )
    assert inbox.InboxStatus(enabled=True, address=address).to_dict() == {
        "enabled": True,
        "address": {"email": "jordan.k7m4q@apply.leafout.in", "active": True, "copies": True, "createdAt": "2026-11-10T09:00:00Z"},
    }  # fmt: skip


def test_an_unclassified_email_has_no_stage_keys() -> None:
    email = inbox.InboxEmail(
        id="e1",
        receivedAt="2026-11-10T09:00:00Z",
        sender="no-reply@greenhouse.io",
        senderDomain="greenhouse.io",
        subject="Your security code",
        status="verification",
    )
    assert set(email.to_dict()) == {"id", "receivedAt", "sender", "senderDomain", "subject", "status"}
    listed = inbox.InboxEmailList.from_dict({"emails": [email.to_dict()]})
    assert listed.emails[0] == email


def test_the_stage_and_statuses_are_refused_outside_the_unions() -> None:
    base = {"id": "e1", "receivedAt": "t", "sender": "a@b.c", "senderDomain": "b.c", "subject": "s"}
    with pytest.raises(ValidationError):
        inbox.InboxEmail.from_dict({**base, "status": "deleted"})
    with pytest.raises(ValidationError):
        inbox.InboxEmail.from_dict({**base, "status": "processed", "stage": "sent"})
    with pytest.raises(ValidationError):
        inbox.InboxEmail.from_dict({**base, "status": "processed", "copyStatus": "queued"})
    ok = inbox.InboxEmail.from_dict(
        {**base, "status": "processed", "stage": "interviewing", "copyStatus": "sent"}
    )
    assert ok.stage == "interviewing" and ok.copyStatus == "sent"
