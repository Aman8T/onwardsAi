"""FastAPI's own OpenTelemetry, as `api.app.TELEMETRY` configures it: the API only, no sign-in secrets, no unredacted
error logs."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.telemetry import TelemetryConfig
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from tayyari.api.app import TELEMETRY, _skip_telemetry
from tayyari.api.errors import install_error_handlers


@pytest.mark.parametrize(
    ("path", "skipped"),
    [
        ("/", True),
        ("/assets/index-3f9a.js", True),
        ("/practice", True),
        ("/api/health", True),
        ("/api/health/deep", True),
        ("/api/auth/magic/verify", True),
        ("/api/auth/google/callback", True),
        ("/api/auth/magic", False),
        ("/api/interview/sessions", False),
        ("/api/speech/speak", False),
    ],
)
def test_only_the_api_is_measured_and_never_a_sign_in_link(path: str, skipped: bool) -> None:
    assert _skip_telemetry({"type": "http", "path": path}) is skipped


def _app(config: TelemetryConfig) -> FastAPI:
    app = FastAPI(telemetry=config)
    install_error_handlers(app)

    @app.get("/api/thing")
    async def thing() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/boom")
    async def boom() -> None:
        raise RuntimeError("resume text that must never leave the redacted log")

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/app.js")
    async def asset() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _get(app: FastAPI, *paths: str) -> list[int]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return [(await client.get(path)).status_code for path in paths]


async def test_api_requests_are_traced_and_the_rest_is_not() -> None:
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))

    statuses = await _get(
        _app({**TELEMETRY, "tracer_provider": tracer_provider}), "/api/thing", "/api/health", "/app.js"
    )

    assert statuses == [200, 200, 200]
    server = [s.name for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]
    assert server == ["GET /api/thing"]


def _log_export() -> tuple[InMemoryLogRecordExporter, LoggerProvider]:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    return exporter, provider


async def test_unhandled_errors_are_not_exported_as_logs() -> None:
    exporter, provider = _log_export()

    statuses = await _get(_app({**TELEMETRY, "logger_provider": provider}), "/api/boom")

    assert statuses == [500]
    assert exporter.get_finished_logs() == ()


async def test_the_log_check_fires_when_logs_are_on() -> None:
    # The known-bad case: FastAPI's default would export the exception, message included.
    exporter, provider = _log_export()

    statuses = await _get(_app({**TELEMETRY, "logs": True, "logger_provider": provider}), "/api/boom")

    assert statuses == [500]
    exported = exporter.get_finished_logs()
    assert len(exported) == 1
    assert "resume text" in str(exported[0].log_record.attributes)
