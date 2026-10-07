"""The analysis pipeline: sources, readers, synthesis, a clean Profile.

`PipelineAnalysisService.run` is an async generator that yields `AnalysisEvent`s in exactly the order and shapes
the web app expects (it replaces ``legacy/pipeline.py``)::

    readers                      one queued card per source
    phase reading                (reader, log and signal events while the sources are read)
    phase synthesizing, synth 1, log "Merging evidence across N sources"
    synth 2 / synth 3 and their log lines, while Sonnet writes
    [log "Tightening the graph, one more pass" and a second synth call, when the first graph is too small]
    [log "Checked N evidence quotes, dropped M"]   (Jev)
    log "Graph ready", synth 3, phase done, done

Jev work happens beside the readers and never holds them up: the first read of the resume (lines and chips
within about two seconds), triage of web pages, and the evidence check after synthesis. A Jev failure never
fails an analysis.

The stream always ends with exactly one `done` or `error`. Failures that are expected (no usable source, a
model that gave no usable answer, the spend ceiling) are reported as an `error` event, never raised. Closing
the iterator early (the client went away) cancels the worker task, which cancels every reader, ticker, signal,
first read and triage task and waits for them before it returns.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any, NoReturn

from tayyari.contract import (
    AnalysisEvent,
    AnalyzeRequest,
    ModelsUsed,
    Profile,
    RequestContext,
    StoredContext,
    StoredSource,
    ev_done,
    ev_error,
    ev_log,
    ev_phase,
    ev_readers,
    ev_synth,
)
from tayyari.core import context as log_context
from tayyari.core.clock import Clock, SystemClock
from tayyari.core.ids import new_profile_id, uuid7
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.core.text import plural
from tayyari.decisions.service import DecisionService, ShadowMaterials
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import SpendCeilingReached
from tayyari.prompts import build_synth_prompt
from tayyari.sample.provider import SampleProvider
from tayyari.storage.ports import ProfileRepo

from .evidence import check_evidence
from .failures import is_auth_error, is_spend_error, user_message
from .firstread import FirstRead
from .normalise import ReadResult, to_profile
from .planning import initial_states, plan_sources_for, resolve_goal
from .readers import CHANNEL_THROTTLE_S, ReaderChannel, SourceReader
from .runctx import RunContext
from .signals import SignalStream
from .synth import synthesize

log = get_logger(__name__)

NOTHING_TO_ANALYSE = "Add a resume, a link or some notes to analyse."
NO_SOURCE_READABLE = "We couldn't read any of your sources. Check the file and links, then try again."
GRAPH_FAILED_PREFIX = "Couldn't build your graph: "


class AnalysisFailed(Exception):
    """Raised after an ``error`` event has already been sent: the stream is over."""


class PipelineAnalysisService:
    """`AnalysisService` built on the Claude client, the decisions facade and the profile repository."""

    def __init__(
        self,
        *,
        settings: Settings,
        claude: ClaudeClient,
        decisions: DecisionService,
        profiles: ProfileRepo,
        sample: SampleProvider,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._claude = claude
        self._decisions = decisions
        self._profiles = profiles
        self._sample = sample
        self._clock: Clock = clock or SystemClock()

    # ── the stream ──

    async def run(self, req: AnalyzeRequest, rc: RequestContext) -> AsyncIterator[AnalysisEvent]:
        """Run the whole analysis, yielding events in the order the UI expects. Never raises (see the module docstring)."""
        queue: asyncio.Queue[AnalysisEvent | None] = asyncio.Queue()
        worker = asyncio.create_task(self._work(req, rc, queue))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _work(
        self, req: AnalyzeRequest, rc: RequestContext, queue: asyncio.Queue[AnalysisEvent | None]
    ) -> None:
        """The worker task: runs the pipeline, turns any failure into the closing event, ends the queue."""
        profile_id = new_profile_id(sample=req.sample)
        analysis_id = uuid7()
        terminal = False

        def send(event: AnalysisEvent) -> None:
            nonlocal terminal
            if terminal:
                return  # exactly one done or error ends the stream
            if event.type in ("done", "error"):
                terminal = True
            queue.put_nowait(event)

        run = RunContext(
            settings=self._settings,
            clock=self._clock,
            claude=self._claude,
            decisions=self._decisions,
            req=req,
            rc=rc,
            profile_id=profile_id,
            analysis_id=analysis_id,
            send=send,
        )
        try:
            with log_context.bind(
                request_id=rc.request_id, analysis_id=analysis_id, profile_id=profile_id, ip_hash=rc.ip_hash
            ):
                await self._analyse(run)
        except AnalysisFailed:
            pass
        except asyncio.CancelledError:
            log.info("analysis_cancelled", reason="client went away")
            raise
        except Exception as err:
            log.error("analysis_failed", error_type=type(err).__name__, exc_info=True)
            send(ev_error(user_message(err)))
        finally:
            queue.put_nowait(None)  # end-of-stream sentinel for `run`

    # ── the pipeline ──

    async def _analyse(self, run: RunContext) -> None:
        req, settings = run.req, run.settings
        send = run.send

        def fail(message: str) -> NoReturn:
            send(ev_error(message))
            raise AnalysisFailed(message)

        goal = resolve_goal(req)
        token = settings.github_token.get_secret_value() if settings.github_token else ""
        planned = plan_sources_for(req, github_token=token, clock=run.clock, scale=run.scale)
        if not planned:
            fail(NOTHING_TO_ANALYSE)

        # (b) announce the readers
        states = initial_states(planned)
        send(ev_readers(list(states.values())))
        send(ev_phase("reading", "Haiku readers in progress"))
        channels = {
            pid: ReaderChannel(
                state, send, now=run.clock.monotonic, throttle_s=CHANNEL_THROTTLE_S * run.scale
            )
            for pid, state in states.items()
        }

        signals = SignalStream(run)
        first_read = FirstRead(run, signals, planned)
        reader = SourceReader(
            run,
            channels,
            signals,
            first_read,
            asyncio.Semaphore(settings.concurrency.reader_concurrency),
        )
        reader_tasks: list[asyncio.Task[ReadResult]] = []
        try:
            # (c) read every source concurrently
            reader_tasks = [asyncio.create_task(reader.read(p)) for p in planned]
            results = list(await asyncio.gather(*reader_tasks))

            ok = [r for r in results if r.reader and r.raw]
            if not ok:
                fail(self._no_reader_message(results))

            # Jev's first read had the whole reading phase; it gets a moment more, then it is dropped.
            await first_read.settle()

            # (d) synthesize
            send(ev_phase("synthesizing", "Sonnet is drawing your graph"))
            send(ev_synth(1, 0))
            send(ev_log("sonnet", f"Merging evidence across {plural(len(ok), 'source')}", "sonnet"))

            resume_raw = next((r.raw for r in results if r.planned.kind == "resume"), None)
            notes_raw = next((r.raw for r in results if r.planned.kind == "notes"), None)
            prompt = build_synth_prompt(
                goal=goal,
                sources=[
                    {
                        "id": r.planned.id,
                        "label": (r.raw.label if r.raw else "") or r.planned.label,
                        "kind": r.planned.kind,
                        "url": (r.raw.url if r.raw else None) or r.planned.url,
                        "ok": bool(r.reader),
                        "error": r.error,
                        "facts": r.raw.meta if r.raw else None,
                        "reader": r.reader,
                    }
                    for r in results
                ],
                resume_text=resume_raw.text
                if resume_raw and resume_raw.ok and not resume_raw.error
                else None,
                notes_text=notes_raw.text if notes_raw else None,
            )

            try:
                synth = await synthesize(run, prompt)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                log.warning("synth_failed", error_type=type(err).__name__, kind=getattr(err, "kind", None))
                if is_spend_error(err):
                    fail(user_message(err))
                fail(f"{GRAPH_FAILED_PREFIX}{user_message(err)}")

            # (e) normalise into a valid Profile, then let Jev check the evidence quotes
            profile = to_profile(
                synth,
                results,
                goal,
                req.sample,
                models=ModelsUsed(reader=settings.reader_model, synth=settings.synth_model),
                profile_id=run.profile_id,
                created_at=run.clock.now(),
            )
            await check_evidence(run, profile, results)
            send(
                ev_log(
                    "sonnet",
                    f"Graph ready · {plural(len(profile.domains), 'domain')}, "
                    f"{plural(len(profile.skills), 'skill')}, {plural(len(profile.roles), 'role')}",
                    "sonnet",
                )
            )

            # (f) persist and finish
            await self._persist(run, profile, results, goal)
            await signals.settle()
            send(ev_synth(3))
            send(ev_phase("done", "Your graph is ready"))
            send(ev_done(profile))
            self._shadow(run, profile, results)
        finally:
            await self._shutdown(first_read, signals, reader_tasks, channels.values())

    # ── helpers ──

    @staticmethod
    def _no_reader_message(results: Sequence[ReadResult]) -> str:
        """Why nothing could be analysed, in the legacy order: spend ceiling, bad key, Claude trouble, unreadable source."""
        ai_errors = [r.ai_error for r in results if r.ai_error is not None]
        spend = next((e for e in ai_errors if isinstance(e, SpendCeilingReached)), None)
        if spend is not None:
            return user_message(spend)
        auth = next((e for e in ai_errors if is_auth_error(e)), None)
        if auth is not None:
            return user_message(auth)
        if ai_errors:
            return f"The readers couldn't reach Claude: {user_message(ai_errors[0])}"
        if len(results) == 1 and results[0].error:
            return f"We couldn't read that source: {results[0].error}"
        return NO_SOURCE_READABLE

    async def _persist(
        self, run: RunContext, profile: Profile, results: Sequence[ReadResult], goal: str | None
    ) -> None:
        """Save the profile and the context chat needs. A failure is logged and never fails the analysis."""
        context = StoredContext(
            sources=[
                StoredSource(
                    id=r.planned.id,
                    label=(r.raw.label if r.raw else "") or r.planned.label,
                    kind=r.planned.kind,
                    url=(r.raw.url if r.raw else None) or r.planned.url,
                    content=(r.raw.text or "") if r.raw and r.raw.ok and not r.raw.error else "",
                )
                for r in results
            ],
            readers={r.planned.id: r.reader for r in results if r.reader and r.raw},
        )
        try:
            await self._profiles.save(
                run.profile_id,
                profile.to_dict(),
                context,
                goal=goal,
                owner_hash=run.rc.ip_hash,
                is_sample=run.req.sample,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("profile_save_failed", error_type=type(exc).__name__, exc_info=True)

    def _shadow(self, run: RunContext, profile: Profile, results: Sequence[ReadResult]) -> None:
        """Queue the shadow decisions after `done`: fire and forget, never delays or breaks the stream."""
        resume_raw = next((r.raw for r in results if r.planned.kind == "resume"), None)
        materials = ShadowMaterials(
            profile=profile,
            reader_outputs={r.planned.id: r.reader for r in results if r.reader and r.raw},
            resume_text=resume_raw.text if resume_raw and resume_raw.ok and not resume_raw.error else None,
        )
        try:
            self._decisions.shadow_compare(materials, run.decision_ctx)
        except Exception as exc:
            log.warning("shadow_compare_failed", error_type=type(exc).__name__)

    @staticmethod
    async def _shutdown(
        first_read: FirstRead,
        signals: SignalStream,
        reader_tasks: Sequence[asyncio.Task[ReadResult]],
        channels: Iterable[ReaderChannel],
    ) -> None:
        """Cancel every child task, close every channel, then wait for the tasks to be gone.

        Runs on the happy path too: nothing may outlive the run. Everything is cancelled before anything is awaited,
        so a second cancellation during the wait cannot leave a task running.
        """
        tasks: list[asyncio.Task[Any]] = [*reader_tasks, *signals.tasks]
        if first_read.task is not None:
            tasks.append(first_read.task)
        for task in tasks:
            task.cancel()  # a no-op for a task that is done
        for ch in channels:
            ch.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["AnalysisFailed", "PipelineAnalysisService"]
