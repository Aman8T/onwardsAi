"""Career chat: one streamed turn of a thread.

Claude gets the whole profile (graph + reader findings + raw source excerpts) as its system prompt and
the node the user clicked as the origin. Threads are in-memory sessions (sessionId -> messages): a
follow-up turn replays the earlier user/assistant messages. When a sessionId is unknown (worker
restarted, session evicted) the turn starts a fresh session and the model is told the earlier messages
are no longer available, as the Node server does when the Agent SDK cannot resume a session.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

from .claude import stream_chat
from .config import MODELS
from .pipeline import humanize_error
from .prompts import build_chat_system
from .store import get_context, get_profile

MAX_SESSIONS = 500
"""Threads kept in memory; the least recently used are forgotten first."""
MAX_MESSAGES = 40
"""Messages kept per thread (20 exchanges); older ones fall off the front."""
RESET_NOTE = (
    "(Note: the earlier messages of this conversation are no longer available — answer this on its own, "
    "using the profile.)\n\n"
)

_sessions: OrderedDict[str, list[dict[str, str]]] = OrderedDict()


def _remember(session_id: str, messages: list[dict[str, str]], answer: str) -> None:
    """Keep the thread for the next turn. A turn with no visible text (tools only) is kept as the user
    message alone: the API would reject an empty assistant message, and merges consecutive user turns."""
    turn = [{"role": "assistant", "content": answer}] if answer.strip() else []
    _sessions[session_id] = [*messages, *turn][-MAX_MESSAGES:]
    _sessions.move_to_end(session_id)
    while len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)


async def run_chat(req: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Stream one chat turn as ChatEvent dicts: delta / tool events, then done (with the sessionId) or error.

    `req` is a validated ChatRequest: profileId, message, optional origin (ChatOrigin dict) and sessionId.
    """
    pid = req["profileId"]
    profile = await get_profile(pid)
    if profile is None:
        yield {"type": "error", "message": "Profile not found — run the analysis again"}
        return
    context = await get_context(pid) or {"sources": [], "readers": {}}
    system = build_chat_system(profile, req.get("origin"), context["sources"], context["readers"], budget=60000)

    session_id = req.get("sessionId") or ""
    history = _sessions.get(session_id) if session_id else None
    message = req["message"]
    if session_id and history is None:
        print(f"[chat] session {session_id} could not be resumed, starting fresh", flush=True)
        message = RESET_NOTE + message
    if history is None:
        session_id, history = uuid.uuid4().hex, []
    else:
        _sessions.move_to_end(session_id)
    messages = [*history, {"role": "user", "content": message}]

    # stream_chat reports through callbacks; a queue turns them into the events this generator yields.
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def on_text(text: str) -> None:
        if text:
            queue.put_nowait({"type": "delta", "text": text})

    def on_tool(name: str, detail: str) -> None:
        queue.put_nowait({"type": "tool", "name": name, "detail": detail[:200]})

    async def turn() -> str:
        try:
            return await stream_chat(MODELS["chat"], system, messages, on_text=on_text, on_tool=on_tool)
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(turn())
    try:
        while (event := await queue.get()) is not None:
            yield event
        answer = await task
        _remember(session_id, messages, answer)
        yield {"type": "done", "sessionId": session_id}
    except Exception as exc:
        print(f"[chat] {exc}", flush=True)
        yield {"type": "error", "message": humanize_error(exc)}
    finally:
        if not task.done():  # the client went away mid-turn: stop the model call too
            task.cancel()
            await asyncio.wait([task])
