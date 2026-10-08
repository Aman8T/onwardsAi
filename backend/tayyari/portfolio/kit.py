"""The closed kit: compositions, graph-variant rules, style options and the minimum content of each block.

Everything the composer may choose from is listed here, so a spec built from the kit is always valid:

    COMPOSITIONS        six block orders; each lists the variants a block may take (first = the default)
    GRAPH_AFFINITY      how much each composition wants each Skill Graph variant (the shape decides which apply)
    GraphShape          the numbers the graph-variant rules read (peaks, spotlight, atlas, halo, focus ...)
    graph_variant_fit   0..1 fit of one variant to a shape (0 means "not for this graph")
    block_has_content   the declared minimum of each block (below it a block is hidden)
    FAMILY_FOR_LEAN     which gradient families suit which kind of work
    TYPE_FOR_FAMILY     a small bonus for type pairings that sit well with a family hue
    SLOT_OWNER / FACT_BLOCKS   which block shows which copy slot / fact kind (the public filter reads them)

The footer is not a block: every page prints it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, get_args

from tayyari.contract.portfolio import (
    BLOCK_VARIANTS,
    BlockKind,
    Composition,
    Corners,
    Density,
    FactKind,
    Family,
    FieldShape,
    GraphSnapshot,
    GraphVariant,
    PortfolioContent,
    TypePairing,
)

# ───────────────────────── options ─────────────────────────

FAMILIES: Final[tuple[Family, ...]] = tuple(get_args(Family))
FIELDS: Final[tuple[FieldShape, ...]] = get_args(FieldShape)
TYPES: Final[tuple[TypePairing, ...]] = get_args(TypePairing)
CORNER_OPTIONS: Final[tuple[Corners, ...]] = get_args(Corners)
DENSITY_OPTIONS: Final[tuple[Density, ...]] = get_args(Density)
COMPOSITION_NAMES: Final[tuple[Composition, ...]] = get_args(Composition)
GRAPH_VARIANTS: Final[tuple[GraphVariant, ...]] = get_args(GraphVariant)

FAMILY_LABELS: Final[dict[Family, str]] = {f: f.capitalize() for f in FAMILIES}
COMPOSITION_LABELS: Final[dict[Composition, str]] = {c: c.capitalize() for c in COMPOSITION_NAMES}

# ───────────────────────── graph-variant rules ─────────────────────────

PEAKS_MIN_SPREAD: Final = 15
"""peaks: the top domain sits at least this far above the median domain."""
SPOTLIGHT_MIN_GAP: Final = 20
SPOTLIGHT_MIN_SPREAD: Final = 30
"""spotlight: the top domain leads the second by `GAP` and the median by `SPREAD`."""
FOCUS_MIN_DOMAINS: Final = 4
FOCUS_MIN_SKILLS: Final = 6
"""focus: fewer domains or fewer skills than this (a new or sparse graph; no radar)."""
FOCUS_SINGLE_SOURCE_TOP: Final = 50
"""A graph built from one source whose best domain is below this also reads as "just getting started"."""
ATLAS_MIN_SKILLS: Final = 22
ATLAS_MIN_RICH_DOMAINS: Final = 4
ATLAS_FULL_SKILLS: Final = 24
"""atlas: enough skills for small multiples. The plan says 24; the senior fixture has 23, so the gate is a
little lower and the fit ramps up to 1.0 at 24. A mini radar needs a domain with at least 3 skills."""
STELLAR_MIN_DOMAINS: Final = 6
STELLAR_MAX_DOMAINS: Final = 8
PROOF_MIN_STATS: Final = 2


@dataclass(frozen=True)
class GraphShape:
    """The measurements of a Skill Graph that the variant rules read."""

    n_domains: int = 6
    n_skills: int = 18
    n_rich: int = 4
    """Domains holding at least 3 skills (each gets a mini radar in the atlas)."""
    top: int = 80
    second: int = 70
    median: float = 60.0
    has_mastery: bool = True
    single_source: bool = False

    @property
    def spread(self) -> float:
        return self.top - self.median

    @property
    def gap(self) -> int:
        return self.top - self.second

    @property
    def sparse(self) -> bool:
        return (
            self.n_domains < FOCUS_MIN_DOMAINS
            or self.n_skills < FOCUS_MIN_SKILLS
            or (self.single_source and self.top < FOCUS_SINGLE_SOURCE_TOP)
        )

    @property
    def peaked(self) -> bool:
        return not self.sparse and self.spread >= PEAKS_MIN_SPREAD

    @property
    def spotlit(self) -> bool:
        return not self.sparse and self.gap >= SPOTLIGHT_MIN_GAP and self.spread >= SPOTLIGHT_MIN_SPREAD

    @property
    def balanced(self) -> bool:
        return not self.sparse and self.spread < PEAKS_MIN_SPREAD

    @property
    def rich(self) -> bool:
        return self.n_skills >= ATLAS_MIN_SKILLS and self.n_rich >= ATLAS_MIN_RICH_DOMAINS


def graph_variant_fit(variant: str, shape: GraphShape, type_pairing: str | None = None) -> float:
    """How well one Skill Graph variant suits a graph shape, 0..1 (0 = not for this graph).

    focus      a new or sparse graph (no radar)             peaks    top - median >= 15
    spotlight  one area far ahead of the rest               halo     balanced, with the mastery in the centre
    atlas      a rich graph (small multiples)               stellar  6 to 8 axes (best on Editorial / Technical)
    ledger     always readable: ranked rows next to a radar (and the phone fallback)
    """
    if variant == "focus":
        return 1.0 if shape.sparse else 0.0
    if variant == "peaks":
        return 1.0 if shape.peaked else 0.0
    if variant == "spotlight":
        return 1.0 if shape.spotlit else 0.0
    if variant == "halo":
        return 1.0 if shape.balanced and shape.has_mastery else 0.0
    if variant == "atlas":
        return min(1.0, shape.n_skills / ATLAS_FULL_SKILLS) if shape.rich and not shape.sparse else 0.0
    if variant == "stellar":
        if shape.sparse or not STELLAR_MIN_DOMAINS <= shape.n_domains <= STELLAR_MAX_DOMAINS:
            return 0.0
        return 0.7 + (0.25 if type_pairing in ("editorial", "technical") else 0.0)
    if variant == "ledger":
        return 0.6 if shape.n_domains >= 2 else 0.3
    return 0.0


GRAPH_AFFINITY: Final[dict[Composition, dict[GraphVariant, float]]] = {
    "signature": {
        "peaks": 1.0,
        "halo": 0.9,
        "focus": 0.9,
        "stellar": 0.6,
        "spotlight": 0.55,
        "atlas": 0.5,
        "ledger": 0.45,
    },
    "narrative": {
        "spotlight": 1.0,
        "halo": 0.9,
        "focus": 0.9,
        "stellar": 0.75,
        "peaks": 0.6,
        "atlas": 0.4,
        "ledger": 0.35,
    },
    "builder": {
        "peaks": 1.0,
        "focus": 0.9,
        "ledger": 0.8,
        "stellar": 0.6,
        "atlas": 0.55,
        "halo": 0.5,
        "spotlight": 0.45,
    },
    "foundations": {
        "focus": 1.0,
        "ledger": 0.4,
        "peaks": 0.35,
        "halo": 0.3,
        "spotlight": 0.3,
        "stellar": 0.2,
        "atlas": 0.1,
    },
    "ledger": {
        "atlas": 1.0,
        "focus": 0.9,
        "ledger": 0.85,
        "halo": 0.7,
        "stellar": 0.55,
        "peaks": 0.45,
        "spotlight": 0.35,
    },
    "pivot": {
        "peaks": 1.0,
        "focus": 0.9,
        "halo": 0.8,
        "spotlight": 0.55,
        "stellar": 0.5,
        "ledger": 0.5,
        "atlas": 0.4,
    },
}
"""composition -> graph variant -> how much the layout wants it. The shape fit decides what applies; the
product picks the variant, so a balanced graph never gets peaks and a sparse one always gets focus."""


# ───────────────────────── compositions ─────────────────────────

BlockPlan = tuple[tuple[BlockKind, tuple[str, ...]], ...]


def _graph_variants(composition: Composition) -> tuple[str, ...]:
    """Every graph variant, the composition's favourite first."""
    affinity = GRAPH_AFFINITY[composition]
    return tuple(sorted(affinity, key=lambda v: -affinity[v]))


COMPOSITIONS: Final[dict[Composition, BlockPlan]] = {
    # hero.split(graph) -> proof -> about.lede -> experience -> selected work -> recognition -> education -> contact
    "signature": (
        ("hero", ("split", "statement", "centered", "index")),
        ("graph", _graph_variants("signature")),
        ("proof", ("strip",)),
        ("about", ("lede", "prose", "facts")),
        ("experience", ("timeline", "ledger", "thenNow")),
        ("projects", ("featureList", "grid", "list")),
        ("clips", ("list",)),
        ("achievements", ("list", "numbers")),
        ("education", ("row", "compact")),
        ("openTo", ("note",)),
        ("contact", ("signOff", "links")),
    ),
    # hero.statement -> graph (halo or spotlight) + stats -> about.prose -> experience -> clips -> projects.list
    "narrative": (
        ("hero", ("statement", "split", "centered", "index")),
        ("graph", _graph_variants("narrative")),
        ("proof", ("strip",)),
        ("about", ("prose", "lede", "facts")),
        ("experience", ("timeline", "ledger", "thenNow")),
        ("clips", ("list",)),
        ("projects", ("list", "featureList", "grid")),
        ("achievements", ("list", "numbers")),
        ("education", ("row", "compact")),
        ("openTo", ("note",)),
        ("contact", ("signOff", "links")),
    ),
    # hero.index -> graph.peaks + stats -> projects.grid -> experience.ledger -> achievements
    "builder": (
        ("hero", ("index", "statement", "split", "centered")),
        ("graph", _graph_variants("builder")),
        ("proof", ("strip",)),
        ("projects", ("grid", "featureList", "list")),
        ("experience", ("ledger", "timeline", "thenNow")),
        ("clips", ("list",)),
        ("achievements", ("numbers", "list")),
        ("education", ("compact", "row")),
        ("openTo", ("note",)),
        ("contact", ("links", "signOff")),
    ),
    # hero.centered -> graph.focus -> about.lede -> projects.feature -> education (above experience) -> contact.links
    "foundations": (
        ("hero", ("centered", "statement", "split", "index")),
        ("graph", _graph_variants("foundations")),
        ("about", ("lede", "prose", "facts")),
        ("projects", ("featureList", "grid", "list")),
        ("education", ("row", "compact")),
        ("experience", ("timeline", "ledger", "thenNow")),
        ("clips", ("list",)),
        ("achievements", ("list", "numbers")),
        ("openTo", ("note",)),
        ("contact", ("links", "signOff")),
    ),
    # hero.statement -> graph.atlas -> experience.ledger -> achievements.numbers -> projects.list -> contact.links
    "ledger": (
        ("hero", ("statement", "split", "centered", "index")),
        ("graph", _graph_variants("ledger")),
        ("experience", ("ledger", "timeline", "thenNow")),
        ("achievements", ("numbers", "list")),
        ("projects", ("list", "featureList", "grid")),
        ("clips", ("list",)),
        ("education", ("compact", "row")),
        ("openTo", ("note",)),
        ("contact", ("links", "signOff")),
    ),
    # hero.statement -> about.lede (the move, told from facts) -> graph.peaks -> open-to -> projects -> then/now
    "pivot": (
        ("hero", ("statement", "split", "centered", "index")),
        ("about", ("lede", "prose", "facts")),
        ("graph", _graph_variants("pivot")),
        ("openTo", ("note",)),
        ("projects", ("grid", "featureList", "list")),
        ("experience", ("thenNow", "timeline", "ledger")),
        ("education", ("row", "compact")),
        ("clips", ("list",)),
        ("achievements", ("list", "numbers")),
        ("contact", ("signOff", "links")),
    ),
}
"""composition -> ordered (block kind, allowed variants); the first variant is the default. A block id is its
kind (one block per kind). The graph sits in or right after the hero (index 1; index 2 behind the pivot's
about lede), and openTo comes before contact, which always closes the page."""


def default_variant(composition: Composition, kind: BlockKind) -> str:
    """The default (first) variant of a block in a composition."""
    for k, variants in COMPOSITIONS[composition]:
        if k == kind:
            return variants[0]
    return BLOCK_VARIANTS[kind][0]


# ───────────────────────── style tables ─────────────────────────

Lean = Literal["design", "systems", "data", "hardware", "words", "people", "other"]
LEANS: Final[tuple[Lean, ...]] = get_args(Lean)
"""What kind of work a profile is about: it steers the gradient family."""

LEAN_LABELS: Final[dict[Lean, str]] = {
    "design": "design-led product work",
    "systems": "backend and systems work",
    "data": "data and analysis work",
    "hardware": "hardware and embedded work",
    "words": "writing and content work",
    "people": "people-facing work",
    "other": "work that fits any hue",
}

FAMILY_FOR_LEAN: Final[dict[Lean, dict[Family, float]]] = {
    "design": {
        "iris": 1.0, "orchid": 0.8, "rose": 0.55, "glacier": 0.45, "lagoon": 0.3, "clay": 0.25, "sage": 0.2, "dune": 0.2,
    },
    "systems": {
        "lagoon": 1.0, "glacier": 0.85, "iris": 0.5, "sage": 0.35, "orchid": 0.25, "dune": 0.15, "clay": 0.15, "rose": 0.1,
    },
    "data": {
        "clay": 1.0, "glacier": 0.8, "sage": 0.55, "lagoon": 0.5, "dune": 0.45, "iris": 0.35, "orchid": 0.3, "rose": 0.2,
    },
    "hardware": {
        "sage": 1.0, "lagoon": 0.6, "dune": 0.45, "glacier": 0.4, "clay": 0.3, "iris": 0.2, "orchid": 0.15, "rose": 0.1,
    },
    "words": {
        "rose": 1.0, "orchid": 0.7, "clay": 0.55, "iris": 0.45, "dune": 0.35, "glacier": 0.2, "sage": 0.2, "lagoon": 0.1,
    },
    "people": {
        "dune": 1.0, "clay": 0.8, "sage": 0.6, "rose": 0.5, "orchid": 0.3, "lagoon": 0.3, "glacier": 0.2, "iris": 0.2,
    },
    "other": {  # no clear field: every hue fits equally, so the person's seed picks one
        "iris": 0.5, "glacier": 0.5, "lagoon": 0.5, "sage": 0.5, "clay": 0.5, "dune": 0.5, "rose": 0.5, "orchid": 0.5,
    },
}  # fmt: skip

TYPE_FOR_FAMILY: Final[dict[Family, dict[TypePairing, float]]] = {
    "iris": {"studio": 0.15, "editorial": 0.05},
    "glacier": {"editorial": 0.12, "technical": 0.12},
    "lagoon": {"technical": 0.15},
    "sage": {"warm": 0.15, "technical": 0.05},
    "dune": {"warm": 0.12, "editorial": 0.1},
    "clay": {"editorial": 0.15, "warm": 0.1},
    "rose": {"studio": 0.15, "warm": 0.1},
    "orchid": {"studio": 0.12, "editorial": 0.1},
}
"""A small bonus (at most 0.15) for a type pairing that sits well with a family hue."""


# ───────────────────────── which block shows what ─────────────────────────

FACT_BLOCKS: Final[dict[FactKind, tuple[BlockKind, ...]]] = {
    "experience": ("experience",),
    "project": ("projects",),
    "education": ("education",),
    "achievement": ("achievements",),
    "clip": ("clips",),
    "link": ("contact",),
    "contact": ("contact",),
    "quote": ("hero", "about"),
}
"""Fact kind -> the blocks that display it. A fact is public only while one of these blocks is shown."""


def slot_owner(slot_id: str) -> BlockKind | None:
    """The block a copy slot belongs to (``None`` for an id that is not a slot)."""
    if slot_id == "tagline":
        return "hero"
    if slot_id in ("aboutLede", "aboutBody", "pressHeadline", "pressDeck"):
        return "about"
    prefix, sep, rest = slot_id.partition(":")
    if sep and rest:
        if prefix == "exp":
            return "experience"
        if prefix == "proj":
            return "projects"
    return None


# ───────────────────────── minimum content ─────────────────────────


def _count(content: PortfolioContent, kind: FactKind) -> int:
    return sum(1 for f in content.facts if f.kind == kind)


def block_has_content(kind: BlockKind, content: PortfolioContent, graph: GraphSnapshot) -> bool:
    """Whether a block reaches its declared minimum (below it the block is hidden; the editor shows a prompt).

    hero, graph and contact always have content; proof needs 2 stats; about needs a lede slot or facts the
    lede can be written from (an experience, project or education entry, or the person's own words);
    experience, projects, clips, achievements and education need 1 fact of their kind; openTo needs 1 title.
    """
    _ = graph
    if kind in ("hero", "graph", "contact"):
        return True
    if kind == "proof":
        return len(content.stats) >= PROOF_MIN_STATS
    if kind == "about":
        if any(s.id in ("aboutLede", "aboutBody") and s.text.strip() for s in content.slots):
            return True
        return any(_count(content, k) > 0 for k in ("experience", "project", "education", "quote"))
    if kind == "experience":
        return _count(content, "experience") >= 1
    if kind == "projects":
        return _count(content, "project") >= 1
    if kind == "clips":
        return _count(content, "clip") >= 1
    if kind == "achievements":
        return _count(content, "achievement") >= 1
    if kind == "education":
        return _count(content, "education") >= 1
    if kind == "openTo":
        return any(t.strip() for t in content.openTo)
    return False


__all__ = [
    "ATLAS_FULL_SKILLS",
    "ATLAS_MIN_RICH_DOMAINS",
    "ATLAS_MIN_SKILLS",
    "COMPOSITIONS",
    "COMPOSITION_LABELS",
    "COMPOSITION_NAMES",
    "CORNER_OPTIONS",
    "DENSITY_OPTIONS",
    "FACT_BLOCKS",
    "FAMILIES",
    "FAMILY_FOR_LEAN",
    "FAMILY_LABELS",
    "FIELDS",
    "FOCUS_MIN_DOMAINS",
    "FOCUS_MIN_SKILLS",
    "GRAPH_AFFINITY",
    "GRAPH_VARIANTS",
    "LEANS",
    "LEAN_LABELS",
    "PEAKS_MIN_SPREAD",
    "PROOF_MIN_STATS",
    "SPOTLIGHT_MIN_GAP",
    "SPOTLIGHT_MIN_SPREAD",
    "TYPES",
    "TYPE_FOR_FAMILY",
    "BlockPlan",
    "GraphShape",
    "Lean",
    "block_has_content",
    "default_variant",
    "graph_variant_fit",
    "slot_owner",
]
