"""The field-list loop (spec 3.2 fill step 3): when the playbook and the person's details leave a required field empty, or the
form sits behind a button nobody recorded, a model looks at the live form and says which saved detail each field asks for.

Each round: scan the page, drop synthetic fields and every field a model may never see (self-ID, never-stored, work
authorisation and sponsorship, consent boxes, files), ask ``ClaudeClient.structured`` (purpose ``apply.map``) inside
``reserve(est_fill_call_usd)``, then let code act: the value always comes from the person's own details (`FillContext`),
an option only when it is one of the field's own, a click only when it is not submit-like. ``valueKey: "question"`` sends
the field to the `answers.ClaudeAnswerer`, whose answers must cite the person's facts (`answers.apply_model_answers`). At
most ``fill_max_rounds`` rounds with ``fill_model`` (Haiku), then one round with ``fill_fallback_model`` (Sonnet) only when a
required field is still empty. Text from the page is data: what reads as an instruction becomes an anomaly and the field or
button it sits on is never acted on (CLAUDE.md rule 3).

The step executor (`run_step`, `step_op`) is shared with `apply.runner`, which replays the same steps at submit.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

from tayyari.apply.answers import (
    FillContext,
    Plan,
    Planned,
    _saved,  # the planner's own lookup (bank, then details, then what code derives): one source of truth
    apply_model_answers,
    pick_option,
    question_key,
)
from tayyari.apply.driver import BrowserDriver, click_refused
from tayyari.apply.forms import FormField
from tayyari.apply.playbook import (
    COUNTRY_KEYS,
    KEY_ALIASES,
    NEVER_STORED_RULE_KEYS,
    PER_OFFER_KEYS,
    PROFILE_KEYS,
    REQUIRED_ONLY_KEYS,
    SELF_ID_KEYS,
    FieldOp,
    Playbook,
    generic_rule_for,
    op_for,
    rule_for,
)
from tayyari.apply.scan import LiveScan
from tayyari.apply.seams import Reserve
from tayyari.contract.apply import FieldSource, NeedsInput
from tayyari.core.logging import get_logger
from tayyari.core.settings import ApplySettings
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import SpendCeilingReached
from tayyari.llm.types import CallContext, Effort, TextBlock
from tayyari.prompts.apply_fill import SPECIAL_KEYS, compact_field, fill_schema, fill_system, fill_user

log = get_logger(__name__)

StepOp = Literal["fill", "select", "check", "choose", "group"]
"""An executable step (`contract.apply.ScriptOp`): never an upload."""

HIDDEN_KEYS: Final[frozenset[str]] = (
    SELF_ID_KEYS | COUNTRY_KEYS | NEVER_STORED_RULE_KEYS | frozenset({"resume", "coverLetter"})
)
"""Fields a model never sees: self-ID, work authorisation and sponsorship (per country, never inferred), ID numbers and the
rest of the never-stored list, signatures and consent, the resume and cover letter."""

NOT_OFFERED: Final[frozenset[str]] = (
    HIDDEN_KEYS
    | REQUIRED_ONLY_KEYS
    | PER_OFFER_KEYS
    | frozenset(KEY_ALIASES)
    | frozenset({"currentCtc", "expectedCtc", "noticePeriod", "servingNotice", "lastWorkingDay", "startDate"})
)
"""Saved details a model is never offered as a ``valueKey``: besides the hidden ones, pay, notice and joining dates,
date of birth, and what is decided per offer (spec 1 rule 7: never drafted, so never mapped by a model either)."""

_TEXT_KINDS: Final = frozenset({"text", "email", "tel", "url", "number", "date", "textarea"})
_MODEL_OPS: Final[dict[str, frozenset[str]]] = {
    **{kind: frozenset({"fill", "choose"}) for kind in _TEXT_KINDS},
    "select": frozenset({"select"}),
    "radio": frozenset({"group"}),
    "checkboxGroup": frozenset({"group"}),
    "combobox": frozenset({"choose", "fill"}),
}
"""The ops a model may pick per field kind; anything else falls back to the field's own op (never ``check``)."""

_INJECTION: Final = re.compile(
    r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:instructions?|rules|prompt|guidelines)\b|"
    r"\b(?:you are|as) an? (?:ai|assistant|language model|llm|bot)\b|\bsystem prompt\b|"
    r"\b(?:ai|assistant|language model|llm|chatgpt|claude|gpt)s?\b.{0,40}\b(?:must|should|need to|please)\b|"
    r"\b(?:tick|check|select) (?:all|every)\b.{0,20}\b(?:box|boxes|option)|\bclick (?:the )?submit\b",
    re.I | re.S,
)
"""Page text that gives an AI instructions (CLAUDE.md rule 3). A field or button whose text matches is never acted on."""

_MAX_CLICKS: Final = 3
_FORM_WAIT_S: Final = 8.0
_MAX_TOKENS: Final = 4000


# ───────────────────────── the step executor (shared with the runner) ─────────────────────────


def step_op(item: FormField, playbook: Playbook) -> FieldOp:
    """How a field is operated: the playbook's op (`playbook.op_for`), made to fit the field's kind (a radio or checkbox
    group is picked by its name, a lone checkbox is ticked, a file is uploaded)."""
    if item.kind in ("radio", "checkboxGroup"):
        return "group"
    if item.kind == "checkbox":
        return "check"
    if item.kind == "file":
        return "upload"
    op = op_for(item, playbook)
    if item.kind == "select" and op not in ("select", "choose"):
        return "select"
    if op in ("group", "check", "upload"):
        return "fill"
    return op


_NAME_SELECTOR: Final = re.compile(r'\[name="((?:[^"\\]|\\.)+)"\]$')


def group_name(selector: str, name: str | None = None) -> str:
    """The ``name`` a radio or checkbox group is picked by (its ``[name="..."]`` selector)."""
    if name:
        return name
    match = _NAME_SELECTOR.search(selector)
    return re.sub(r"\\(.)", r"\1", match.group(1)) if match else selector


async def run_step(
    driver: BrowserDriver, selector: str, op: str, value: str, frame: int = 0, *, name: str | None = None
) -> None:
    """One executable step on the live page. ``upload`` is not a step: the resume is attached only by the approved submit
    path, after the ``submitting`` marker."""
    if op == "fill":
        await driver.fill(selector, value, frame)
    elif op == "select":
        await driver.select_label(selector, value, frame)
    elif op == "check":
        await driver.check(selector, frame)
    elif op == "choose":
        await driver.choose(selector, value, frame)
    elif op == "group":
        await driver.pick_in_group(group_name(selector, name), value, frame)
    else:
        raise ValueError(f"not a fill step: {op}")


def hidden_from_model(item: FormField, playbook: Playbook) -> bool:
    """True for a field a model must never see (`HIDDEN_KEYS`), a file, a lone checkbox (consent is the person's) or a
    synthetic one."""
    if item.synthetic or item.kind in ("file", "checkbox"):
        return True
    return bool({rule_for(item, playbook), generic_rule_for(item)} & HIDDEN_KEYS)


def offered_details(ctx: FillContext) -> dict[str, tuple[str, FieldSource]]:
    """The person's saved details a model may name as a ``valueKey`` (key -> (value, source)); only keys with a value."""
    out: dict[str, tuple[str, FieldSource]] = {}
    for key in PROFILE_KEYS:
        if key in NOT_OFFERED:
            continue
        found = _saved(ctx, key)
        if found.value is not None and found.source is not None:
            out[key] = (found.value, found.source)
    return out


def injected(text: str) -> bool:
    return bool(_INJECTION.search(text))


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _quoted_in(text: str, anomalies: Collection[str]) -> bool:
    folded = _fold(text)
    if not folded:
        return False
    for quote in anomalies:
        q = _fold(quote)
        # The quote inside the text, or a long text inside the quote: a short label ("Email") that a long quoted
        # paragraph happens to contain is not blocked.
        if len(q) >= 8 and (q in folded or (len(folded) >= 20 and folded in q)):
            return True
    return False


# ───────────────────────── the loop ─────────────────────────


class LoopAnswerer(Protocol):
    async def answer(
        self, questions: list[FormField], ctx: FillContext, *, escalate: bool = False
    ) -> dict[str, dict[str, Any]]: ...


@dataclass(slots=True)
class LoopRun:
    """What the loop did: the fields it filled (executed on the page, each with the source of its value), suggestions for
    the person, the clicks made and refused, anomalies, and what it cost."""

    filled: list[Planned] = field(default_factory=list)
    needs: list[NeedsInput] = field(default_factory=list)
    clicks: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    """The model of every call, in order."""
    cost_micro_usd: int = 0
    rounds: int = 0
    blocked: str | None = None
    """``captcha``, ``login`` or ``no_form`` when the model said so and no button revealed a form."""
    escalated: bool = False

    @property
    def calls(self) -> int:
        return len(self.models)

    @property
    def model(self) -> str | None:
        return self.models[-1] if self.models else None

    def add_anomaly(self, text: str) -> None:
        text = " ".join(text.split())[:300]
        if text and text not in self.anomalies:
            self.anomalies.append(text)


@dataclass(slots=True)
class _Round:
    progress: bool = False
    still_empty: bool = True


class FieldListLoop:
    """Haiku, then Sonnet, mapping live form fields to the person's details. Every call is reserved and recorded."""

    def __init__(
        self,
        claude: ClaudeClient,
        answerer: LoopAnswerer | None,
        reserve: Reserve,
        settings: ApplySettings,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._claude, self._answerer, self._reserve, self._settings = claude, answerer, reserve, settings
        self._monotonic = monotonic

    def available(self) -> list[str]:
        models = [self._settings.fill_model, self._settings.fill_fallback_model]
        return list(dict.fromkeys(m for m in models if m))

    async def open_form(
        self, driver: BrowserDriver, ctx: FillContext, *, request_id: str | None = None
    ) -> tuple[LiveScan, LoopRun]:
        """No form on screen: up to ``fill_max_rounds`` Haiku rounds that only click (to reveal it, or to decline a cookie
        banner). Returns the last scan, which `LiveScan.has_form` when it worked."""
        run, started = LoopRun(), self._monotonic()
        scan = await driver.scan()
        for _ in range(self._settings.fill_max_rounds):
            if scan.has_form or self._late(started):
                break
            buttons = await driver.buttons()
            data = await self._ask(self._settings.fill_model, [], buttons, ctx, {}, "", run, request_id)
            if data is None:
                break
            run.rounds += 1
            clicked = await self._clicks(driver, data, scan, run)
            if clicked:
                scan = await driver.wait_for_form(_FORM_WAIT_S)
                continue
            blocked = data.get("blocked")
            run.blocked = blocked if blocked in ("captcha", "login", "no_form") else None
            break
        return scan, run

    async def fill(
        self,
        driver: BrowserDriver,
        plan: Plan,
        ctx: FillContext,
        playbook: Playbook,
        *,
        open_keys: Collection[str],
        request_id: str | None = None,
    ) -> LoopRun:
        """Fill the required fields still empty. ``open_keys`` are the fields the deterministic plan could not place (no
        rule matched, or the value did not stick); a field that appears after a click and is not in the plan is open too.
        Hidden fields are never sent, whatever the keys say."""
        run, started = LoopRun(), self._monotonic()
        known = set(plan.question_keys) - set(open_keys)
        feedback = ""
        for _ in range(self._settings.fill_max_rounds):
            if self._late(started):
                break
            state = await self._fill_round(
                driver, ctx, playbook, known, self._settings.fill_model, False, feedback, run, request_id
            )
            if not state.still_empty:
                return run
            if not state.progress:
                break
            feedback = self._feedback(run)
        fallback = self._settings.fill_fallback_model
        if fallback and fallback != self._settings.fill_model and not self._late(started):
            run.escalated = True
            await self._fill_round(
                driver, ctx, playbook, known, fallback, True, self._feedback(run), run, request_id
            )
        return run

    # ── one round ──

    async def _fill_round(
        self, driver: BrowserDriver, ctx: FillContext, playbook: Playbook, known: set[str], model: str,
        escalate: bool, feedback: str, run: LoopRun, request_id: str | None,
    ) -> _Round:  # fmt: skip
        scan = await driver.scan()
        fields = [f for f in scan.actionable if f.key not in known and not hidden_from_model(f, playbook)]
        if not any(f.required and not f.value.strip() for f in fields):
            return _Round(progress=False, still_empty=False)
        offered = offered_details(ctx)
        buttons = await driver.buttons()
        data = await self._ask(model, fields, buttons, ctx, offered, feedback, run, request_id)
        if data is None:
            return _Round(progress=False, still_empty=True)
        run.rounds += 1
        state = _Round()
        if await self._clicks(driver, data, scan, run):
            state.progress = True
            again = await driver.wait_for_form(_FORM_WAIT_S)
            if [f.key for f in again.actionable] != [f.key for f in scan.actionable]:
                return state  # the form changed: the indexes are stale, ask again
        questions: list[FormField] = []
        for act in data.get("actions") or []:
            item = self._target(act, fields, run)
            if item is None:
                continue
            key = act.get("valueKey")
            if key == "question":
                questions.append(item)
                continue
            if key not in offered or act.get("confidence") != "high":
                if key not in offered and key not in SPECIAL_KEYS:
                    log.info("apply_loop_key_ignored", field=item.key)
                continue
            value, source = offered[str(key)]
            placed = self._placed(item, act, value, source, str(key), playbook, scan)
            if placed is None:
                run.errors.append(f'"{item.label[:60]}": none of its options fits the saved {key}')
                continue
            planned, op = placed
            if await self._execute(driver, planned, op, run):
                state.progress = True
        if questions and await self._answer(driver, questions, ctx, playbook, escalate, run, scan):
            state.progress = True
        after = await driver.scan()
        rest = [f for f in after.actionable if f.key not in known and not hidden_from_model(f, playbook)]
        state.still_empty = any(f.required and not f.value.strip() for f in rest)
        return state

    def _target(self, act: Mapping[str, Any], fields: list[FormField], run: LoopRun) -> FormField | None:
        index = act.get("field")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(fields):
            return None
        item = fields[index]
        texts = [item.label, *(t for _, t in item.options)]
        if any(injected(t) for t in texts) or any(_quoted_in(t, run.anomalies) for t in texts):
            run.add_anomaly(item.label)
            return None  # an injected label is reported, never acted on
        return item

    def _placed(
        self, item: FormField, act: Mapping[str, Any], value: str, source: FieldSource, key: str,
        playbook: Playbook, scan: LiveScan,
    ) -> tuple[Planned, str] | None:  # fmt: skip
        own = step_op(item, playbook)
        wanted = act.get("op")
        op: str = wanted if isinstance(wanted, str) and wanted in _MODEL_OPS.get(item.kind, ()) else own
        if op not in ("fill", "select", "choose", "group"):
            return None
        order = next((i for i, f in enumerate(scan.fields) if f.key == item.key), None)
        qkey = question_key(item, key)
        texts = [text for _, text in item.options]
        if not texts:
            return Planned(item, value, source, key, "high", question_key=qkey, order=order), op
        picked = pick_option(item, value)
        if picked is not None:
            option_value, text = picked
            return Planned(item, text, source, key, "high", option_value=option_value, question_key=qkey, order=order), op  # fmt: skip
        choice = _fold(str(act.get("choice") or ""))
        hit = next(((v, t) for v, t in item.options if choice and _fold(t) == choice), None)
        if hit is None:
            return None
        # The model matched the saved value to an option code could not match: shown in Review as one to check.
        return Planned(item, hit[1], source, key, "medium", option_value=hit[0], question_key=qkey, order=order), op  # fmt: skip

    async def _execute(self, driver: BrowserDriver, planned: Planned, op: str, run: LoopRun) -> bool:
        item = planned.item
        try:
            await run_step(driver, item.selector, op, planned.value, item.frame, name=item.name)
        except Exception as exc:
            run.errors.append(f'"{item.label[:60]}" ({op}): {type(exc).__name__}')
            return False
        run.filled = [p for p in run.filled if p.item.key != item.key] + [planned]
        return True

    async def _answer(
        self, driver: BrowserDriver, questions: list[FormField], ctx: FillContext, playbook: Playbook,
        escalate: bool, run: LoopRun, scan: LiveScan,
    ) -> bool:  # fmt: skip
        if self._answerer is None:
            return False
        drafted = await self._answerer.answer(
            questions, ctx, escalate=escalate
        )  # SpendCeilingReached propagates
        temp = Plan(question_keys={q.key: question_key(q) for q in questions})
        apply_model_answers(temp, questions, drafted, ctx)
        progress = False
        for planned in temp.filled:
            planned.order = next((i for i, f in enumerate(scan.fields) if f.key == planned.item.key), None)
            if await self._execute(driver, planned, step_op(planned.item, playbook), run):
                progress = True
        done = {p.item.key for p in run.filled}
        run.needs = [n for n in run.needs if n.key not in done and n.key not in {m.key for m in temp.needs}]
        run.needs += [n for n in temp.needs if n.key not in done]
        return progress

    async def _clicks(
        self, driver: BrowserDriver, data: Mapping[str, Any], scan: LiveScan, run: LoopRun
    ) -> bool:
        clicked = False
        for text in [t for t in data.get("clicks") or [] if isinstance(t, str)][:_MAX_CLICKS]:
            if click_refused(text, form_on_screen=scan.has_form):
                run.refused.append(text)
                continue
            if injected(text) or _quoted_in(text, run.anomalies):
                run.add_anomaly(text)
                run.refused.append(text)
                continue
            outcome = await driver.click_text(text, form_on_screen=scan.has_form)
            if outcome == "clicked":
                run.clicks.append(text)
                clicked = True
            elif outcome == "refused":
                run.refused.append(text)
        return clicked

    async def _ask(
        self, model: str, fields: list[FormField], buttons: list[str], ctx: FillContext,
        offered: Mapping[str, tuple[str, FieldSource]], feedback: str, run: LoopRun, request_id: str | None,
    ) -> dict[str, Any] | None:  # fmt: skip
        rows = [
            compact_field(i, f.label, f.kind, f.required, bool(f.value.strip()), [t for _, t in f.options])
            for i, f in enumerate(fields)
        ]
        for f in fields:  # code's own check, beside the model's: an instruction in a label is an anomaly
            if injected(f.label):
                run.add_anomaly(f.label)
        user = fill_user(
            job={"title": ctx.job_title, "company": ctx.company},
            fields=rows,
            buttons=buttons,
            details={key: value for key, (value, _) in offered.items()},
            feedback=feedback,
        )
        call = CallContext(purpose="apply.map", request_id=request_id, profile_id=ctx.profile_id)
        effort: Effort | None = None if model == self._settings.fill_model else "low"
        try:
            async with self._reserve(self._settings.est_fill_call_usd):
                result = await self._claude.structured(
                    model=model, system=[TextBlock(fill_system(), cache=True)], user=[TextBlock(user)],
                    schema=fill_schema(sorted(offered)), max_tokens=_MAX_TOKENS, effort=effort, ctx=call,
                )  # fmt: skip
        except SpendCeilingReached:
            raise
        except Exception as exc:  # the fields stay with the person
            log.warning("apply_loop_call_failed", model=model, error=type(exc).__name__)
            return None
        run.models.append(model)
        run.cost_micro_usd += result.usage.cost_micro_usd
        data = result.data if isinstance(result.data, dict) else {}
        for quote in data.get("anomalies") or []:
            if isinstance(quote, str):
                run.add_anomaly(quote)
        return data

    def _feedback(self, run: LoopRun) -> str:
        parts = []
        if run.errors:
            parts.append("Steps that failed: " + "; ".join(run.errors[-6:]) + ".")
        parts.append("Required fields are still empty: try another op or valueKey, or 'none'.")
        return " ".join(parts)

    def _late(self, started: float) -> bool:
        return self._monotonic() - started > self._settings.fill_deadline_s


__all__ = [
    "HIDDEN_KEYS",
    "NOT_OFFERED",
    "FieldListLoop",
    "LoopAnswerer",
    "LoopRun",
    "StepOp",
    "group_name",
    "hidden_from_model",
    "injected",
    "offered_details",
    "run_step",
    "step_op",
]
