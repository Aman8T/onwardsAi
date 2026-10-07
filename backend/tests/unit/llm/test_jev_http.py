"""The raw httpx Jev adapter, against a respx-mocked ``POST /v1/systemone`` (no real network).

Covers the playground-shaped success (request id, evaluation time, usage), every error mapping and which ones are
retried, the timeout paths, malformed bodies, the disabled paths (nothing is sent) and the privacy rules for the
state (email and phone masking, the PII switch, lone surrogates).
"""

from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from tayyari.core.settings import JevSettings
from tayyari.llm import jev_http
from tayyari.llm.errors import JevError
from tayyari.llm.jev import JevClient, JevQuestion
from tayyari.llm.jev_http import (
    ENDPOINT_PATH,
    MAX_RESPONSE_BYTES,
    HttpJevClient,
    base_url_problem,
    prepare_state,
)
from tayyari.llm.models import jev_cost_micro
from tayyari.llm.types import CallContext
from tests.fakes.clock import FakeClock

URL = f"https://api.typesafe.ai{ENDPOINT_PATH}"
CTX = CallContext(purpose="decision_fallback", request_id="rid-1")
KEY = "tsk_super_secret_value"

QUESTIONS = {
    "public_proof": JevQuestion.noul("Does the person link to public work?"),
    "career_family": JevQuestion.choice("Which field?", {"design_ux": "design", "other": None}),
    "depth": JevQuestion.score(
        "How deep?", ["none", "learning", "junior", "competent", "advanced", "expert"]
    ),
}

PLAYGROUND_BODY: dict[str, Any] = {
    "model": "jev-1.13.0",
    "request_id": "req_8f3a1c",
    "evaluation_time_ms": 412,
    "answers": {
        "public_proof": {"type": "noul", "noul": 0.93},
        "career_family": {
            "type": "choice",
            "choice": "design_ux",
            "confidence": 0.8,
            "probabilities": {"design_ux": 0.9, "other": 0.1},
        },
        "depth": {
            "type": "score",
            "score": 3.1,
            "confidence": 0.7,
            "probabilities": {"0": 0.0, "1": 0.02, "2": 0.08, "3": 0.8, "4": 0.1, "5": 0.0},
            "legend": {"0": "none", "1": "learning", "2": "junior", "3": "competent"},
        },
    },
    "usage": {"input_tokens": 1_000_000, "output_tokens": 14},
}


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        yield mock


def make_client(
    *,
    key: str | None = KEY,
    clock: FakeClock | None = None,
    http: httpx.AsyncClient | None = None,
    **settings: Any,
) -> HttpJevClient:
    return HttpJevClient(
        settings=JevSettings(**settings),
        api_key=SecretStr(key) if key is not None else None,
        http=http,
        clock=clock if clock is not None else FakeClock(),
    )


async def ask(
    client: HttpJevClient, state: Any = None, *, questions: Any = None, timeout_s: float = 2.5
) -> Any:
    return await client.system_one(
        state if state is not None else {"resume": "Designed things."},
        questions if questions is not None else QUESTIONS,
        timeout_s=timeout_s,
        ctx=CTX,
    )


def sent(route: respx.Route, call: int = 0) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(route.calls[call].request.content)
    return body


# ───────────────────────── success ─────────────────────────


async def test_playground_shaped_success_is_parsed_and_priced(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    client = make_client()
    response = await ask(client)

    assert response.model == "jev-1.13.0"
    assert response.request_id == "req_8f3a1c"
    assert response.input_tokens == 1_000_000
    assert response.output_tokens == 14
    assert response.cost_micro_usd == jev_cost_micro("jev-1.13.0", 1_000_000) == 42_000
    assert response.response_json["evaluation_time_ms"] == 412

    noul = response.answers["public_proof"]
    assert noul.kind == "noul" and noul.noul == 0.93 and noul.confidence == pytest.approx(0.86)
    choice = response.answers["career_family"]
    assert choice.choice == "design_ux" and choice.confidence == 0.8
    score = response.answers["depth"]
    assert score.score_level == 3 and score.score_expected == 3.1 and score.confidence == 0.7
    assert route.call_count == 1


async def test_request_shape_headers_and_request_json(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    state = {"resume": "Designed things.", "notes": None}
    response = await ask(make_client(), state)

    request = route.calls[0].request
    assert request.method == "POST"
    assert str(request.url) == URL
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert set(body) == {"state", "model", "questions"}
    assert body["state"] == state
    assert body["model"] == "jev-1.13.0"
    assert body["questions"] == {qid: q.to_wire() for qid, q in QUESTIONS.items()}
    assert body["questions"]["depth"]["criteria"][3] == "competent"
    assert response.request_json == body  # exactly what was sent


async def test_model_argument_overrides_the_pinned_model(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(
        return_value=httpx.Response(200, json={**PLAYGROUND_BODY, "model": "jev-1.14.0"})
    )
    client = make_client()
    response = await client.system_one({"a": 1}, QUESTIONS, model="jev-1.14.0", timeout_s=2.5, ctx=CTX)
    assert sent(route)["model"] == "jev-1.14.0"
    assert response.model == "jev-1.14.0"
    assert client.model_id == "jev-1.13.0"


async def test_base_url_and_trailing_slash_come_from_settings(router: respx.MockRouter) -> None:
    route = router.post("https://jev.example.test/v1/systemone").mock(
        return_value=httpx.Response(200, json=PLAYGROUND_BODY)
    )
    await ask(make_client(base_url="https://jev.example.test/"))
    assert route.call_count == 1


async def test_request_id_falls_back_to_a_response_header(router: respx.MockRouter) -> None:
    body = {k: v for k, v in PLAYGROUND_BODY.items() if k != "request_id"}
    router.post(URL).mock(return_value=httpx.Response(200, json=body, headers={"x-request-id": "hdr-77"}))
    assert (await ask(make_client())).request_id == "hdr-77"


async def test_missing_usage_is_estimated_so_spend_is_never_undercounted(router: respx.MockRouter) -> None:
    body = {k: v for k, v in PLAYGROUND_BODY.items() if k != "usage"}
    route = router.post(URL).mock(return_value=httpx.Response(200, json=body))
    response = await ask(make_client(), {"resume": "x" * 4000})
    sent_bytes = len(route.calls[0].request.content)
    assert response.input_tokens == sent_bytes // 4
    assert response.input_tokens > 1000
    assert response.cost_micro_usd == jev_cost_micro("jev-1.13.0", response.input_tokens)
    assert response.output_tokens == 0


async def test_latency_is_measured_on_the_injected_clock(router: respx.MockRouter) -> None:
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.advance(0.25)
        return httpx.Response(200, json=PLAYGROUND_BODY)

    router.post(URL).mock(side_effect=handler)
    assert (await ask(make_client(clock=clock))).latency_ms == 250


async def test_the_adapter_satisfies_the_protocol_shape() -> None:
    client: JevClient = make_client()
    assert client.model_id == "jev-1.13.0"
    await client.aclose()


# ───────────────────────── HTTP errors ─────────────────────────


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (400, "bad_request"), (422, "bad_request"), (404, "bad_request")],
)
async def test_client_errors_map_to_a_kind_and_are_not_retried(
    router: respx.MockRouter, status: int, kind: str
) -> None:
    route = router.post(URL).mock(
        return_value=httpx.Response(
            status, json={"error": {"message": "nope"}}, headers={"x-request-id": "e-1"}
        )
    )
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == kind
    assert caught.value.status == status
    assert caught.value.request_id == "e-1"
    assert "nope" in str(caught.value)
    assert route.call_count == 1


async def test_429_carries_retry_after_and_is_not_retried(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "rate_limit"
    assert caught.value.retry_after_s == 7.0
    assert caught.value.retryable
    assert route.call_count == 1


async def test_429_without_a_header_or_with_a_far_date(router: respx.MockRouter) -> None:
    router.post(URL).mock(return_value=httpx.Response(429))
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.retry_after_s is None

    router.post(URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"})
    )
    with pytest.raises(JevError) as far:
        await ask(make_client())
    assert far.value.retry_after_s == 3600.0

    router.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "soon"}))
    with pytest.raises(JevError) as junk:
        await ask(make_client())
    assert junk.value.retry_after_s is None


async def test_a_5xx_is_retried_once_then_succeeds(router: respx.MockRouter) -> None:
    clock = FakeClock()
    route = router.post(URL).mock(
        side_effect=[httpx.Response(503, text="busy"), httpx.Response(200, json=PLAYGROUND_BODY)]
    )
    response = await ask(make_client(clock=clock))
    assert route.call_count == 2
    assert response.request_id == "req_8f3a1c"
    assert len(clock.sleeps) == 1 and 0.1 <= clock.sleeps[0] <= 0.4  # a short jittered pause
    assert response.latency_ms == round(clock.total_slept * 1000)


async def test_a_5xx_twice_is_a_server_error(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(side_effect=[httpx.Response(503), httpx.Response(502, text="bad gateway")])
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "server"
    assert caught.value.status == 502
    assert caught.value.retryable
    assert route.call_count == 2


async def test_max_retries_zero_means_a_single_attempt(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json=PLAYGROUND_BODY)]
    )
    with pytest.raises(JevError) as caught:
        await ask(make_client(max_retries=0))
    assert caught.value.kind == "server"
    assert route.call_count == 1


async def test_more_retries_are_honoured(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(500), httpx.Response(200, json=PLAYGROUND_BODY)]
    )
    assert (await ask(make_client(max_retries=2))).request_id == "req_8f3a1c"
    assert route.call_count == 3


# ───────────────────────── transport failures ─────────────────────────


async def test_a_timeout_is_retried_and_the_second_attempt_can_succeed(router: respx.MockRouter) -> None:
    clock = FakeClock()
    route = router.post(URL).mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json=PLAYGROUND_BODY)]
    )
    response = await ask(make_client(clock=clock))
    assert route.call_count == 2
    assert response.answers["public_proof"].noul == 0.93
    assert len(clock.sleeps) == 1


async def test_two_timeouts_are_a_timeout_error(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "timeout"
    assert route.call_count == 2


async def test_connection_errors_are_retried_then_reported(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "connection"
    assert route.call_count == 2


async def test_a_connection_reset_can_recover_on_the_retry(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(
        side_effect=[httpx.ConnectError("reset"), httpx.Response(200, json=PLAYGROUND_BODY)]
    )
    assert (await ask(make_client())).model == "jev-1.13.0"
    assert route.call_count == 2


async def test_an_attempt_that_outlasts_timeout_s_is_cut_off(router: respx.MockRouter) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json=PLAYGROUND_BODY)

    router.post(URL).mock(side_effect=slow)
    with pytest.raises(JevError) as caught:
        await ask(make_client(max_retries=0), timeout_s=0.05)
    assert caught.value.kind == "timeout"
    assert "0.05" in str(caught.value)


# ───────────────────────── malformed responses ─────────────────────────


def _answers_without(qid: str) -> dict[str, Any]:
    return {"answers": {k: v for k, v in PLAYGROUND_BODY["answers"].items() if k != qid}}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json=["answers"]),
        httpx.Response(200, json={"model": "jev-1.13.0"}),
        httpx.Response(200, json={"answers": "none"}),
        httpx.Response(200, json=_answers_without("depth")),
        httpx.Response(
            200, json={"answers": {**PLAYGROUND_BODY["answers"], "public_proof": {"type": "choice"}}}
        ),
        httpx.Response(
            200, json={"answers": {**PLAYGROUND_BODY["answers"], "public_proof": {"noul": "likely"}}}
        ),
        httpx.Response(200, json={"answers": {**PLAYGROUND_BODY["answers"], "depth": {"type": "score"}}}),
        httpx.Response(301, headers={"location": "https://elsewhere.test"}),
    ],
    ids=[
        "html",
        "list",
        "no-answers",
        "answers-not-object",
        "missing-answer",
        "wrong-type",
        "non-numeric-noul",
        "score-without-probabilities",
        "redirect",
    ],
)
async def test_a_malformed_body_is_an_invalid_response_and_is_not_retried(
    router: respx.MockRouter, response: httpx.Response
) -> None:
    route = router.post(URL).mock(return_value=response)
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "invalid_response"
    assert not caught.value.retryable
    assert route.call_count == 1


async def test_an_invalid_answer_keeps_the_request_id(router: respx.MockRouter) -> None:
    body = {**PLAYGROUND_BODY, "answers": {**PLAYGROUND_BODY["answers"], "public_proof": {"noul": "likely"}}}
    router.post(URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.request_id == "req_8f3a1c"


# ───────────────────────── disabled ─────────────────────────


async def test_no_key_means_disabled_and_nothing_is_sent(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    for key in (None, "", "   "):
        client = make_client(key=key)
        assert not client.enabled
        with pytest.raises(JevError) as caught:
            await ask(client)
        assert caught.value.kind == "disabled"
        assert not caught.value.retryable
    assert route.call_count == 0


async def test_the_kill_switch_means_disabled_even_with_a_key(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    client = make_client(enabled=False)
    assert not client.enabled
    with pytest.raises(JevError) as caught:
        await ask(client)
    assert caught.value.kind == "disabled"
    assert route.call_count == 0


async def test_an_empty_question_set_is_a_bad_request_without_a_call(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    with pytest.raises(JevError) as caught:
        await ask(make_client(), questions={})
    assert caught.value.kind == "bad_request"
    assert route.call_count == 0


async def test_a_state_that_is_not_json_is_a_bad_request_without_a_call(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    with pytest.raises(JevError) as caught:
        await ask(make_client(), {"when": object()})
    assert caught.value.kind == "bad_request"
    assert route.call_count == 0


# ───────────────────────── transport hardening ─────────────────────────


class RecordingLog:
    """Stands in for the module's structlog logger."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _add(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    info = warning = error = _add

    def named(self, event: str) -> list[dict[str, Any]]:
        return [fields for name, fields in self.events if name == event]


@pytest.fixture
def recorded_log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    stub = RecordingLog()
    monkeypatch.setattr(jev_http, "log", stub)
    return stub


async def test_the_environment_proxy_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """#21: the key must never be sent through a proxy that merely happens to be set in the environment."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")

    control = httpx.AsyncClient()  # the default (trust_env=True) really would pick the proxy up
    assert control._mounts
    await control.aclose()

    client = make_client()
    assert client._http._mounts == {}
    assert client._http.trust_env is False
    await client.aclose()


async def test_the_request_asks_for_an_uncompressed_answer(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    await ask(make_client())
    assert route.calls[0].request.headers["accept-encoding"] == "identity"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.typesafe.ai",
        "http://localhost.evil.test",
        "http://127.0.0.1.evil.test",
        "http://user@localhost.evil.test",
        "ftp://api.typesafe.ai",
        "api.typesafe.ai",
        "//api.typesafe.ai",
        "https://",
        "",
    ],
)
async def test_a_base_url_that_is_not_https_is_refused_and_nothing_is_sent(
    router: respx.MockRouter, recorded_log: RecordingLog, base_url: str
) -> None:
    route = router.route().mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    client = make_client(base_url=base_url)
    assert not client.enabled
    with pytest.raises(JevError) as caught:
        await ask(client)
    assert caught.value.kind == "disabled" and not caught.value.retryable
    assert "jev.base_url" in str(caught.value)
    assert route.call_count == 0
    (logged,) = recorded_log.named("jev_base_url_refused")
    assert "jev.base_url" in logged["reason"]
    assert KEY not in json.dumps(logged)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8080",
        "http://127.0.0.1:9000/",
        "http://[::1]:9000",
        "http://LOCALHOST",
        "https://jev.example.test/",
    ],
)
async def test_https_and_plain_http_to_this_machine_are_accepted(
    router: respx.MockRouter, recorded_log: RecordingLog, base_url: str
) -> None:
    route = router.route().mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    client = make_client(base_url=base_url)
    assert client.enabled
    assert (await ask(client)).request_id == "req_8f3a1c"
    assert route.call_count == 1
    assert recorded_log.named("jev_base_url_refused") == []


def test_base_url_problem_names_the_reason_without_the_credentials_in_the_url() -> None:
    assert base_url_problem("https://api.typesafe.ai") is None
    assert base_url_problem("http://localhost") is None
    reason = base_url_problem("http://user:hunter2@api.typesafe.ai/x")
    assert reason is not None and "hunter2" not in reason and "api.typesafe.ai" in reason
    assert base_url_problem("http://[::1") is not None  # unparseable


def _padded(size: int) -> bytes:
    """A valid Jev answer of exactly ``size`` bytes."""
    base = json.dumps({**PLAYGROUND_BODY, "pad": ""}, separators=(",", ":")).encode()
    return json.dumps({**PLAYGROUND_BODY, "pad": "x" * (size - len(base))}, separators=(",", ":")).encode()


async def test_an_answer_of_exactly_the_cap_is_accepted(router: respx.MockRouter) -> None:
    body = _padded(MAX_RESPONSE_BYTES)
    assert len(body) == MAX_RESPONSE_BYTES == 1024 * 1024
    router.post(URL).mock(return_value=httpx.Response(200, content=body))
    assert (await ask(make_client())).request_id == "req_8f3a1c"


async def test_a_success_body_over_the_cap_is_an_invalid_response_and_is_not_retried(
    router: respx.MockRouter,
) -> None:
    route = router.post(URL).mock(
        return_value=httpx.Response(
            200, content=_padded(MAX_RESPONSE_BYTES + 1), headers={"x-request-id": "big-1"}
        )
    )
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "invalid_response" and not caught.value.retryable
    assert caught.value.request_id == "big-1" and caught.value.status == 200
    assert "larger than" in str(caught.value)
    assert route.call_count == 1


async def test_the_body_is_not_read_past_the_cap(router: respx.MockRouter) -> None:
    """A server that never stops sending is cut off after the cap, not drained."""
    pulled = 0
    chunk = b"x" * 65_536

    async def endless() -> AsyncIterator[bytes]:
        nonlocal pulled
        while True:
            pulled += 1
            yield chunk

    router.post(URL).mock(return_value=httpx.Response(200, content=endless()))
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "invalid_response"
    assert MAX_RESPONSE_BYTES // len(chunk) <= pulled <= MAX_RESPONSE_BYTES // len(chunk) + 2


async def test_a_declared_length_over_the_cap_is_refused_before_the_body_is_read(
    router: respx.MockRouter,
) -> None:
    pulled = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal pulled
        pulled += 1
        yield b"{}"

    router.post(URL).mock(
        return_value=httpx.Response(
            200, content=body(), headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}
        )
    )
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "invalid_response"
    assert pulled == 0


async def test_a_compressed_bomb_is_stopped_by_the_cap_on_the_decoded_size(
    router: respx.MockRouter,
) -> None:
    bomb = gzip.compress(b"0" * (8 * MAX_RESPONSE_BYTES))  # about 8 KB on the wire
    assert len(bomb) < 20_000
    route = router.post(URL).mock(
        return_value=httpx.Response(200, content=bomb, headers={"content-encoding": "gzip"})
    )
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "invalid_response"
    assert route.call_count == 1


async def test_a_compressed_answer_within_the_cap_still_works(router: respx.MockRouter) -> None:
    packed = gzip.compress(json.dumps(PLAYGROUND_BODY).encode())
    router.post(URL).mock(
        return_value=httpx.Response(200, content=packed, headers={"content-encoding": "gzip"})
    )
    assert (await ask(make_client())).request_id == "req_8f3a1c"


async def test_an_error_body_over_the_cap_is_truncated_not_fatal(router: respx.MockRouter) -> None:
    """The status decides the error kind; only the first characters of the body are ever looked at."""
    huge = b'{"error":{"message":"' + b"y" * (3 * MAX_RESPONSE_BYTES) + b'"}}'
    route = router.post(URL).mock(return_value=httpx.Response(400, content=huge))
    with pytest.raises(JevError) as caught:
        await ask(make_client())
    assert caught.value.kind == "bad_request" and caught.value.status == 400
    assert len(str(caught.value)) < 300
    assert route.call_count == 1


async def test_a_5xx_with_a_huge_body_is_still_retried(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(
        side_effect=[
            httpx.Response(502, content=b"z" * (2 * MAX_RESPONSE_BYTES)),
            httpx.Response(200, json=PLAYGROUND_BODY),
        ]
    )
    assert (await ask(make_client())).request_id == "req_8f3a1c"
    assert route.call_count == 2


async def test_a_slow_body_is_cut_off_by_the_attempt_deadline(router: respx.MockRouter) -> None:
    async def trickle() -> AsyncIterator[bytes]:
        yield b"{"
        await asyncio.sleep(2)
        yield b"}"

    router.post(URL).mock(return_value=httpx.Response(200, content=trickle()))
    with pytest.raises(JevError) as caught:
        await ask(make_client(max_retries=0), timeout_s=0.05)
    assert caught.value.kind == "timeout"


# ───────────────────────── privacy ─────────────────────────

RESUME = (
    "Jane Doe\n"
    "Contact: jane.doe@example.com | +44 20 7946 0958\n"
    "Address: 12 Baker Street, London\n"
    "Designed a design system used by 40 teams between 2019 and 2022."
)
STATE = {"resume": RESUME, "notes": ["Call me on (415) 555-2671", "nothing sensitive"], "n": 3, "flag": True}


async def test_emails_phones_and_addresses_are_masked_in_the_sent_state(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    response = await ask(make_client(), STATE)

    state = sent(route)["state"]
    assert "jane.doe@example.com" not in json.dumps(state)
    assert "7946" not in json.dumps(state)
    assert "555-2671" not in json.dumps(state)
    assert "Baker Street" not in json.dumps(state)
    assert "[email]" in state["resume"] and "[phone]" in state["resume"] and "[address]" in state["resume"]
    assert "[phone]" in state["notes"][0]
    # everything else is left alone, including keys that the log redactor would blank
    assert set(state) == {"resume", "notes", "n", "flag"}
    assert "Designed a design system used by 40 teams between 2019 and 2022." in state["resume"]
    assert state["notes"][1] == "nothing sensitive" and state["n"] == 3 and state["flag"] is True
    assert response.request_json["state"] == state  # the logged request is what was sent


async def test_send_pii_true_sends_the_state_untouched(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    await ask(make_client(send_pii=True), STATE)
    assert sent(route)["state"] == STATE


async def test_a_plain_string_state_is_redacted_too(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    await ask(make_client(), "mail me: a.b@c.io")
    assert sent(route)["state"] == "mail me: [email]"


async def test_the_callers_state_is_never_mutated(router: respx.MockRouter) -> None:
    router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    state = {"resume": RESUME, "nested": {"items": [RESUME]}}
    before = json.dumps(state)
    await ask(make_client(), state)
    assert json.dumps(state) == before


@pytest.mark.parametrize("send_pii", [False, True])
async def test_lone_surrogates_are_replaced_so_the_body_is_valid(
    router: respx.MockRouter, send_pii: bool
) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    await ask(make_client(send_pii=send_pii), {"resume": "broken \ud800 text"})
    assert sent(route)["state"] == {"resume": "broken ? text"}


def test_prepare_state_is_idempotent_and_leaves_keys_alone() -> None:
    once = prepare_state(STATE, redact=True)
    assert prepare_state(once, redact=True) == once
    assert isinstance(once, dict) and "resume" in once
    assert prepare_state(STATE, redact=False) == STATE


# ───────────────────────── lifecycle and secrets ─────────────────────────


async def test_aclose_closes_a_client_it_created_but_not_one_it_was_given() -> None:
    owned = make_client()
    await owned.aclose()
    assert owned._http.is_closed

    http = httpx.AsyncClient()
    borrowed = make_client(http=http)
    await borrowed.aclose()
    assert not http.is_closed
    await http.aclose()


async def test_the_key_never_appears_in_errors_or_repr(router: respx.MockRouter) -> None:
    router.post(URL).mock(return_value=httpx.Response(401, json={"error": "invalid key"}))
    client = make_client()
    assert KEY not in repr(client)
    with pytest.raises(JevError) as caught:
        await ask(client)
    assert KEY not in str(caught.value) and KEY not in repr(caught.value)


async def test_an_injected_client_is_used_for_the_request(router: respx.MockRouter) -> None:
    route = router.post(URL).mock(return_value=httpx.Response(200, json=PLAYGROUND_BODY))
    async with httpx.AsyncClient() as http:
        response = await ask(make_client(http=http))
    assert response.model == "jev-1.13.0"
    assert route.call_count == 1
