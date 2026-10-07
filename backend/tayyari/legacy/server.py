"""Tayyari HTTP API.

  POST /api/analyze        multipart (resume?, links, goal?, notes?, sample?) -> NDJSON stream of AnalysisEvent
  POST /api/chat           JSON ChatRequest -> NDJSON stream of ChatEvent
  GET  /api/profile/{id}   a saved Profile
  GET  /api/sample-profile the pre-computed sample Profile (offline demo)
  GET  /api/health         { ok, models, hasSample }

Failures are JSON ``{"error": message}`` bodies before a stream starts and ``{"type": "error"}`` events
once it has. When the web build exists in STATIC_DIR, it is served too, with index.html as the SPA
fallback for every non-/api path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from starlette.types import Receive, Scope, Send

from .. import __version__
from .chat import run_chat
from .config import API_KEY, MODELS, STATIC_DIR
from ..ingest.classify import usable_links
from .pipeline import humanize_error, run_analysis
from .store import SAMPLE_PROFILE_PATH, get_profile, has_sample_profile
from .types import AnalyzeInput

MAX_RESUME_BYTES = 10 * 1024 * 1024
MAX_FIELD_BYTES = 256 * 1024
MAX_ANALYZE_BODY_BYTES = MAX_RESUME_BYTES + 1024 * 1024
"""Resume plus the (capped) text fields and multipart framing; anything bigger is refused before parsing."""
MAX_CHAT_BODY_BYTES = 1024 * 1024
ACCEPTED_EXT = {".pdf", ".docx", ".txt", ".md"}
ACCEPTED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
}
HEARTBEAT_S = 15.0
"""Blank lines keep idle proxies from closing long, quiet streams; the client skips them."""
POLL_S = 1.0
"""How often a quiet stream checks whether the client is still there."""

Event = dict[str, Any]


# ───────────────────────── NDJSON streaming ─────────────────────────


async def _pump(events: AsyncIterator[Event], queue: asyncio.Queue[bytes | None], tag: str) -> None:
    """Serialise `events` into `queue`. A crash becomes a final 'error' event unless a terminal event
    (done / error) was already sent; None marks the end."""
    terminal = False
    try:
        async for event in events:
            terminal = terminal or event.get("type") in ("done", "error")
            queue.put_nowait(f"{json.dumps(event, ensure_ascii=False)}\n".encode())
    except Exception as exc:
        print(f"[{tag}] {exc}", flush=True)
        if not terminal:
            error = {"type": "error", "message": humanize_error(exc)}
            queue.put_nowait(f"{json.dumps(error, ensure_ascii=False)}\n".encode())
    finally:
        queue.put_nowait(None)


class NdjsonResponse(StreamingResponse):
    """One JSON object per line, flushed as produced, with blank-line heartbeats while quiet.

    The producer behind `events` (the pipeline or a model call) is cancelled as soon as the client goes
    away, however that is noticed: a failed send, a disconnect seen while the stream is quiet, or the
    server cancelling the task. A client hanging up is logged like Node's '[tag] cancelled by client',
    never as an error.
    """

    def __init__(self, request: Request, events: AsyncIterator[Event], tag: str) -> None:
        self.request, self.events, self.tag = request, events, tag
        self.finished = False
        self._lines = self._body()
        super().__init__(
            self._lines,
            media_type="application/x-ndjson; charset=utf-8",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except (ClientDisconnect, OSError):  # a send failed: the client hung up mid-stream
            if not self.finished:
                self._cancelled()
        finally:
            await self._lines.aclose()

    def _cancelled(self) -> None:
        print(f"[{self.tag}] cancelled by client", flush=True)

    async def _body(self) -> AsyncGenerator[bytes, None]:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        producer = asyncio.create_task(_pump(self.events, queue, self.tag))
        quiet = 0.0
        try:
            while True:
                try:
                    async with asyncio.timeout(POLL_S):
                        line = await queue.get()
                except TimeoutError:
                    if await self.request.is_disconnected():
                        self.finished = True
                        self._cancelled()
                        return
                    quiet += POLL_S
                    if quiet >= HEARTBEAT_S:
                        quiet = 0.0
                        yield b"\n"
                    continue
                if line is None:
                    self.finished = True
                    return
                quiet = 0.0
                yield line
        except asyncio.CancelledError:
            self._cancelled()
            raise
        finally:
            if not producer.done():
                producer.cancel()
                await asyncio.wait([producer])


# ───────────────────────── request parsing ─────────────────────────


def _parse_links(values: list[str]) -> list[str]:
    """The `links` field(s): a JSON array string, or whitespace/comma separated URLs. At most 20, 500 chars each."""
    out: list[str] = []
    for value in values:
        if not value.strip():
            continue
        try:
            parsed: Any = json.loads(value)
        except ValueError:
            parsed = value.replace(",", " ").split()
        items = parsed if isinstance(parsed, list) else [parsed]
        out.extend(item.strip()[:500] for item in items if isinstance(item, str) and item.strip())
    return out[:20]


def _field(value: object, max_len: int) -> str | None:
    return value.strip()[:max_len] if isinstance(value, str) and value.strip() else None


def _accepted(upload: UploadFile) -> bool:
    ext = Path(upload.filename or "").suffix.lower()
    return ext in ACCEPTED_EXT or (not ext and (upload.content_type or "") in ACCEPTED_MIME)


async def _read_resume(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(1 << 20):
        size += len(chunk)
        if size > MAX_RESUME_BYTES:
            raise HTTPException(413, "That file is larger than 10 MB.")
        chunks.append(chunk)
    return b"".join(chunks)


def _check_length(request: Request, limit: int) -> None:
    """Refuse an oversized body from its Content-Length, before any of it is read or spooled."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise HTTPException(413, "Request body too large")


async def _json_body(request: Request, limit: int) -> Any:
    _check_length(request, limit)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "Request body too large")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except ValueError:
        raise HTTPException(400, "Invalid JSON body") from None


class ChatOriginBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["skill", "domain", "source", "role", "requirement", "you", "free"]
    refId: str | None = Field(default=None, max_length=200)
    title: str = Field(default="", max_length=200)
    caption: str = Field(default="", max_length=300)


class ChatBody(BaseModel):
    """ChatRequest from shared/types.ts, with the same limits as the Node server."""

    model_config = ConfigDict(extra="ignore")

    profileId: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    origin: ChatOriginBody | None = None
    sessionId: str | None = Field(default=None, max_length=200)


def _describe(err: ValidationError) -> str:
    first = err.errors()[0]
    path = ".".join(str(p) for p in first["loc"]) or "body"
    return f"Invalid chat request: {path} {first['msg']}"


# ───────────────────────── app ─────────────────────────


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    print(
        f"Tayyari API {__version__}  (reader {MODELS['reader']} · synth {MODELS['synth']} · chat {MODELS['chat']})",
        flush=True,
    )
    if (STATIC_DIR / "index.html").is_file():
        print(f"Serving the web app from {STATIC_DIR}", flush=True)
    if not API_KEY:
        print("[server] ANTHROPIC_API_KEY is not set: analysis and chat will fail until it is.", flush=True)
    yield


app = FastAPI(
    title="Tayyari API", version=__version__, lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None
)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(ClientDisconnect)
async def _client_gone(_: Request, __: ClientDisconnect) -> Response:
    # The client left while its request body was still being read: nothing to answer, nothing to log.
    return Response(status_code=499)


@app.exception_handler(Exception)
async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
    # The server also logs the traceback; the client only learns that it was not their fault.
    return JSONResponse({"error": "Something went wrong on the server."}, status_code=500)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "models": MODELS, "hasSample": has_sample_profile()}


@app.post("/api/analyze")
async def analyze(request: Request) -> Response:
    _check_length(request, MAX_ANALYZE_BODY_BYTES)
    form = await request.form(max_files=1, max_fields=20, max_part_size=MAX_FIELD_BYTES)
    try:
        sample = form.get("sample") in ("1", "true")
        links = _parse_links([v for v in form.getlist("links") if isinstance(v, str)])
        goal = _field(form.get("goal"), 300)
        notes = _field(form.get("notes"), 20000)
        upload = form.get("resume")
        if not isinstance(upload, UploadFile):
            upload = None
        elif not _accepted(upload):
            raise HTTPException(400, "Upload your resume as a PDF, DOCX, TXT or Markdown file.")
        # In sample mode the fixtures replace whatever was uploaded.
        resume = await _read_resume(upload) if upload and not sample else None
    finally:
        await form.close()

    if not sample and resume is None and not usable_links(links) and not notes:
        raise HTTPException(
            400,
            "None of those links look like valid web addresses. Add a resume or a full URL such as github.com/you."
            if links
            else "Add a resume, a link or some notes to analyse.",
        )

    inp = AnalyzeInput(
        resume_bytes=resume,
        resume_name=upload.filename if upload and resume is not None else None,
        links=links,
        goal=goal,
        notes=notes,
        sample=sample,
    )
    return NdjsonResponse(request, run_analysis(inp), "analyze")


@app.post("/api/chat")
async def chat(request: Request) -> Response:
    body = await _json_body(request, MAX_CHAT_BODY_BYTES)
    try:
        req = ChatBody.model_validate(body)
    except ValidationError as err:
        raise HTTPException(400, _describe(err)) from None
    return NdjsonResponse(request, run_chat(req.model_dump()), "chat")


@app.get("/api/profile/{pid}")
async def profile(pid: str) -> JSONResponse:
    found = await get_profile(pid)
    if found is None:
        raise HTTPException(404, "Profile not found")
    return JSONResponse(found)


@app.get("/api/sample-profile")
async def sample_profile() -> FileResponse:
    if not has_sample_profile():
        raise HTTPException(404, 'No pre-computed sample profile yet. Run "npm run make-sample" on the server.')
    return FileResponse(SAMPLE_PROFILE_PATH, media_type="application/json", headers={"Cache-Control": "no-cache"})


@app.api_route("/api/{_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def api_not_found(_path: str) -> Response:
    raise HTTPException(404, "Not found")


@app.api_route("/{path:path}", methods=["GET", "HEAD"])
async def web_app(path: str) -> Response:
    """The built web app: a real file under STATIC_DIR (cached an hour, the assets are content-hashed),
    otherwise index.html (never cached) so client-side routes deep-link."""
    index = STATIC_DIR / "index.html"
    if path == "api" or not index.is_file():  # the /api/... catch-all above does not see the bare prefix
        raise HTTPException(404, "Not found")
    target = (STATIC_DIR / path).resolve()
    if path and target.is_relative_to(STATIC_DIR.resolve()) and target != index.resolve() and target.is_file():
        return FileResponse(target, headers={"Cache-Control": "public, max-age=3600"})
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
