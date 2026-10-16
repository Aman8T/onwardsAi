"""The referral note for one role: three short lines that ask a connection to refer the person, checked in code.

The note is ``Hi [name], `` + three lines (part ``company``, ``you``, ``role``), then the ask "Would you be open to referring
me?" and the link to the employer's posting. One Claude Haiku call (`jobsearch.referral`) writes the lines in the person's
voice and returns, for each, its citations first (posting quotes, evidence ids) and then its text. Code checks every line:

    quotes      every “quoted” span of the text is a passage of the posting (three words or more, `judge.quoted`)
    anchors     every number, identifier and capitalised name of the text is in the line's corpus: the evidence lines it
                cites, the posting, the employer, the job title, the place, and the person's current role and location.
                A word that opens a sentence is checked too (a sentence is not a free pass for an invented company), and
                so are spelled-out numbers ("three engineers") and the words of seniority (led, managed, founded ...)
    cites       ``you`` cites an evidence line that exists, and so does ``role``; ``company`` and ``role`` quote the
                posting; ``you`` and ``role`` use words of the evidence they cite (a citation is not decoration); the
                ``company`` line says nothing about what the person has done
    form        at most 220 characters, no praise words (passionate, rockstar, world-class, ninja, guru) unless the
                posting or the evidence uses them

A line that fails is replaced by a template built only from facts (the employer, the title, the place, one posting quote,
one evidence line, the level code worked out). A model that fails, or a posting with text aimed at an AI, gives the
all-template note. Every line says where it comes from (``sources``), in words the person reads.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Final

from tayyari.contract.jobsearch import AiJob, ReferralLine, ReferralNote, ReferralPart
from tayyari.contract.models import Profile
from tayyari.core.logging import get_logger
from tayyari.core.text import clip_words, tidy
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError
from tayyari.llm.types import CallContext, TextBlock
from tayyari.portfolio.ground import Corpus, split_sentences, tokens_of, unsupported_anchors, vocabulary
from tayyari.prompts.jobsearch_prep import REFERRAL_SYSTEM, build_referral_prompt

from .brief import Evidence, evidence_lines
from .judge import fold, has_injection, quoted
from .prepare import cited_evidence, clean_quote, has_puffery

log = get_logger(__name__)

PARTS: Final[tuple[ReferralPart, ...]] = ("company", "you", "role")
ASK: Final = "Would you be open to referring me?"
GREETING: Final = "Hi [name], "
REFERRAL_MAX_TOKENS: Final = 2000
MAX_LINE_CHARS: Final = 220
MAX_LINE_EVIDENCE: Final = 2
"""Evidence lines one line may cite (and so the facts it may use)."""
TEMPLATE_QUOTE_MAX_WORDS: Final = 18
TEMPLATE_EVIDENCE_CHARS: Final = 170
POSTING: Final = "the posting"
THEIR_ASK: Final = "their ask"


def _line_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["quotes", "evidence", "text"],
        "properties": {
            "quotes": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "text": {"type": "string"},
        },
    }


REFERRAL_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(PARTS),
    "properties": {part: _line_schema() for part in PARTS},
}
"""Each line: the posting quotes and the evidence ids first, the text last."""

_SPAN = re.compile(r"[“\"]([^“”\"]+)[”\"]")
_QUOTE_MARKS = '“”"'
_COMPANY_CLAIM = re.compile(
    r"\bI(?:['’]ve|\s+(?:have|had|built|led|worked|wrote|shipped|managed|made|ran|spent|did|used|founded|launched"
    r"|created|developed|designed|rebuilt|migrated|raised|grew))\b",
    re.IGNORECASE,
)
"""The company line cites no evidence, so it may not say what the person has done."""
_SPELLED: Final[dict[str, int]] = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
"""Spelled-out numbers: "three" is fine when the corpus says 3 (or "three")."""
_SCALES: Final = vocabulary(
    "hundred hundreds thousand thousands million millions billion billions lakh lakhs crore crores dozen dozens"
)
"""Scale words claim a size: the corpus must use the word itself."""
_OPENERS: Final = vocabulary(
    "i my me we our us the a an it its this that these those their they them there here and but so because since also "
    "now with as at in on for from to when while after before if one both each every most more many what why how "
    "your you he she his her then than though although however instead"
)
"""Words that may open a sentence without being a name."""
_INFLATION: Final[dict[str, frozenset[str]]] = {
    group: frozenset(forms.split())
    for group, forms in {
        "led": "led lead leads leading leader leaders",
        "headed": "headed head heads",
        "founded": "founded founder founders founding cofounded cofounder",
        "managed": "managed manage manages managing manager managers",
        "directed": "directed directs directing director directors",
        "spearheaded": "spearheaded spearhead spearheads spearheading",
        "owned": "owned own owns owning owner owners ownership",
        "architected": "architected architect architects architecting",
    }.items()
}
_INFLATION_GROUP: Final[dict[str, str]] = {
    form: group for group, forms in _INFLATION.items() for form in forms
}
_POSSESSIVE: Final = vocabulary("her his their my our your its whose own")
_COMMON: Final = vocabulary(
    "with that this from have has into over their about which were been than then them they when what also more most some "
    "such only each very your team teams work works using used make made"
)
"""Words of an evidence line that do not tie a sentence to it."""
_IRREGULAR_PAST: Final = vocabulary(
    "built wrote led ran made won taught grew cut put set sold got found drove spoke took brought kept held began rose "
    "sped drew chose split swept shut"
)


@dataclass(frozen=True, slots=True)
class _Source:
    """What a line is checked against, prepared once."""

    job: AiJob
    posting_text: str
    folded: str
    base: str
    evidence: Mapping[str, Evidence]


@dataclass(frozen=True, slots=True)
class _Item:
    """One line as the model returned it, read: its text, the evidence it cites that exists and the quotes that are real."""

    text: str
    cited: list[Evidence]
    quotes: list[str]
    spans: list[str]


@dataclass(slots=True)
class ReferralChecked:
    """The note's lines and what was replaced (by reason, for the log)."""

    lines: list[ReferralLine] = field(default_factory=list)
    replaced: dict[str, str] = field(default_factory=dict)
    """Part -> the reason its line was replaced by a template."""
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, count: int = 1) -> None:
        if count:
            self.dropped[reason] = self.dropped.get(reason, 0) + count


# ───────────────────────── reading ─────────────────────────


def _read_item(raw: object, src: _Source, part: ReferralPart, out: ReferralChecked) -> _Item:
    """The model's line as data: tolerant of anything, counting the citations that do not check out."""
    item: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    text = tidy(item.get("text"))
    quotes: list[str] = []
    cited_quotes = item.get("quotes")
    for value in cited_quotes if isinstance(cited_quotes, list) else []:
        quote = clean_quote(value)
        if quoted(quote, src.folded):
            quotes.append(quote)
        else:
            out.drop("cited_quote_not_found")
    cited: list[Evidence] = []
    if part != "company":
        cited, bad = cited_evidence(item.get("evidence"), src.evidence, limit=MAX_LINE_EVIDENCE)
        out.drop("bad_evidence", bad)
    return _Item(text=text, cited=cited, quotes=quotes, spans=[clean_quote(m) for m in _SPAN.findall(text)])


# ───────────────────────── checks ─────────────────────────


def _unsupported(text: str, corpus: Corpus) -> list[str]:
    """Names and numbers of ``text`` that ``corpus`` lacks. A sentence's first word is checked too (it is a name unless
    it is an ordinary opener or a word of the corpus), which the anchors alone leave out."""
    missing: list[str] = []
    for sentence in split_sentences(text):
        opener = re.match(r"\W*([^\W_]+)", sentence)
        first = opener.group(1) if opener else ""
        for anchor in unsupported_anchors(f"and {sentence}", corpus):
            if anchor == first and first.casefold() in _OPENERS:
                continue
            missing.append(anchor)
    return missing


def _unsupported_words(text: str, corpus: Corpus) -> list[str]:
    """Spelled-out numbers ("three engineers") and seniority words (led, managed ...) the corpus does not use."""
    tokens = tokens_of(text)
    missing = [
        t
        for t in dict.fromkeys(tokens)
        if (t in _SCALES and t not in corpus.token_set)
        or (t in _SPELLED and t not in corpus.token_set and str(_SPELLED[t]) not in corpus.numbers)
    ]
    seen: set[str] = set()
    for i, word in enumerate(tokens):
        group = _INFLATION_GROUP.get(word)
        if group is None or group in seen:
            continue
        if word == "own" and i > 0 and tokens[i - 1] in _POSSESSIVE:
            continue
        seen.add(group)
        if not any(form in corpus.token_set for form in _INFLATION[group]):
            missing.append(word)
    return missing


def _key(token: str) -> str:
    return token[:-1] if token.endswith("s") and len(token) > 4 else token


def _ties_to(item: _Item) -> bool:
    """Whether the line's own words (outside the posting quotes) use a word or number of an evidence line it cites."""
    own = _SPAN.sub(" ", item.text)
    mine = {_key(t) for t in tokens_of(own)}
    for line in item.cited:
        for token in tokens_of(line.cite.text):
            if (any(c.isdigit() for c in token) or (len(token) >= 4 and token not in _COMMON)) and _key(
                token
            ) in mine:
                return True
    return False


def _problem(part: ReferralPart, item: _Item, src: _Source) -> str | None:
    """Why the line cannot be used, or ``None`` when every check passes."""
    if not item.text:
        return "line_missing"
    if len(item.text) > MAX_LINE_CHARS:
        return "line_too_long"
    if sum(item.text.count(mark) for mark in _QUOTE_MARKS) % 2:
        return "quote_unbalanced"
    if any(not quoted(span, src.folded) for span in item.spans):
        return "quote_not_found"
    if part in ("company", "role") and not item.spans:
        return "no_quote"
    if part in ("you", "role") and not item.cited:
        return "no_evidence"
    corpus_text = "\n".join([src.base, *(line.cite.text for line in item.cited)])
    if has_puffery(item.text, corpus_text):
        return "puffery"
    if part == "company" and _COMPANY_CLAIM.search(item.text):
        return "company_claims"
    corpus = Corpus.of(corpus_text)
    if _unsupported(item.text, corpus) or _unsupported_words(item.text, corpus):
        return "unsupported"
    if part != "company" and not _ties_to(item):
        return "not_tied_to_evidence"
    return None


# ───────────────────────── templates ─────────────────────────


def _article(title: str) -> str:
    return "an" if title[:1].upper() in "AEIO" else "a"


def _source_label(source: str) -> str:
    """``Resume`` -> ``your resume``: where a line comes from, in words the person reads."""
    return f"your {tidy(source).lower()}"


def _reads_as_action(text: str) -> bool:
    """Whether ``text`` opens with a past-tense verb ("Rebuilt the checkout ..."), so ``I`` + it reads as a sentence."""
    match = re.match(r"([A-Za-z]+)\s", text)
    if match is None:
        return False
    word = match.group(1).lower()
    if len(word) > 3 and word.endswith("ed") and not word.endswith("eed"):
        return True
    return any(word.removeprefix(prefix) in _IRREGULAR_PAST for prefix in ("", "re", "over", "under", "out"))


def _company_template(job: AiJob, quote: str | None) -> str:
    label = tidy(job.place.label)
    where = f"({label})" if label.lower().startswith("remote") else f"in {label}"
    base = f"{job.employer} is hiring {_article(job.title)} {job.title} {where}."
    extra = f" Their posting says “{quote}”." if quote else ""
    return base + extra if len(base + extra) <= MAX_LINE_CHARS else base


def _you_template(job: AiJob, line: Evidence | None) -> tuple[str, list[str]]:
    if line is None:
        return f"I am applying for the {job.title} role.", [POSTING]
    text = tidy(line.cite.text)
    if _reads_as_action(text):
        clause = text.split(";")[0].strip().rstrip(".")
        body = clip_words(clause[:1].lower() + clause[1:], TEMPLATE_EVIDENCE_CHARS)
        end = "" if body.endswith("…") else "."
        return f"I {body}{end}", [_source_label(line.cite.source)]
    source = tidy(line.cite.source)
    label = "resume" if source.lower() == "resume" else source
    return f"My {label} shows “{clip_words(text, TEMPLATE_EVIDENCE_CHARS)}”.", [
        _source_label(line.cite.source)
    ]


def _role_template(job: AiJob) -> str:
    article = _article(job.title)
    if job.level == "at":
        return f"It is {article} {job.title} role at my level."
    if job.level == "step_up":
        return f"It is {article} {job.title} role I want to grow into."
    return f"It is {article} {job.title} role I would like to be considered for."


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# ───────────────────────── the note ─────────────────────────


def check_referral(
    raw: Mapping[str, Any] | None,
    *,
    job: AiJob,
    profile: Profile,
    evidence: Mapping[str, Evidence],
    posting_text: str,
) -> ReferralChecked:
    """Check what the model returned (``None``: nothing) and build the three lines, a template for each that fails."""
    out = ReferralChecked()
    data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    person = profile.person
    base = "\n".join(
        [
            posting_text,
            job.employer,
            job.title,
            job.place.label,
            tidy(person.currentRole),
            tidy(person.location),
        ]
    )
    src = _Source(job, posting_text, fold(posting_text), base, evidence)
    for part in PARTS:
        item = _read_item(data.get(part), src, part, out)
        problem = _problem(part, item, src) if raw is not None else "model_failed"
        if problem is None:
            if part == "company":
                sources = [POSTING]
            else:
                sources = _dedupe([_source_label(line.cite.source) for line in item.cited])
                if part == "you":
                    sources = [*sources, THEIR_ASK] if item.spans else sources
                else:
                    sources = [POSTING, *sources]
            out.lines.append(ReferralLine(part=part, text=item.text, sources=sources))
            continue
        out.replaced[part] = problem
        if part == "company":
            short = next((q for q in item.quotes if len(q.split()) <= TEMPLATE_QUOTE_MAX_WORDS), None)
            out.lines.append(ReferralLine(part=part, text=_company_template(job, short), sources=[POSTING]))
        elif part == "you":
            line = item.cited[0] if item.cited else (next(iter(evidence.values()), None))
            text, sources = _you_template(job, line)
            out.lines.append(ReferralLine(part=part, text=text, sources=sources))
        else:
            out.lines.append(ReferralLine(part=part, text=_role_template(job), sources=[POSTING]))
    first = out.lines[0]
    out.lines[0] = ReferralLine(part=first.part, text=f"{GREETING}{first.text}", sources=first.sources)
    return out


async def _ask(
    *,
    claude: ClaudeClient,
    model: str,
    profile: Profile,
    job: AiJob,
    posting_text: str,
    evidence: tuple[Evidence, ...],
    ctx: CallContext,
) -> Mapping[str, Any] | None:
    prompt = build_referral_prompt(
        title=job.title,
        employer=job.employer,
        place=job.place.label,
        role=tidy(profile.person.currentRole),
        location=tidy(profile.person.location),
        posting_text=posting_text,
        evidence=[line.line() for line in evidence],
    )
    try:
        result = await claude.structured(
            model=model,
            system=[TextBlock(REFERRAL_SYSTEM)],
            user=[TextBlock(prompt)],
            schema=REFERRAL_SCHEMA,
            max_tokens=REFERRAL_MAX_TOKENS,
            effort="low",
            ctx=replace(ctx, purpose="jobsearch.referral"),
        )
    except ClaudeError as exc:
        log.warning("jobsearch_referral_failed", job=job.id, kind=exc.kind)
        return None
    return result.data


async def write_referral(
    *,
    claude: ClaudeClient,
    model: str,
    profile: Profile,
    job: AiJob,
    posting_text: str,
    ctx: CallContext,
    now: datetime,
) -> ReferralNote:
    """The referral note for ``job`` (see the module docstring). One model call; a failed call gives the template note."""
    evidence = evidence_lines(profile)
    raw: Mapping[str, Any] | None = None
    if has_injection(posting_text):
        log.warning("jobsearch_referral_anomaly", job=job.id, anomaly="text aimed at an AI in the posting")
    else:
        raw = await _ask(
            claude=claude,
            model=model,
            profile=profile,
            job=job,
            posting_text=posting_text,
            evidence=evidence,
            ctx=ctx,
        )
    checked = check_referral(
        raw,
        job=job,
        profile=profile,
        evidence={line.id: line for line in evidence},
        posting_text=posting_text,
    )
    log.info(
        "jobsearch_referral",
        job=job.id,
        answered=raw is not None,
        replaced=checked.replaced,
        dropped=checked.dropped,
    )
    return ReferralNote(
        jobId=job.id, lines=checked.lines, ask=ASK, link=job.applyUrl, createdAt=now.isoformat()
    )


__all__ = [
    "ASK",
    "GREETING",
    "REFERRAL_SCHEMA",
    "ReferralChecked",
    "check_referral",
    "write_referral",
]
