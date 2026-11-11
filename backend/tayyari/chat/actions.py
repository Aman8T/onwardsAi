"""The coach's trailers: pure parsers, validators and the stream filter around them.

The coach may end an answer with one final line of one of two kinds. ``[[action: <destination> | <target id>]]`` (the
target is left out for a destination that needs none; `tayyari.contract.capabilities` is the list): the app draws a
button from it. ``[[rewrite: <line id> | <new text>]]``, only when a resume is in view: the app offers different words for
that line, which the person accepts or dismisses. Nothing here calls anything or touches storage:

* `TrailerFilter` sits between the model's text deltas and the client. It holds back text from a ``[[`` until it is known
  whether that opens a trailer, so a trailer split across deltas is never half shown, and a ``[[...]]`` that is neither
  kind (``[[1]]``) is released as the plain text it is. Whitespace before a possible trailer is held too, so the answer
  does not end on a blank line. Whatever is released is the text that is stored and replayed: no trailer is ever in it.
  The answer may end in one trailer or in a run of them (a button and a rewrite, in either order): the filter hands all of
  them back in order, and the caller keeps the first valid one and drops the rest as ``multiple``.
* `validate` turns an action trailer into a `Proposal` only when the destination is in the list and the target id (when
  the destination needs one) is one of the ids present in this turn's context (`ActionTargets`). The label comes from the
  list and the title from the resolved target; no model prose is kept.
* `validate_rewrite` turns a rewrite trailer into a `RewriteProposal` only when the line id is one of the lines shown to
  the model this turn, the words are one line within `MAX_LINE_CHARS`, they differ from the line's current text and the
  resume's own check (`ResumeInView.check`: the facts the line cites must support them) passes. Only what the line itself
  says is kept; the model's reasoning never is.
* Anything else is `Dropped` with a reason. Callers log the reason and the destination, never the text.

Drop reasons: ``multiple`` (a second trailer after a valid one, or one after a trailer in the middle of the text),
``mid_text`` (one that is not at the very end), ``malformed`` (it opens like a trailer and does not parse or is too long),
``incomplete`` (the stream stopped inside one), for an action ``unknown_destination``, ``unexpected_target`` (a target on a
destination that takes none), ``missing_target`` and ``unknown_target``; for a rewrite ``no_resume`` (none in view),
``unknown_line``, ``empty``, ``too_long``, ``placeholder`` (a ``[x]``), ``unchanged`` and ``unsupported_<kind>`` (the
facts do not support a figure, name, term or scope verb it uses) or ``unsupported``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from tayyari.chat.ports import ResumeInView
from tayyari.contract.capabilities import BY_DESTINATION, Destination
from tayyari.contract.resume import MAX_LINE_CHARS
from tayyari.core.limits import CHAT_ACTION_TRAILER_MAX_CHARS, CHAT_REWRITE_TRAILER_MAX_CHARS

HEAD: Final[str] = "[[action:"
REWRITE_HEAD: Final[str] = "[[rewrite:"
HEADS: Final[tuple[str, ...]] = (HEAD, REWRITE_HEAD)
OPEN: Final[str] = "[["
CLOSE: Final[str] = "]]"

_BODY = re.compile(
    r"\[\[action:\s*(?P<destination>[A-Za-z0-9_]{1,40})\s*(?:\|\s*(?P<target>[^\[\]|\n]*?)\s*)?\]\]\Z"
)
_REWRITE_BODY = re.compile(r"\[\[rewrite:\s*(?P<line>[A-Za-z0-9_-]{1,24})\s*\|\s*(?P<text>[^\n]*?)\s*\]\]\Z")
_PLACEHOLDER = re.compile(r"\[x\]")

ActionTargets = Mapping[str, Mapping[str, str | None]]
"""Per target kind (``role``, ``plan``, ``match``, ``tracked``, ``skill``): the ids present in this turn's context, each with
its own title (or None)."""

LineIds = Mapping[str, str]
"""The short ids of the lines shown to the model this turn, each with the line's real id (``l3`` to ``b-exp-1.3``)."""


@dataclass(frozen=True, slots=True)
class Trailer:
    """A parsed ``[[action: ...]]``: the destination as written and the target (None when none was written)."""

    destination: str
    target: str | None


@dataclass(frozen=True, slots=True)
class RewriteTrailer:
    """A parsed ``[[rewrite: ...]]``: the short line id as written and the new words."""

    line: str
    text: str


AnyTrailer = Trailer | RewriteTrailer


@dataclass(frozen=True, slots=True)
class Dropped:
    """A trailer that is not shown, with why. ``destination`` is only what the model wrote there (a short slug; for a
    rewrite, ``rewrite``)."""

    reason: str
    destination: str | None = None


@dataclass(frozen=True, slots=True)
class Proposal:
    """A valid button: ``label`` is from the capability list, ``title`` the resolved target's own title."""

    destination: Destination
    target: str | None
    label: str
    title: str | None


@dataclass(frozen=True, slots=True)
class RewriteProposal:
    """A valid rewrite: the resume and the line (its real id), what the line says now and the words proposed."""

    resume_id: str
    line_id: str
    before: str
    after: str


def parse_trailer(segment: str) -> Trailer | None:
    """The `Trailer` of an exact ``[[action: ...]]`` segment, or None when it does not parse."""
    m = _BODY.fullmatch(segment)
    if m is None:
        return None
    target = (m.group("target") or "").strip()
    return Trailer(m.group("destination"), target or None)


def parse_rewrite(segment: str) -> RewriteTrailer | None:
    """The `RewriteTrailer` of an exact ``[[rewrite: <line id> | <text>]]`` segment, or None when it does not parse (no
    line id, no ``|``, no text)."""
    m = _REWRITE_BODY.fullmatch(segment)
    if m is None:
        return None
    text = m.group("text").strip()
    return RewriteTrailer(m.group("line"), text) if text else None


def parse_any(segment: str) -> AnyTrailer | None:
    """Either kind of trailer, or None."""
    return parse_trailer(segment) or parse_rewrite(segment)


def destination_of(trailer: AnyTrailer) -> str:
    """What a drop logs for the trailer: its destination, or ``rewrite``."""
    return trailer.destination if isinstance(trailer, Trailer) else "rewrite"


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


def validate_rewrite(
    trailer: RewriteTrailer, view: ResumeInView | None, ids: LineIds, current: Mapping[str, str]
) -> RewriteProposal | Dropped:
    """A `RewriteProposal` when ``trailer`` names a line shown this turn (``ids`` maps its short id to its real one;
    ``current`` maps real ids to the words they have now) and its words are one line within `MAX_LINE_CHARS`, hold no
    ``[x]``, differ from what the line says and pass the resume's own check against the facts the line cites."""
    if view is None:
        return Dropped("no_resume", "rewrite")
    line_id = ids.get(trailer.line)
    if line_id is None or line_id not in current:
        return Dropped("unknown_line", "rewrite")
    after = " ".join(trailer.text.split())
    if not after:
        return Dropped("empty", "rewrite")
    if len(after) > MAX_LINE_CHARS:
        return Dropped("too_long", "rewrite")
    if _PLACEHOLDER.search(after):
        return Dropped("placeholder", "rewrite")
    before = current[line_id]
    if after.casefold() == " ".join(before.split()).casefold():
        return Dropped("unchanged", "rewrite")
    reason = view.check(line_id, after)
    if reason is not None:
        return Dropped(reason, "rewrite")
    return RewriteProposal(view.resume_id, line_id, before, after)


@dataclass(frozen=True, slots=True)
class Finished:
    """What is left at the end of the stream: text to release, the trailers that closed the answer (in order) and what was
    dropped."""

    text: str = ""
    trailers: tuple[AnyTrailer, ...] = ()
    dropped: tuple[Dropped, ...] = ()

    @property
    def trailer(self) -> AnyTrailer | None:
        """The first trailer, or None."""
        return self.trailers[0] if self.trailers else None


def _cap(segment: str) -> int:
    return (
        CHAT_REWRITE_TRAILER_MAX_CHARS if segment.startswith(REWRITE_HEAD) else CHAT_ACTION_TRAILER_MAX_CHARS
    )


def _opens_trailer(candidate: str) -> bool:
    """Does ``candidate`` (starting at a ``[[``) open a trailer, or could it still turn into one when more text comes?"""
    return any(candidate.startswith(h) or h.startswith(candidate) for h in HEADS)


def _only_trailers_follow(rest: str) -> bool:
    """Is ``rest`` nothing but more trailers (whole, or the start of one that is still arriving)? Then the first trailer is
    not in the middle of the text: the answer ends in a run of trailers."""
    tail = rest.lstrip()
    while tail:
        if not _opens_trailer(tail):
            return False
        j = tail.find(CLOSE)
        if j == -1:
            return len(tail) <= _cap(tail) and "\n" not in tail
        if len(tail[: j + 2]) > _cap(tail):
            return False
        tail = tail[j + 2 :].lstrip()
    return True


@dataclass(slots=True)
class TrailerFilter:
    """Feeds model text in, gets the text that is safe to show out. Call `finish` once at the end of the stream."""

    _pending: str = ""
    _mid: int = 0
    _released: list[str] = field(default_factory=list)
    _dropped: list[Dropped] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Everything released so far (after `finish`: the whole answer without its trailers)."""
        return "".join(self._released)

    @property
    def dropped(self) -> tuple[Dropped, ...]:
        return tuple(self._dropped)

    def feed(self, chunk: str) -> str:
        """Add text; returns what can be shown now ("" while a possible trailer is held back)."""
        self._pending += chunk
        return self._release(_Out())

    def finish(self) -> Finished:
        """The stream is over. A complete trailer (or a run of them) closes the answer; a half one is dropped (never shown)."""
        pending = self._pending
        self._pending = ""
        tail = pending.lstrip()
        if not tail.startswith(OPEN):
            if not tail and self._dropped:
                return Finished(
                    "", (), self.dropped
                )  # the blank that followed a dropped trailer is not part of the answer
            return Finished(self._emit(pending), (), self.dropped)
        found: list[AnyTrailer] = []
        while tail:
            j = tail.find(CLOSE) if tail.startswith(OPEN) else -1
            if j == -1:
                self._dropped.append(Dropped("incomplete"))
                break
            parsed = parse_any(tail[: j + 2])
            if parsed is None:
                self._dropped.append(Dropped("malformed"))
            else:
                found.append(parsed)
            tail = tail[j + 2 :].lstrip()
        if self._mid and found:
            self._dropped.extend(Dropped("multiple", destination_of(t)) for t in found)
            found = []
        return Finished("", tuple(found), self.dropped)

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
            if not _opens_trailer(candidate):
                # an ordinary "[[": plain text, step past it and look at the rest
                out.add(self._emit(lead + OPEN))
                self._pending = candidate[2:]
                continue
            j = candidate.find(CLOSE)
            if j == -1:
                if len(candidate) > _cap(candidate) or "\n" in candidate:
                    self._dropped.append(Dropped("malformed"))
                    out.add(self._emit(lead + OPEN))
                    self._pending = candidate[2:]
                    continue
                self._pending = lead + candidate  # maybe a trailer: hold
                return out.text
            segment = candidate[: j + 2]
            rest = candidate[j + 2 :]
            trailer = parse_any(segment) if len(segment) <= _cap(segment) else None
            if trailer is None:
                self._dropped.append(Dropped("malformed"))
                self._pending = lead + rest
                continue
            if _only_trailers_follow(rest):
                # at the very end so far, or a run of trailers: hold until the stream says
                self._pending = lead + segment + rest
                return out.text
            self._mid += 1
            self._dropped.append(Dropped("mid_text", destination_of(trailer)))
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
    """`extract`: the whole text without its trailers, the trailers in order and what was dropped."""

    text: str
    trailers: tuple[AnyTrailer, ...]
    dropped: tuple[Dropped, ...]

    @property
    def trailer(self) -> AnyTrailer | None:
        """The first trailer, or None."""
        return self.trailers[0] if self.trailers else None


def extract(text: str) -> Extracted:
    """The one-shot form of `TrailerFilter` for a finished answer (the same rules, so what is stored matches what was shown)."""
    filt = TrailerFilter()
    filt.feed(text)
    finished = filt.finish()
    return Extracted(filt.text, finished.trailers, finished.dropped)


__all__ = [
    "CLOSE",
    "HEAD",
    "HEADS",
    "OPEN",
    "REWRITE_HEAD",
    "ActionTargets",
    "AnyTrailer",
    "Dropped",
    "Extracted",
    "Finished",
    "LineIds",
    "Proposal",
    "RewriteProposal",
    "RewriteTrailer",
    "Trailer",
    "TrailerFilter",
    "destination_of",
    "extract",
    "parse_any",
    "parse_rewrite",
    "parse_trailer",
    "validate",
    "validate_rewrite",
]
