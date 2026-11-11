"""The coach's button: a pure parser, a validator and the stream filter around them.

The coach may end an answer with one final line, ``[[action: <destination> | <target id>]]`` (the target is left out for
a destination that needs none; `tayyari.contract.capabilities` is the list). The app draws a button from it. Nothing here
calls anything or touches storage:

* `TrailerFilter` sits between the model's text deltas and the client. It holds back text from a ``[[`` until it is known
  whether that opens a trailer, so a trailer split across deltas is never half shown, and a ``[[...]]`` that is not an
  action (``[[1]]``) is released as the plain text it is. Whitespace before a possible trailer is held too, so the answer
  does not end on a blank line. Whatever is released is the text that is stored and replayed: the trailer is never in it.
* `validate` turns a parsed trailer into a `Proposal` only when the destination is in the list and the target id (when
  the destination needs one) is one of the ids present in this turn's context (`ActionTargets`). The label comes from the
  list and the title from the resolved target; no model prose is kept.
* Anything else is `Dropped` with a reason. Callers log the reason and the destination, never the text.

Drop reasons: ``multiple`` (two action lines), ``mid_text`` (one that is not at the very end), ``malformed`` (it opens
like a trailer and does not parse or is too long), ``incomplete`` (the stream stopped inside one),
``unknown_destination``, ``unexpected_target`` (a target on a destination that takes none), ``missing_target`` and
``unknown_target``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from tayyari.contract.capabilities import BY_DESTINATION, Destination
from tayyari.core.limits import CHAT_ACTION_TRAILER_MAX_CHARS

HEAD: Final[str] = "[[action:"
OPEN: Final[str] = "[["
CLOSE: Final[str] = "]]"

_BODY = re.compile(
    r"\[\[action:\s*(?P<destination>[A-Za-z0-9_]{1,40})\s*(?:\|\s*(?P<target>[^\[\]|\n]*?)\s*)?\]\]\Z"
)

ActionTargets = Mapping[str, Mapping[str, str | None]]
"""Per target kind (``role``, ``plan``, ``match``, ``tracked``, ``skill``): the ids present in this turn's context, each with
its own title (or None)."""


@dataclass(frozen=True, slots=True)
class Trailer:
    """A parsed ``[[action: ...]]``: the destination as written and the target (None when none was written)."""

    destination: str
    target: str | None


@dataclass(frozen=True, slots=True)
class Dropped:
    """A trailer that is not shown, with why. ``destination`` is only what the model wrote there (a short slug)."""

    reason: str
    destination: str | None = None


@dataclass(frozen=True, slots=True)
class Proposal:
    """A valid button: ``label`` is from the capability list, ``title`` the resolved target's own title."""

    destination: Destination
    target: str | None
    label: str
    title: str | None


def parse_trailer(segment: str) -> Trailer | None:
    """The `Trailer` of an exact ``[[action: ...]]`` segment, or None when it does not parse."""
    m = _BODY.fullmatch(segment)
    if m is None:
        return None
    target = (m.group("target") or "").strip()
    return Trailer(m.group("destination"), target or None)


def validate(trailer: Trailer, targets: ActionTargets) -> Proposal | Dropped:
    """A `Proposal` when the destination is listed and its target (if it needs one) is in this turn's context."""
    cap = BY_DESTINATION.get(trailer.destination)
    if cap is None:
        return Dropped("unknown_destination", trailer.destination)
    if cap.target == "none":
        if trailer.target is not None:
            return Dropped("unexpected_target", cap.destination)
        return Proposal(cap.destination, None, cap.label, None)
    if trailer.target is None:
        return Dropped("missing_target", cap.destination)
    known = targets.get(cap.target, {})
    if trailer.target not in known:
        return Dropped("unknown_target", cap.destination)
    return Proposal(cap.destination, trailer.target, cap.label, known[trailer.target] or None)


@dataclass(frozen=True, slots=True)
class Finished:
    """What is left at the end of the stream: text to release, the trailer that closed the answer and what was dropped."""

    text: str = ""
    trailer: Trailer | None = None
    dropped: tuple[Dropped, ...] = ()


@dataclass(slots=True)
class TrailerFilter:
    """Feeds model text in, gets the text that is safe to show out. Call `finish` once at the end of the stream."""

    _pending: str = ""
    _mid: int = 0
    _released: list[str] = field(default_factory=list)
    _dropped: list[Dropped] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Everything released so far (after `finish`: the whole answer without its trailer)."""
        return "".join(self._released)

    @property
    def dropped(self) -> tuple[Dropped, ...]:
        return tuple(self._dropped)

    def feed(self, chunk: str) -> str:
        """Add text; returns what can be shown now ("" while a possible trailer is held back)."""
        self._pending += chunk
        return self._release(_Out())

    def finish(self) -> Finished:
        """The stream is over. A complete trailer closes the answer; a half one is dropped (never shown)."""
        pending = self._pending
        self._pending = ""
        tail = pending.lstrip()
        if not tail.startswith(OPEN):
            if not tail and self._dropped:
                return Finished(
                    "", None, self.dropped
                )  # the blank that followed a dropped action line is not part of the answer
            return Finished(self._emit(pending), None, self.dropped)
        j = tail.find(CLOSE)
        if j == -1:
            self._dropped.append(Dropped("incomplete"))
            return Finished("", None, self.dropped)
        trailer = parse_trailer(tail[: j + 2])
        if trailer is None:
            self._dropped.append(Dropped("malformed"))
            return Finished("", None, self.dropped)
        if self._mid:
            self._dropped.append(Dropped("multiple", trailer.destination))
            return Finished("", None, self.dropped)
        return Finished("", trailer, self.dropped)

    # ───────────────────────── internals ─────────────────────────

    def _emit(self, text: str) -> str:
        if text:
            self._released.append(text)
        return text

    def _release(self, out: _Out) -> str:
        while True:
            p = self._pending
            i = p.find(OPEN)
            if i == -1:
                cut = len(p.rstrip())
                if cut == len(p) and p.endswith("["):
                    cut = len(
                        p[:-1].rstrip()
                    )  # a lone "[" may be the first half of "[[", and the blank before it goes with it
                self._pending = p[cut:]
                out.add(self._emit(p[:cut]))
                return out.text
            lead_at = len(p[:i].rstrip())
            out.add(self._emit(p[:lead_at]))
            lead = p[lead_at:i]
            candidate = p[i:]
            if not (candidate.startswith(HEAD) or HEAD.startswith(candidate)):
                # an ordinary "[[": plain text, step past it and look at the rest
                out.add(self._emit(lead + OPEN))
                self._pending = candidate[2:]
                continue
            j = candidate.find(CLOSE)
            if j == -1:
                if len(candidate) > CHAT_ACTION_TRAILER_MAX_CHARS or "\n" in candidate:
                    self._dropped.append(Dropped("malformed"))
                    out.add(self._emit(lead + OPEN))
                    self._pending = candidate[2:]
                    continue
                self._pending = lead + candidate  # maybe a trailer: hold
                return out.text
            segment = candidate[: j + 2]
            rest = candidate[j + 2 :]
            trailer = parse_trailer(segment) if len(segment) <= CHAT_ACTION_TRAILER_MAX_CHARS else None
            if trailer is None:
                self._dropped.append(Dropped("malformed"))
                self._pending = lead + rest
                continue
            if not rest.strip():
                self._pending = lead + segment + rest  # at the very end so far: hold until the stream says
                return out.text
            self._mid += 1
            self._dropped.append(Dropped("mid_text", trailer.destination))
            self._pending = lead + rest


class _Out:
    """The text released by one `feed` call."""

    __slots__ = ("_parts",)

    def __init__(self) -> None:
        self._parts: list[str] = []

    def add(self, text: str) -> None:
        if text:
            self._parts.append(text)

    @property
    def text(self) -> str:
        return "".join(self._parts)


@dataclass(frozen=True, slots=True)
class Extracted:
    """`extract`: the whole text without its trailer, the trailer and what was dropped."""

    text: str
    trailer: Trailer | None
    dropped: tuple[Dropped, ...]


def extract(text: str) -> Extracted:
    """The one-shot form of `TrailerFilter` for a finished answer (the same rules, so what is stored matches what was shown)."""
    filt = TrailerFilter()
    filt.feed(text)
    finished = filt.finish()
    return Extracted(filt.text, finished.trailer, finished.dropped)


__all__ = [
    "CLOSE",
    "HEAD",
    "OPEN",
    "ActionTargets",
    "Dropped",
    "Extracted",
    "Finished",
    "Proposal",
    "Trailer",
    "TrailerFilter",
    "extract",
    "parse_trailer",
    "validate",
]
