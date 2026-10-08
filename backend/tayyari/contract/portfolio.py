"""Pydantic mirror of shared/portfolio.ts: the Resume → Portfolio contract.

Same rules as `tayyari.contract.models`: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and
`tests/unit/contract/test_portfolio_parity.py` fails when the two drift. The kit constants
(`KIT_VERSION`, `BLOCK_VARIANTS`, `FAMILY_HUES`, `SLOT_LIMITS`) are checked against the TypeScript consts.

A block's ``variant`` is a plain string here and in TypeScript: a retired variant must not make old stored
documents unreadable. It is validated against `BLOCK_VARIANTS` when a spec is written (`validate_spec`).
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/portfolio.ts)

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, TypeAdapter

from tayyari.contract.models import _Model

# ───────────────────────── catalog ─────────────────────────

BlockKind = Literal[
    "hero", "graph", "proof", "about", "experience", "projects", "clips", "achievements", "education", "openTo", "contact"
]  # fmt: skip
Composition = Literal["signature", "narrative", "builder", "foundations", "ledger", "pivot"]
Family = Literal["iris", "glacier", "lagoon", "sage", "dune", "clay", "rose", "orchid"]
FieldShape = Literal["dawn", "horizon", "corner"]
TypePairing = Literal["studio", "editorial", "technical", "warm"]
Corners = Literal["square", "soft", "round"]
Density = Literal["airy", "compact"]
GraphVariant = Literal["peaks", "halo", "ledger", "stellar", "atlas", "spotlight", "focus"]
PortfolioSkin = Literal["standard", "press"]
SlotOrigin = Literal["model", "resume", "user"]
FactKind = Literal["experience", "project", "education", "achievement", "clip", "link", "contact", "quote"]
PublishState = Literal["draft", "published"]
ProgressStep = Literal["facts", "design", "copy", "check"]

KIT_VERSION: Final = 1

BLOCK_VARIANTS: Final[dict[str, tuple[str, ...]]] = {
    "hero": ("statement", "split", "centered", "index"),
    "graph": ("peaks", "halo", "ledger", "stellar", "atlas", "spotlight", "focus"),
    "proof": ("strip",),
    "about": ("lede", "prose", "facts"),
    "experience": ("timeline", "ledger", "thenNow"),
    "projects": ("featureList", "grid", "list"),
    "clips": ("list",),
    "achievements": ("numbers", "list"),
    "education": ("row", "compact"),
    "openTo": ("note",),
    "contact": ("signOff", "links"),
}

FAMILY_HUES: Final[dict[str, int]] = {
    "iris": 275,
    "glacier": 245,
    "lagoon": 200,
    "sage": 155,
    "dune": 85,
    "clay": 45,
    "rose": 10,
    "orchid": 320,
}

SLOT_LIMITS: Final[dict[str, int]] = {
    "tagline": 90,
    "aboutLede": 160,
    "aboutBody": 420,
    "pressHeadline": 80,
    "pressDeck": 180,
    "exp": 300,
    "proj": 200,
}
"""Character limits per slot id; per-fact slots (``exp:<factId>``, ``proj:<factId>``) use their prefix."""

FIXED_SLOTS: Final[tuple[str, ...]] = ("tagline", "aboutLede", "aboutBody", "pressHeadline", "pressDeck")
FACT_SLOT_PREFIXES: Final[dict[str, str]] = {"exp": "experience", "proj": "project"}
"""Per-fact slot prefix → the fact kind it belongs to."""

GRAPH_PUBLIC_MIN_SKILL: Final = 45
"""Skills scoring below this are never sent to a viewer of the public page."""


def slot_limit(slot_id: str) -> int | None:
    """The character limit of a slot id, or None when the id is not a known slot."""
    if slot_id in SLOT_LIMITS and slot_id in FIXED_SLOTS:
        return SLOT_LIMITS[slot_id]
    prefix, sep, rest = slot_id.partition(":")
    if sep and rest and prefix in FACT_SLOT_PREFIXES:
        return SLOT_LIMITS[prefix]
    return None


# ───────────────────────── presentation ─────────────────────────


class PortfolioStyle(_Model):
    family: Family
    field: FieldShape
    type: TypePairing
    corners: Corners
    density: Density


class BlockSpec(_Model):
    id: str = Field(max_length=40)
    kind: BlockKind
    variant: str = Field(max_length=40)
    visible: bool
    public: bool


class GraphSpec(_Model):
    hiddenDomainIds: list[str] = Field(max_length=16)
    stats: bool


class PortfolioPrivacy(_Model):
    email: bool
    phone: bool
    grades: bool


class DesignReason(_Model):
    dimension: str
    choice: str
    because: str


class PortfolioSpec(_Model):
    composition: Composition
    style: PortfolioStyle
    blocks: list[BlockSpec] = Field(max_length=24)
    graph: GraphSpec
    privacy: PortfolioPrivacy
    reasons: list[DesignReason]


# ───────────────────────── content ─────────────────────────


class SourceRef(_Model):
    sourceId: str
    quote: str


class Fact(_Model):
    id: str
    kind: FactKind
    title: str
    org: str | None = None
    location: str | None = None
    start: str | None = None
    end: str | None = None
    current: bool | None = None
    url: str | None = None
    detail: str | None = None
    grade: str | None = None
    highlights: list[str]
    tags: list[str]
    sourceRefs: list[SourceRef]


class CopySlot(_Model):
    id: str
    text: str
    origin: SlotOrigin
    factIds: list[str]


class ProofStat(_Model):
    value: str
    label: str
    sourceId: str


class PracticeStats(_Model):
    sessions: int
    mockInterviews: int
    domainsPractised: int
    asOf: str


class PortfolioPerson(_Model):
    name: str
    headline: str
    role: str
    location: str | None = None
    yearsExperience: float | None = None
    monogram: str


class PortfolioContent(_Model):
    person: PortfolioPerson
    facts: list[Fact]
    slots: list[CopySlot]
    stats: list[ProofStat]
    practice: PracticeStats | None = None
    openTo: list[str]


# ───────────────────────── graph snapshot ─────────────────────────


class GraphSkill(_Model):
    id: str
    name: str
    score: int
    evidence: str | None = None


class GraphDomain(_Model):
    id: str
    name: str
    short: str
    score: int
    skills: list[GraphSkill]


class GraphMastery(_Model):
    score: int
    role: str


class GraphSnapshot(_Model):
    asOf: str
    domains: list[GraphDomain]
    skillCount: int
    mastery: GraphMastery | None = None
    sources: list[str]


# ───────────────────────── portfolio ─────────────────────────


class PublishInfo(_Model):
    state: PublishState
    publishedAt: str | None = None
    defaultView: PortfolioSkin


class Portfolio(_Model):
    profileId: str
    publicId: str
    version: int
    kitVersion: int
    createdAt: str
    updatedAt: str
    lookIndex: int
    looks: list[PortfolioSpec]
    spec: PortfolioSpec
    content: PortfolioContent
    graph: GraphSnapshot
    publish: PublishInfo
    graphOutdated: bool | None = None


class PublicPortfolio(_Model):
    publicId: str
    kitVersion: int
    spec: PortfolioSpec
    content: PortfolioContent
    graph: GraphSnapshot
    publishedAt: str | None = None
    defaultView: PortfolioSkin


# ───────────────────────── requests ─────────────────────────

SLOT_EDIT_MAX_CHARS: Final = max(SLOT_LIMITS.values())
PROFILE_ID_MAX_CHARS: Final = 96


class PortfolioRequest(_Model):
    profileId: str = Field(min_length=1, max_length=PROFILE_ID_MAX_CHARS)
    regenerate: bool | None = None


class SlotEdit(_Model):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(max_length=SLOT_EDIT_MAX_CHARS)


class PortfolioPatch(_Model):
    lookIndex: int | None = Field(default=None, ge=0, le=2)
    style: PortfolioStyle | None = None
    blocks: list[BlockSpec] | None = Field(default=None, max_length=24)
    graph: GraphSpec | None = None
    privacy: PortfolioPrivacy | None = None
    slots: list[SlotEdit] | None = Field(default=None, max_length=40)
    resetSlots: list[str] | None = Field(default=None, max_length=40)
    defaultView: PortfolioSkin | None = None
    refreshGraph: bool | None = None


# ───────────────────────── stream events ─────────────────────────


class PortfolioProgressEvent(_Model):
    type: Literal["progress"] = "progress"
    step: ProgressStep
    label: str


class PortfolioDraftEvent(_Model):
    type: Literal["draft"] = "draft"
    portfolio: Portfolio


class PortfolioDoneEvent(_Model):
    type: Literal["done"] = "done"
    portfolio: Portfolio


class PortfolioErrorEvent(_Model):
    type: Literal["error"] = "error"
    message: str


PortfolioEvent = Annotated[
    PortfolioProgressEvent | PortfolioDraftEvent | PortfolioDoneEvent | PortfolioErrorEvent,
    Field(discriminator="type"),
]
PortfolioEventAdapter: TypeAdapter[PortfolioEvent] = TypeAdapter(PortfolioEvent)

PORTFOLIO_TERMINAL_TYPES: Final = frozenset({"done", "error"})


# ───────────────────────── spec validation ─────────────────────────

GRAPH_MAX_INDEX: Final = 2
"""The Skill Graph block sits within the first three blocks (in or right after the hero, or after a lede)."""


def spec_problems(spec: PortfolioSpec) -> list[str]:
    """Why a spec is not a valid page under the kit (empty list when it is).

    Rules: unique block ids, every variant in `BLOCK_VARIANTS[kind]`, exactly one graph block, placed
    within the first three blocks, a hero block first.
    """
    problems: list[str] = []
    ids = [b.id for b in spec.blocks]
    if len(ids) != len(set(ids)):
        problems.append("duplicate block ids")
    for b in spec.blocks:
        if b.variant not in BLOCK_VARIANTS.get(b.kind, ()):
            problems.append(f"unknown variant {b.variant!r} for {b.kind}")
    graphs = [i for i, b in enumerate(spec.blocks) if b.kind == "graph"]
    if len(graphs) != 1:
        problems.append("a portfolio has exactly one Skill Graph block")
    elif graphs[0] > GRAPH_MAX_INDEX:
        problems.append("the Skill Graph block must be within the first three blocks")
    if not spec.blocks or spec.blocks[0].kind != "hero":
        problems.append("the first block is the hero")
    return problems
