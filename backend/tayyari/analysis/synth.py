"""The synthesis stage: one Sonnet call that merges every reader's evidence into the skill graph.

`synthesize` streams the synth steps while the model writes (step 2 at 2,500 characters, step 3 at 6,500, a
character count at most every 0.3 s in between), then applies the graph-size gate: below the minimums in
`core.limits` (``SYNTH_MIN_*``) the answer is rejected and ONE more call is made with the legacy repair note.
Failures of the call itself are typed `ClaudeError`s; the client has already retried what is worth retrying, so
nothing here retries them again.
"""

from __future__ import annotations

from typing import Any

from tayyari.contract import SynthStep, ev_log, ev_synth
from tayyari.core import limits
from tayyari.core.text import clip, tidy
from tayyari.llm.errors import ClaudeInvalidOutput
from tayyari.llm.types import TextBlock
from tayyari.prompts import SYNTH_SYSTEM

from .runctx import RunContext
from .schemas import SYNTH_SCHEMA

STEP2_CHARS = 2500
STEP3_CHARS = 6500
PROGRESS_THROTTLE_S = 0.3

TIGHTENING_LOG = "Tightening the graph, one more pass"
"""The log line shown before the second call."""


def graph_size_problem(d: dict[str, Any]) -> str | None:
    """None when the synthesizer's graph is big enough, else the legacy reason text (it goes into the repair note)."""
    domains, skills, roles = d.get("domains") or [], d.get("skills") or [], d.get("roles") or []
    if (
        len(domains) < limits.SYNTH_MIN_DOMAINS
        or len(skills) < limits.SYNTH_MIN_SKILLS
        or len(roles) < limits.SYNTH_MIN_ROLES
    ):
        return (
            f"structured output failed validation: graph too small "
            f"({len(domains)} domains, {len(skills)} skills, {len(roles)} roles)"
        )
    return None


def repair_note(problem: str) -> str:
    """The legacy repair note, with the asked-for sizes read from `core.limits`."""
    reason = clip(tidy(problem), limits.SYNTH_REPAIR_REASON_CLIP_CHARS)
    d_lo, d_hi = limits.SYNTH_DOMAINS_RANGE
    s_lo, s_hi = limits.SYNTH_SKILLS_RANGE
    r_lo, r_hi = limits.SYNTH_ROLES_RANGE
    q_lo, q_hi = limits.SYNTH_REQUIREMENTS_RANGE
    return (
        f"IMPORTANT — your previous attempt failed ({reason}). Return ONLY the structured output, valid against the schema: "
        "every required field present, scores as integers 0–100, kebab-case ids, evidence.sourceId taken from the list above, "
        f"{d_lo}–{d_hi} domains, {s_lo}–{s_hi} skills, {r_lo}–{r_hi} roles with {q_lo}–{q_hi} requirements each."
    )


class _Progress:
    """The synth step counter: monotonic across both calls, throttled while the model writes."""

    def __init__(self, run: RunContext) -> None:
        self._run = run
        self.step: SynthStep = 1
        self._last_emit = 0.0
        self.last_chars = -1
        self.offset = 0
        """A second call restarts the client's count; this keeps the UI's counter monotonic."""

    def __call__(self, generated: int) -> None:
        run = self._run
        chars = self.offset + generated
        if chars == self.last_chars:
            return  # thinking deltas carry no visible characters
        self.last_chars = chars
        now = run.clock.monotonic()
        if self.step < 2 and chars >= STEP2_CHARS:
            self.step = 2
            run.send(ev_synth(2, chars))
            run.send(ev_log("sonnet", "Scoring domains and skills", "sonnet"))
            self._last_emit = now
        elif self.step < 3 and chars >= STEP3_CHARS:
            self.step = 3
            run.send(ev_synth(3, chars))
            run.send(ev_log("sonnet", "Ranking next roles by alignment", "sonnet"))
            self._last_emit = now
        elif now - self._last_emit >= PROGRESS_THROTTLE_S * run.scale:
            self._last_emit = now
            run.send(ev_synth(self.step, chars))


async def synthesize(run: RunContext, prompt: str) -> dict[str, Any]:
    """The synthesizer's graph for ``prompt``, big enough to use. Raises `ClaudeError` (see `llm.errors`)."""
    progress = _Progress(run)
    settings = run.settings

    async def attempt(repair: str | None = None) -> dict[str, Any]:
        user = [TextBlock(prompt, cache=settings.synth_cache == "system+data")]
        if repair:
            user.append(TextBlock(repair))
        res = await run.claude.structured(
            model=settings.synth_model,
            system=[TextBlock(SYNTH_SYSTEM, cache=settings.synth_cache != "off")],
            user=user,
            schema=SYNTH_SCHEMA,
            max_tokens=settings.synth_max_tokens,
            effort=settings.synth_effort,
            on_progress=progress,
            ctx=run.call_ctx("synth"),
        )
        return res.data

    data = await attempt()
    problem = graph_size_problem(data)
    if problem is None:
        return data

    run.send(ev_log("sonnet", TIGHTENING_LOG, "sonnet"))
    progress.offset = max(0, progress.last_chars)
    data = await attempt(repair_note(problem))
    problem = graph_size_problem(data)
    if problem is not None:
        raise ClaudeInvalidOutput([problem])
    return data


__all__ = [
    "PROGRESS_THROTTLE_S",
    "STEP2_CHARS",
    "STEP3_CHARS",
    "TIGHTENING_LOG",
    "graph_size_problem",
    "repair_note",
    "synthesize",
]
