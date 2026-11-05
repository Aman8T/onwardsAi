"""Gemini Computer Use behind `BrowserAgent`, over plain REST (``generateContent`` with the ``computer_use`` tool).

Shape implemented (Gemini API docs for ``gemini-2.5-computer-use-preview-10-2025``; the docs could not be fetched from the
build environment, so check them before relying on this): the tool ``{"computer_use": {"environment": "ENVIRONMENT_BROWSER"}}``,
the model's ``functionCall`` parts (``click_at``, ``type_text_at``, ``scroll_document``, ``scroll_at``, ``key_combination``,
``hover_at``, ``navigate``, ``wait_5_seconds``, ...) with x/y on a 0-999 grid, and our ``functionResponse`` with the page URL
and a new screenshot. A ``safety_decision`` of ``require_confirmation`` is never acknowledged by us: the run stops and the
person is asked. ``report_done`` and ``report_blocked`` are our own functions; only the last three screenshots stay in the
history. Costs use preview prices (USD per million tokens, input 1.25, output 10.0) until checked.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx2

from tayyari.apply.agent import AgentOutcome, Guarded

PRICE_IN, PRICE_OUT = 1.25, 10.0
KEEP_SHOTS = 3
EXCLUDED = ["open_web_browser", "search", "go_forward", "go_back", "drag_and_drop"]
OWN_FUNCTIONS = [
    {"name": "report_done", "description": "Call when every field you can fill is filled.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}}},
    {"name": "report_blocked", "description": "Call on a CAPTCHA, a login or an account wall.",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
]  # fmt: skip


def _png(data: bytes) -> dict[str, Any]:
    return {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(data).decode()}}


def _prune(contents: list[dict[str, Any]]) -> None:
    """Keep the newest `KEEP_SHOTS` screenshots; older ones are dropped from the history (the text stays)."""
    seen = 0
    for turn in reversed(contents):
        for part in turn.get("parts", []):
            holders = [part, *list((part.get("functionResponse") or part.get("function_response") or {}).get("parts", []))]
            for holder in holders:
                if "inline_data" in holder or "inlineData" in holder:
                    seen += 1
                    if seen > KEEP_SHOTS:
                        holder.pop("inline_data", None)
                        holder.pop("inlineData", None)
                        holder["text"] = "(older screenshot removed)"


class GeminiAgent:
    name = "gemini"

    def __init__(
        self, api_key: str, model: str, endpoint: str, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._key, self._model, self._endpoint, self._transport = (
            api_key,
            model,
            endpoint.rstrip("/"),
            transport,
        )

    async def run(self, task: str, actions: Guarded, max_steps: int) -> AgentOutcome:
        w, h = actions.viewport()
        contents: list[dict[str, Any]] = [
            {"role": "user", "parts": [{"text": task}, _png(await actions.screenshot())]}
        ]
        tools = [{"computer_use": {"environment": "ENVIRONMENT_BROWSER", "excluded_predefined_functions": EXCLUDED}},
                 {"function_declarations": OWN_FUNCTIONS}]  # fmt: skip
        cost = 0.0
        async with httpx2.AsyncClient(transport=self._transport, timeout=90.0) as client:
            for step in range(1, max_steps + 1):
                res = await client.post(
                    f"{self._endpoint}/models/{self._model}:generateContent",
                    headers={"x-goog-api-key": self._key, "content-type": "application/json"},
                    json={"contents": contents, "tools": tools},
                )
                if res.status_code >= 400:
                    return AgentOutcome("error", step, int(cost), f"Gemini answered {res.status_code}.")
                data = res.json()
                usage = data.get("usageMetadata") or {}
                cost += (
                    usage.get("promptTokenCount", 0) * PRICE_IN
                    + usage.get("candidatesTokenCount", 0) * PRICE_OUT
                )
                content = ((data.get("candidates") or [{}])[0]).get("content") or {
                    "role": "model",
                    "parts": [],
                }
                contents.append(content)
                calls = [p.get("functionCall") or p.get("function_call") for p in content.get("parts", [])]
                calls = [c for c in calls if c]
                if not calls:
                    return AgentOutcome("done", step, int(cost))
                replies = []
                for call in calls:
                    name, args = call.get("name", ""), dict(call.get("args") or {})
                    if (args.get("safety_decision") or {}).get("decision") == "require_confirmation":
                        return AgentOutcome(
                            "needs_user",
                            step,
                            int(cost),
                            "The AI agent asked for a confirmation; finish it yourself.",
                        )
                    if name == "report_done":
                        return AgentOutcome("done", step, int(cost))
                    if name == "report_blocked":
                        return AgentOutcome(
                            "blocked", step, int(cost), str(args.get("reason") or "")[:200] or None
                        )
                    error = await self._act(name, args, actions, w, h)
                    response: dict[str, Any] = {"url": await actions.url()}
                    if error:
                        response["error"] = error
                    replies.append(
                        {
                            "functionResponse": {
                                "name": name,
                                "response": response,
                                "parts": [_png(await actions.screenshot())],
                            }
                        }
                    )
                contents.append({"role": "user", "parts": replies})
                _prune(contents)
        return AgentOutcome("gave_up", max_steps, int(cost))

    async def _act(self, name: str, args: dict[str, Any], actions: Guarded, w: int, h: int) -> str | None:
        def px(key: str, size: int) -> int:
            return int(float(args.get(key, 500)) / 1000 * size)

        if name == "click_at":
            return await actions.click(px("x", w), px("y", h))
        if name == "type_text_at":  # press_enter is never honoured: Enter can submit the form
            return await actions.type(str(args.get("text", "")), px("x", w), px("y", h))
        if name == "key_combination":
            return await actions.key(str(args.get("keys", "")))
        if name in ("scroll_document", "scroll_at"):
            direction, amount = str(args.get("direction", "down")), int(args.get("magnitude", 800))
            dx, dy = {
                "up": (0, -amount),
                "down": (0, amount),
                "left": (-amount, 0),
                "right": (amount, 0),
            }.get(direction, (0, amount))
            x, y = (px("x", w), px("y", h)) if name == "scroll_at" else (w // 2, h // 2)
            await actions.scroll(x, y, dx, dy)
            return None
        if name == "hover_at":
            return None
        if name == "navigate":
            return await actions.navigate(str(args.get("url", "")))
        if name == "wait_5_seconds":
            await actions.wait(5)
            return None
        return f"{name} is not available here."
