"""The work sample's optional assistant: a plain, brief helper, as an interview that allows AI would give one.

One small structured call per message (``{reply}``), with the task, its material, the work as it stands and the earlier
messages as untrusted data. Nothing about the assistant is graded: the debrief only reports, from code, how many things were
asked and whether its code is in the work (`checks.assistant_use`). A model that fails raises: the message is not counted, so
the person can ask again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Final

from tayyari.contract.interview import AssistantTurn, WorkTask
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext, TextBlock
from tayyari.prompts.interview import ASSISTANT_SYSTEM, build_assistant_prompt

ASSISTANT_MAX_TOKENS: Final = 700
REPLY_MAX_CHARS: Final = 2000
EARLIER_TURNS: Final = 6
"""How many earlier exchanges the assistant is shown."""
FALLBACK_REPLY: Final = "I couldn't answer that. Try asking it another way."

ASSISTANT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply"],
    "properties": {"reply": {"type": "string"}},
}


def clean_reply(value: object) -> str:
    """The assistant's reply as stored: trimmed, at most 2,000 characters (cut at a line), never empty."""
    if not isinstance(value, str) or not value.strip():
        return FALLBACK_REPLY
    reply = value.strip()
    if len(reply) <= REPLY_MAX_CHARS:
        return reply
    cut = reply[:REPLY_MAX_CHARS]
    return f"{cut[: cut.rfind(chr(10))] if cut.rfind(chr(10)) > REPLY_MAX_CHARS // 2 else cut}…"


async def ask_assistant(
    *,
    claude: ClaudeClient,
    model: str,
    task: WorkTask,
    work: str,
    earlier: Sequence[AssistantTurn],
    message: str,
    ctx: CallContext,
) -> str:
    """The assistant's reply to ``message``."""
    prompt = build_assistant_prompt(
        task=task.task,
        material_name=task.materialName,
        material=task.material,
        work=work,
        earlier=[(t.you, t.assistant) for t in earlier[-EARLIER_TURNS:]],
        message=message,
    )
    result = await claude.structured(
        model=model,
        system=[TextBlock(ASSISTANT_SYSTEM)],
        user=[TextBlock(prompt)],
        schema=ASSISTANT_SCHEMA,
        max_tokens=ASSISTANT_MAX_TOKENS,
        effort=None,
        ctx=replace(ctx, purpose="interview.assistant"),
    )
    return clean_reply(result.data.get("reply"))


__all__ = ["ASSISTANT_SCHEMA", "FALLBACK_REPLY", "ask_assistant", "clean_reply"]
