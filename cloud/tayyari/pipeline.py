"""
The analysis pipeline (port of server/pipeline.ts): sources → deterministic ingest → Haiku readers
(concurrently) → Sonnet synthesizer → normalised Profile. Every step streams AnalysisEvent dicts so the
analysis screen can show readers working, signals arriving and the graph being drawn.

`run_analysis()` is an async generator; it never raises (failures become an {type:'error'} event) and
cancellation (client gone) tears down every reader, ticker and signal task before propagating.
"""

from __future__ import annotations

import asyncio
import math
import random
import re
import secrets
import sys
import time
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, NoReturn, TypeGuard, cast

from . import store
from .claude import AUTH_MESSAGE, ClaudeError, run_structured
from .config import MODELS, READER_THINKING
from .ingest import PlannedSource, SourceDoc, ingest_planned, plan_sources
from .prompts import (
    READER_SYSTEM,
    SYNTH_SYSTEM,
    build_reader_prompt,
    build_synth_prompt,
    clip,
)
from .sample import SAMPLE_GOAL, SAMPLE_INGEST_LOGS
from .schemas import READER_SCHEMA, SYNTH_SCHEMA
from .types import (
    AnalyzeInput,
    Profile,
    ReaderState,
    SourceKind,
    ev_done,
    ev_error,
    ev_log,
    ev_phase,
    ev_reader,
    ev_readers,
    ev_signal,
    ev_synth,
)

AGENT: dict[str, str] = {
    'resume': 'Resume reader',
    'github': 'GitHub analyst',
    'lovable': 'Lovable reviewer',
    'linkedin': 'LinkedIn reader',
    'portfolio': 'Link reader',
    'link': 'Link reader',
    'notes': 'Notes reader',
}

# Short name used as the "who" column of the live log.
WHO: dict[str, str] = {
    'resume': 'resume',
    'github': 'github',
    'lovable': 'lovable',
    'linkedin': 'linkedin',
    'portfolio': 'web',
    'link': 'web',
    'notes': 'notes',
}

# Readers extract rather than reason, so extended thinking is off unless TAYYARI_READER_THINKING is set
# (the Node server made the same choice: ~5x faster and a third of the cost on the sample GitHub source).
_READER_THINKING: dict[str, Any] | None = {'type': 'enabled', 'budget_tokens': 4096} if READER_THINKING else None

__all__ = ['AUTH_MESSAGE', 'AnalysisFailed', 'humanize_error', 'is_abort_error', 'is_auth_error', 'run_analysis', 'to_profile']

Send = Callable[[dict[str, Any] | None], None]


def _log(tag: str, *parts: Any) -> None:
    print(f'[pipeline] [{tag}]', *parts, file=sys.stdout, flush=True)


# ───────────────────────── errors ─────────────────────────

_AUTH_RE = re.compile(
    r'\b401\b|unauthori[sz]ed|authenticat|invalid api key|api[_ ]key|/login\b|not logged in|oauth|credential|token (?:has )?expired|expired token',
    re.I,
)
_BUSY_RE = re.compile(r'rate.?limit|\b429\b|overloaded|\b529\b', re.I)
_INCOMPLETE_RE = re.compile(r'failed validation|no JSON object|without a result', re.I)
_NETWORK_RE = re.compile(r'timed out|timeout|connection error|could not connect', re.I)
_TRAILING_PUNCT = re.compile(r'[\s,;:·-]+$')
"""Separators left dangling where clip_words cut the text (the input is at most a few hundred characters)."""


class AnalysisFailed(Exception):
    """Raised after an {type:'error'} event has already been emitted."""


def err_text(err: Any) -> str:
    """The message of an error, as Node's `err.message` (empty when the exception carries none)."""
    if not isinstance(err, BaseException):
        return str(err or '')
    message = getattr(err, 'message', None)
    return message if isinstance(message, str) and message else str(err)


def is_abort_error(err: Any) -> bool:
    return isinstance(err, asyncio.CancelledError)


def is_auth_error(err: Any) -> bool:
    return bool(_AUTH_RE.search(err_text(err)))


def humanize_error(err: Any) -> str:
    """One short, human sentence for any failure (shown in the UI)."""
    if is_abort_error(err):
        return 'Cancelled.'
    if is_auth_error(err):
        return AUTH_MESSAGE
    m = tidy(err_text(err))
    if _BUSY_RE.search(m):
        return 'Claude is busy right now (rate limited or overloaded). Try again in a minute.'
    if _INCOMPLETE_RE.search(m):
        return 'The model returned an incomplete answer. Try again.'
    if _NETWORK_RE.search(m):
        return 'Could not reach Claude (network error or timeout). Try again.'
    # clip_words, not the prompt-text clip(), so UI strings never get a "…[trimmed]" marker
    return clip_words(m, 180) or 'Something went wrong.'


def _reader_error_line(err: BaseException) -> str:
    if is_auth_error(err):
        return 'API key missing or invalid · skipped'
    return clip_words(re.sub(r'\.$', '', humanize_error(err)), 64)


# ───────────────────────── small utils ─────────────────────────


def tidy(s: Any) -> str:
    return ' '.join(s.split()) if isinstance(s, str) else ''


def clip_words(s: Any, max_len: int) -> str:
    """Cut at a word boundary to at most `max_len` characters, with an ellipsis when cut."""
    t = tidy(s)
    if len(t) <= max_len:
        return t
    cut = t[: max_len - 1]
    sp = cut.rfind(' ')
    head = cut[:sp] if sp > max_len * 0.5 else cut
    return f'{_TRAILING_PUNCT.sub("", head)}…'


def short_label(s: Any, max_len: int) -> str:
    """Node label: whole words up to `max_len` characters ("Frontend Engineering" → "Frontend"), no ellipsis unless one word is too long."""
    t = tidy(s)
    if len(t) <= max_len:
        return t
    out = ''
    for w in t.split(' '):
        nxt = f'{out} {w}' if out else w
        if len(nxt) > max_len:
            break
        out = nxt
    out = re.sub(r'[\s&,;:·-]+$', '', out)
    return out or f'{t[: max_len - 1]}…'


def _js_round(v: float) -> int:
    """Math.round: halves round towards +infinity."""
    return int(math.floor(v + 0.5))


def clamp(n: Any, lo: int = 0, hi: int = 100, fallback: int = 0) -> int:
    if isinstance(n, bool):
        v: float = float(n)
    elif isinstance(n, (int, float)):
        v = float(n)
    elif isinstance(n, str):
        try:
            v = float(n.strip() or 'nan')
        except ValueError:
            return fallback
    else:
        return fallback
    if not math.isfinite(v):
        return fallback
    return _js_round(min(hi, max(lo, v)))


def _round1(v: float) -> float:
    return _js_round(v * 10) / 10


def _is_num(v: Any) -> TypeGuard[int | float]:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def slug(s: str) -> str:
    t = unicodedata.normalize('NFKD', tidy(s).lower())
    t = re.sub('[̀-ͯ]', '', t)
    t = t.replace('&', ' and ').replace('++', 'pp').replace('#', 'sharp')
    t = re.sub(r'[^a-z0-9]+', '-', t).strip('-')[:40].rstrip('-')
    return t or 'item'


def loose_key(s: Any) -> str:
    """Loose comparison key: "REST & GraphQL" ≈ "rest-graphql"."""
    return re.sub(r'[^a-z0-9+#]+', '', tidy(s).lower())


def unique_id(base: str, used: set[str]) -> str:
    pid = base
    n = 2
    while pid in used:
        pid = f'{base}-{n}'
        n += 1
    used.add(pid)
    return pid


def plural(n: int, word: str) -> str:
    return f'{n} {word}{"" if n == 1 else "s"}'


def fact_strings(facts: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    """Strings from a facts value: list[str] or objects with name/title/label."""
    for k in keys:
        v = facts.get(k)
        if not isinstance(v, list):
            continue
        out: list[str] = []
        for x in v:
            if isinstance(x, str):
                out.append(tidy(x))
            elif isinstance(x, dict):
                out.append(tidy(x.get('name') or x.get('title') or x.get('label') or x.get('full_name') or ''))
        out = [x for x in out if x]
        if out:
            return out
    return []


# Section names the ingesters add themselves; never shown as "what the reader is reading".
_GENERIC_HEADING = re.compile(
    r'^(headings|page text|profile|totals|structured data|ui copy|routes|backend hints|external services|libraries|notes on this data|forks|other original|recent public activity|technologies mentioned|top repositories|profile readme|github:)',
    re.I,
)
_PAGE_HEADING = re.compile(r'^- h[1-3]: (.+)$', re.M)
_MD_HEADING = re.compile(r'^#{2,3}[ \t]+(?:\d+\.[ \t]+)?(.+)$', re.M)


def content_headings(content: str) -> list[str]:
    """Page headings ("- h2: Checkout, rebuilt" lines from the web ingester) or markdown sub-headings."""
    page = [tidy(m.group(1)) for m in _PAGE_HEADING.finditer(content)]
    md = [h for h in (tidy(m.group(1)) for m in _MD_HEADING.finditer(content)) if not _GENERIC_HEADING.search(h)]
    out: list[str] = []
    for h in page + md:
        h = re.sub(r'^["“]|["”]$', '', h)
        if 2 < len(h) < 70 and h not in out:
            out.append(h)
    return out[:10]


def split_signal(s: str, fallback: str) -> dict[str, str]:
    """"TypeScript · 17 repos" → {label: "TypeScript", detail: "17 repos"}."""
    i = s.find(' · ')
    if i > 0:
        return {'label': clip_words(s[:i], 32), 'detail': clip_words(s[i + 3 :], 40)}
    return {'label': clip_words(s, 32), 'detail': fallback}


# ───────────────────────── planning ─────────────────────────


@dataclass
class ReadResult:
    planned: PlannedSource
    raw: SourceDoc | None = None
    reader: dict[str, Any] | None = None
    error: str | None = None
    ai_error: BaseException | None = None


def _with_replay(p: PlannedSource, i: int) -> PlannedSource:
    """Sample mode: replay plausible fetch logs with short delays so the demo feels like the live product."""
    inner = p.ingest

    async def ingest(log: Callable[[str], None]) -> SourceDoc:
        await asyncio.sleep(0.18 + i * 0.26)
        for line in SAMPLE_INGEST_LOGS.get(p.id, []):
            await asyncio.sleep(0.3 + random.random() * 0.4)
            log(line)
        return await inner(log)

    return PlannedSource(p.id, p.kind, p.label, p.kind_label, p.src, p.url, ingest)


def _planned(inp: AnalyzeInput) -> list[PlannedSource]:
    planned = plan_sources(inp)
    return [_with_replay(p, i) for i, p in enumerate(planned)] if inp.sample else planned


# ───────────────────────── readers ─────────────────────────


class ReaderChannel:
    """Throttled emitter for one reader's state (progress updates arrive per token)."""

    def __init__(self, state: dict[str, Any], send: Send):
        self.state = state
        self._send = send
        self._loop = asyncio.get_running_loop()
        self._last = 0.0
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False

    def _flush(self) -> None:
        self._timer = None
        if self._closed:
            return
        self._last = self._loop.time()
        self._send(ev_reader(self.state))

    def update(self, patch: Mapping[str, Any], force: bool = False) -> None:
        self.state.update(patch)
        if self._closed:
            return
        wait = 0.26 - (self._loop.time() - self._last)
        if force or wait <= 0:
            if self._timer:
                self._timer.cancel()
            self._flush()
        elif self._timer is None:
            self._timer = self._loop.call_later(wait, self._flush)

    def close(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = None
        self._closed = True


def activity_lines(raw: SourceDoc) -> list[str]:
    """Rotating, specific activity lines shown while a reader works."""
    facts = raw.meta or {}
    heads = fact_strings(facts, ['headings', 'sections', 'caseStudies', 'pages', 'titles', 'projects'])
    headings = heads or content_headings(raw.text or '')
    lines: list[str] = []
    if raw.kind == 'github':
        repos = fact_strings(facts, ['topRepos', 'repos', 'repositories', 'pinned'])
        langs = fact_strings(facts, ['languages'])
        verbs = ['Reading README', 'Scanning commits', 'Checking tests and CI', 'Reading package.json']
        for i, r in enumerate(repos[:6]):
            lines.append(f'{verbs[i % len(verbs)]} · {r}')
        if langs:
            lines.insert(2, f'Mapping {plural(len(langs), "language")} across repos')
        if not lines:
            lines += ['Reading repositories', 'Scanning READMEs', 'Checking tests and CI']
    elif raw.kind == 'resume':
        lines += [f'Reading {h.lower()} · {clip_words(raw.src, 28)}' for h in headings[:5]]
        lines += ['Weighing impact in each bullet', 'Separating listed skills from used ones']
    elif raw.kind == 'lovable':
        routes = [r for r in fact_strings(facts, ['routes']) if r != '/']
        lines += [f'Opening route · {clip_words(r, 30)}' for r in routes[:2]]
        lines += [f'Walking through · {clip_words(h, 32)}' for h in headings[:4]]
        lines += ['Checking auth and data wiring', 'Reviewing the AI features']
    elif raw.kind == 'portfolio':
        lines += [f'Parsing case study: “{clip_words(h, 30)}”' for h in headings[:5]]
        lines.append('Looking for outcomes and numbers')
    elif raw.kind == 'linkedin':
        lines += ['Reading headline and about', 'Reading experience entries', 'Checking featured work']
    elif raw.kind == 'notes':
        lines += ['Reading your notes', 'Separating claims from goals']
    else:
        lines += [f'Reading · {clip_words(h, 34)}' for h in headings[:4]]
        lines.append(f'Extracting evidence · {clip_words(raw.src, 30)}')
    return lines or [f'Reading {clip_words(raw.src, 34)}']


def clean_reader(r: Mapping[str, Any]) -> dict[str, Any]:
    """Trim and clamp a reader's output to the limits the UI expects."""
    person_in = r.get('person') if isinstance(r.get('person'), Mapping) else None
    person: dict[str, Any] = {}
    if person_in:
        if tidy(person_in.get('name')):
            person['name'] = tidy(person_in.get('name'))
        if tidy(person_in.get('currentRole')):
            person['currentRole'] = tidy(person_in.get('currentRole'))
        yrs = person_in.get('yearsExperience')
        if _is_num(yrs) and yrs >= 0:
            person['yearsExperience'] = _round1(float(yrs))
        if tidy(person_in.get('location')):
            person['location'] = tidy(person_in.get('location'))
    out: dict[str, Any] = {'sub': clip_words(r.get('sub'), 48), 'summary': tidy(r.get('summary'))}
    if person:
        out['person'] = person
    signals = [
        {'label': clip_words(s.get('label'), 32), 'detail': clip_words(s.get('detail'), 40)} for s in (r.get('signals') or []) if isinstance(s, Mapping)
    ]
    out['signals'] = [s for s in signals if s['label']][:12]
    skills = [
        {'name': clip_words(s.get('name'), 40), 'evidence': clip_words(s.get('evidence'), 160), 'strength': clamp(s.get('strength'), 1, 5, 2)}
        for s in (r.get('skills') or [])
        if isinstance(s, Mapping)
    ]
    out['skills'] = [s for s in skills if s['name']][:25]
    out['notes'] = [n for n in (clip_words(n, 140) for n in (r.get('notes') or [])) if n][:4]
    return out


def quick_detail(raw: SourceDoc) -> str:
    if raw.kind == 'resume':
        return 'resume'
    if raw.kind == 'portfolio':
        return 'portfolio'
    if raw.kind == 'notes':
        return 'notes'
    return raw.label


def result_line(out: Mapping[str, Any]) -> str:
    """Crisp result line, e.g. "6 roles · 4 projects · 23 skills extracted" or "34 repos · 9 languages · 18 skills extracted"."""
    parts = [p.strip() for p in re.split(r'\s*·\s*', str(out.get('sub') or '')) if p.strip()]
    if any(re.search(r'skill', p, re.I) for p in parts):
        return f'{" · ".join(parts[:3])} extracted'
    return ' · '.join([*parts[:2], f'{plural(len(out.get("skills") or []), "skill")} extracted'])


# ───────────────────────── main entry ─────────────────────────


async def run_analysis(inp: AnalyzeInput) -> AsyncIterator[dict[str, Any]]:
    """
    Run the whole analysis, yielding AnalysisEvent dicts in the order the UI expects. Never raises: any
    failure ends the stream with an {type:'error'} event. Closing the iterator early (client disconnect)
    cancels the work.
    """
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    worker = asyncio.create_task(_run(inp, queue.put_nowait))
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


async def _run(inp: AnalyzeInput, send: Send) -> None:
    try:
        await _analyse(inp, send)
    except AnalysisFailed:
        pass
    except asyncio.CancelledError:
        _log('analyze', 'cancelled by client')
        raise
    except Exception as err:
        _log('analyze', repr(err))
        send(ev_error(humanize_error(err)))
    finally:
        send(None)  # end-of-stream sentinel for run_analysis


async def _analyse(inp: AnalyzeInput, send: Send) -> None:
    def fail(message: str) -> NoReturn:
        send(ev_error(message))
        raise AnalysisFailed(message)

    goal = tidy(inp.goal) or (SAMPLE_GOAL if inp.sample else '') or None
    planned = _planned(inp)
    if not planned:
        fail('Add a resume, a link or some notes to analyse.')

    # (b) announce the readers
    states: dict[str, dict[str, Any]] = {
        p.id: ReaderState(
            sourceId=p.id, kind=cast(SourceKind, p.kind), src=p.src, kindLabel=p.kind_label, agent=AGENT.get(p.kind, 'Link reader'), status='queued', line='Queued', progress=0
        ).to_dict()
        for p in planned
    }
    send(ev_readers(list(states.values())))
    send(ev_phase('reading', 'Haiku readers in progress'))
    channels = {p.id: ReaderChannel(states[p.id], send) for p in planned}

    # (c) read every source concurrently
    # Signals stream in one at a time (~150ms apart), deduped across all sources so the cloud stays clean.
    signal_tasks: list[asyncio.Task[None]] = []
    seen: set[str] = set()

    def stream_signals(items: Sequence[Mapping[str, str]], kind: str, after: Awaitable[None] | None = None) -> asyncio.Task[None]:
        async def run() -> None:
            if after is not None:
                try:
                    await after
                except Exception:
                    pass
            for it in items:
                k = loose_key(it['label'])
                if not k or k in seen:
                    continue
                seen.add(k)
                send(ev_signal(it['label'], it['detail'], kind))
                await asyncio.sleep(0.15)

        task = asyncio.create_task(run())
        signal_tasks.append(task)
        return task

    async def read_one(p: PlannedSource) -> ReadResult:
        ch = channels[p.id]
        who = WHO.get(p.kind, 'web')
        raw: SourceDoc | None = None
        ticker: asyncio.Task[None] | None = None
        try:
            ch.update({'status': 'reading', 'line': f'Opening {clip_words(p.src, 36)}', 'progress': 6}, True)

            # deterministic ingest (expected failures come back as a doc with ok=False, never as an exception)
            def log(text: str) -> None:
                send(ev_log(who, text, p.kind))
                ch.update({'line': clip_words(text, 70), 'progress': min(35, max(10, ch.state['progress'] + 7))})

            raw = await ingest_planned(p, log)
            ch.update({'kindLabel': raw.kind_label or p.kind_label, 'src': raw.src or p.src})

            error = raw.error or ('' if raw.ok else 'Could not read this source.')
            if error:
                first = re.split(r'(?<=\.)\s', tidy(error))[0] or error
                line = clip_words(re.sub(r'\.$', '', first), 64)
                ch.update({'status': 'error', 'line': line}, True)
                send(ev_log(who, f'Could not read {raw.label}: {clip_words(error, 90)}', p.kind))
                return ReadResult(planned=p, raw=raw, error=error)

            ch.update({'progress': max(ch.state['progress'], 35)})
            quick = stream_signals([split_signal(s, quick_detail(raw)) for s in raw.quick_signals or []], p.kind)

            # Haiku reader (no extended thinking: readers extract, they do not reason)
            lines = activity_lines(raw)
            ch.update({'line': lines[0]}, True)

            async def tick() -> None:
                li = 0
                while True:
                    await asyncio.sleep(1.4)
                    li = (li + 1) % len(lines)
                    # never lower progress (on_progress may already have pushed it past 90)
                    pr = ch.state['progress']
                    ch.update({'line': lines[li], 'progress': pr + 1 if pr < 90 else pr})

            def on_progress(chars: int) -> None:
                pr = 35 + _js_round(57 * (1 - math.exp(-chars / 1800)))
                if pr > ch.state['progress']:
                    ch.update({'progress': pr})

            pdf = raw.pdf_bytes if raw.pdf_bytes and not (raw.text or '').strip() else None
            ticker = asyncio.create_task(tick())
            try:
                res = await run_structured(
                    MODELS['reader'],
                    READER_SYSTEM(raw.kind),
                    build_reader_prompt(
                        {
                            'id': raw.id,
                            'kind': raw.kind,
                            'label': raw.label,
                            'kindLabel': raw.kind_label,
                            'url': raw.url,
                            'src': raw.src,
                            'content': raw.text or '',
                            'facts': raw.meta,
                            'pdfAttached': pdf is not None,
                        }
                    ),
                    READER_SCHEMA,
                    on_progress=on_progress,
                    effort='low',
                    thinking=_READER_THINKING,
                    pdf=pdf,
                )
                out = clean_reader(res)
            finally:
                ticker.cancel()
                await asyncio.gather(ticker, return_exceptions=True)

            ch.update({'status': 'done', 'progress': 100, 'line': result_line(out)}, True)
            send(ev_log(who, f'{raw.label} read · {plural(len(out["skills"]), "skill")}, {plural(len(out["signals"]), "signal")}', p.kind))
            if out['notes']:
                send(ev_log(who, clip_words(out['notes'][0], 90), p.kind))
            stream_signals([{'label': s['label'], 'detail': s['detail'] or quick_detail(raw)} for s in out['signals']], p.kind, quick)
            return ReadResult(planned=p, raw=raw, reader=out)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _log(f'reader {p.id}', repr(err))
            line = _reader_error_line(err)
            ch.update({'status': 'error', 'line': line}, True)
            send(ev_log(who, f'Reader failed: {line}', p.kind))
            return ReadResult(planned=p, raw=raw, error=humanize_error(err), ai_error=err)
        finally:
            ch.close()

    try:
        results = await asyncio.gather(*(read_one(p) for p in planned))

        ok = [r for r in results if r.reader and r.raw]
        if not ok:
            ai_errors = [r for r in results if r.ai_error is not None]
            if any(is_auth_error(r.ai_error) for r in ai_errors):
                fail(AUTH_MESSAGE)
            if ai_errors:
                fail(f"The readers couldn't reach Claude: {humanize_error(ai_errors[0].ai_error)}")
            fail(
                f"We couldn't read that source: {results[0].error}"
                if len(results) == 1 and results[0].error
                else "We couldn't read any of your sources. Check the file and links, then try again."
            )

        # (d) synthesize
        send(ev_phase('synthesizing', 'Sonnet is drawing your graph'))
        send(ev_synth(1, 0))
        send(ev_log('sonnet', f'Merging evidence across {plural(len(ok), "source")}', 'sonnet'))

        resume_raw = next((r.raw for r in results if r.planned.kind == 'resume'), None)
        notes_raw = next((r.raw for r in results if r.planned.kind == 'notes'), None)
        prompt = build_synth_prompt(
            goal=goal,
            sources=[
                {
                    'id': r.planned.id,
                    'label': (r.raw.label if r.raw else '') or r.planned.label,
                    'kind': r.planned.kind,
                    'url': (r.raw.url if r.raw else None) or r.planned.url,
                    'ok': bool(r.reader),
                    'error': r.error,
                    'facts': r.raw.meta if r.raw else None,
                    'reader': r.reader,
                }
                for r in results
            ],
            resume_text=resume_raw.text if resume_raw and resume_raw.ok and not resume_raw.error else None,
            notes_text=notes_raw.text if notes_raw else None,
        )

        try:
            synth = await _synthesize(prompt, send)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _log('synth', repr(err))
            fail(f"Couldn't build your graph: {humanize_error(err)}")

        # (e) normalise into a valid Profile
        profile = to_profile(synth, results, goal, inp.sample)
        send(
            ev_log(
                'sonnet',
                f'Graph ready · {plural(len(profile["domains"]), "domain")}, {plural(len(profile["skills"]), "skill")}, {plural(len(profile["roles"]), "role")}',
                'sonnet',
            )
        )

        # (f) persist and finish
        context = {
            'sources': [
                {
                    'id': r.planned.id,
                    'label': (r.raw.label if r.raw else '') or r.planned.label,
                    'kind': r.planned.kind,
                    **({'url': url} if (url := (r.raw.url if r.raw else None) or r.planned.url) else {}),
                    'content': (r.raw.text or '') if r.raw and r.raw.ok and not r.raw.error else '',
                }
                for r in results
            ],
            'readers': {r.planned.id: r.reader for r in ok},
        }
        try:
            await store.save_profile(profile)
            await store.save_context(profile['id'], context)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _log('store', 'could not save profile', repr(err))  # still cached in memory
        await asyncio.gather(*signal_tasks, return_exceptions=True)
        send(ev_synth(3))
        send(ev_phase('done', 'Your graph is ready'))
        send(ev_done(profile))
    finally:
        for task in signal_tasks:
            task.cancel()
        for ch in channels.values():
            ch.close()
        if signal_tasks:
            await asyncio.gather(*signal_tasks, return_exceptions=True)


# ───────────────────────── synthesis ─────────────────────────


async def _synthesize(prompt: str, send: Send) -> dict[str, Any]:
    step = 1
    last_emit = 0.0
    last_chars = -1
    offset = 0  # a retry restarts the SDK's count; keep the UI's counter monotonic

    def on_progress(generated: int) -> None:
        nonlocal step, last_emit, last_chars
        chars = offset + generated
        if chars == last_chars:
            return  # thinking deltas carry no visible characters
        last_chars = chars
        now = time.monotonic()
        if step < 2 and chars >= 2500:
            step = 2
            send(ev_synth(step, chars))
            send(ev_log('sonnet', 'Scoring domains and skills', 'sonnet'))
            last_emit = now
        elif step < 3 and chars >= 6500:
            step = 3
            send(ev_synth(step, chars))
            send(ev_log('sonnet', 'Ranking next roles by alignment', 'sonnet'))
            last_emit = now
        elif now - last_emit > 0.3:
            last_emit = now
            send(ev_synth(step, chars))

    async def attempt(repair: str | None = None) -> dict[str, Any]:
        d = await run_structured(
            MODELS['synth'],
            SYNTH_SYSTEM,
            f'{prompt}\n\n{repair}' if repair else prompt,
            SYNTH_SCHEMA,
            max_tokens=16000,
            on_progress=on_progress,
            effort='medium',
        )
        domains, skills, roles = d.get('domains') or [], d.get('skills') or [], d.get('roles') or []
        if len(domains) < 2 or len(skills) < 4 or len(roles) < 1:
            raise ClaudeError(f'structured output failed validation: graph too small ({len(domains)} domains, {len(skills)} skills, {len(roles)} roles)')
        return d

    try:
        return await attempt()
    except asyncio.CancelledError:
        raise
    except Exception as err:
        if is_auth_error(err):
            raise
        _log('synth', 'first attempt failed, retrying once:', err_text(err))
        send(ev_log('sonnet', 'Tightening the graph, one more pass', 'sonnet'))
        offset = max(0, last_chars)
        reason = clip(tidy(err_text(err)), 300)
        return await attempt(
            f'IMPORTANT — your previous attempt failed ({reason}). Return ONLY the structured output, valid against the schema: '
            'every required field present, scores as integers 0–100, kebab-case ids, evidence.sourceId taken from the list above, '
            '4–7 domains, 12–26 skills, 4–6 roles with 4–7 requirements each.'
        )


# ───────────────────────── normalisation ─────────────────────────


def _base36(n: int) -> str:
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    out = ''
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out or '0'


def to_profile(synth: Mapping[str, Any], results: Sequence[ReadResult], goal: str | None, sample: bool) -> dict[str, Any]:
    """Turn the synthesizer's output into a valid Profile: stable ids, deduped names, clamped scores, resolved references."""
    ok_ids = [r.planned for r in results if r.reader]
    used: set[str] = {'you', *(r.planned.id for r in results)}

    # source id resolution: exact, case-insensitive, label, kind
    def resolve_source(raw: Any) -> str | None:
        s = tidy(raw)
        if not s:
            return None
        lower = s.lower()
        exact = next((p for p in ok_ids if p.id == s), None) or next((p for p in ok_ids if p.id.lower() == lower), None)
        if exact:
            return exact.id
        by_label = [p for p in ok_ids if p.label.lower() == lower or loose_key(p.label) == loose_key(s)]
        if len(by_label) == 1:
            return by_label[0].id
        by_kind = [p for p in ok_ids if p.kind == lower]
        if len(by_kind) == 1:
            return by_kind[0].id
        return None

    # roles first (titles are needed for skill.unlocks)
    raw_roles: list[Mapping[str, Any]] = []
    for r in sorted((r for r in synth.get('roles') or [] if tidy(r.get('title'))), key=lambda r: -clamp(r.get('alignment'))):
        if all(loose_key(x.get('title')) != loose_key(r.get('title')) for x in raw_roles):
            raw_roles.append(r)
    raw_roles = raw_roles[:6]
    role_titles = [clip_words(r.get('title'), 30) for r in raw_roles]

    def match_role(t: Any) -> str | None:
        k = loose_key(t)
        if not k:
            return None
        return next((title for title in role_titles if loose_key(title) == k), None) or next(
            (title for title in role_titles if k in loose_key(title) or loose_key(title) in k), None
        )

    # domains
    domain_alias: dict[str, str] = {}
    domains: list[dict[str, Any]] = []
    for d in synth.get('domains') or []:
        if len(domains) >= 7:
            break
        name = tidy(d.get('name')) or tidy(d.get('short'))
        if not name or any(loose_key(x['name']) == loose_key(name) for x in domains):
            continue
        did = unique_id(slug(tidy(d.get('id')) or name), used)
        for alias in (d.get('id'), slug(tidy(d.get('id'))), name, d.get('short')):
            if tidy(alias):
                domain_alias[loose_key(alias)] = did
        domains.append(
            {'id': did, 'name': clip_words(name, 40), 'short': short_label(tidy(d.get('short')) or name, 16), 'score': clamp(d.get('score')), 'summary': tidy(d.get('summary'))}
        )

    mastery_in: Mapping[str, Any] = m if isinstance(m := synth.get('mastery'), Mapping) else {}
    if not domains:
        domains.append({'id': unique_id('core', used), 'name': 'Core skills', 'short': 'Core', 'score': clamp(mastery_in.get('score')), 'summary': ''})

    # skills
    skill_alias: dict[str, str] = {}
    skills: list[dict[str, Any]] = []
    for s in synth.get('skills') or []:
        if len(skills) >= 28:
            break
        name = tidy(s.get('name'))
        if not name or any(loose_key(x['name']) == loose_key(name) for x in skills):
            continue
        sid = unique_id(slug(tidy(s.get('id')) or name), used)
        for alias in (s.get('id'), name):
            if tidy(alias):
                skill_alias[loose_key(alias)] = sid
        dk = loose_key(s.get('domainId'))
        domain_id = (
            domain_alias.get(dk)
            or next((v for k, v in domain_alias.items() if (len(dk) >= 4 and dk in k) or (len(k) >= 4 and k in dk)), None)
            or domains[0]['id']
        )
        mapped = [
            {'sourceId': resolve_source(e.get('sourceId')) or '', 'text': clip_words(e.get('text'), 120)}
            for e in s.get('evidence') or []
            if isinstance(e, Mapping)
        ]
        # as in Node: keep the first occurrence of each text, even when that occurrence had no valid source
        first_text: dict[str, int] = {}
        for i, e in enumerate(mapped):
            first_text.setdefault(e['text'], i)
        evidence = [e for i, e in enumerate(mapped) if e['sourceId'] and e['text'] and first_text[e['text']] == i][:3]
        unlocks: list[str] = []
        for u in s.get('unlocks') or []:
            title = match_role(u) or clip_words(u, 30)
            if title and title not in unlocks:
                unlocks.append(title)
        skills.append({'id': sid, 'name': clip_words(name, 24), 'domainId': domain_id, 'score': clamp(s.get('score')), 'evidence': evidence, 'unlocks': unlocks[:3]})

    # Drop domains nobody points at (keep at least two), then group skills by domain order.
    populated = [d for d in domains if any(s['domainId'] == d['id'] for s in skills)]
    final_domains = populated if len(populated) >= 2 else domains
    order = {d['id']: i for i, d in enumerate(final_domains)}
    skills = [s if s['domainId'] in order else {**s, 'domainId': final_domains[0]['id']} for s in skills]
    skills = [s for _, s in sorted(enumerate(skills), key=lambda t: (order[t[1]['domainId']], t[0]))]

    skill_by_id = {s['id']: s for s in skills}

    def resolve_skill(raw: Any, name: str) -> str | None:
        if isinstance(raw, str) and raw in skill_by_id:
            return raw
        for cand in (raw, name):
            if not tidy(cand):
                continue
            k = loose_key(cand)
            hit = skill_alias.get(k) or next((s['id'] for s in skills if loose_key(s['name']) in (k, re.sub(r's$', '', k))), None)
            if hit:
                return hit
        return None

    # roles
    roles: list[dict[str, Any]] = []
    for ri, r in enumerate(raw_roles):
        requirements: list[dict[str, Any]] = []
        for q in r.get('requirements') or []:
            if len(requirements) >= 7:
                break
            if not isinstance(q, Mapping):
                continue
            name = tidy(q.get('name'))
            if not name:
                continue
            skill_id = resolve_skill(q.get('skillId'), name)
            skill = skill_by_id.get(skill_id) if skill_id else None
            label = clip_words(skill['name'] if skill else name, 28)
            # dedupe on the resolved identity: two paraphrases of one skill must not both appear
            if any((skill_id and x.get('skillId') == skill_id) or loose_key(x['name']) == loose_key(label) for x in requirements):
                continue
            current = skill['score'] if skill else clamp(q.get('current'))
            target = clamp(q.get('target'), 0, 100, 70)
            have = current >= 55 if q.get('have') else current >= target
            if not have and target <= current:
                target = min(100, current + 10)
            req: dict[str, Any] = {'name': label}
            if skill_id:
                req['skillId'] = skill_id
            req.update({'have': have, 'current': current, 'target': target})
            requirements.append(req)
        roles.append(
            {
                'id': unique_id(slug(tidy(r.get('id')) or tidy(r.get('title'))), used),
                'title': role_titles[ri],
                'alignment': clamp(r.get('alignment')),
                'eta': tidy(r.get('eta')) or '~6 months',
                'why': clip_words(r.get('why'), 200),
                'requirements': requirements,
            }
        )

    # sources
    synth_sources: dict[str, Mapping[str, Any]] = {}
    for s in synth.get('sources') or []:
        if not isinstance(s, Mapping):
            continue
        src_id = resolve_source(s.get('id'))
        if src_id and src_id not in synth_sources:
            synth_sources[src_id] = s
    sources: list[dict[str, Any]] = []
    for r in results:
        p = r.planned
        ss = synth_sources.get(p.id)
        raw_notes = (ss.get('notes') if ss and ss.get('notes') else (r.reader or {}).get('notes')) or []
        notes = [n for n in (clip_words(n, 140) for n in raw_notes) if n][:4]
        url = (r.raw.url if r.raw else None) or p.url
        entry: dict[str, Any] = {'id': p.id, 'kind': p.kind, 'label': (r.raw.label if r.raw else '') or p.label}
        if url:
            entry['url'] = url
        entry.update(
            {
                'sub': clip_words(tidy(ss.get('sub') if ss else '') or (r.reader or {}).get('sub') or _fallback_sub(r), 48),
                'summary': r.reader['summary'] if r.reader else (f'Could not be read: {r.error}' if r.error else ''),
                'notes': notes,
                'ok': bool(r.reader),
            }
        )
        if not r.reader and r.error:
            entry['error'] = r.error
        sources.append(entry)

    # person
    reader_people = [
        r.reader['person'] for r in sorted(results, key=lambda r: 0 if r.planned.kind == 'resume' else 1) if r.reader and r.reader.get('person')
    ]

    def rp(k: str) -> str:
        return next((tidy(x.get(k)) for x in reader_people if tidy(x.get(k))), '')

    sp: Mapping[str, Any] = m if isinstance(m := synth.get('person'), Mapping) else {}
    synth_name = tidy(sp.get('name'))
    name = synth_name if synth_name and synth_name.lower() != 'you' else rp('name') or 'You'
    current_role = tidy(sp.get('currentRole')) or rp('currentRole') or 'Professional'
    location = tidy(sp.get('location')) or rp('location') or None
    yrs_raw = sp.get('yearsExperience') if _is_num(sp.get('yearsExperience')) else next((x['yearsExperience'] for x in reader_people if _is_num(x.get('yearsExperience'))), None)
    years = _round1(float(yrs_raw)) if yrs_raw is not None and yrs_raw >= 0 else None
    headline = tidy(sp.get('headline')) or ' · '.join(
        part for part in [current_role, f'{years} yrs' if years is not None else '', location.split(',')[0].strip() if location else ''] if part
    )
    person: dict[str, Any] = {'name': name, 'headline': clip_words(headline, 80), 'currentRole': clip_words(current_role, 40)}
    if location:
        person['location'] = location
    if years is not None:
        person['yearsExperience'] = years
    person['summary'] = tidy(sp.get('summary'))

    profile: dict[str, Any] = {
        'id': f'{"sample-" if sample else ""}{_base36(int(time.time() * 1000))}-{secrets.token_hex(3)}',
        'createdAt': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
    }
    if goal:
        profile['goal'] = goal
    profile.update(
        {
            'person': person,
            'mastery': {'score': clamp(mastery_in.get('score')), 'insight': clip_words(mastery_in.get('insight'), 140)},
            'sources': sources,
            'domains': final_domains,
            'skills': skills,
            'roles': roles,
            'models': {'reader': MODELS['reader'], 'synth': MODELS['synth']},
        }
    )
    return Profile.model_validate(profile).to_dict()


def _fallback_sub(r: ReadResult) -> str:
    f = r.raw.meta if r.raw and isinstance(r.raw.meta, Mapping) else {}

    def num(k: str) -> int | None:
        v = f.get(k)
        return int(v) if _is_num(v) else None

    parts: list[str] = []
    repos = num('repos') if num('repos') is not None else num('publicRepos')
    if repos is not None:
        parts.append(plural(repos, 'repo'))
    langs = len(f['languages']) if isinstance(f.get('languages'), list) else 0
    if langs:
        parts.append(plural(langs, 'language'))
    pages = num('pages')
    if not parts and pages is not None:
        parts.append(plural(pages, 'page'))
    cases = num('caseStudies')
    if cases is not None:
        parts.append(plural(cases, 'case study').replace('studys', 'studies'))
    if parts:
        return ' · '.join(parts)
    if not r.reader and r.error:
        return 'Could not be read'
    return (r.raw.kind_label if r.raw else '') or r.planned.kind_label
