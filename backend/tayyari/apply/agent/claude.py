"""Claude computer use behind `BrowserAgent`: the ``computer_toolset_20260801`` toolset (Claude 5.5 models; no beta header).

Claude's calls are ``tool_use`` blocks named after the member (``screenshot``, ``left_click``, ``type``, ``key``, ``scroll``,
...) with ``toolset_name: "computer"``, several per turn; each gets one ``tool_result`` echoing ``toolset_name``, in order, and
after the first failure the rest get "Not executed: an earlier computer action in this turn failed." (the API's batch rule).
Coordinates are pixels of the screenshots we return (1280x800 by default, inside the 2576 px limit). Zoom is switched off.
``report_done`` / ``report_blocked`` are our own tools. The response content is passed back unchanged (thinking included).
This adapter calls the Anthropic SDK directly (the shared `ClaudeClient` has no tool loop); its cost is counted in the run.
"""

from __future__ import annotations

import base64
from typing import Any

from tayyari.apply.agent import AgentOutcome, Guarded

PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.0, 20.0)}
NOT_EXECUTED = "Not executed: an earlier computer action in this turn failed."
OWN_TOOLS = [
    {"name": "report_done", "description": "Call when every field you can fill is filled.",
     "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}}}},
    {"name": "report_blocked", "description": "Call on a CAPTCHA, a login or an account wall.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
]  # fmt: skip


def anthropic_client(api_key: str) -> Any:
    import anthropic

    return anthropic.AsyncAnthropic(api_key=api_key)


def _image(data: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode()},
    }


def _dump(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    dumped = block.model_dump(exclude_none=True)
    return dict(dumped) if isinstance(dumped, dict) else {}


class ClaudeAgent:
    name = "claude"

    def __init__(self, client: Any, model: str) -> None:
        self._client, self._model = client, model

    async def run(self, task: str, actions: Guarded, max_steps: int) -> AgentOutcome:
        p_in, p_out = PRICES.get(self._model, (2.0, 10.0))
        tools = [{"type": "computer_toolset_20260801", "configs": {"zoom": {"enabled": False}}}, *OWN_TOOLS]
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "text", "text": task}, _image(await actions.screenshot())]}
        ]
        cost = 0.0
        for step in range(1, max_steps + 1):
            res = await self._client.messages.create(
                model=self._model, max_tokens=4096, tools=tools, messages=messages
            )
            usage = getattr(res, "usage", None)
            if usage is not None:
                cost += getattr(usage, "input_tokens", 0) * p_in + getattr(usage, "output_tokens", 0) * p_out
            content = [_dump(b) for b in res.content]
            messages.append({"role": "assistant", "content": content})
            uses = [b for b in content if b.get("type") == "tool_use"]
            if not uses:
                return AgentOutcome("done", step, int(cost))
            results: list[dict[str, Any]] = []
            failed = False
            for b in uses:
                name, args, toolset = b.get("name", ""), dict(b.get("input") or {}), b.get("toolset_name")
                if not toolset and name == "report_done":
                    return AgentOutcome("done", step, int(cost))
                if not toolset and name == "report_blocked":
                    return AgentOutcome(
                        "blocked", step, int(cost), str(args.get("reason") or "")[:200] or None
                    )
                result: dict[str, Any] = {"type": "tool_result", "tool_use_id": b.get("id")}
                if toolset:
                    result["toolset_name"] = toolset
                if failed:
                    results.append({**result, "content": NOT_EXECUTED, "is_error": True})
                    continue
                error, image = await self._act(name, args, actions)
                if error:
                    failed = True
                    results.append({**result, "content": f"Error: {error}", "is_error": True})
                else:
                    results.append(
                        {**result, "content": [image] if image else [{"type": "text", "text": "OK"}]}
                    )
            messages.append({"role": "user", "content": results})
        return AgentOutcome("gave_up", max_steps, int(cost))

    async def _act(
        self, name: str, args: dict[str, Any], actions: Guarded
    ) -> tuple[str | None, dict[str, Any] | None]:
        xy = args.get("coordinate") or [None, None]
        x, y = (int(xy[0]), int(xy[1])) if xy[0] is not None else (None, None)
        if name == "screenshot":
            return None, _image(await actions.screenshot())
        if name in ("left_click", "double_click", "triple_click"):
            if x is None or y is None:
                return "A click needs a coordinate.", None
            return await actions.click(x, y), None
        if name == "type":
            return await actions.type(str(args.get("text", ""))), None
        if name == "key":
            return await actions.key(str(args.get("text", ""))), None
        if name == "scroll":
            amount = int(args.get("scroll_amount", 3)) * 100
            dx, dy = {
                "up": (0, -amount),
                "down": (0, amount),
                "left": (-amount, 0),
                "right": (amount, 0),
            }.get(str(args.get("scroll_direction")), (0, amount))
            w, h = actions.viewport()
            await actions.scroll(x if x is not None else w // 2, y if y is not None else h // 2, dx, dy)
            return None, None
        if name == "mouse_move":
            return None, None
        if name == "wait":
            await actions.wait(float(args.get("duration", 1)))
            return None, None
        if name == "cursor_position":
            return None, {"type": "text", "text": "X=0, Y=0"}
        return f"{name} is not available here.", None
