"""The referral note for one role: one short paragraph of three sentences that ask a connection to refer the person, checked
in code.

The paragraph is a small story in three parts, each continuing from the one before (``intro`` · ``fit`` · ``draw``), then the
ask "Would you be open to referring me?" and the link to the employer's posting. The web app puts "Hi [name]," in front.

    intro   who the person is: "I'm" or "I", their current role (and place) and at most one cited evidence line that
            defines them. No posting quote.
    fit     why they are a great fit for what the employer needs. The search already worked out which of the posting's
            asks the evidence meets (``AiJob.met``: each ask with the evidence line that met it), so the model picks
            ONE of those, quotes it and says what the person did that meets it, citing exactly that ask's line.
    draw    why the employer is a great fit for the person: a quote of the posting's own words about the company's
            mission or the role's work, and one plain line on why that is where the person wants to go next. It says
            nothing about what the person has done.

One Claude Haiku call (`jobsearch.referral`) writes the sentences in the person's voice and returns, for each, its
citations first (posting quotes, evidence ids) and then its text. Code checks every sentence:

    quotes      every “quoted” span of the text is a passage of the posting (three words or more, `judge.quoted`)
    anchors     every number, identifier and capitalised name of the text is in the sentence's corpus: the evidence it
                cites plus, for ``fit`` and ``draw``, the posting, the employer, the job title and the place; for
                ``intro`` only the person's current role and place (it is about them, not the job). A word that opens a
                sentence is checked too (a sentence is not a free pass for an invented company), and so are spelled-out
                numbers ("three engineers") and the words of seniority (led, managed, founded ...)
    match       ``fit`` quotes exactly one passage, of one of the asks in ``job.met``, and cites exactly the evidence line
                that met that ask (not another line, not two); it uses words of that line (a citation is not decoration)
    cites       ``intro`` cites at most one evidence line and says nothing but the person's role, place and that line;
                ``draw`` quotes the posting and says nothing about what the person has done
    form        one sentence of at most 170 characters (three of them, the greeting and the ask make a 70 to 90 word
                paragraph), no question, greeting or sign-off (code adds those), no praise words (passionate,
                rockstar, world-class, ninja, guru) unless the posting or the evidence uses them

A sentence that fails is replaced by a template built only from facts, written so that it still reads in the paragraph:
"I'm a {role} in {place}." / "{employer} wants “{ask}”, and I {what the evidence says}." / "What draws me is
“{posting quote}”.". A model that fails, or a posting with text aimed at an AI, gives the all-template note. Every sentence
says where it comes from (``sources``), in words the person reads.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Final

from tayyari.contract.discover import Cite
from tayyari.contract.jobsearch import AiJob, AskMet, ReferralLine, ReferralNote, ReferralPart
from tayyari.contract.models import Profile
from tayyari.core.logging import get_logger
from tayyari.core.text import clip_words, tidy
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError
from tayyari.llm.types import CallContext, TextBlock
from tayyari.portfolio.ground import Corpus, split_sentences, tokens_of, unsupported_anchors, vocabulary
from tayyari.prompts.jobsearch_prep import REFERRAL_SYSTEM, ReferralMatch, build_referral_prompt

from .brief import Evidence, evidence_lines
from .judge import fold, has_injection, quoted
from .prepare import cited_evidence, clean_quote, has_puffery

log = get_logger(__name__)

PARTS: Final[tuple[ReferralPart, ...]] = ("intro", "fit", "draw")
ASK: Final = "Would you be open to referring me?"
REFERRAL_MAX_TOKENS: Final = 2000
MAX_LINE_CHARS: Final = 170
"""The longest ``intro`` or ``draw`` sentence."""
MAX_FIT_CHARS: Final = 230
"""``fit`` carries a passage of the posting's ask and what the person did, so it may run longer (the paragraph stays
around 70 to 90 words)."""
"""One sentence: three of them, the greeting and the ask are a paragraph of about 70 to 90 words."""
MAX_LINE_EVIDENCE: Final = 2
"""Evidence ids read from a sentence (``intro`` may keep one, ``fit`` exactly one: the checks say so)."""
TEMPLATE_QUOTE_MAX_WORDS: Final = 18
"""The longest posting quote a ``draw`` template carries."""
FIT_QUOTE_WORDS: Final = 12
"""The words of the ask a ``fit`` template quotes (the start of it, which is still a passage of the posting)."""
FIT_QUOTE_MIN_WORDS: Final = 3
POSTING: Final = "the posting"
PROFILE: Final = "your profile"
"""Where the person's role and place come from when no evidence line is cited."""


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
"""Each sentence: the posting quotes and the evidence ids first, the text last."""

_SPAN = re.compile(r"[“\"]([^“”\"]+)[”\"]")
_QUOTE_MARKS = '“”"'
_I_OPENING = re.compile(r"I(?:['’]m\b|\s)")
_GREETING = re.compile(r"^\W*(?:hi|hello|hey|dear|greetings)\b", re.IGNORECASE)
_SIGN_OFF = re.compile(r"\b(?:thanks|thank\s+you|regards|cheers|sincerely)\b", re.IGNORECASE)
_I_VERB = re.compile(r"\bI\s+([A-Za-z]+)\b")
_OWN_CLAIM = re.compile(
    r"\bI['’]ve\b"
    r"|\bmy\s+(?:[\w-]+\s+)?(?:experience|background|career|skills?|resume|portfolio|track\s+record|years?|projects?)\b"
    r"|\bI(?:['’]m|\s+am)\s+(?:an?|the)\b",
    re.IGNORECASE,
)
_DONE_WORDS: Final = vocabulary("have had did was were spent")
"""What the person has done is the evidence's to say: ``draw`` cites none, so it may not say any of it."""
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
class _Match:
    """One ask of the posting that the person's evidence meets, with the evidence line that met it."""

    ask: AskMet
    line: Evidence
    folded: str


@dataclass(frozen=True, slots=True)
class _Source:
    """What a sentence is checked against, prepared once."""

    job: AiJob
    posting_text: str
    folded: str
    base: str
    """The posting, the employer, the job title and place, and the person's role and place: what ``fit`` and ``draw`` may use."""
    who: str
    """The person's current role and place: all ``intro`` may use besides its one evidence line."""
    role: str
    evidence: Mapping[str, Evidence]
    matches: tuple[_Match, ...]


@dataclass(frozen=True, slots=True)
class _Item:
    """One sentence as the model returned it, read: its text, the evidence it cites that exists and the quotes that are real."""

    text: str
    cited: list[Evidence]
    quotes: list[str]
    spans: list[str]


@dataclass(slots=True)
class ReferralChecked:
    """The note's sentences and what was replaced (by reason, for the log)."""

    lines: list[ReferralLine] = field(default_factory=list)
    replaced: dict[str, str] = field(default_factory=dict)
    """Part -> the reason its sentence was replaced by a template."""
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, count: int = 1) -> None:
        if count:
            self.dropped[reason] = self.dropped.get(reason, 0) + count


# ───────────────────────── the match the search already worked out ─────────────────────────


def referral_matches(job: AiJob, lines: Iterable[Evidence]) -> tuple[_Match, ...]:
    """The asks of ``job`` that the evidence met (strongest first: those met in full, then the partly met), each with the
    evidence line that met it. An ask whose line is no longer in the profile's evidence is left out."""
    by_cite = {(line.cite.source, line.cite.text): line for line in lines}
    found: list[_Match] = []
    for met in sorted(job.met, key=lambda m: m.partly):
        line = by_cite.get((met.cite.source, met.cite.text))
        if line is not None:
            found.append(_Match(met, line, fold(met.ask)))
    return tuple(found)


# ───────────────────────── reading ─────────────────────────


def _read_item(raw: object, src: _Source, part: ReferralPart, out: ReferralChecked) -> _Item:
    """The model's sentence as data: tolerant of anything, counting the citations that do not check out."""
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
    if part != "draw":
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
    """Whether the sentence's own words (outside the posting quotes) use a word or number of an evidence line it cites."""
    own = _SPAN.sub(" ", item.text)
    mine = {_key(t) for t in tokens_of(own)}
    for line in item.cited:
        for token in tokens_of(line.cite.text):
            if (any(c.isdigit() for c in token) or (len(token) >= 4 and token not in _COMMON)) and _key(
                token
            ) in mine:
                return True
    return False


def _past(word: str) -> bool:
    """Whether ``word`` (the one after "I") is a verb of something done: built, led, rebuilt, have, had ..."""
    w = word.lower()
    if w in _DONE_WORDS:
        return True
    if len(w) > 3 and w.endswith("ed") and not w.endswith("eed"):
        return True
    return any(w.removeprefix(prefix) in _IRREGULAR_PAST for prefix in ("", "re", "over", "under", "out"))


def _claims_done(text: str) -> bool:
    """Whether the words outside the quotes say what the person has done or is ("I built", "my experience", "I am a ...")."""
    own = _SPAN.sub(" ", text)
    return _OWN_CLAIM.search(own) is not None or any(_past(m.group(1)) for m in _I_VERB.finditer(own))


def _form_problem(text: str) -> str | None:
    """What a sentence of the paragraph may not be: a question, a greeting, a sign-off or more than one sentence (code adds
    the greeting and the ask; the paragraph is three sentences)."""
    outside = _SPAN.sub(" ", text)
    if "?" in outside:
        return "question"
    if _GREETING.search(outside):
        return "greeting"
    if _SIGN_OFF.search(outside):
        return "sign_off"
    if len(split_sentences(_SPAN.sub(" quoted ", text))) > 1:
        return "not_one_sentence"
    return None


def _grounded(item: _Item, corpus_text: str) -> str | None:
    """The checks every sentence passes: no praise word and no invented name, number or seniority."""
    if has_puffery(item.text, corpus_text):
        return "puffery"
    corpus = Corpus.of(corpus_text)
    if _unsupported(item.text, corpus) or _unsupported_words(item.text, corpus):
        return "unsupported"
    return None


def _intro_problem(item: _Item, src: _Source) -> str | None:
    if item.spans or any(mark in item.text for mark in _QUOTE_MARKS):
        return "quote_in_intro"
    if not _I_OPENING.match(item.text):
        return "not_first_person"
    if len(item.cited) > 1:
        return "cites_many"
    problem = _grounded(item, "\n".join([src.who, *(line.cite.text for line in item.cited)]))
    if problem is not None:
        return problem
    tied = _ties_to(item) if item.cited else bool(src.role) and fold(src.role) in fold(item.text)
    return None if tied else "not_tied_to_evidence"


def _fit_problem(item: _Item, src: _Source) -> str | None:
    if not item.spans:
        return "no_quote"
    if len(item.spans) > 1:
        return "many_quotes"
    holders = [m for m in src.matches if quoted(item.spans[0], m.folded)]
    if not holders:
        return "not_a_match"
    if not item.cited:
        return "no_evidence"
    if len(item.cited) != 1 or all(m.line.id != item.cited[0].id for m in holders):
        return "wrong_evidence"
    problem = _grounded(item, "\n".join([src.base, item.cited[0].cite.text]))
    if problem is not None:
        return problem
    return None if _ties_to(item) else "not_tied_to_evidence"


def _draw_problem(item: _Item, src: _Source) -> str | None:
    if not item.spans:
        return "no_quote"
    if _claims_done(item.text):
        return "claims_done"
    return _grounded(item, src.base)


def _problem(part: ReferralPart, item: _Item, src: _Source) -> str | None:
    """Why the sentence cannot be used, or ``None`` when every check passes."""
    if not item.text:
        return "line_missing"
    if len(item.text) > (MAX_FIT_CHARS if part == "fit" else MAX_LINE_CHARS):
        return "line_too_long"
    if sum(item.text.count(mark) for mark in _QUOTE_MARKS) % 2:
        return "quote_unbalanced"
    if any(not quoted(span, src.folded) for span in item.spans):
        return "quote_not_found"
    form = _form_problem(item.text)
    if form is not None:
        return form
    if part == "intro":
        return _intro_problem(item, src)
    if part == "fit":
        return _fit_problem(item, src)
    return _draw_problem(item, src)


# ───────────────────────── templates ─────────────────────────


def _article(title: str) -> str:
    return "an" if title[:1].upper() in "AEIO" else "a"


def _source_label(source: str) -> str:
    """``Resume`` -> ``your resume``: where a sentence comes from, in words the person reads."""
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


def _place_of(location: str) -> str:
    """``Bengaluru, India`` -> ``Bengaluru``: the city (or whatever comes first) of the person's location."""
    return tidy(location).split(",")[0].strip()


def _intro_template(job: AiJob, role: str, location: str) -> tuple[str, list[str]]:
    role = role.rstrip(".")
    if not role:
        return f"I'm applying for the {job.title} role at {job.employer}.", [POSTING]
    place = _place_of(location)
    where = f" in {place}" if place else ""
    return f"I'm {_article(role)} {role}{where}.", [PROFILE]


def _evidence_clause(cite: Cite, budget: int) -> str:
    """What the evidence line says, to follow "and": "I rebuilt ..." for a line that reads as an action, "my resume shows
    “...”" for any other, at most ``budget`` characters (``""`` when that is too little to say anything)."""
    text = tidy(cite.text)
    if _reads_as_action(text):
        clause = text.split(";")[0].strip().rstrip(".")
        room = budget - 2
        return f"I {clip_words(clause[:1].lower() + clause[1:], room)}" if room >= 20 else ""
    # a note-like line ("Recipe Genie: OpenAI via Supabase Edge Function, structured JSON") is pointed to as it stands,
    # never clipped mid-phrase: the name and its detail in brackets, or the name alone, or the first clause
    frame = "I can point to "
    room = budget - len(frame) - 1
    text = text.split(";")[0].strip().rstrip(".")
    name, colon, detail = text.partition(":")
    candidates = (
        [f"{name.strip()} ({detail.strip()})", name.strip()]
        if colon and detail.strip()
        else [text, text.split(",")[0].strip()]
    )
    for candidate in candidates:
        if 3 <= len(candidate) <= room:
            return f"{frame}{candidate}"
    return ""


def _closed(clause: str) -> str:
    return clause if clause.endswith("…") else f"{clause}."


def _fit_template(job: AiJob, held: _Match | None, line: Evidence | None) -> tuple[str, list[str]]:
    """``fit`` from facts: the ask the search matched and what the matched line says, else the nearest evidence line."""
    if held is not None:
        ask = _plain(tidy(held.ask.ask))
        words = ask.split()
        for count in range(min(len(words), FIT_QUOTE_WORDS), FIT_QUOTE_MIN_WORDS - 1, -1):
            quote = " ".join(words[:count]).rstrip(" ,;:-.")
            head = f"{job.employer} wants “{quote}”, and "
            clause = _evidence_clause(held.line.cite, MAX_FIT_CHARS - len(head) - 1)
            if clause:
                return f"{head}{_closed(clause)}", [POSTING, _source_label(held.line.cite.source)]
    if line is not None:
        clause = _evidence_clause(line.cite, MAX_FIT_CHARS - len("For this role, ") - 1)
        if clause:
            return f"For this role, {_closed(clause)}", [POSTING, _source_label(line.cite.source)]
    if job.level == "at":
        return "It is a role at my level.", [POSTING]
    if job.level == "step_up":
        return "It is a role I want to grow into.", [POSTING]
    return "It is a role I would like to be considered for.", [POSTING]


def _plain(quote: str) -> str:
    """``quote`` with no quote mark inside it (a mark inside would end the quotation early)."""
    return re.sub(r"[“”\"]", "'", quote)


def _draw_template(job: AiJob, quote: str | None) -> str:
    if quote:
        text = f"What draws me is “{_plain(quote)}”."
        if len(text) <= MAX_LINE_CHARS:
            return text
    return f"What draws me is the {job.title} role at {job.employer}."


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _draw_quote(item: _Item, src: _Source) -> str | None:
    """The first real posting quote of the model's draw sentence (cited or inside its text) short enough for a template."""
    return next(
        (
            q
            for q in [*item.quotes, *item.spans]
            if len(q.split()) <= TEMPLATE_QUOTE_MAX_WORDS and quoted(q, src.folded)
        ),
        None,
    )


def _chosen_match(item: _Item, src: _Source) -> _Match | None:
    """The match a failed ``fit`` is rebuilt from: the ask the model quoted, else the one whose line it cited, else the first."""
    for span in item.spans:
        held = next((m for m in src.matches if quoted(span, m.folded)), None)
        if held is not None:
            return held
    if item.cited:
        held = next((m for m in src.matches if m.line.id == item.cited[0].id), None)
        if held is not None:
            return held
    return src.matches[0] if src.matches else None


# ───────────────────────── the note ─────────────────────────


def _sources_of(part: ReferralPart, item: _Item) -> list[str]:
    labels = _dedupe([_source_label(line.cite.source) for line in item.cited])
    if part == "intro":
        return labels or [PROFILE]
    if part == "fit":
        return [POSTING, *labels]
    return [POSTING]


def check_referral(
    raw: Mapping[str, Any] | None,
    *,
    job: AiJob,
    profile: Profile,
    evidence: Mapping[str, Evidence],
    posting_text: str,
) -> ReferralChecked:
    """Check what the model returned (``None``: nothing) and build the three sentences, a template for each that fails."""
    out = ReferralChecked()
    data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    person = profile.person
    role = tidy(person.currentRole)
    location = tidy(person.location)
    base = "\n".join([posting_text, job.employer, job.title, job.place.label, role, location])
    src = _Source(
        job=job,
        posting_text=posting_text,
        folded=fold(posting_text),
        base=base,
        who="\n".join([role, location]),
        role=role,
        evidence=evidence,
        matches=referral_matches(job, evidence.values()),
    )
    for part in PARTS:
        item = _read_item(data.get(part), src, part, out)
        problem = _problem(part, item, src) if raw is not None else "model_failed"
        if problem is None:
            out.lines.append(ReferralLine(part=part, text=item.text, sources=_sources_of(part, item)))
            continue
        out.replaced[part] = problem
        if part == "intro":
            text, sources = _intro_template(job, role, location)
        elif part == "fit":
            held = _chosen_match(item, src)
            line = item.cited[0] if item.cited else next(iter(evidence.values()), None)
            text, sources = _fit_template(job, held, line)
        else:
            text, sources = _draw_template(job, _draw_quote(item, src)), [POSTING]
        out.lines.append(ReferralLine(part=part, text=text, sources=sources))
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
        matches=[ReferralMatch(m.ask.ask, m.line.id, m.ask.partly) for m in referral_matches(job, evidence)],
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
    "PARTS",
    "REFERRAL_SCHEMA",
    "ReferralChecked",
    "check_referral",
    "referral_matches",
    "write_referral",
]
