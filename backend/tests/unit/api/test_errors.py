from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from tayyari.api import errors
from tayyari.api.deps import RequestIdMiddleware
from tayyari.api.errors import GENERIC_ERROR_MESSAGE, install_error_handlers, user_message_for
from tayyari.contract import ChatRequestBody
from tayyari.core.errors import (
    AppError,
    BadRequest,
    ClientClosed,
    NotFound,
    PayloadTooLarge,
    RateLimited,
    ServiceUnavailable,
)
from tayyari.llm.errors import (
    BUSY_MESSAGE,
    SPEND_CEILING_MESSAGE,
    ClaudeError,
    ClaudeRateLimited,
    SpendCeilingReached,
)
from tests.unit.api.helpers import RecordingLog


class Tiny(BaseModel):
    count: int = Field(ge=1)
    name: str


def make_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/bad")
    async def bad() -> None:
        raise BadRequest("That did not look right.")

    @app.get("/missing")
    async def missing() -> None:
        raise NotFound()

    @app.get("/big")
    async def big() -> None:
        raise PayloadTooLarge("Request body too large")

    @app.get("/limited")
    async def limited() -> None:
        raise RateLimited("Slow down, please.", retry_after_s=12.2)

    @app.get("/limited-zero")
    async def limited_zero() -> None:
        raise RateLimited(retry_after_s=0.0)

    @app.get("/spend")
    async def spend() -> None:
        raise SpendCeilingReached(7200.4)

    @app.get("/unavailable")
    async def unavailable() -> None:
        raise ServiceUnavailable(detail="db pool exhausted: postgres://user:pw@host/db")

    @app.get("/teapot")
    async def teapot() -> None:
        raise AppError("Public text only.", status=418, code="teapot", detail="private stack detail")

    @app.get("/closed")
    async def closed() -> None:
        raise ClientClosed()

    @app.get("/gone")
    async def gone() -> None:
        raise ClientDisconnect()

    @app.get("/http404")
    async def http404() -> None:
        raise HTTPException(404, "Profile not found")

    @app.get("/http401")
    async def http401() -> None:
        raise HTTPException(401, "Sign in first", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/http204")
    async def http204() -> None:
        raise HTTPException(204)

    @app.get("/http-structured")
    async def http_structured() -> None:
        raise HTTPException(422, {"anything": "goes"})

    @app.post("/tiny")
    async def tiny(body: Tiny) -> dict[str, int]:
        return {"count": body.count}

    @app.get("/items")
    async def items(n: int) -> dict[str, int]:
        return {"n": n}

    @app.post("/chat")
    async def chat(body: ChatRequestBody) -> dict[str, str]:
        return {"message": body.message}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal state: password=hunter2")

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    recorder = RecordingLog()
    monkeypatch.setattr(errors, "log", recorder)
    return recorder


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=make_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ───────────────────────────── AppError ─────────────────────────────


@pytest.mark.parametrize(
    ("path", "status", "message"),
    [
        ("/bad", 400, "That did not look right."),
        ("/missing", 404, "Not found"),
        ("/big", 413, "Request body too large"),
        ("/limited", 429, "Slow down, please."),
        ("/spend", 503, SPEND_CEILING_MESSAGE),
        ("/unavailable", 503, "Leafout is temporarily unavailable. Please try again soon."),
        ("/teapot", 418, "Public text only."),
    ],
)
async def test_app_errors_become_json_with_their_status_and_user_message(
    client: httpx.AsyncClient, path: str, status: int, message: str
) -> None:
    res = await client.get(path)
    assert res.status_code == status
    assert res.headers["content-type"].startswith("application/json")
    assert res.json() == {"error": message}


async def test_retry_after_is_whole_seconds_rounded_up(client: httpx.AsyncClient) -> None:
    limited = await client.get("/limited")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "13"
    spend = await client.get("/spend")
    assert spend.status_code == 503
    assert spend.headers["retry-after"] == "7201"
    assert (await client.get("/limited-zero")).headers["retry-after"] == "1"


async def test_no_retry_after_header_when_there_is_none(client: httpx.AsyncClient) -> None:
    for path in ("/bad", "/missing", "/teapot", "/unavailable"):
        assert "retry-after" not in (await client.get(path)).headers


async def test_spend_ceiling_is_an_app_error_with_the_documented_answer(client: httpx.AsyncClient) -> None:
    assert issubclass(SpendCeilingReached, AppError)
    res = await client.get("/spend")
    assert res.json() == {
        "error": "Leafout has reached today's usage limit. The pre-computed sample profile still works. Try again tomorrow."
    }


async def test_detail_is_logged_but_never_sent(client: httpx.AsyncClient, log: RecordingLog) -> None:
    for path in ("/teapot", "/unavailable"):
        res = await client.get(path)
        assert "private" not in res.text
        assert "postgres" not in res.text
    details = {fields["detail"] for _, _, fields in log.records}
    assert "private stack detail" in details
    assert any("postgres://" in d for d in details if d)


async def test_a_closed_client_gets_a_bare_499(client: httpx.AsyncClient) -> None:
    for path in ("/closed", "/gone"):
        res = await client.get(path)
        assert res.status_code == 499
        assert res.content == b""


async def test_error_responses_carry_a_request_id(client: httpx.AsyncClient) -> None:
    res = await client.get("/limited", headers={"x-request-id": "req-77"})
    assert res.headers["x-request-id"] == "req-77"
    assert len((await client.get("/bad")).headers["x-request-id"]) == 32


# ───────────────────────────── HTTPException and routing ─────────────────────────────


async def test_http_exceptions_become_json(client: httpx.AsyncClient) -> None:
    res = await client.get("/http404")
    assert (res.status_code, res.json()) == (404, {"error": "Profile not found"})
    res = await client.get("/http401")
    assert (res.status_code, res.json()) == (401, {"error": "Sign in first"})
    assert res.headers["www-authenticate"] == "Bearer"


async def test_204_has_no_body(client: httpx.AsyncClient) -> None:
    res = await client.get("/http204")
    assert res.status_code == 204
    assert res.content == b""


async def test_a_non_string_detail_is_stringified(client: httpx.AsyncClient) -> None:
    res = await client.get("/http-structured")
    assert res.status_code == 422
    assert set(res.json()) == {"error"}
    assert isinstance(res.json()["error"], str)


async def test_unknown_routes_and_wrong_methods_are_json(client: httpx.AsyncClient) -> None:
    unknown = await client.get("/nothing-here")
    assert (unknown.status_code, unknown.json()) == (404, {"error": "Not Found"})
    wrong = await client.post("/ok")
    assert (wrong.status_code, wrong.json()) == (405, {"error": "Method Not Allowed"})
    assert "GET" in wrong.headers["allow"]


# ───────────────────────────── request validation ─────────────────────────────


async def test_a_validation_error_is_a_400_with_the_first_problem(client: httpx.AsyncClient) -> None:
    res = await client.post("/chat", json={"profileId": "p1", "message": "   "})
    assert res.status_code == 400
    assert res.json() == {"error": "Invalid request: message String should have at least 1 character"}


async def test_a_missing_field_names_the_field(client: httpx.AsyncClient) -> None:
    res = await client.post("/chat", json={"message": "hi"})
    assert res.json() == {"error": "Invalid request: profileId Field required"}


async def test_nested_locations_are_dotted(client: httpx.AsyncClient) -> None:
    res = await client.post("/chat", json={"profileId": "p", "message": "hi", "origin": {"kind": "nonsense"}})
    assert res.status_code == 400
    error = res.json()["error"]
    assert error.startswith("Invalid request: origin.kind ")


async def test_query_problems_keep_their_source(client: httpx.AsyncClient) -> None:
    res = await client.get("/items", params={"n": "abc"})
    assert res.status_code == 400
    assert res.json()["error"].startswith("Invalid request: query.n ")


async def test_invalid_json_uses_the_legacy_text(client: httpx.AsyncClient) -> None:
    res = await client.post("/chat", content=b"{not json", headers={"content-type": "application/json"})
    assert (res.status_code, res.json()) == (400, {"error": "Invalid JSON body"})


async def test_a_bad_value_is_never_echoed_back(client: httpx.AsyncClient) -> None:
    secret = "MY-SECRET-RESUME-TEXT"
    res = await client.post("/tiny", json={"count": 0, "name": secret})
    assert res.status_code == 400
    assert secret not in res.text
    assert res.json()["error"].startswith("Invalid request: count ")


async def test_validation_messages_are_capped(client: httpx.AsyncClient) -> None:
    res = await client.post("/chat", json={"profileId": "p" * 500, "message": "hi"})
    assert res.status_code == 400
    assert len(res.json()["error"]) < 260


# ───────────────────────────── the unexpected ─────────────────────────────


async def test_any_other_exception_is_a_generic_500_and_the_traceback_is_logged(
    client: httpx.AsyncClient, log: RecordingLog
) -> None:
    res = await client.get("/boom", headers={"x-request-id": "trace-me"})
    assert res.status_code == 500
    assert res.json() == {"error": "Something went wrong on the server."} == {"error": GENERIC_ERROR_MESSAGE}
    assert "hunter2" not in res.text
    assert res.headers["x-request-id"] == "trace-me"  # the 500 handler runs outside the middleware

    (level, event, fields) = log.records[-1]
    assert (level, event) == ("error", "unhandled_exception")
    exc_type, exc, tb = fields["exc_info"]
    assert exc_type is RuntimeError
    assert "hunter2" in str(
        exc
    )  # in the server log (redaction is the logging pipeline's job), not in the answer
    assert tb is not None
    assert fields["path"] == "/boom"


async def test_a_claude_error_outside_a_stream_is_still_a_generic_500(client: httpx.AsyncClient) -> None:
    app = make_app()

    @app.get("/claude")
    async def claude() -> None:
        raise ClaudeRateLimited(5.0)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        res = await c.get("/claude")
    assert res.status_code == 500
    assert res.json() == {"error": GENERIC_ERROR_MESSAGE}


async def test_healthy_routes_are_untouched(client: httpx.AsyncClient) -> None:
    res = await client.get("/ok")
    assert (res.status_code, res.json()) == (200, {"ok": True})


# ───────────────────────────── user_message_for ─────────────────────────────


def test_user_message_for() -> None:
    assert user_message_for(SpendCeilingReached(1)) == SPEND_CEILING_MESSAGE
    assert user_message_for(BadRequest("fix it")) == "fix it"
    assert user_message_for(ClaudeRateLimited(3.0)) == BUSY_MESSAGE
    assert user_message_for(ClaudeError("unknown", user_message="from claude")) == "from claude"
    assert user_message_for(RuntimeError("secret")) == GENERIC_ERROR_MESSAGE
    assert user_message_for(KeyError("k")) == GENERIC_ERROR_MESSAGE
