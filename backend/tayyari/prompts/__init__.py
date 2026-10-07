"""Prompts and prompt builders with limits from tayyari.core.limits."""

from .chat import ORIGIN_GUIDE, budget_excerpts, build_chat_system
from .common import clip, compact_json, escape_attr
from .reader import READER_FOCUS, READER_SYSTEM, build_reader_prompt
from .synth import SYNTH_SYSTEM, build_synth_prompt

__all__ = [
    "ORIGIN_GUIDE",
    "READER_FOCUS",
    "READER_SYSTEM",
    "SYNTH_SYSTEM",
    "budget_excerpts",
    "build_chat_system",
    "build_reader_prompt",
    "build_synth_prompt",
    "clip",
    "compact_json",
    "escape_attr",
]
