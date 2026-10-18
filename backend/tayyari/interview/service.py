"""Practice the interview: plan, start, answer, hand in work, end, and everything a person can do with a finished one.

    plan      code only: `plan.build_plan` from the person's evidence, a role's asks and gaps (a search's) or requirements (the
              graph's), or a work-sample template.
    start     the plan again (the same inputs give the same plan), the slots the person skipped dropped, one phrasing call
              (`phrase`), the first question asked. Everything is stored with the session.
    answer    `router.classify` decides what the turn is. "Say it again", "skip", "I don't know", a dispute and an answer too
              short to judge cost no model call; anything else is judged once (`judge`), and `flow` picks the next line.
    end       the debrief: counts, the first thing to work on and the summary in code; Say, Avoid and Fix from one model call,
              checked against the person's words; a work sample's checks from one more. Stored, then returned as stored.

Every write is a compare-and-swap through `InterviewRepo.save` (the version goes up by one), so two tabs cannot overwrite
each other: the loser gets `InterviewMovedOn`. A model that fails while a person answers raises (nothing is recorded, they can
answer again); one that fails while the debrief is written gives the debrief from code alone.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import replace

from tayyari.contract.interview import (
    AnswerRequest,
    AssistantReply,
    AssistantRequest,
    AssistantTurn,
    CheckResult,
    Debrief,
    FactRequest,
    InterviewLine,
    InterviewPlan,
    InterviewSession,
    PlanRequest,
    PracticeHistory,
    SessionView,
    StartRequest,
    WorkSubmit,
)
from tayyari.contract.models import Profile
from tayyari.core.clock import Clock
from tayyari.core.errors import BadRequest, ServiceUnavailable
from tayyari.core.logging import get_logger
from tayyari.core.settings import InterviewSettings
from tayyari.core.text import clip
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError
from tayyari.llm.types import CallContext
from tayyari.storage.ports import InterviewRepo, StoredInterview

from . import flow
from .assistant import ask_assistant
from .checks import assistant_use, run_checks, unassessed
from .debrief import Outcome, assemble, check_written, outcomes, write_debrief
from .errors import (
    AssistantUnavailable,
    BadPlan,
    InterviewEnded,
    InterviewMovedOn,
    InterviewNotFound,
    ModelUnavailable,
    WorkFirst,
)
from .history import build_history, debrief_of
from .judge import check_judgement, judge_answer
from .phrase import phrase_slots
from .plan import build_plan, plan_minutes
from .roles import RoleLookup
from .router import classify
from .speech import SpeechContext, SpeechUpcoming, speech_context, upcoming_of
from .state import SessionState, SlotState, as_utc, read_state, seconds_since, session_of

log = get_logger(__name__)

WORK_CONTEXT_CHARS = 4000
"""How much of a work sample's task, work and note the judge sees when it reads an answer about them."""
AGAIN_WORK_MINUTES = 3
NOT_ENOUGH = "Your profile doesn't have enough to ask about yet. Add a project or a link, then try again."
SWITCHED_OFF = "Practice is switched off right now."


class InterviewService:
    def __init__(
        self,
        *,
        claude: ClaudeClient,
        settings: InterviewSettings,
        sessions: InterviewRepo,
        roles: RoleLookup,
        clock: Clock,
    ) -> None:
        self._claude = claude
        self._settings = settings
        self._sessions = sessions
        self._roles = roles
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def require_enabled(self) -> None:
        if not self.enabled:
            raise ServiceUnavailable(SWITCHED_OFF)

    # ───────────────────────── plan and start ─────────────────────────

    async def plan(self, profile: Profile, req: PlanRequest) -> InterviewPlan:
        """The plan for a kind of practice. No model call."""
        return (await build_plan(profile, req, self._roles)).plan

    async def start(self, profile: Profile, req: StartRequest, ctx: CallContext) -> InterviewSession:
        """Start an interview: the plan, minus the slots the person skipped, phrased once, with its first question asked."""
        self.require_enabled()
        built = await build_plan(
            profile,
            PlanRequest(
                profileId=profile.id,
                kind=req.kind,
                role=req.role,
                graphRole=req.graphRole,
                length=req.length,
                focus=req.focus,
            ),
            self._roles,
        )
        plan = built.plan
        skipped = set(req.skip or [])
        slots = [slot for slot in plan.slots if slot.id not in skipped]
        if not slots:
            raise BadPlan("Keep at least one question." if plan.slots else NOT_ENOUGH)
        template = built.template
        call = self._ctx(profile, ctx)
        if template is not None:
            follow = {f"s{i + 1}": f for i, f in enumerate(template.follow_ups)}
            states = [
                SlotState(
                    id=slot.id,
                    covers=list(follow[slot.id].covers),
                    question=follow[slot.id].question,
                    needsAssistant=follow[slot.id].about_assistant,
                )
                for slot in slots
            ]
        else:
            job = built.job
            lines = await phrase_slots(
                claude=self._claude,
                model=self._settings.model,
                slots=slots,
                title=built.title,
                employer=job.employer if job is not None else None,
                posting_text=built.posting_text,
                posting_chars=self._settings.posting_chars,
                ctx=call,
            )
            states = [
                SlotState(
                    id=slot.id,
                    covers=list(lines[slot.id].covers),
                    question=lines[slot.id].question,
                    push=lines[slot.id].push,
                    floor=lines[slot.id].floor,
                )
                for slot in slots
            ]
        state = SessionState(
            kind=plan.kind,
            title=plan.title,
            minutes=plan_minutes(plan.kind, len(slots), template),
            slots=slots,
            slotStates=states,
            role=plan.role,
            graphRole=plan.graphRole,
            employer=built.job.employer if built.job is not None else None,
            task=template.to_task() if template is not None else None,
            checks=list(template.checks) if template is not None else [],
        )
        if template is not None:
            flow.present(state, InterviewLine(kind="question", text=template.task), 0)
        else:
            flow.begin(state, 0)
        now = self._clock.now()
        item = StoredInterview(
            id=secrets.token_hex(16),
            profile_id=profile.id,
            kind=plan.kind,
            status="active",
            created_at=now,
            updated_at=now,
            version=1,
            state=state.dump(),
            debrief=None,
        )
        await self._sessions.create(item)
        log.info("interview_started", kind=plan.kind, slots=len(slots))
        return session_of(item, state)

    # ───────────────────────── the interview ─────────────────────────

    async def answer(
        self, profile: Profile, session_id: str, req: AnswerRequest, ctx: CallContext
    ) -> InterviewSession:
        """The person's turn: a button or words. The interviewer's next line comes back with the session."""
        self.require_enabled()
        item, state = await self._open(profile, session_id)
        if state.kind == "work" and state.work is None:
            raise WorkFirst()
        current = state.slot()
        if current is None:
            raise InterviewEnded()
        slot, st = current
        at = seconds_since(item.created_at, self._clock.now())
        route = classify(req.text, req.action)
        if route == "dispute" and not flow.can_dispute(slot, st):
            route = "answer"
        if route == "repeat":
            flow.repeat(state)
            return session_of(item, state)
        if route == "unclear":
            flow.on_unclear(state, req.text, req.mode, at)
        elif route == "skip":
            flow.on_skip(state, at)
        elif route == "dont_know":
            flow.on_dont_know(state, req.text, req.mode, at)
        elif route == "dispute":
            flow.on_dispute(state, req.text, req.mode, at)
        else:
            line = state.current
            rung = line.rung if line is not None and line.rung is not None else "surface"
            try:
                raw = await judge_answer(
                    claude=self._claude,
                    model=self._settings.model,
                    slot=slot,
                    question=line.text if line is not None else st.question,
                    covers=st.covers,
                    answer=req.text,
                    rung=rung,
                    context=self._work_context(state) if slot.kind == "task" else None,
                    ctx=self._ctx(profile, ctx),
                )
            except ClaudeError as exc:
                raise self._unavailable(exc) from exc
            judged = check_judgement(
                raw,
                answer=req.text,
                kind=slot.kind,
                rung=rung,
                sources=slot.sources,
                question=line.text if line is not None else "",
            )
            log.info(
                "interview_judged", kind=slot.kind, rung=rung, verdict=judged.verdict, dropped=judged.dropped
            )
            flow.on_judged(state, judged, req.text, req.mode, at)
        flow.spend(state, self._settings.max_turns, at)
        return await self._save(item, state)

    async def submit_work(self, profile: Profile, session_id: str, req: WorkSubmit) -> InterviewSession:
        """Hand in a work sample; the interviewer then asks about it. No model call."""
        item, state = await self._open(profile, session_id)
        if state.kind != "work":
            raise BadPlan("This interview has no work to hand in.")
        if state.work is not None:
            return session_of(item, state)
        note = (req.testNote or "").strip()
        if not req.work.strip() and not note:
            raise BadPlan("There's nothing to hand in yet. Write your version first.")
        at = seconds_since(item.created_at, self._clock.now())
        state.work = req.work
        state.testNote = req.testNote
        flow.begin(state, at)
        return await self._save(item, state)

    async def assistant(
        self,
        profile: Profile,
        session_id: str,
        req: AssistantRequest,
        ctx: CallContext,
        *,
        unlimited: bool = False,
    ) -> AssistantReply:
        """One message to the work sample's assistant. Not graded; the debrief only reports how it was used.

        ``unlimited`` (an account on the unlimited list) lifts the per-session cap on messages: it is never told it has used
        them, and ``left`` stays at the full allowance so a client that stops at 0 keeps going. A setting of 0 still means
        "no assistant" for everyone."""
        self.require_enabled()
        item, state = await self._open(profile, session_id)
        limit = self._settings.assistant_messages
        task = state.task
        if state.kind != "work" or task is None or not task.assistant or limit <= 0:
            raise AssistantUnavailable()
        if not unlimited and len(state.assistant) >= limit:
            raise AssistantUnavailable("You've used this session's assistant messages.")
        message = req.message.strip()
        if not message:
            raise BadRequest("Write a message for the assistant first.")
        try:
            reply = await ask_assistant(
                claude=self._claude,
                model=self._settings.model,
                task=task,
                work=req.work if req.work is not None else (state.work or ""),
                earlier=state.assistant,
                message=message,
                ctx=self._ctx(profile, ctx),
            )
        except ClaudeError as exc:
            raise self._unavailable(exc) from exc
        state.assistant.append(AssistantTurn(you=message, assistant=reply))
        await self._save(item, state)
        asked = len(state.assistant)
        return AssistantReply(reply=reply, asked=asked, left=limit if unlimited else max(0, limit - asked))

    async def end(self, profile: Profile, session_id: str, ctx: CallContext) -> Debrief:
        """End the interview (a finished one returns its debrief again) and write the debrief."""
        item, state = await self._load(profile, session_id)
        if item.status == "done":
            return self._debrief(item, state)
        outs = outcomes(state)
        call = self._ctx(profile, ctx)
        if self.enabled:
            written, checks = await asyncio.gather(
                write_debrief(
                    claude=self._claude,
                    model=self._settings.model,
                    outs=outs,
                    title=state.title,
                    ctx=call,
                ),
                self._checks(state, outs, call),
            )
        else:
            written = check_written(None, outs)
            checks = unassessed(state.checks) if state.kind == "work" else None
        debrief = assemble(
            session_id=item.id,
            state=state,
            created_at=as_utc(item.created_at),
            retention_days=self._settings.retention_days,
            written=written,
            checks=checks,
            assistant_use=assistant_use(state.assistant, state.work) if state.kind == "work" else None,
        )
        try:
            await self._save(item, state, status="done", debrief=debrief.to_dict())
        except InterviewMovedOn:
            again = await self._sessions.get(profile.id, session_id)
            stored = debrief_of(again) if again is not None and again.status == "done" else None
            if stored is None:
                raise
            return stored
        log.info("interview_ended", kind=state.kind, counts=debrief.counts.model_dump())
        return debrief

    # ───────────────────────── after the interview ─────────────────────────

    async def view(self, profile: Profile, session_id: str) -> SessionView | None:
        """A session, and its debrief once it is done. None for an unknown id or another profile's."""
        item = await self._sessions.get(profile.id, session_id)
        state = read_state(item) if item is not None else None
        if item is None or state is None:
            return None
        return SessionView(
            session=session_of(item, state),
            debrief=self._debrief(item, state) if item.status == "done" else None,
        )

    async def speech_context(self, profile: Profile, session_id: str) -> SpeechContext | None:
        """What speech may do for a session: whether it can still be answered, the interviewer's own lines the voice may
        read, and the names and terms that help a recogniser. None for an unknown id or another profile's. No model call."""
        item = await self._sessions.get(profile.id, session_id)
        state = read_state(item) if item is not None else None
        if item is None or state is None:
            return None
        return speech_context(state, active=item.status != "done")

    async def speech_upcoming(
        self, profile: Profile, session_id: str, *, followups: bool = True
    ) -> SpeechUpcoming | None:
        """The lines the voice should have ready for a session that is still going: the one being said now, then what may be
        said next (see `tayyari.interview.speech`). None for an unknown id, another profile's, or a finished interview.
        ``followups=False`` leaves out the current question's follow-up and easier question. No model call."""
        item = await self._sessions.get(profile.id, session_id)
        state = read_state(item) if item is not None else None
        if item is None or state is None or item.status == "done":
            return None
        return upcoming_of(state, followups=followups)

    async def history(self, profile: Profile, *, per_day: int | None = None) -> PracticeHistory:
        """The person's practice, newest first, and how many interviews they have left today when ``per_day`` is given."""
        items = await self._sessions.list(profile.id, self._settings.history_limit)
        left: int | None = None
        if per_day is not None:
            midnight = as_utc(self._clock.now()).replace(hour=0, minute=0, second=0, microsecond=0)
            started = sum(1 for i in items if as_utc(i.created_at) >= midnight and not i.state.get("againOf"))
            left = max(0, per_day - started)
        return build_history(items, left_today=left)

    async def again(self, profile: Profile, session_id: str, slot_id: str) -> InterviewSession:
        """Ask one question of a finished interview again: a new interview of that one slot, with its stored lines. No model
        call, and it never counts as a new interview."""
        item, state = await self._load(profile, session_id)
        index = next((i for i, slot in enumerate(state.slots) if slot.id == slot_id), None)
        if index is None:
            raise BadPlan("That question isn't in this interview.")
        slot, st = state.slots[index], state.slotStates[index]
        fresh = SessionState(
            kind=state.kind,
            title=state.title,
            minutes=AGAIN_WORK_MINUTES if state.kind == "work" else plan_minutes(state.kind, 1),
            slots=[slot.model_copy(update={"id": "s1"})],
            slotStates=[
                SlotState(
                    id="s1",
                    covers=list(st.covers),
                    question=st.question,
                    push=st.push,
                    floor=st.floor,
                    needsAssistant=st.needsAssistant,
                )
            ],
            role=state.role,
            graphRole=state.graphRole,
            employer=state.employer,
            task=state.task,
            checks=list(state.checks),
            work=state.work,
            testNote=state.testNote,
            assistant=list(state.assistant),
            againOf=item.id,
        )
        if state.kind == "work" and state.work is None and state.task is not None:
            flow.present(fresh, InterviewLine(kind="question", text=state.task.task), 0)
        else:
            flow.begin(fresh, 0)
        now = self._clock.now()
        new = StoredInterview(
            id=secrets.token_hex(16),
            profile_id=profile.id,
            kind=state.kind,
            status="active",
            created_at=now,
            updated_at=now,
            version=1,
            state=fresh.dump(),
            debrief=None,
        )
        await self._sessions.create(new)
        return session_of(new, fresh)

    async def set_fact(self, profile: Profile, session_id: str, req: FactRequest) -> Debrief | None:
        """Keep (or take back) what the person said as a fact marked "From you". None when there is nothing to keep."""
        item = await self._sessions.get(profile.id, session_id)
        debrief = debrief_of(item) if item is not None and item.status == "done" else None
        if item is None or debrief is None:
            return None
        target = next((r for r in debrief.results if r.slotId == req.slotId), None)
        if target is None or not target.newFact:
            return None
        updated = debrief.model_copy(
            update={
                "results": [
                    r.model_copy(update={"factAdded": req.add}) if r.slotId == req.slotId else r
                    for r in debrief.results
                ]
            }
        )
        saved = replace(
            item, debrief=updated.to_dict(), updated_at=self._clock.now(), version=item.version + 1
        )
        if not await self._sessions.save(saved):
            raise InterviewMovedOn()
        return updated

    async def delete(self, profile: Profile, session_id: str) -> bool:
        """Delete a session and its debrief now. False when there was none."""
        return await self._sessions.delete(profile.id, session_id)

    # ───────────────────────── helpers ─────────────────────────

    async def _load(self, profile: Profile, session_id: str) -> tuple[StoredInterview, SessionState]:
        item = await self._sessions.get(profile.id, session_id)
        state = read_state(item) if item is not None else None
        if item is None or state is None:
            raise InterviewNotFound()
        return item, state

    async def _open(self, profile: Profile, session_id: str) -> tuple[StoredInterview, SessionState]:
        """A session that can still be answered: not another profile's, not ended."""
        item, state = await self._load(profile, session_id)
        if item.status == "done":
            raise InterviewEnded()
        return item, state

    async def _save(
        self,
        item: StoredInterview,
        state: SessionState,
        *,
        status: str = "active",
        debrief: dict[str, object] | None = None,
    ) -> InterviewSession:
        saved = replace(
            item,
            state=state.dump(),
            status=status,
            debrief=debrief,
            updated_at=self._clock.now(),
            version=item.version + 1,
        )
        if not await self._sessions.save(saved):
            raise InterviewMovedOn()
        return session_of(saved, state)

    @staticmethod
    def _unavailable(exc: ClaudeError) -> ModelUnavailable:
        """A model that failed while the person waited: a 503 with the client's own sentence (busy, cut off ...), and the wait
        it asked for. Nothing was saved, so they can answer again."""
        log.warning("interview_model_failed", kind=exc.kind)
        return ModelUnavailable(exc.user_message, retry_after_s=getattr(exc, "retry_after_s", None))

    @staticmethod
    def _ctx(profile: Profile, ctx: CallContext) -> CallContext:
        return ctx if ctx.profile_id else replace(ctx, profile_id=profile.id)

    @staticmethod
    def _work_context(state: SessionState) -> str | None:
        """What the judge may read to understand an answer about a work sample: the task, the work, the note, and what was
        asked of the assistant. Context only: quotes are still taken from the answer."""
        task = state.task
        if task is None:
            return None
        parts = [f"Task: {task.task}", f"The person's work ({task.materialName}):\n{state.work or '(none)'}"]
        if state.testNote:
            parts.append(f"How they would test it: {state.testNote}")
        if state.assistant:
            parts.append("They asked the assistant: " + " | ".join(t.you for t in state.assistant))
        return clip("\n\n".join(parts), WORK_CONTEXT_CHARS)

    async def _checks(
        self, state: SessionState, outs: list[Outcome], ctx: CallContext
    ) -> list[CheckResult] | None:
        """A work sample's checks judged (all Not assessed when no work was handed in); None for the other kinds."""
        task = state.task
        if state.kind != "work" or task is None:
            return None
        if state.work is None:
            return unassessed(state.checks)
        return await run_checks(
            claude=self._claude,
            model=self._settings.model,
            task=task,
            checks=state.checks,
            work=state.work,
            test_note=state.testNote or "",
            answers=[(o.question, o.answer_text) for o in outs if o.answers],
            ctx=ctx,
        )

    def _debrief(self, item: StoredInterview, state: SessionState) -> Debrief:
        """The debrief stored with a finished session; if it cannot be read, one from code alone (never stored)."""
        stored = debrief_of(item)
        if stored is not None:
            return stored
        outs = outcomes(state)
        return assemble(
            session_id=item.id,
            state=state,
            created_at=as_utc(item.created_at),
            retention_days=self._settings.retention_days,
            written=check_written(None, outs),
            checks=unassessed(state.checks) if state.kind == "work" else None,
        )


__all__ = ["InterviewService"]
