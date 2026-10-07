"""The Jev transport: one raw httpx adapter for ``POST {base_url}/v1/systemone``.

It implements `tayyari.llm.jev.JevClient`. There is deliberately no SDK: the TypeSafe SDK pulls in a second
HTTP stack and logs request bodies at debug level, which here would mean resume text. An SDK adapter would be
one more file implementing the same Protocol.

Behaviour:
  - Request: ``{"state", "model", "questions": {id: question.to_wire()}}`` with ``Authorization: Bearer <key>``.
    `JevResponse.request_json` is exactly the JSON that was sent (after redaction), so a stored request can be
    replayed without the adapter.
  - Privacy: unless ``settings.send_pii`` is on, every string in the state goes through `core.redact.redact_pii`
    first (emails, phones and address lines become ``[email]``, ``[phone]``, ``[address]``). Lone surrogates
    (a PDF extraction artefact that strict JSON parsers reject) are replaced either way.
  - Time: ``timeout_s`` bounds each attempt (a hard `asyncio.timeout` on top of httpx's per-phase timeouts).
    A timeout, a connection error and a 5xx are retried up to ``settings.max_retries`` times (default one)
    after a short jittered pause on the injected clock, so one call takes at most
    ``(max_retries + 1) * timeout_s`` plus the pauses.
  - Errors, all `JevError`: 401/403 ``auth``, 429 ``rate_limit`` (with ``retry_after_s`` when the header is
    there; never retried), other 4xx ``bad_request``, 5xx ``server``, a body that is not the expected JSON
    ``invalid_response``, and ``disabled`` (nothing is sent) when Jev is switched off or no key is set.
  - Cost: input tokens from the response's ``usage`` (estimated from the request size when it is missing, so
    spend is never undercounted) priced with `jev_cost_micro`.

Bodies are never logged: only the status, the error kind and the request id.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import SecretStr

from ..core.clock import Clock, SystemClock
from ..core.logging import get_logger
from ..core.redact import redact_pii
from ..core.settings import JevSettings
from .errors import JevError, JevErrorKind
from .jev import JevAnswer, JevQuestion, JevResponse, JsonContent, normalise_answer
from .models import jev_cost_micro
from .types import CallContext

log = get_logger(__name__)

ENDPOINT_PATH = "/v1/systemone"
_REQUEST_ID_HEADERS = ("x-typesafe-request-id", "x-request-id", "request-id")
_RETRY_KINDS: frozenset[JevErrorKind] = frozenset({"timeout", "connection", "server"})
_BACKOFF_BASE_S = 0.15
_BACKOFF_JITTER_S = 0.15
_MAX_RETRY_AFTER_S = 3600.0
_DETAIL_CHARS = 200
_BYTES_PER_TOKEN = 4  # rough estimate used only when the API does not report usage


# ───────────────────────── state preparation ─────────────────────────


def _clean_text(text: str, *, redact: bool) -> str:
    if redact:
        text = redact_pii(text)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-8", errors="replace").decode("utf-8")
    return text


def _walk(value: Any, *, redact: bool) -> Any:
    if isinstance(value, str):
        return _clean_text(value, redact=redact)
    if isinstance(value, Mapping):
        return {str(k): _walk(v, redact=redact) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_walk(v, redact=redact) for v in value]
    return value


def prepare_state(state: JsonContent, *, redact: bool) -> JsonContent:
    """A deep copy of ``state`` that is safe to send: lone surrogates replaced, and with ``redact=True`` every
    string leaf (never a key) passed through `redact_pii`. Applying it twice gives the same result."""
    prepared: JsonContent = _walk(state, redact=redact)
    return prepared


# ───────────────────────── response helpers ─────────────────────────


def _retry_after(headers: httpx.Headers) -> float | None:
    """Seconds from ``Retry-After`` (a number or an HTTP date); None when absent or unreadable."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    if math.isnan(seconds):
        return None
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_S)


def _header_request_id(headers: httpx.Headers) -> str | None:
    for name in _REQUEST_ID_HEADERS:
        value: str | None = headers.get(name)
        if value:
            return value[:128]
    return None


def _error_detail(response: httpx.Response) -> str | None:
    """The provider's own error text for logs, clipped. Never the request body."""
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:_DETAIL_CHARS] or None
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            error = error.get("message")
        for candidate in (error, body.get("message"), body.get("detail")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()[:_DETAIL_CHARS]
    return None


def _count(usage: Any, name: str) -> int | None:
    if not isinstance(usage, Mapping):
        return None
    value = usage.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return max(int(value), 0)


# ───────────────────────── the client ─────────────────────────


class HttpJevClient:
    """`JevClient` over raw httpx. See the module docstring for the contract."""

    def __init__(
        self,
        *,
        settings: JevSettings,
        api_key: SecretStr | None,
        http: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        key = api_key.get_secret_value().strip() if api_key is not None else ""
        self._key: str | None = key or None
        self._owns_http = http is None
        self._http: httpx.AsyncClient = (
            http
            if http is not None
            else httpx.AsyncClient(limits=httpx.Limits(max_connections=32, max_keepalive_connections=8))
        )
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._url = settings.base_url.rstrip("/") + ENDPOINT_PATH
        self._rng = random.Random()

    def __repr__(self) -> str:
        return f"HttpJevClient(url={self._url!r}, model={self._settings.model!r}, key={'set' if self._key else 'unset'})"

    @property
    def model_id(self) -> str:
        return self._settings.model

    @property
    def enabled(self) -> bool:
        """True when a call would actually be sent (switched on and a key is present)."""
        return self._settings.enabled and self._key is not None

    async def aclose(self) -> None:
        """Close the connection pool when this adapter created it; a client passed in stays open."""
        if self._owns_http:
            await self._http.aclose()

    async def system_one(
        self,
        state: JsonContent,
        questions: Mapping[str, JevQuestion],
        *,
        model: str | None = None,
        timeout_s: float,
        ctx: CallContext,
    ) -> JevResponse:
        if not self._settings.enabled:
            raise JevError("disabled", "Jev is switched off (TAYYARI_JEV__ENABLED=false)")
        if self._key is None:
            raise JevError("disabled", "no Jev API key is configured")
        if not questions:
            raise JevError("bad_request", "a Jev call needs at least one question")

        requested = model or self._settings.model
        request_json: dict[str, Any] = {
            "state": prepare_state(state, redact=not self._settings.send_pii),
            "model": requested,
            "questions": {qid: q.to_wire() for qid, q in questions.items()},
        }
        try:
            body = json.dumps(request_json, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise JevError("bad_request", f"the request is not JSON-serialisable: {exc}") from exc

        started = self._clock.monotonic()
        attempts = 1 + max(0, self._settings.max_retries)
        for attempt in range(attempts):
            if attempt:
                await self._clock.sleep(_BACKOFF_BASE_S * attempt + self._rng.uniform(0.0, _BACKOFF_JITTER_S))
            try:
                response = await self._send(body, timeout_s, self._key)
                self._raise_for_status(response)
                return self._parse(response, questions, requested, request_json, body, started)
            except JevError as exc:
                if exc.kind not in _RETRY_KINDS or attempt == attempts - 1:
                    raise
                log.warning(
                    "jev_retry",
                    kind=exc.kind,
                    status=exc.status,
                    attempt=attempt + 1,
                    purpose=ctx.purpose,
                    request_id=ctx.request_id,
                )
        raise JevError("server", "unreachable: no attempt was made")  # pragma: no cover

    # ── one attempt ──

    async def _send(self, body: bytes, timeout_s: float, key: str) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "tayyari-backend",
        }
        try:
            async with asyncio.timeout(timeout_s):
                return await self._http.post(
                    self._url, content=body, headers=headers, timeout=httpx.Timeout(timeout_s)
                )
        except TimeoutError as exc:
            raise JevError("timeout", f"Jev did not answer within {timeout_s:g}s") from exc
        except httpx.TimeoutException as exc:
            raise JevError("timeout", f"Jev timed out ({type(exc).__name__})") from exc
        except httpx.DecodingError as exc:
            raise JevError("invalid_response", "the response body could not be decoded") from exc
        except httpx.HTTPError as exc:
            raise JevError("connection", f"could not reach Jev ({type(exc).__name__})") from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        request_id = _header_request_id(response.headers)
        detail = _error_detail(response)
        suffix = f": {detail}" if detail else ""
        kind: JevErrorKind
        retry_after: float | None = None
        if status in (401, 403):
            kind = "auth"
        elif status == 429:
            kind = "rate_limit"
            retry_after = _retry_after(response.headers)
        elif status == 408:
            kind = "timeout"
        elif status >= 500:
            kind = "server"
        elif 400 <= status < 500:
            kind = "bad_request"
        else:
            kind = "invalid_response"
        raise JevError(
            kind,
            f"Jev answered HTTP {status}{suffix}",
            retry_after_s=retry_after,
            request_id=request_id,
            status=status,
        )

    def _parse(
        self,
        response: httpx.Response,
        questions: Mapping[str, JevQuestion],
        requested: str,
        request_json: dict[str, Any],
        body: bytes,
        started: float,
    ) -> JevResponse:
        header_id = _header_request_id(response.headers)

        def invalid(message: str) -> JevError:
            return JevError("invalid_response", message, request_id=header_id, status=response.status_code)

        try:
            payload = response.json()
        except ValueError:
            raise invalid("the response is not JSON") from None
        if not isinstance(payload, dict):
            raise invalid("the response is not a JSON object")
        raw_answers = payload.get("answers")
        if not isinstance(raw_answers, Mapping):
            raise invalid("the response has no 'answers' object")

        body_id = payload.get("request_id")
        request_id = body_id[:128] if isinstance(body_id, str) and body_id else header_id

        answers: dict[str, JevAnswer] = {}
        for qid, question in questions.items():
            raw = raw_answers.get(qid)
            if raw is None:
                raise invalid(f"the response has no answer for question {qid!r}")
            try:
                answers[qid] = normalise_answer(question.kind, raw)
            except JevError as exc:
                exc.request_id = request_id
                exc.status = response.status_code
                raise

        served = payload.get("model")
        model_served = served if isinstance(served, str) and served.strip() else requested
        usage = payload.get("usage")
        input_tokens = _count(usage, "input_tokens")
        if input_tokens is None:
            input_tokens = max(1, len(body) // _BYTES_PER_TOKEN)
        output_tokens = _count(usage, "output_tokens") or 0

        return JevResponse(
            model=model_served,
            answers=answers,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
            latency_ms=round((self._clock.monotonic() - started) * 1000),
            request_json=request_json,
            response_json=payload,
            cost_micro_usd=jev_cost_micro(model_served, input_tokens),
        )


__all__ = ["ENDPOINT_PATH", "HttpJevClient", "prepare_state"]
