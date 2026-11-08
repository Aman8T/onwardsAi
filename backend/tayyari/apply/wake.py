"""A nudge from the routes to an in-process apply worker: something was queued or approved, so look now.

The worker backs off while the queue is empty (`ApplySettings.worker_idle_max_seconds`), so an idle app does not keep the
database awake with a query every few seconds. A start, an answer, an approval or a "Try again" calls `poke`; the worker
compares `generation` between its short waits and claims at once when it moved. A worker in its own process never sees
the nudge and finds the row on its next backed-off pass.
"""

from __future__ import annotations

_generation = 0


def poke() -> None:
    """Something runnable was queued or approved in this process."""
    global _generation
    _generation += 1


def generation() -> int:
    return _generation


__all__ = ["generation", "poke"]
