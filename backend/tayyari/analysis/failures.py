"""Turning exceptions into the sentences the person sees (replaces the legacy regex humaniser).

Typed errors carry their own `user_message` (`llm.errors`, `core.errors`); nothing here parses English. An
exception we do not know is never shown as text, because its message can hold paths, provider output or
content: it becomes one generic sentence and the traceback goes to the log.
"""

from __future__ import annotations

import asyncio

from tayyari.core.errors import AppError
from tayyari.core.limits import HUMAN_ERROR_MAX_CHARS, READER_ERROR_LINE_MAX_CHARS
from tayyari.core.text import clip_words
from tayyari.llm.errors import (
    AUTH_MESSAGE,
    NETWORK_MESSAGE,
    ClaudeAuthError,
    ClaudeError,
    ClaudeNotConfigured,
    SpendCeilingReached,
)

GENERIC_MESSAGE = "Something went wrong."
CANCELLED_MESSAGE = "Cancelled."
READER_AUTH_LINE = "AI service unavailable · skipped"
"""The one-line reason under a reader that could not start because the server's key is missing or invalid."""


def user_message(err: BaseException | None) -> str:
    """One short sentence for any failure: the typed message, ``Cancelled.``, a network sentence for a timeout."""
    if isinstance(err, asyncio.CancelledError):
        return CANCELLED_MESSAGE
    if isinstance(err, ClaudeError | AppError):
        return clip_words(err.user_message, HUMAN_ERROR_MAX_CHARS)
    if isinstance(err, TimeoutError):
        return NETWORK_MESSAGE
    return GENERIC_MESSAGE


def is_auth_error(err: BaseException | None) -> bool:
    """The server's Anthropic key is missing, invalid, not allowed, or the account has a billing problem."""
    return isinstance(err, ClaudeAuthError | ClaudeNotConfigured)


def is_spend_error(err: BaseException | None) -> bool:
    """The daily spend ceiling stopped the call."""
    return isinstance(err, SpendCeilingReached)


def reader_error_line(err: BaseException) -> str:
    """The short line under a failed reader card (at most `READER_ERROR_LINE_MAX_CHARS` characters)."""
    if is_auth_error(err) and user_message(err) == AUTH_MESSAGE:
        return READER_AUTH_LINE
    text = user_message(err)
    return clip_words(text[:-1] if text.endswith(".") else text, READER_ERROR_LINE_MAX_CHARS)


__all__ = [
    "CANCELLED_MESSAGE",
    "GENERIC_MESSAGE",
    "READER_AUTH_LINE",
    "is_auth_error",
    "is_spend_error",
    "reader_error_line",
    "user_message",
]
