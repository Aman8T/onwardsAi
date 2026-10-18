"""The Prepare service: the plan for a role, assembled in code, written into by two model calls at once, checked, stored.

    load_profile   the profile and its context, the way the chat loads them (the bundled sample is seeded on first use)
    assemble       the plan with no moves and no whys yet (`prepare.assemble`): the ``gaps`` event
    stored         the plan already written for this role (and requirement), to hand back instead of writing again
    replay         ``gaps`` then ``done`` for a stored plan: no model call, no spend
    run            ``gaps`` at once, then ``standing`` and ``moves`` as each call lands (either order), then ``done`` with
                   the stored plan; or ``error`` (``no_moves`` when nothing survived the checks, ``failed`` otherwise)
    get            a stored plan by id, for its profile only
    save_state     the person's changes, compare-and-swapped on the version (`PlanChanged` when stale)
    repos          the repositories to start from, ranked in code from the stored GitHub digest (`prepare.github`)
    headline       one model call writing a headline for the role, kept only when every name and number checks

Every model call goes through the `ClaudeClient` Protocol with a ``prepare.*`` purpose (recorded with its cost). The
purposes are not in the client's "wants thinking" table, so Haiku 5.5 runs them with thinking off and Sonnet 5.5 at the
chat effort. A call that fails or runs late degrades: standing failing leaves the gaps with their numbers alone; moves
failing ends the stream with ``no_moves`` (a retry is the client's). The daily spend ceiling always ends the stream.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, Final
from urllib.parse import urlparse

from pydantic import ValidationError

from tayyari.contract.models import Profile
from tayyari.contract.prepare import (
    PrepareDoneEvent,
    PrepareErrorEvent,
    PrepareEvent,
    PrepareGapsEvent,
    PrepareHeadline,
    PrepareMove,
    PrepareMovesEvent,
    PreparePlan,
    PrepareRepos,
    PrepareRequest,
    PrepareStandingEvent,
    PrepareState,
    PrepareStateSaved,
    PrepareStateUpdate,
)
from tayyari.contract.stored import StoredContext
from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError, SpendCeilingReached
from tayyari.llm.types import CallContext, Effort, Purpose, TextBlock
from tayyari.prepare.assemble import assemble, source_labels
from tayyari.prepare.errors import FAILED_CODE, NO_MOVES, NO_MOVES_CODE, BadState, PlanChanged, PlanNotFound
from tayyari.prepare.github import repos_for
from tayyari.prepare.schemas import headline_schema, moves_schema, standing_schema
from tayyari.prepare.verify import StandingChecked, check_headline, check_moves, check_standing
from tayyari.prompts.prepare import (
    HEADLINE_SYSTEM,
    MOVES_SYSTEM,
    STANDING_SYSTEM,
    build_headline_prompt,
    build_moves_prompt,
    build_standing_prompt,
    reader_notes,
)
from tayyari.sample.provider import SampleProvider
from tayyari.storage.ports import PrepareRepo, ProfileRepo, StoredPlan

log = get_logger(__name__)

CALL_TIMEOUT_S: Final = 90.0
"""The longest one call may take before its stage settles for the honest default."""
STANDING_MAX_TOKENS: Final = 4000
MOVES_MAX_TOKENS: Final = 8000
HEADLINE_MAX_TOKENS: Final = 1500
"""Output budgets; on Sonnet 5.5 the adaptive thinking tokens count against them too."""


def new_plan_id() -> str:
    """A plan's id: ``token_hex(16)`` (32 hex characters), a capability like a profile id."""
    return secrets.token_hex(16)


def plan_of(stored: StoredPlan) -> PreparePlan | None:
    """The `PreparePlan` a stored row holds, or None when the row is in a shape this build cannot read."""
    try:
        return PreparePlan.model_validate(
            {**stored.content, "state": stored.state, "version": stored.version}
        )
    except ValidationError:
        log.warning("prepare_plan_unreadable", plan_id=stored.id)
        return None


def _content_of(plan: PreparePlan) -> dict[str, Any]:
    return plan.model_dump(mode="json", exclude_none=True, exclude={"state", "version"})


def _http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def check_state(plan: PreparePlan, state: PrepareState) -> None:
    """Raise `BadState` unless every done key and link names a move, phase and task the plan has."""
    moves = {m.id: m for m in plan.moves}
    if state.moveIndex >= len(plan.moves):
        raise BadState()
    for key in state.done:
        parts = key.split(":")
        if len(parts) != 3 or parts[0] not in moves:
            raise BadState()
        try:
            phase_index, task_index = int(parts[1]), int(parts[2])
        except ValueError:
            raise BadState() from None
        phases = moves[parts[0]].phases
        if not 0 <= phase_index < len(phases) or not 0 <= task_index < len(phases[phase_index].tasks):
            raise BadState()
    for link in state.links:
        move = moves.get(link.moveId)
        if move is None or not 0 <= link.phaseIndex < len(move.phases) or not _http_url(link.url):
            raise BadState()


class PrepareService:
    """See the module docstring."""

    def __init__(
        self,
        *,
        claude: ClaudeClient,
        model: str,
        repo: PrepareRepo,
        profiles: ProfileRepo,
        sample: SampleProvider,
        clock: Clock,
        effort: Effort | None = None,
        call_timeout_s: float = CALL_TIMEOUT_S,
    ) -> None:
        self._claude = claude
        self._model = model
        self._repo = repo
        self._profiles = profiles
        self._sample = sample
        self._clock = clock
        self._effort = effort
        self._timeout = call_timeout_s if call_timeout_s > 0 else CALL_TIMEOUT_S

    # ───────────────────────── the profile ─────────────────────────

    async def load_profile(self, pid: str) -> tuple[Profile, StoredContext] | None:
        """The profile and its context; the bundled sample persona is saved on first use (a plan needs its row)."""
        found = await self._profiles.get_for_chat(pid)
        if found is None and self._sample.is_sample_profile(pid):
            found = await self._seed_sample(pid)
        if found is None:
            return None
        document, context = found
        try:
            return Profile.from_dict(document), context
        except ValidationError:
            return None

    async def _seed_sample(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        sample = self._sample.sample_profile
        if sample is None:
            return None
        document, context = sample.to_dict(), self._sample.sample_context
        try:
            await self._profiles.save(
                pid, document, context, goal=sample.goal, owner_hash=None, is_sample=True
            )
            stored = await self._profiles.get_for_chat(pid)
            if stored is not None:
                return stored
        except Exception:
            log.warning("prepare_sample_seed_failed", profile_id=pid, exc_info=True)
        return document, context

    # ───────────────────────── the plan ─────────────────────────

    def assemble(
        self, profile: Profile, context: StoredContext, req: PrepareRequest, *, plan_id: str | None = None
    ) -> PreparePlan:
        """The plan with no moves and no whys yet (raises `RoleNotFound`, `RequirementNotFound`, `AlreadyStrong`)."""
        return assemble(
            profile,
            context,
            role_id=req.roleId,
            requirement=req.requirement,
            plan_id=plan_id or new_plan_id(),
            created_at=self._clock.now(),
        )

    async def stored(self, profile_id: str, role_id: str, requirement: str | None) -> PreparePlan | None:
        """The newest plan written for this profile, role and requirement, or None."""
        found = await self._repo.find(profile_id, role_id, requirement)
        return None if found is None else plan_of(found)

    async def replay(self, plan: PreparePlan) -> AsyncIterator[PrepareEvent]:
        """``gaps`` then ``done`` for a plan that is already stored."""
        yield PrepareGapsEvent(plan=plan)
        yield PrepareDoneEvent(plan=plan)

    async def run(
        self, profile: Profile, context: StoredContext, plan: PreparePlan, call_ctx: CallContext
    ) -> AsyncIterator[PrepareEvent]:
        """The stream: see the module docstring."""
        yield PrepareGapsEvent(plan=plan)
        standing_task = asyncio.create_task(self._standing(profile, plan, call_ctx), name="prepare-standing")
        moves_task = asyncio.create_task(self._moves(profile, context, plan, call_ctx), name="prepare-moves")
        pending: set[asyncio.Task[Any]] = {standing_task, moves_task}
        standing: StandingChecked | None = None
        moves: list[PrepareMove] = []
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task is standing_task:
                        standing = standing_task.result()
                        if standing is not None:
                            yield PrepareStandingEvent(headline=standing.headline, gaps=standing.gaps)
                    else:
                        moves = moves_task.result()
                        if not moves:
                            yield PrepareErrorEvent(message=NO_MOVES, code=NO_MOVES_CODE)
                            return
                        yield PrepareMovesEvent(moves=moves)
        except (ClaudeError, SpendCeilingReached) as exc:
            log.warning("prepare_failed", error=type(exc).__name__)
            yield PrepareErrorEvent(message=exc.user_message, code=FAILED_CODE)
            return
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        final = plan.model_copy(
            update={
                "headline": standing.headline if standing is not None else None,
                "gaps": standing.gaps if standing is not None else plan.gaps,
                "moves": moves,
            }
        )
        now = self._clock.now()
        await self._repo.create(
            StoredPlan(
                id=final.id,
                profile_id=final.profileId,
                role_id=final.roleId,
                requirement=final.requirement,
                content=_content_of(final),
                state=final.state.to_dict(),
                version=final.version,
                created_at=now,
                updated_at=now,
            )
        )
        log.info(
            "prepare_done",
            plan_id=final.id,
            profile_id=final.profileId,
            role_id=final.roleId,
            scope=final.scope,
            gaps=len(final.gaps),
            whys=standing.why_count if standing is not None else 0,
            headline=final.headline is not None,
            moves=len(final.moves),
        )
        yield PrepareDoneEvent(plan=final)

    # ───────────────────────── reading back and the state ─────────────────────────

    async def get(self, profile_id: str, plan_id: str) -> PreparePlan | None:
        found = await self._repo.get(profile_id, plan_id)
        return None if found is None else plan_of(found)

    async def save_state(
        self, profile_id: str, plan_id: str, update: PrepareStateUpdate
    ) -> PrepareStateSaved:
        """Keep what the person changed; `PlanNotFound`, `BadState` or `PlanChanged` otherwise."""
        plan = await self.get(profile_id, plan_id)
        if plan is None:
            raise PlanNotFound()
        check_state(plan, update.state)
        saved = await self._repo.save_state(
            profile_id,
            plan_id,
            state=update.state.to_dict(),
            expected_version=update.version,
            now=self._clock.now(),
        )
        if not saved:
            raise PlanChanged()
        return PrepareStateSaved(state=update.state, version=update.version + 1)

    async def repos(self, profile_id: str, plan_id: str, context: StoredContext) -> PrepareRepos | None:
        """The repositories ranked for the plan (None when the plan is not this profile's); no network."""
        plan = await self.get(profile_id, plan_id)
        return None if plan is None else repos_for(plan, context)

    # ───────────────────────── the calls ─────────────────────────

    async def _ask(
        self,
        *,
        purpose: Purpose,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        call_ctx: CallContext,
    ) -> dict[str, Any] | None:
        """One structured call; a failed or late call gives None (logged). The spend ceiling propagates."""
        try:
            async with asyncio.timeout(self._timeout):
                result = await self._claude.structured(
                    model=self._model,
                    system=[TextBlock(system)],
                    user=[TextBlock(user)],
                    schema=schema,
                    max_tokens=max_tokens,
                    effort=self._effort,
                    ctx=replace(call_ctx, purpose=purpose),
                )
        except SpendCeilingReached:
            raise
        except (ClaudeError, TimeoutError) as exc:
            log.warning("prepare_call_failed", purpose=purpose, error=type(exc).__name__)
            return None
        return result.data

    async def _standing(
        self, profile: Profile, plan: PreparePlan, call_ctx: CallContext
    ) -> StandingChecked | None:
        data = await self._ask(
            purpose="prepare.standing",
            system=STANDING_SYSTEM,
            user=build_standing_prompt(
                role_title=plan.roleTitle,
                eta=plan.eta,
                why=self._role_why(profile, plan),
                gaps=plan.gaps,
                strong=plan.strong,
                evidence=plan.evidence,
                person_headline=profile.person.headline,
                person_summary=profile.person.summary,
            ),
            schema=standing_schema([e.id for e in plan.evidence], [g.name for g in plan.gaps]),
            max_tokens=STANDING_MAX_TOKENS,
            call_ctx=call_ctx,
        )
        if data is None:
            return None
        return check_standing(data, gaps=plan.gaps, evidence=plan.evidence, eta=plan.eta)

    async def _moves(
        self, profile: Profile, context: StoredContext, plan: PreparePlan, call_ctx: CallContext
    ) -> list[PrepareMove]:
        data = await self._ask(
            purpose="prepare.moves",
            system=MOVES_SYSTEM,
            user=build_moves_prompt(
                role_title=plan.roleTitle,
                eta=plan.eta,
                why=self._role_why(profile, plan),
                gaps=plan.gaps,
                strong=plan.strong,
                evidence=plan.evidence,
                person_headline=profile.person.headline,
                person_summary=profile.person.summary,
                readers=reader_notes(context.readers, source_labels(profile, context)),
            ),
            schema=moves_schema([e.id for e in plan.evidence], [g.name for g in plan.gaps]),
            max_tokens=MOVES_MAX_TOKENS,
            call_ctx=call_ctx,
        )
        if data is None:
            return []
        return check_moves(data, gaps=plan.gaps, evidence=plan.evidence, eta=plan.eta)

    async def headline(self, profile: Profile, plan: PreparePlan, call_ctx: CallContext) -> PrepareHeadline:
        """A headline for the plan's role, kept only when every name and number is in what it cites."""
        current = profile.person.headline
        data = await self._ask(
            purpose="prepare.headline",
            system=HEADLINE_SYSTEM,
            user=build_headline_prompt(
                role_title=plan.roleTitle, current_headline=current, gaps=plan.gaps, evidence=plan.evidence
            ),
            schema=headline_schema([e.id for e in plan.evidence]),
            max_tokens=HEADLINE_MAX_TOKENS,
            call_ctx=call_ctx,
        )
        checked = check_headline(data, evidence=plan.evidence, role_title=plan.roleTitle, current=current)
        log.info("prepare_headline", plan_id=plan.id, kept=checked.proposed is not None)
        return checked

    @staticmethod
    def _role_why(profile: Profile, plan: PreparePlan) -> str:
        return next((r.why for r in profile.roles if r.id == plan.roleId), "")


__all__ = [
    "CALL_TIMEOUT_S",
    "HEADLINE_MAX_TOKENS",
    "MOVES_MAX_TOKENS",
    "STANDING_MAX_TOKENS",
    "PrepareService",
    "check_state",
    "new_plan_id",
    "plan_of",
]
