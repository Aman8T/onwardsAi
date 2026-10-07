from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from tayyari.contract import AnalyzeRequest, ChatOrigin, ChatOriginBody, ChatRequestBody, RequestContext
from tayyari.core import limits

# The numbers of legacy server.ChatBody / ChatOriginBody (and of the Node server before it). The limits module
# is the single source now; this pins it to the values the web app has always been held to.
LEGACY_LIMITS = {
    "profileId": 120,
    "message": 8000,
    "sessionId": 200,
    "origin.refId": 200,
    "origin.title": 200,
    "origin.caption": 300,
}


def _body(**overrides: Any) -> dict[str, Any]:
    return {"profileId": "p1", "message": "hello", **overrides}


def test_limits_are_the_legacy_numbers() -> None:
    assert limits.CHAT_PROFILE_ID_MAX_CHARS == LEGACY_LIMITS["profileId"]
    assert limits.CHAT_MESSAGE_MAX_CHARS == LEGACY_LIMITS["message"]
    assert limits.CHAT_SESSION_ID_MAX_CHARS == LEGACY_LIMITS["sessionId"]
    assert limits.CHAT_ORIGIN_REF_ID_MAX_CHARS == LEGACY_LIMITS["origin.refId"]
    assert limits.CHAT_ORIGIN_TITLE_MAX_CHARS == LEGACY_LIMITS["origin.title"]
    assert limits.CHAT_ORIGIN_CAPTION_MAX_CHARS == LEGACY_LIMITS["origin.caption"]


class TestChatRequestBody:
    def test_minimal_body(self) -> None:
        body = ChatRequestBody.model_validate(_body())
        assert body.origin is None
        assert body.sessionId is None
        assert body.to_dict() == {"profileId": "p1", "message": "hello"}

    def test_full_body(self) -> None:
        body = ChatRequestBody.model_validate(
            _body(
                sessionId="abc",
                origin={"kind": "skill", "refId": "react", "title": "React", "caption": "Frontend · 82/100"},
            )
        )
        assert body.sessionId == "abc"
        assert body.origin == ChatOriginBody(
            kind="skill", refId="react", title="React", caption="Frontend · 82/100"
        )

    def test_profile_id_and_message_are_stripped(self) -> None:
        body = ChatRequestBody.model_validate(_body(profileId="  p1\n", message="  hi there \n"))
        assert body.profileId == "p1"
        assert body.message == "hi there"

    @pytest.mark.parametrize("field", ["profileId", "message"])
    @pytest.mark.parametrize("value", ["", "   ", "\n\t"])
    def test_blank_required_text_is_rejected(self, field: str, value: str) -> None:
        with pytest.raises(ValidationError):
            ChatRequestBody.model_validate(_body(**{field: value}))

    @pytest.mark.parametrize("field", ["profileId", "message"])
    def test_missing_required_field_is_rejected(self, field: str) -> None:
        data = _body()
        del data[field]
        with pytest.raises(ValidationError):
            ChatRequestBody.model_validate(data)

    @pytest.mark.parametrize(
        ("path", "limit"),
        [
            ("profileId", limits.CHAT_PROFILE_ID_MAX_CHARS),
            ("message", limits.CHAT_MESSAGE_MAX_CHARS),
            ("sessionId", limits.CHAT_SESSION_ID_MAX_CHARS),
            ("origin.refId", limits.CHAT_ORIGIN_REF_ID_MAX_CHARS),
            ("origin.title", limits.CHAT_ORIGIN_TITLE_MAX_CHARS),
            ("origin.caption", limits.CHAT_ORIGIN_CAPTION_MAX_CHARS),
        ],
    )
    def test_each_text_limit_is_enforced_exactly(self, path: str, limit: int) -> None:
        def build(length: int) -> dict[str, Any]:
            value = "x" * length
            data = _body(origin={"kind": "free"})
            if path.startswith("origin."):
                data["origin"][path.split(".")[1]] = value
            else:
                data[path] = value
            return data

        ChatRequestBody.model_validate(build(limit))
        with pytest.raises(ValidationError):
            ChatRequestBody.model_validate(build(limit + 1))

    def test_message_limit_counts_after_stripping(self) -> None:
        padded = " " * 50 + "x" * limits.CHAT_MESSAGE_MAX_CHARS + " " * 50
        assert (
            len(ChatRequestBody.model_validate(_body(message=padded)).message)
            == limits.CHAT_MESSAGE_MAX_CHARS
        )

    def test_unknown_keys_are_ignored(self) -> None:
        body = ChatRequestBody.model_validate(_body(extra=1, origin={"kind": "free", "extra": 2}))
        assert body.to_dict() == {
            "profileId": "p1",
            "message": "hello",
            "origin": {"kind": "free", "title": "", "caption": ""},
        }

    def test_origin_kind_must_be_known(self) -> None:
        with pytest.raises(ValidationError):
            ChatRequestBody.model_validate(_body(origin={"kind": "banana"}))


class TestChatOriginBody:
    def test_title_and_caption_may_be_left_out(self) -> None:
        origin = ChatOriginBody.model_validate({"kind": "you"})
        assert origin.title == ""
        assert origin.caption == ""
        assert origin.refId is None

    def test_it_is_a_chat_origin(self) -> None:
        origin = ChatOriginBody(kind="role", refId="r1", title="AI Product Engineer", caption="57% aligned")
        assert isinstance(origin, ChatOrigin)
        assert origin.to_dict() == {
            "kind": "role",
            "refId": "r1",
            "title": "AI Product Engineer",
            "caption": "57% aligned",
        }
        # What a repository stores is validated by the strict model.
        assert ChatOrigin.from_dict(origin.to_dict()) == ChatOrigin(
            kind="role", refId="r1", title="AI Product Engineer", caption="57% aligned"
        )


class TestAnalyzeRequest:
    def test_defaults_are_empty(self) -> None:
        req = AnalyzeRequest()
        assert req.resume_bytes is None
        assert req.resume_name is None
        assert req.links == ()
        assert req.goal is None
        assert req.notes is None
        assert req.sample is False

    def test_fields(self) -> None:
        req = AnalyzeRequest(
            resume_bytes=b"%PDF-1.7",
            resume_name="cv.pdf",
            links=("https://github.com/x", "https://x.dev"),
            goal="Move into product",
            notes="Some notes",
            sample=True,
        )
        assert req.links == ("https://github.com/x", "https://x.dev")
        assert req.sample is True

    def test_it_is_frozen_and_hashable_fields_stay_put(self) -> None:
        req = AnalyzeRequest(goal="g")
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.goal = "other"  # type: ignore[misc]

    def test_repr_leaves_out_user_content(self) -> None:
        req = AnalyzeRequest(
            resume_bytes=b"SECRET-RESUME",
            resume_name="cv.pdf",
            links=("https://SECRET-LINK.example",),
            goal="SECRET-GOAL",
            notes="SECRET-NOTES",
        )
        text = repr(req)
        assert "SECRET" not in text
        assert "cv.pdf" in text


class TestRequestContext:
    def _ctx(self) -> RequestContext:
        return RequestContext(
            request_id="req-1",
            ip_hash="abcdef0123456789",
            client_ip="203.0.113.9",
            started_at=datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
        )

    def test_fields(self) -> None:
        rc = self._ctx()
        assert rc.request_id == "req-1"
        assert rc.ip_hash == "abcdef0123456789"
        assert rc.client_ip == "203.0.113.9"
        assert rc.started_at.tzinfo is UTC

    def test_positional_order_matches_the_brief(self) -> None:
        now = datetime(2026, 10, 7, tzinfo=UTC)
        assert RequestContext("r", "h", "1.2.3.4", now) == RequestContext(
            request_id="r", ip_hash="h", client_ip="1.2.3.4", started_at=now
        )

    def test_unknown_caller_is_representable(self) -> None:
        rc = RequestContext("r", None, None, datetime(2026, 10, 7, tzinfo=UTC))
        assert rc.ip_hash is None
        assert rc.client_ip is None

    def test_it_is_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            self._ctx().request_id = "other"  # type: ignore[misc]

    def test_repr_leaves_out_the_raw_ip(self) -> None:
        text = repr(self._ctx())
        assert "203.0.113.9" not in text
        assert "req-1" in text
        assert "abcdef0123456789" in text
