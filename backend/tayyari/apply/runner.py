"""The runner: fill an employer's form for review, and send exactly the approved values.

`preview` (spec 3.1 step 2) opens the form behind the guard, fills it from the person's details and answer bank (code),
then cited model answers, then the field-list loop for what is still empty, reads the form back from the DOM and returns
the review: every field with its selector, source, group and order, the questions for the person, the executable fill
script and the digest. It never attaches the resume and never lifts the guard (spec 1 rule 3).

`submit_approved` (spec 3.1 step 5, in the order the A1 decisions set) replays the review's script with no model, reads the
form back, recomputes the digest with the sha256 of the resume it will attach, checks the submit control is there, writes
the ``submitting`` marker, lifts the guard only for the playbook's submit and upload hosts, attaches the resume and waits
for the widget to show its file name, clicks once, then reads the outcome. The success signature is ``sent``; anything
else after the click is ``check_this_one``, never retried (spec 1 rules 4 and 5).

The digest is `answers.digest_rows` over the read-back form (stable key and live value of every field but files) and the
resume's sha256, computed the same way in both passes, so widgets that reformat input do not bounce every submit. A script
step names its field by selector; the replay finds that field in the submit browser's own scan and uses the frame it is in
there, so a frame index from the preview is never trusted on its own.

"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final, Literal

from tayyari.apply.answers import (
    Answerer,
    FillContext,
    Plan,
    Resume,
    ResumePlan,
    apply_model_answers,
    digest_rows,
    plan_fields,
    question_key,
    stable_key,
)
from tayyari.apply.driver import BrowserDriver
from tayyari.apply.fill_loop import FieldListLoop, LoopRun, hidden_from_model, injected, run_step, step_op
from tayyari.apply.forms import FormField, blocker_of, checks_on_submit, closed_of
from tayyari.apply.playbook import ATTESTATION_KEYS, COUNTRY_KEYS, SELF_ID_KEYS, Playbook, rule_for
from tayyari.apply.scan import LiveScan, resume_name_inferable
from tayyari.apply.verify import find_live, value_matches, verify
from tayyari.contract.apply import (
    ApplicationReview,
    CitedFact,
    FilledField,
    NeedsInput,
    ResumeChoice,
    ReviewResume,
    RunMode,
    ScriptStep,
    StopReason,
)
from tayyari.core.logging import get_logger

log = get_logger(__name__)

BOT_CHECK_WARNING = "This form runs a bot check when it is submitted. If it asks for a human check, you will get a link to finish it yourself."


FillStatus = Literal["ready", "needs_user", "failed", "closed", "needs_signin"]
"""``ready``: a review with nothing required left open; ``needs_user``: a review with questions for the person (both go
to ``waiting_for_you``); ``closed``: the posting is gone; ``needs_signin``: a login wall or a CAPTCHA (``stop_reason``);
``failed``: the form never opened."""


@dataclass(slots=True)
class FillResult:
    status: FillStatus
    plan: Plan | None = None
    reason: str | None = None
    """A short code for metrics: captcha, login_wall, no_form, step_failed, navigation, needs_answers, form_changed and
    the `forms.closed_of` reasons (gone, not_accepting, filled, unavailable) or closed."""
    note: str | None = None
    screenshot: bytes | None = None
    submit_label: str | None = None
    warnings: list[str] = field(default_factory=list)
    fields_total: int = 0
    steps: int = 0
    stop_reason: StopReason | None = None
    fields: list[FilledField] = field(default_factory=list)
    """The review rows, in the form's order: the value as read back from the page, its source, selector, group, options."""
    needs: list[NeedsInput] = field(default_factory=list)
    skipped: list[FilledField] = field(default_factory=list)
    script: list[ScriptStep] = field(default_factory=list)
    """The executable fill script the submit pass replays (never an upload step)."""
    digest: str | None = None
    resume: ResumePlan | None = None
    """The resume that goes with the form, attached only by `submit_approved`."""
    facts: list[CitedFact] = field(default_factory=list)
    """The facts the model answers cite, copied now (the application outlives the match)."""
    mode: RunMode = "playbook"
    model: str | None = None
    cost_micro_usd: int = 0
    """What the field-list loop's model calls cost (the answerer's own calls are recorded by the client)."""
    anomalies: list[str] = field(default_factory=list)
    """Page text that read as instructions to a model (CLAUDE.md rule 3), never acted on."""
    refused: list[str] = field(default_factory=list)
    """Clicks refused as submit-like."""


SubmitStatus = Literal["failed", "sent", "check_this_one", "form_changed"]
"""`submit_approved`: ``sent`` (the success signature), ``check_this_one`` (clicked, no clear outcome), ``form_changed``
(the read-back digest differs: back to the person with ``review``), ``failed`` (nothing clicked: ``clicked`` is False)."""


@dataclass(slots=True)
class SubmitResult:
    status: SubmitStatus
    reason: str | None = None
    note: str | None = None
    screenshot: bytes | None = None
    steps: int = 0
    clicked: bool = False
    """True once the submit control's click was attempted: from then on the outcome is never retried."""
    review: FillResult | None = None
    """For ``form_changed``: the form as it reads now, to store as the new review (`review_of`)."""


# ───────────────────────── A1: the review (preview) ─────────────────────────

PERSONAL_GROUP: Final = "Personal information"
ELIGIBILITY_GROUP: Final = "Eligibility"
QUESTIONS_GROUP: Final = "Application questions"
SELF_ID_GROUP: Final = "Voluntary disclosures"
DECLARATION_GROUP: Final = "Declaration"

_PERSONAL_KEYS: Final = frozenset(
    {
        "firstName", "lastName", "fullName", "email", "phone", "phoneCountry", "location", "country", "currentCompany",
        "currentTitle", "linkedin", "github", "website", "preferredCities", "languages", "dob",
    }
)  # fmt: skip

OPEN_WAIT_S: Final = 8.0
"""How long a click that opens the form waits for it."""
SUBMIT_WAIT_S: Final = 20.0
"""How long the submit pass waits for the success signature after the click."""
ATTACH_WAIT_S: Final = 30.0
"""How long the submit pass waits for the resume widget to show the file's name."""

_CODE_PROMPT: Final = re.compile(
    r"verification code|security code|one[- ]time (?:pass)?code|\botp\b|confirmation code|"
    r"enter the code|code (?:we )?(?:just )?(?:sent|emailed)|check your (?:e-?mail|inbox) for a code",
    re.I,
)
"""An emailed code the form asks for after the click (spec 3.2: the row becomes ``check_this_one``)."""


def group_of(rule_key: str | None) -> str:
    """The Review group a field is shown under, from the key that placed it (the scan does not read the form's own
    section headings yet): personal information, eligibility, the form's questions, voluntary self-ID, the declaration."""
    if rule_key is None:
        return QUESTIONS_GROUP
    if rule_key in ATTESTATION_KEYS:
        return DECLARATION_GROUP
    if rule_key in SELF_ID_KEYS:
        return SELF_ID_GROUP
    if rule_key in COUNTRY_KEYS:
        return ELIGIBILITY_GROUP
    if rule_key in _PERSONAL_KEYS:
        return PERSONAL_GROUP
    return QUESTIONS_GROUP


def read_back_rows(scan: LiveScan) -> list[tuple[str, str | None]]:
    """``(stable key, live value)`` of every field a step can act on, files left out (the resume is in the digest by its
    sha256)."""
    return [(stable_key(f), f.value) for f in scan.actionable if f.kind != "file"]


def read_back_digest(scan: LiveScan, resume_sha: str | None) -> str:
    return digest_rows(read_back_rows(scan), resume_sha)


async def _snap(driver: BrowserDriver) -> bytes | None:
    try:
        return await driver.screenshot()
    except Exception:
        return None


async def _open_form(driver: BrowserDriver, playbook: Playbook, *, wait_s: float) -> LiveScan:
    """The live scan once the form is on screen: the playbook's open texts are clicked in turn (a cookie banner's
    "Deny", "Apply for this job") until it is."""
    scan = await driver.scan()
    for text in playbook.open_texts:
        if scan.has_form:
            break
        if await driver.click_text(text, form_on_screen=scan.has_form) == "clicked":
            scan = await driver.wait_for_form(wait_s)
    return scan


async def _stop_of(driver: BrowserDriver, scan: LiveScan) -> tuple[StopReason, str, str] | None:
    """A CAPTCHA or a login wall on screen: (stop reason, metrics reason, note)."""
    page = await driver.content()
    blocker = blocker_of(page)
    if blocker == "captcha":
        return (
            "challenge",
            "captcha",
            "The site asks for a human check (CAPTCHA). Open the form to finish it yourself.",
        )
    if blocker == "login_wall" or scan.blockers.password_field:
        return (
            "signin",
            "login_wall",
            "The site wants you to sign in first. Open the form to sign in and apply.",
        )
    return None


def _closed_text(text: str, playbook: Playbook) -> str | None:
    found = closed_of(text, None)
    if found is not None:
        return found
    if playbook.closed_text and re.search(playbook.closed_text, text, re.I):
        return "closed"
    return None


def _merge_loop(plan: Plan, run: LoopRun) -> None:
    for planned in run.filled:
        key = planned.item.key
        plan.filled = [p for p in plan.filled if p.item.key != key] + [planned]
        plan.needs = [n for n in plan.needs if n.key != key]
        plan.skipped = [s for s in plan.skipped if s.key != key]
        if planned.question_key:
            plan.question_keys[key] = planned.question_key
        if planned.source == "model":
            plan.model_fields += 1
    for need in run.needs:
        plan.needs = [n for n in plan.needs if n.key != need.key] + [need]


def _not_stuck_need(planned_item: FormField, plan: Plan, ctx: FillContext) -> NeedsInput:
    qkey = plan.question_keys.get(planned_item.key) or question_key(planned_item)
    return NeedsInput(
        key=planned_item.key, label=planned_item.label, kind=planned_item.kind, required=planned_item.required,
        reason="The form did not keep this value. Check it here, or on the employer's page.",
        options=[text for _, text in planned_item.options] or None, questionKey=qkey,
        requiredBy=ctx.company if (planned_item.required and ctx.company) else None,
    )  # fmt: skip


async def _execute_plan(
    driver: BrowserDriver, plan: Plan, playbook: Playbook, keys: set[str] | None = None
) -> int:
    steps = 0
    for planned in plan.filled:
        if keys is not None and planned.item.key not in keys:
            continue
        op = step_op(planned.item, playbook)
        if op == "upload":
            continue  # never in a fill: the resume waits for the approved submit
        try:
            await run_step(
                driver, planned.item.selector, op, planned.value, planned.item.frame, name=planned.item.name
            )
            steps += 1
        except Exception as exc:
            log.info("apply_step_failed", op=op, error=type(exc).__name__)
    return steps


def _script(plan: Plan, playbook: Playbook) -> list[ScriptStep]:
    steps: list[ScriptStep] = []
    for planned in sorted(plan.filled, key=lambda p: (p.order is None, p.order or 0)):
        op = step_op(planned.item, playbook)
        if op == "upload" or not planned.item.selector:
            continue
        steps.append(
            ScriptStep(
                selector=planned.item.selector, op=op, frame=planned.item.frame, value=planned.value,
                optionValue=planned.option_value,
            )
        )  # fmt: skip
    return steps


def _review_fields(plan: Plan, final: LiveScan) -> list[FilledField]:
    rows: list[FilledField] = []
    by_key = {p.item.key: p for p in plan.filled}
    for row in plan.review_fields():
        planned = by_key[row.key]
        live = find_live(final, planned.item)
        value = live.value if live is not None and live.value.strip() else row.value
        rows.append(row.model_copy(update={"value": value, "group": group_of(planned.profile_key)}))
    return sorted(rows, key=lambda r: (r.order is None, r.order or 0))


def _cited(plan: Plan, ctx: FillContext) -> list[CitedFact]:
    known = {str(f.get("id")): str(f.get("text", "")) for f in ctx.facts}
    ids = [i for p in plan.filled for i in (p.fact_ids or [])]
    ids += [i for n in plan.needs for i in (n.factIds or [])]
    return [CitedFact(id=i, text=known[i]) for i in dict.fromkeys(ids) if i in known]


def _with_live(skipped: list[FilledField], scan: LiveScan) -> list[FilledField]:
    """Skipped rows with the value the page itself holds (a pre-ticked box, a default-selected option): the digest signs
    every live value, so Review shows each of them."""
    live = {f.key: f for f in scan.actionable if f.kind != "file"}
    rows: list[FilledField] = []
    for row in skipped:
        found = live.get(row.key)
        if found is not None and found.value.strip() and not row.value:
            row = row.model_copy(update={"value": found.value})
        rows.append(row)
    return rows


async def preview(
    driver: BrowserDriver,
    playbook: Playbook,
    url: str,
    ctx: FillContext,
    answerer: Answerer | None,
    loop: FieldListLoop | None = None,
    *,
    request_id: str | None = None,
    open_wait_s: float = OPEN_WAIT_S,
) -> FillResult:
    """The fill pass: everything a review needs, nothing sent. The driver comes from ``PageOpener.new_page('preview')``,
    whose probe already came back blocked; this never calls ``allow_submit_hosts`` and never uploads.

    Order: open the page (gone -> ``closed``), open the form (the playbook's open texts, then the loop's clicks), stop on
    a login wall or a CAPTCHA (``needs_signin``), plan every field (`answers.plan_fields`), draft open questions from
    cited facts, execute, re-scan and retry a field that did not stick once (then it is a question), run the field-list
    loop when a required field the rules could not place is still empty, then read the form back for the review and its
    digest. `llm.errors.SpendCeilingReached` from a model call is not caught: the row stays queued."""
    steps = 0
    try:
        status = await driver.goto(url)
        steps += 1
    except Exception as exc:
        return FillResult("failed", reason="navigation", note=f"The page did not open ({type(exc).__name__}).",
                          stop_reason="fill_error", steps=steps)  # fmt: skip
    gone = closed_of("", status)
    if gone is not None:
        return FillResult(
            "closed", reason=gone, note="The posting is no longer on the employer's site.", steps=steps
        )
    scan = await _open_form(driver, playbook, wait_s=open_wait_s)
    opener: LoopRun | None = None
    if not scan.has_form and loop is not None and (await _stop_of(driver, scan)) is None:
        scan, opener = await loop.open_form(driver, ctx, request_id=request_id)
    spent = opener.cost_micro_usd if opener else 0
    stop = await _stop_of(driver, scan)
    if stop is not None:
        reason, metric, note = stop
        return FillResult("needs_signin", reason=metric, note=note, screenshot=await _snap(driver), steps=steps,
                          stop_reason=reason, cost_micro_usd=spent)  # fmt: skip
    if not scan.has_form:
        closed = _closed_text(await driver.visible_text(), playbook)
        if closed is not None:
            return FillResult("closed", reason=closed, note="The posting is no longer taking applications.",
                              steps=steps, cost_micro_usd=spent)  # fmt: skip
        if opener is not None and opener.blocked in ("captcha", "login"):
            reason = "challenge" if opener.blocked == "captcha" else "signin"
            return FillResult("needs_signin", reason=opener.blocked, note="The site needs you before the form opens.",
                              screenshot=await _snap(driver), steps=steps, stop_reason=reason, cost_micro_usd=spent)  # fmt: skip
        return FillResult("failed", reason="no_form", note="No application form on the page.", steps=steps,
                          stop_reason="fill_error", cost_micro_usd=spent)  # fmt: skip

    fields = scan.actionable
    plan, questions = plan_fields(fields, playbook, ctx)
    # A label that gives an AI instructions is reported and never sent to a model (CLAUDE.md rule 3); it stays a question.
    flagged = [q.label for q in questions if injected(q.label)]
    asked = [q for q in questions if not injected(q.label)]
    drafted = await answerer.answer(asked, ctx) if (answerer is not None and asked) else {}
    apply_model_answers(plan, questions, drafted, ctx)
    steps += await _execute_plan(driver, plan, playbook)

    need_keys = {n.key for n in plan.needs}
    checked = verify(plan, await driver.scan(), "preview", skip=need_keys)
    if checked.not_stuck:
        steps += await _execute_plan(driver, plan, playbook, set(checked.not_stuck))  # one retry
        checked = verify(plan, await driver.scan(), "preview", skip=need_keys)
    not_stuck = set(checked.not_stuck)
    for planned in [p for p in plan.filled if p.item.key in not_stuck]:
        plan.filled.remove(planned)
        plan.needs = [n for n in plan.needs if n.key != planned.item.key] + [
            _not_stuck_need(planned.item, plan, ctx)
        ]

    run: LoopRun | None = None
    filled = {p.item.key for p in plan.filled}
    open_keys = {
        f.key
        for f in fields
        if f.key not in filled
        and not hidden_from_model(f, playbook)
        and (rule_for(f, playbook) is None or f.key in not_stuck)
    }
    still_empty = {n.key for n in plan.needs if n.required} & open_keys
    if loop is not None and still_empty:
        run = await loop.fill(driver, plan, ctx, playbook, open_keys=open_keys, request_id=request_id)
        _merge_loop(plan, run)

    final = await driver.scan()
    late = verify(plan, final, "preview", skip={n.key for n in plan.needs})
    for planned in [p for p in plan.filled if p.item.key in set(late.not_stuck)]:
        plan.filled.remove(planned)
        plan.needs = [n for n in plan.needs if n.key != planned.item.key] + [
            _not_stuck_need(planned.item, plan, ctx)
        ]

    page = await driver.content()
    warnings = [BOT_CHECK_WARNING] if checks_on_submit(page) else []
    waiting = any(n.required for n in plan.needs)
    calls = (opener.calls if opener else 0) + (run.calls if run else 0)
    model = (run.model if run and run.model else None) or (opener.model if opener else None)
    if model is None and plan.model_fields:
        model = getattr(answerer, "model", None)
    anomalies = list(
        dict.fromkeys([*flagged, *(opener.anomalies if opener else []), *(run.anomalies if run else [])])
    )
    refused = [*(opener.refused if opener else []), *(run.refused if run else [])]
    return FillResult(
        "needs_user" if waiting else "ready", plan, "needs_answers" if waiting else None, None, await _snap(driver),
        scan.blockers.submit_texts[0] if scan.blockers.submit_texts else None, warnings, len(fields), steps,
        stop_reason="questions" if waiting else None, fields=_review_fields(plan, final), needs=list(plan.needs),
        skipped=_with_live(list(plan.skipped), final), script=_script(plan, playbook),
        digest=read_back_digest(final, plan.resume.sha256 if plan.resume else None), resume=plan.resume,
        facts=_cited(plan, ctx), mode="loop" if calls else "playbook", model=model,
        cost_micro_usd=(opener.cost_micro_usd if opener else 0) + (run.cost_micro_usd if run else 0),
        anomalies=anomalies, refused=refused,
    )  # fmt: skip


def review_of(
    result: FillResult,
    *,
    captured_at: str,
    resume_choice: ResumeChoice = "saved",
    playbook_id: str | None = None,
) -> ApplicationReview:
    """The `contract.apply.ApplicationReview` to store for a preview (or for a ``form_changed`` submit's new read)."""
    resume = (
        ReviewResume(name=result.resume.name, sha256=result.resume.sha256, choice=resume_choice)
        if result.resume is not None
        else None
    )
    return ApplicationReview(
        digest=result.digest or "", capturedAt=captured_at, mode=result.mode, fields=result.fields,
        skipped=result.skipped, warnings=result.warnings, playbookId=playbook_id, submitLabel=result.submit_label,
        resume=resume, facts=result.facts or None, script=result.script or None, model=result.model,
    )  # fmt: skip


# ───────────────────────── A1: the approved submit ─────────────────────────

MarkSubmitting = Callable[[], Awaitable[bool]]
"""Commits ``approved -> submitting`` (with ``submitting_at``) as a compare-and-set; False when the row moved meanwhile
(the person cancelled): then nothing is clicked."""


def resume_field(scan: LiveScan, playbook: Playbook) -> FormField | None:
    """The form's resume input in this scan (placed the way the planner places it), or None."""
    for item in scan.actionable:
        if item.kind == "file" and rule_for(item, playbook) == "resume":
            return item
    return None


def attach_name(resume: Resume, ctx: FillContext) -> str | None:
    """The file name the resume is attached under: its own when it is specific enough to recognise on the page
    (`scan.resume_name_inferable`), else ``<First>-<Last>-Resume.<ext>`` from the person's details; None when neither is,
    because then the widget's showing the file cannot be confirmed."""
    if resume_name_inferable(resume.name):
        return resume.name
    ext = re.search(r"\.[a-z0-9]{2,5}$", resume.name, re.I)
    bank = {k: (e.value or "") for k, e in ctx.bank.items()}
    full = bank.get("fullName") or ctx.details.get("fullName") or " ".join(
        p for p in (bank.get("firstName") or ctx.details.get("firstName"), bank.get("lastName") or ctx.details.get("lastName")) if p
    )  # fmt: skip
    stem = "-".join(re.findall(r"[A-Za-z0-9]+", full or ""))
    if not stem:
        return None
    name = f"{stem}-Resume{ext.group(0) if ext else '.pdf'}"
    return name if resume_name_inferable(name) else None


def _resume_shown(scan: LiveScan) -> bool:
    return scan.blockers.resume_name_on_page


def _count(pattern: str | re.Pattern[str] | None, text: str) -> int:
    if not pattern:
        return 0
    return len(re.findall(pattern, text, re.I)) if isinstance(pattern, str) else len(pattern.findall(text))


async def _changed(
    driver: BrowserDriver, review: ApplicationReview, final: LiveScan, ctx: FillContext, playbook: Playbook,
    resume: ResumePlan | None, steps: int,
) -> FillResult:  # fmt: skip
    """The form as it reads now, for the person to check again. The review's rows keep their live values (rows gone from
    the page are dropped). Fields that are new go through the planner's own rules (`answers.plan_fields`), so an ID
    number stays an on-site need, a work-authorisation question keeps its per-country key, self-ID defaults to decline and
    a required consent tick is the person's. What the rules can place is filled here (no upload, the guard still up) and
    becomes a row; the digest is read back after that."""
    known_selectors = {r.selector for r in review.fields if r.selector} | {
        st.selector for st in review.script or []
    }
    known_keys = {r.key for r in review.fields} | {sk.key for sk in review.skipped}
    if resume is not None:
        known_keys.add(resume.key)
    fresh = [
        f for f in final.actionable
        if f.key not in known_keys and not (f.selector and f.selector in known_selectors)
    ]  # fmt: skip
    plan, questions = plan_fields(fresh, playbook, ctx)
    plan.resume = None  # the resume is the caller's (`resume`), signed by its sha256
    positions = {f.key: i for i, f in enumerate(final.actionable)}
    for planned in plan.filled:
        planned.order = positions.get(planned.item.key, planned.order)
    for q in questions:  # open questions: no model at submit time, so they are the person's
        if q.required and not q.value.strip():
            plan.needs.append(
                NeedsInput(
                    key=q.key, label=q.label, kind=q.kind, required=True,
                    questionKey=plan.question_keys.get(q.key) or question_key(q),
                    reason="This form changed since you approved it. Answer this for this form.",
                    options=[t for _, t in q.options] or None, perOffer=True, requiredBy=ctx.company or None,
                )
            )  # fmt: skip
        else:
            plan.skipped.append(FilledField(key=q.key, label=q.label, kind=q.kind, required=q.required))
    if plan.filled:
        steps += await _execute_plan(driver, plan, playbook)
        final = await driver.scan()
        checked = verify(plan, final, "preview", skip={n.key for n in plan.needs})
        not_stuck = set(checked.not_stuck)
        for planned in [p for p in plan.filled if p.item.key in not_stuck]:
            plan.filled.remove(planned)
            plan.needs = [n for n in plan.needs if n.key != planned.item.key] + [
                _not_stuck_need(planned.item, plan, ctx)
            ]
    by_selector = {f.selector: f for f in final.actionable if f.selector}
    rows: list[FilledField] = []
    for row in review.fields:
        live = by_selector.get(row.selector or "")
        if live is not None:
            rows.append(row.model_copy(update={"value": live.value or None}))
    rows = sorted([*rows, *_review_fields(plan, final)], key=lambda r: (r.order is None, r.order or 0))
    script = [st for st in review.script or [] if st.selector in by_selector] + _script(plan, playbook)
    digest = read_back_digest(final, resume.sha256 if resume is not None else None)
    needs = list(plan.needs)
    waiting = any(n.required for n in needs)
    return FillResult(
        "needs_user" if waiting else "ready", None, "form_changed", "The form changed since you approved it: check it again.",
        await _snap(driver), review.submitLabel, list(review.warnings), len(final.fields), steps,
        stop_reason="form_changed", fields=rows, needs=needs,
        skipped=_with_live([*review.skipped, *plan.skipped], final), script=script, digest=digest, resume=resume,
        facts=list(review.facts or []), mode=review.mode, model=review.model,
    )  # fmt: skip


def _form_frame(scan: LiveScan) -> int:
    """The frame most of the form's fields are in (its submit control is there too); 0 when the scan has none."""
    frames = [f.frame for f in scan.actionable]
    return max(set(frames), key=frames.count) if frames else 0


async def _replay(driver: BrowserDriver, script: list[ScriptStep], scan: LiveScan) -> int:
    """Run each step on the field with its selector in this browser's scan (in the frame it is in here). A step whose
    field is gone is skipped: the read-back digest then says the form changed."""
    steps = 0
    for step in script:
        live = next((f for f in scan.actionable if f.selector == step.selector), None)
        if live is None:
            continue
        try:
            await run_step(driver, step.selector, step.op, step.value or "", live.frame, name=live.name)
            steps += 1
        except Exception as exc:
            log.info("apply_replay_step_failed", op=step.op, error=type(exc).__name__)
    return steps


async def submit_approved(
    driver: BrowserDriver,
    review: ApplicationReview,
    ctx: FillContext,
    *,
    url: str,
    playbook: Playbook,
    mark_submitting: MarkSubmitting,
    resume: Resume | None = None,
    wait_s: float = SUBMIT_WAIT_S,
    attach_wait_s: float = ATTACH_WAIT_S,
    open_wait_s: float = OPEN_WAIT_S,
    poll_s: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> SubmitResult:
    """Send the approved review once. The driver comes from ``PageOpener.new_page('submit', submit and upload hosts)``.
    ``resume`` is the file to attach (default ``ctx.resume``); its sha256 is in the digest.

    Before the click every stop is ``failed`` with ``clicked=False`` (the engine releases the hold), except a changed
    form, which is ``form_changed`` with the new read in ``review``. ``mark_submitting`` returning False is ``failed`` with
    reason ``not_marked``: the row moved (the person cancelled) and is left as it is. Between the marker and the click
    attempt every stop, an exception included, is still ``failed`` with ``clicked=False`` (``submitting -> failed``); the
    digest is read back once more after the attach (``form_changed_after_attach``). Only once ``click_submit`` is
    attempted is the outcome the success signature (``sent``) or else ``check_this_one``, never retried."""
    resume = resume if resume is not None else ctx.resume
    steps = 0

    def failed(reason: str, note: str, shot: bytes | None = None) -> SubmitResult:
        log.info("apply_submit_stopped", reason=reason, clicked=False)
        return SubmitResult("failed", reason, note, shot, steps, clicked=False)

    try:
        status = await driver.goto(url)
        steps += 1
    except Exception as exc:
        return failed("navigation", f"The form did not open ({type(exc).__name__}). Nothing was sent.")
    if closed_of("", status) is not None:
        return failed("closed", "The posting is no longer on the employer's site. Nothing was sent.")
    scan = await _open_form(driver, playbook, wait_s=open_wait_s)
    stop = await _stop_of(driver, scan)
    if stop is not None:
        return failed(stop[0], stop[2], await _snap(driver))
    if not scan.has_form:
        if _closed_text(await driver.visible_text(), playbook) is not None:
            return failed("closed", "The posting is no longer taking applications. Nothing was sent.")
        return failed("no_form", "The form did not open. Nothing was sent.", await _snap(driver))
    if review.resume is not None and resume is None:
        return failed("no_resume", "The resume for this application is missing. Nothing was sent.")

    steps += await _replay(driver, list(review.script or []), scan)
    after = await driver.scan()
    retry = [
        s for s in review.script or []
        if (live := next((f for f in after.actionable if f.selector == s.selector), None)) is not None
        and not value_matches(live.kind, s.value or "", live.value)
    ]  # fmt: skip
    if retry:
        steps += await _replay(driver, retry, after)  # one retry for a widget that did not keep the value
    final = await driver.scan()
    field_for_resume = resume_field(final, playbook) if resume is not None else None
    resume_plan = (
        ResumePlan(
            resume.name,
            resume.sha256,
            resume.media_type,
            field_for_resume.key,
            field_for_resume.selector,
            field_for_resume.required,
        )
        if resume is not None and field_for_resume is not None
        else None
    )
    # As in the preview: the resume is signed only when the form has a resume input to attach it to.
    digest = read_back_digest(final, resume_plan.sha256 if resume_plan is not None else None)
    if digest != review.digest or (review.resume is not None and field_for_resume is None):
        log.info("apply_submit_stopped", reason="form_changed", clicked=False)
        changed = await _changed(driver, review, final, ctx, playbook, resume_plan, steps)
        return SubmitResult(
            "form_changed", "form_changed", changed.note, changed.screenshot, changed.steps, clicked=False,
            review=changed,
        )  # fmt: skip

    form_frame = _form_frame(final)
    if not await driver.exists(playbook.submit_selector, form_frame):
        return failed(
            "submit_missing", "The form's submit button is not there. Nothing was sent.", await _snap(driver)
        )
    if not playbook.submit_hosts:
        # The guard could only be lifted for no host, so the click could never go through.
        return failed("no_submit_hosts", "We do not know where this form sends to, so nothing was sent.")
    name: str | None = None
    if resume is not None and field_for_resume is not None:
        name = attach_name(resume, ctx)
        if name is None:
            return failed(
                "resume_name", "The resume's file name cannot be recognised on the form. Nothing was sent."
            )
        if _resume_shown(await driver.scan(name)):
            # The flag must go from false to true across the attach, or the attach cannot be confirmed.
            return failed("resume_check", "The form already shows a file with this name. Nothing was sent.")

    if not await mark_submitting():
        return failed(
            "not_marked", "The application changed before sending (it was cancelled). Nothing was sent."
        )
    # From here to the click attempt nothing has been sent: any stop, an exception included, is failed, clicked=False.
    try:
        driver.allow_submit_hosts([*playbook.submit_hosts, *playbook.upload_hosts])
        if resume is not None and field_for_resume is not None and name is not None:
            try:
                await driver.upload(
                    field_for_resume.selector, name, resume.data, resume.media_type, field_for_resume.frame
                )
                steps += 1
            except Exception as exc:
                return failed(
                    "attach_failed",
                    f"The resume could not be attached ({type(exc).__name__}). Nothing was sent.",
                )
            deadline = monotonic() + attach_wait_s
            while not _resume_shown(await driver.scan(name)):
                if monotonic() >= deadline:
                    return failed("resume_not_attached", "The form did not show the resume as attached. Nothing was sent.",
                                  await _snap(driver))  # fmt: skip
                await sleep(poll_s)
        # The digest once more, just before the click (CLAUDE.md rule 9): a resume parser or a page script may change
        # values on attach, and the employer must get exactly what the person approved.
        last = await driver.scan(name or "")
        if read_back_digest(last, resume_plan.sha256 if resume_plan is not None else None) != digest:
            return failed(
                "form_changed_after_attach",
                "The form changed its values when the resume was attached. Nothing was sent: check it again.",
                await _snap(driver),
            )
        before_url, before_text = await driver.current_url(), await driver.visible_text()
    except Exception as exc:
        log.warning("apply_submit_pre_click_error", error=type(exc).__name__)
        return failed(
            "pre_click_error", f"Sending stopped before the click ({type(exc).__name__}). Nothing was sent."
        )
    before_fields = {f.key for f in final.fields}
    try:
        await driver.click_submit(playbook.submit_selector, form_frame)
        steps += 1
    except Exception as exc:
        log.warning("apply_submit_click_error", error=type(exc).__name__)
        return SubmitResult("check_this_one", "click_error", "The send was started but did not finish cleanly. Check it on the employer's page.",
                            await _snap(driver), steps, clicked=True)  # fmt: skip
    try:
        return await _outcome(
            driver, playbook, before_url, before_text, before_fields, steps, wait_s, poll_s, sleep, monotonic
        )
    except Exception as exc:
        log.warning("apply_submit_outcome_error", error=type(exc).__name__)
        return SubmitResult("check_this_one", "error", "We could not read what happened after sending. Check it on the employer's page.",
                            await _snap(driver), steps, clicked=True)  # fmt: skip


async def _outcome(
    driver: BrowserDriver, playbook: Playbook, before_url: str, before_text: str, before_fields: set[str], steps: int,
    wait_s: float, poll_s: float, sleep: Callable[[float], Awaitable[None]], monotonic: Callable[[], float],
) -> SubmitResult:  # fmt: skip
    """Poll the page after the click: only the success signature (new on the page) is ``sent``."""
    deadline = monotonic() + wait_s

    def unclear(reason: str, note: str, shot: bytes | None) -> SubmitResult:
        log.info("apply_submit_outcome", status="check_this_one", reason=reason)
        return SubmitResult("check_this_one", reason, note, shot, steps, clicked=True)

    while True:
        here, text = await driver.current_url(), await driver.visible_text()
        moved = (
            bool(playbook.success_url)
            and here != before_url
            and bool(re.search(playbook.success_url or "", here, re.I))
        )
        if moved or _count(playbook.success_text, text) > _count(playbook.success_text, before_text):
            log.info("apply_submit_outcome", status="sent")
            return SubmitResult("sent", None, None, await _snap(driver), steps, clicked=True)
        live = await driver.scan()
        asks_code = _count(_CODE_PROMPT, text) > _count(_CODE_PROMPT, before_text) or any(
            f.key not in before_fields and _CODE_PROMPT.search(f.label) for f in live.fields
        )
        if asks_code:
            return unclear("verification_code", "The form asks for a code it emailed you. Open the form, type the code and send it there.",
                           await _snap(driver))  # fmt: skip
        if blocker_of(await driver.content()) == "captcha":
            return unclear("challenge", "The site asked for a human check after sending. Check it on the employer's page.",
                           await _snap(driver))  # fmt: skip
        if _count(playbook.error_text, text) > _count(playbook.error_text, before_text):
            return unclear("form_error", "The form showed an error after sending. Check it on the employer's page.",
                           await _snap(driver))  # fmt: skip
        if monotonic() >= deadline:
            return unclear("no_confirmation", "No confirmation page after sending. Check it on the employer's page.",
                           await _snap(driver))  # fmt: skip
        await sleep(poll_s)


__all__ = [
    "ATTACH_WAIT_S",
    "BOT_CHECK_WARNING",
    "DECLARATION_GROUP",
    "ELIGIBILITY_GROUP",
    "OPEN_WAIT_S",
    "PERSONAL_GROUP",
    "QUESTIONS_GROUP",
    "SELF_ID_GROUP",
    "SUBMIT_WAIT_S",
    "FillResult",
    "FillStatus",
    "MarkSubmitting",
    "SubmitResult",
    "SubmitStatus",
    "attach_name",
    "group_of",
    "preview",
    "read_back_digest",
    "read_back_rows",
    "resume_field",
    "review_of",
    "submit_approved",
]
