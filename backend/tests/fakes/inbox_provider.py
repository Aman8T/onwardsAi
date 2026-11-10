"""An in-memory `InboundMailProvider`: received emails by id, the provider's list, and every copy "sent" (nothing leaves
the process). Set ``fail_get`` / ``fail_send`` / ``fail_list`` to make a call raise `InboundMailError`."""

from __future__ import annotations

from dataclasses import dataclass, field

from tayyari.inbox.provider import (
    LIST_MAX,
    CopyMessage,
    InboundMailError,
    ReceivedEmail,
    ReceivedSummary,
)


@dataclass
class FakeInboundMailProvider:
    emails: dict[str, ReceivedEmail] = field(default_factory=dict)
    listed: list[ReceivedSummary] = field(default_factory=list)
    sent: list[CopyMessage] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)
    fail_get: bool = False
    fail_send: bool = False
    fail_list: bool = False

    def add(self, email: ReceivedEmail) -> None:
        self.emails[email.id] = email

    async def get_received(self, email_id: str) -> ReceivedEmail:
        self.fetched.append(email_id)
        if self.fail_get or email_id not in self.emails:
            raise InboundMailError("the mail provider answered 404: not_found")
        return self.emails[email_id]

    async def list_received(self, limit: int = LIST_MAX) -> list[ReceivedSummary]:
        if self.fail_list:
            raise InboundMailError("the mail provider answered 500")
        return self.listed[:limit]

    async def send_copy(self, message: CopyMessage) -> None:
        if self.fail_send:
            raise InboundMailError("the mail provider answered 422: validation_error")
        self.sent.append(message)


__all__ = ["FakeInboundMailProvider"]
