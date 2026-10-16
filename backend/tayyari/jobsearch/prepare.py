"""The prep page for one role: what to lead with, the gaps and a small step for each, and the questions to expect.

    lead        built in code from the role's met asks (the person's own evidence, each a quote checked by the read)
    one call    Claude Haiku (`jobsearch.prepare`) sees the posting as data, the met asks, the gaps and the evidence lines
                and returns, for each gap, the evidence ids nearest to it and a step, and 3 to 4 likely questions, each with
                the posting's words for the requirement it is about and the evidence ids a story could be told from
    checks      in code, and anything that fails is dropped or replaced and counted for the log:
        gap         the quote must be one of the role's own gaps; ``nearest`` is the first evidence id that exists. A gap
                    the model left out is still shown, with a step of ours
        step        at most 200 characters, no claim about the person ("you have", "your experience shows" ...), no puffery,
                    and no name or number that is not in the posting, the gap or the evidence it cites; otherwise
                    `Write two lines on how you would show “gap” before you apply.`
        question    the ask must be a passage of the posting (`judge.quoted`); the question ends with "?" and every name
                    and number in it is in the posting; a question that fails is dropped
        story       built in code from the evidence ids that exist: a situation placeholder, what you did (a cited
                    line, at most two), the result (a cited line that holds a digit and is not already shown, else a placeholder that asks for a
                    number) and looking back. The model writes no part of it
    failure     a model that fails, or a posting with text aimed at an AI, still gives a page: the lead, the gaps with our
                steps, and no questions

Nothing the model writes about the person reaches the page: a step is advice, a question is about the posting, a story is the
person's own evidence lines and placeholders.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Final

from tayyari.contract.jobsearch import AiJob, PrepGap, PrepLead, PrepQuestion, RolePrep, StoryPart
from tayyari.contract.models import Profile
from tayyari.core.logging import get_logger
from tayyari.core.text import clip_words, tidy
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError
from tayyari.llm.types import CallContext, TextBlock
from tayyari.portfolio.ground import Corpus, unsupported_anchors
from tayyari.prompts.jobsearch_prep import PREPARE_SYSTEM, build_prepare_prompt

from .brief import Evidence, evidence_lines
from .judge import MAX_GAPS_SHOWN, fold, has_injection, quoted

log = get_logger(__name__)

PREPARE_MAX_TOKENS: Final = 3500
MAX_QUESTIONS: Final = 4
STEP_MAX_CHARS: Final = 200
QUESTION_MAX_CHARS: Final = 160
QUESTION_MIN_WORDS: Final = 4
STORY_MAX_LINES: Final = 2
"""Cited evidence lines a story shows under "What you did"."""
STORY_MAX_IDS: Final = 3
"""Evidence ids of one question that count (the result is looked for among them)."""
GAP_IN_STEP_CHARS: Final = 120

SITUATION: Final = "[add: the team and the problem]"
DID: Final = "[add: what you did yourself, step by step]"
RESULT: Final = "[add: what changed, with a number you can stand behind]"
LOOKING_BACK: Final = "[add: what you would do differently]"

PREPARE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gaps", "questions"],
    "properties": {
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gap", "evidence", "step"],
                "properties": {
                    "gap": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "step": {"type": "string"},
                },
            },
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ask", "evidence", "question"],
                "properties": {
                    "ask": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "question": {"type": "string"},
                },
            },
        },
    },
}
"""Citations before text in every item: the gap and the evidence ids before the step, the ask and the ids before the question."""

_EDGE = re.compile(r"^[\s\-*•:;,.\"'“”‘’]+|[\s\-*•:;,.\"'“”‘’]+$")
_DIGIT = re.compile(r"\d")
_CLAIM = re.compile(
    r"\byou(?:['’]ve|['’]re|\s+(?:have|had|are|were|already|did|built|used|led|worked|wrote|shipped|managed|made|ran"
    r"|own|owned|clearly|obviously|currently|really))\b"
    r"|\byour\s+(?:experience|resume|cv|work|profile|background|portfolio|github|projects?|skills?|record|history)"
    r"\s+(?:shows?|showed|proves?|proved|demonstrates?|has|have|includes?|mentions?|says?|lists?|confirms?|already)\b"
    r"|\bI(?:['’]ve|['’]m|\s+(?:have|had|am|built|led|worked|wrote|shipped|managed|made|ran|own|owned|know))\b",
    re.IGNORECASE,
)
"""Words that say something about the person. A step is advice; it may not assert what they have done, know or are."""
_PUFFERY: Final = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bpassionate\b",
        r"\bworld[-\s]class\b",
        r"\brock[-\s]?stars?\b",
        r"\bninjas?\b",
        r"\bgurus?\b",
        r"\bvisionar(?:y|ies)\b",
        r"\bcutting[-\s]edge\b",
        r"\bsynerg(?:y|ies|istic)\b",
        r"\bresults?[-\s]driven\b",
    )
)


def clean_quote(value: object) -> str:
    """A quote a model returned, without the spaces, quote marks and punctuation round it (non-text becomes "")."""
    return _EDGE.sub("", value) if isinstance(value, str) else ""


def has_puffery(text: str, corpus_text: str) -> bool:
    """Whether ``text`` uses a praise word (passionate, rockstar, world-class ...) that ``corpus_text`` does not use itself."""
    return any(p.search(text) and not p.search(corpus_text) for p in _PUFFERY)


def cited_evidence(
    raw: object, evidence: Mapping[str, Evidence], *, limit: int
) -> tuple[list[Evidence], int]:
    """The evidence lines a model cited that exist (in the order cited, no repeats, at most ``limit``), and how many of
    the ids it gave do not exist."""
    found: list[Evidence] = []
    bad = 0
    for value in raw if isinstance(raw, list) else []:
        line = evidence.get(value.strip().upper()) if isinstance(value, str) else None
        if line is None:
            bad += 1
        elif all(line.id != seen.id for seen in found):
            found.append(line)
    return found[:limit], bad


@dataclass(slots=True)
class PrepChecked:
    """The gaps and questions that survived the checks, and what was dropped or replaced (by reason, for the log)."""

    gaps: list[PrepGap] = field(default_factory=list)
    questions: list[PrepQuestion] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, count: int = 1) -> None:
        if count:
            self.dropped[reason] = self.dropped.get(reason, 0) + count


def fallback_step(gap: str) -> str:
    """The step when the model's is missing or fails a check: a small thing to do that claims nothing."""
    return f"Write two lines on how you would show “{clip_words(gap, GAP_IN_STEP_CHARS)}” before you apply."


def build_story(lines: list[Evidence]) -> list[StoryPart]:
    """A story skeleton from the cited evidence lines: situation, what you did, result, looking back. Every part either
    shows one of the person's own lines or asks them to fill it in."""
    parts = [StoryPart(label="Situation", placeholder=SITUATION)]
    shown = lines[:STORY_MAX_LINES]
    parts += [StoryPart(label="What you did", cite=line.cite) for line in shown] or [
        StoryPart(label="What you did", placeholder=DID)
    ]
    used = {line.cite.text for line in shown}
    numeric = next(
        (
            line
            for line in lines[:STORY_MAX_IDS]
            if _DIGIT.search(line.cite.text) and line.cite.text not in used
        ),
        None,
    )
    parts.append(
        StoryPart(label="Result", cite=numeric.cite)
        if numeric is not None
        else StoryPart(label="Result", placeholder=RESULT)
    )
    parts.append(StoryPart(label="Looking back", placeholder=LOOKING_BACK))
    return parts


def _step_problem(step: object, corpus: Corpus, corpus_text: str) -> str | None:
    text = tidy(step)
    if not text or len(text) > STEP_MAX_CHARS:
        return "step_malformed"
    if _CLAIM.search(text):
        return "step_claims"
    if has_puffery(text, corpus_text):
        return "step_puffery"
    if unsupported_anchors(text, corpus):
        return "step_unsupported"
    return None


def _check_gaps(
    raw: object,
    job: AiJob,
    evidence: Mapping[str, Evidence],
    base: str,
    out: PrepChecked,
) -> list[PrepGap]:
    """Every gap of the role, in order; the model's evidence and step for it when they pass, ours otherwise."""
    listed = {fold(gap): gap for gap in job.gaps[:MAX_GAPS_SHOWN]}
    answers: dict[str, Mapping[str, Any]] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            out.drop("gap_malformed")
            continue
        key = fold(clean_quote(item.get("gap")))
        if key not in listed:
            out.drop("gap_not_listed")
        elif key in answers:
            out.drop("gap_duplicate")
        else:
            answers[key] = item

    gaps: list[PrepGap] = []
    for key, gap in listed.items():
        item = answers.get(key)
        cited: list[Evidence] = []
        step = fallback_step(gap)
        if item is not None:
            cited, bad = cited_evidence(item.get("evidence"), evidence, limit=STORY_MAX_IDS)
            out.drop("bad_evidence", bad)
            corpus_text = "\n".join([base, gap, *(line.cite.text for line in cited)])
            problem = _step_problem(item.get("step"), Corpus.of(corpus_text), corpus_text)
            if problem is None:
                step = tidy(item.get("step"))
            else:
                out.drop(problem)
        else:
            out.drop("gap_without_step")
        gaps.append(PrepGap(gap=gap, nearest=cited[0].cite if cited else None, step=step))
    return gaps


def _question_problem(question: str, corpus: Corpus) -> str | None:
    if len(question) > QUESTION_MAX_CHARS or len(question.split()) < QUESTION_MIN_WORDS:
        return "question_malformed"
    if not question.endswith("?"):
        return "question_no_mark"
    if unsupported_anchors(question, corpus):
        return "question_unsupported"
    return None


def _check_questions(
    raw: object,
    evidence: Mapping[str, Evidence],
    folded: str,
    corpus: Corpus,
    out: PrepChecked,
) -> list[PrepQuestion]:
    questions: list[PrepQuestion] = []
    asked: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if len(questions) == MAX_QUESTIONS:
            break
        if not isinstance(item, Mapping):
            out.drop("question_malformed")
            continue
        ask = clean_quote(item.get("ask"))
        if not quoted(ask, folded):
            out.drop("ask_not_quoted")
            continue
        question = tidy(item.get("question"))
        problem = _question_problem(question, corpus)
        if problem is not None:
            out.drop(problem)
            continue
        if fold(ask) in asked:
            out.drop("question_duplicate")
            continue
        asked.add(fold(ask))
        cited, bad = cited_evidence(item.get("evidence"), evidence, limit=STORY_MAX_IDS)
        out.drop("bad_evidence", bad)
        questions.append(PrepQuestion(question=question, ask=ask, story=build_story(cited)))
    return questions


def check_prep(
    raw: Mapping[str, Any] | None,
    *,
    job: AiJob,
    evidence: Mapping[str, Evidence],
    posting_text: str,
) -> PrepChecked:
    """Check what the model returned (``None``: it returned nothing) against the posting, the role's gaps and the evidence
    lines (see the module docstring). Never raises on a malformed answer; it drops what it cannot use."""
    out = PrepChecked()
    data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    base = "\n".join([posting_text, job.title, job.employer, job.place.label])
    out.gaps = _check_gaps(data.get("gaps"), job, evidence, base, out)
    out.questions = _check_questions(
        data.get("questions"), evidence, fold(posting_text), Corpus.of(base), out
    )
    return out


async def _ask(
    *,
    claude: ClaudeClient,
    model: str,
    job: AiJob,
    posting_text: str,
    evidence: tuple[Evidence, ...],
    ctx: CallContext,
) -> Mapping[str, Any] | None:
    prompt = build_prepare_prompt(
        title=job.title,
        employer=job.employer,
        posting_text=posting_text,
        met=job.met,
        gaps=job.gaps[:MAX_GAPS_SHOWN],
        evidence=[line.line() for line in evidence],
    )
    try:
        result = await claude.structured(
            model=model,
            system=[TextBlock(PREPARE_SYSTEM)],
            user=[TextBlock(prompt)],
            schema=PREPARE_SCHEMA,
            max_tokens=PREPARE_MAX_TOKENS,
            effort="low",
            ctx=replace(ctx, purpose="jobsearch.prepare"),
        )
    except ClaudeError as exc:
        log.warning("jobsearch_prepare_failed", job=job.id, kind=exc.kind)
        return None
    return result.data


async def prepare_role(
    *,
    claude: ClaudeClient,
    model: str,
    profile: Profile,
    job: AiJob,
    posting_text: str,
    ctx: CallContext,
    now: datetime,
) -> RolePrep:
    """The prep page of ``job`` for the person of ``profile`` (see the module docstring). One model call; a failed call
    gives the page without questions and with our steps."""
    evidence = evidence_lines(profile)
    raw: Mapping[str, Any] | None = None
    if has_injection(posting_text):
        log.warning("jobsearch_prepare_anomaly", job=job.id, anomaly="text aimed at an AI in the posting")
    else:
        raw = await _ask(
            claude=claude, model=model, job=job, posting_text=posting_text, evidence=evidence, ctx=ctx
        )
    checked = check_prep(
        raw, job=job, evidence={line.id: line for line in evidence}, posting_text=posting_text
    )
    log.info(
        "jobsearch_prepare",
        job=job.id,
        answered=raw is not None,
        gaps=len(checked.gaps),
        questions=len(checked.questions),
        dropped=checked.dropped,
    )
    return RolePrep(
        jobId=job.id,
        title=job.title,
        employer=job.employer,
        lead=[PrepLead(ask=m.ask, cite=m.cite, partly=m.partly) for m in job.met],
        gaps=checked.gaps,
        questions=checked.questions,
        createdAt=now.isoformat(),
    )


__all__ = [
    "DID",
    "LOOKING_BACK",
    "PREPARE_SCHEMA",
    "RESULT",
    "SITUATION",
    "PrepChecked",
    "build_story",
    "check_prep",
    "cited_evidence",
    "clean_quote",
    "fallback_step",
    "has_puffery",
    "prepare_role",
]
