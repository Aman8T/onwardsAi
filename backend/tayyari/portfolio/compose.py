"""The deterministic designer: features and a seed in, up to three valid, distinct `PortfolioSpec`s out.

Nothing here calls a model. Every choice (composition, graph variant, gradient family, type pairing, field
shape, corners, density) is scored from the profile's `Features` by fit rules; a seeded `random.Random` only
breaks ties between equal scores. Specs are built from the closed kit (`kit.COMPOSITIONS`), so they pass
`spec_problems` by construction: hero first, exactly one Skill Graph block within the first three, every
variant from the catalog. Each spec carries `reasons` written by the rules that fired.

Look 1 is the best fit. Looks 2 and 3 take the next compositions and are pushed away from what the earlier
looks used (a hue, a type pairing, a graph variant already shown costs points), so the three always differ.
"""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Final

from tayyari.contract.portfolio import (
    BlockKind,
    BlockSpec,
    Composition,
    Corners,
    Density,
    DesignReason,
    Family,
    FieldShape,
    GraphSnapshot,
    GraphSpec,
    GraphVariant,
    PortfolioContent,
    PortfolioPrivacy,
    PortfolioSpec,
    PortfolioStyle,
    TypePairing,
)

from .features import Features
from .kit import (
    COMPOSITION_LABELS,
    COMPOSITION_NAMES,
    COMPOSITIONS,
    CORNER_OPTIONS,
    DENSITY_OPTIONS,
    FAMILIES,
    FAMILY_FOR_LEAN,
    FAMILY_LABELS,
    FIELDS,
    GRAPH_AFFINITY,
    GRAPH_VARIANTS,
    LEAN_LABELS,
    PROOF_MIN_STATS,
    TYPE_FOR_FAMILY,
    TYPES,
    GraphShape,
    block_has_content,
    graph_variant_fit,
)

LOOK_COUNT: Final = 3
FAMILY_REPEAT_PENALTY: Final = 0.45
STYLE_REPEAT_PENALTY: Final = 0.3
GRAPH_REPEAT_PENALTY: Final = 0.2
EXPERIENCE_LEDGER_MIN: Final = 8
PROJECT_LIST_MIN: Final = 8
COMPACT_ENTRIES: Final = 16


# ───────────────────────── seeds ─────────────────────────


def person_seed(profile_id: str, look_round: int = 0) -> int:
    """A stable 64-bit seed for one person and one round of "try another look" (never Python's salted hash)."""
    digest = hashlib.sha256(f"{profile_id}:{look_round}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


# ───────────────────────── scoring plumbing ─────────────────────────


class _Score[T]:
    """One option's score and the reasons (weight, text) behind it."""

    __slots__ = ("_base_note", "notes", "score", "value")

    def __init__(self, value: T, base: float = 0.0, base_note: str = "") -> None:
        self.value = value
        self.score = base
        self.notes: list[tuple[float, str]] = []
        self._base_note = base_note

    def add(self, condition: bool, weight: float, note: str | None = None) -> None:
        if not condition:
            return
        self.score += weight
        if note and weight > 0:
            self.notes.append((weight, note))

    def because(self, limit: int = 2) -> str:
        """The strongest reasons as one phrase, e.g. "4 roles to lay out and public work to show"."""
        top = [text for _, text in sorted(self.notes, key=lambda n: -n[0])[:limit]]
        return _phrase(top or [self._base_note])


def _phrase(parts: Sequence[str]) -> str:
    text = " and ".join(p for p in parts if p)
    return text[:1].upper() + text[1:]


def _pick[T](
    options: Sequence[_Score[T]],
    rng: random.Random,
    penalty: Mapping[str, float] | None = None,
    banned: Collection[str] = (),
) -> _Score[T]:
    """The best-scoring option; equal scores are broken by the seeded generator (drawn for every option, in
    list order, so the sequence never depends on what was banned)."""
    jitter = [rng.random() for _ in options]
    pen = penalty or {}
    pool = [(i, o) for i, o in enumerate(options) if str(o.value) not in banned]
    best = min(pool, key=lambda io: (-round(io[1].score - pen.get(str(io[1].value), 0.0), 6), jitter[io[0]]))
    return best[1]


@dataclass
class _History:
    """What the earlier looks of one `compose` call already used."""

    families: Counter[str] = field(default_factory=Counter)
    types: Counter[str] = field(default_factory=Counter)
    fields: Counter[str] = field(default_factory=Counter)
    corners: Counter[str] = field(default_factory=Counter)
    densities: Counter[str] = field(default_factory=Counter)
    graphs: Counter[str] = field(default_factory=Counter)
    pairs: set[tuple[str, str]] = field(default_factory=set)


def _penalty(counter: Counter[str], weight: float) -> dict[str, float]:
    return {value: weight * count for value, count in counter.items()}


# ───────────────────────── composition ─────────────────────────


def _composition_options(f: Features) -> list[_Score[Composition]]:
    sh = f.shape
    n_exp, n_proj = f.n_experience, f.n_projects
    options: list[_Score[Composition]] = []

    s: _Score[Composition] = _Score("signature", 0.45, "a balanced page that suits most profiles")
    s.add(n_exp >= 2, 0.20, f"{n_exp} roles to lay out")
    s.add(f.has_public_proof, 0.15, "public work to show")
    s.add(sh.peaked and not sh.spotlit, 0.10, "a graph with clear peaks")
    s.add(f.stage in ("early", "mid"), 0.10, "a working record to show")
    s.add(f.stage == "student", -0.30)
    s.add(f.stage == "switcher", -0.25)
    s.add(sh.spotlit, -0.15)
    s.add(sh.sparse, -0.10)
    s.add(f.writing_led, -0.10)
    options.append(s)

    s = _Score("narrative", 0.15, "a story-first page")
    s.add(
        f.writing_led,
        0.75,
        f"{f.n_clips} writing samples carry the story" if f.n_clips else "writing-led work",
    )
    s.add(sh.spotlit, 0.30, "one standout area, framed calmly")
    options.append(s)

    s = _Score("builder", 0.10, "a project-first page")
    s.add(f.project_led, 0.55, f"{n_proj} projects are the main proof")
    s.add(f.has_public_proof, 0.15, "public work to show")
    s.add(n_proj >= 3, 0.10, f"{n_proj} projects to feature")
    s.add(f.stage == "senior", -0.20)
    s.add(f.stage == "student", -0.10)
    options.append(s)

    s = _Score("foundations", 0.0, "a page that leads with learning")
    s.add(f.stage == "student", 0.90, "early in the journey, so education and projects lead")
    s.add(sh.sparse, 0.30, "a new graph that reads best as areas of focus")
    s.add(n_exp == 0, 0.15, "no work history to lead with")
    options.append(s)

    s = _Score("ledger", 0.05, "a record that reads as rows")
    s.add(f.stage == "senior", 0.70, "a senior record that reads best as a ledger")
    s.add(n_exp >= 3, 0.20, f"{n_exp} roles in the record")
    s.add(sh.rich, 0.15, "a rich graph for small multiples")
    s.add(f.measured, 0.10, "measured results to list")
    options.append(s)

    s = _Score("pivot", 0.0, "a page that tells a change of field")
    s.add(f.stage == "switcher", 1.00, "a change of field to tell from the facts")
    s.add(n_exp >= 2, 0.10, "earlier work to set against the new role")
    options.append(s)

    # the order of COMPOSITION_NAMES keeps tie-breaking independent of how the rules above are written
    order = {name: i for i, name in enumerate(COMPOSITION_NAMES)}
    return sorted(options, key=lambda o: order[o.value])


# ───────────────────────── style dimensions ─────────────────────────


def _family_options(f: Features) -> list[_Score[Family]]:
    prefs = FAMILY_FOR_LEAN[f.lean]
    label = LEAN_LABELS[f.lean]
    return [_Score(fam, prefs[fam], f"suits {label}") for fam in FAMILIES]


def _type_options(f: Features, composition: Composition, family: Family) -> list[_Score[TypePairing]]:
    lean, stage = f.lean, f.stage
    story = composition in ("signature", "narrative")
    bonus = TYPE_FOR_FAMILY[family]

    studio: _Score[TypePairing] = _Score("studio", 0.40, "expressive serif headlines are a safe default")
    studio.add(lean in ("design", "words"), 0.30, "expressive serif headlines suit design and written work")
    studio.add(story, 0.25, "a page built around the graph and the story")

    editorial: _Score[TypePairing] = _Score("editorial", 0.15, "a reading-first serif")
    editorial.add(stage == "switcher", 0.50, "a reading-first serif to tell a change of field")
    editorial.add(composition == "pivot", 0.30, "the pivot layout is a story to read")
    editorial.add(lean == "data", 0.25, "analysis told in plain words")
    editorial.add(f.writing_led, 0.20, "writing is the work")

    technical: _Score[TypePairing] = _Score("technical", 0.05, "mono details for precise work")
    technical.add(lean == "systems", 0.50, "mono details suit backend and systems work")
    technical.add(stage == "senior", 0.40, "a senior record reads well in tight type")
    technical.add(composition == "ledger", 0.20, "the ledger layout is built from numbers")
    technical.add(composition == "builder", 0.20, "a build log reads well in mono")
    technical.add(lean == "hardware", 0.15, "mono details suit hardware work")

    warm: _Score[TypePairing] = _Score("warm", 0.10, "a friendly rounded face")
    warm.add(stage == "student", 0.60, "a friendly rounded face for an early-career page")
    warm.add(stage == "early", 0.25, "a friendly rounded face for a first few years")
    warm.add(lean == "people", 0.30, "people-facing work reads warmly")
    warm.add(composition == "foundations", 0.20, "the foundations layout is a friendly start")

    options = [studio, editorial, technical, warm]
    for o in options:
        o.score += bonus.get(o.value, 0.0)
    order = {name: i for i, name in enumerate(TYPES)}
    return sorted(options, key=lambda o: order[o.value])


def _field_options(
    f: Features, composition: Composition, type_pairing: TypePairing
) -> list[_Score[FieldShape]]:
    dawn: _Score[FieldShape] = _Score("dawn", 0.10, "two soft lobes give the page the most atmosphere")
    dawn.add(composition == "signature", 0.50, "the signature layout carries the fullest backdrop")
    dawn.add(type_pairing == "studio", 0.30, "serif headlines sit well on a fuller backdrop")

    horizon: _Score[FieldShape] = _Score("horizon", 0.0, "a calm band")
    horizon.add(
        composition in ("ledger", "foundations", "builder"),
        0.50,
        "a calm band keeps a dense or early page quiet",
    )
    horizon.add(type_pairing in ("technical", "warm"), 0.30, "this type pairing suits a flat band")
    horizon.add(f.shape.sparse, 0.15, "a new graph needs little backdrop")

    corner: _Score[FieldShape] = _Score("corner", 0.0, "a single corner glow")
    corner.add(
        composition in ("narrative", "pivot"), 0.50, "a single corner glow keeps attention on the words"
    )
    corner.add(type_pairing == "editorial", 0.30, "a reading serif wants a quiet backdrop")

    order = {name: i for i, name in enumerate(FIELDS)}
    return sorted([dawn, horizon, corner], key=lambda o: order[o.value])


def _corner_options(f: Features, type_pairing: TypePairing) -> list[_Score[Corners]]:
    square: _Score[Corners] = _Score("square", 0.0, "sharp corners for a precise feel")
    square.add(
        type_pairing in ("technical", "editorial"), 0.50, "sharp corners suit precise or editorial type"
    )
    square.add(f.stage in ("senior", "switcher"), 0.20, "a settled career reads well with sharp corners")

    soft: _Score[Corners] = _Score("soft", 0.45, "soft corners are the friendly default")
    soft.add(type_pairing == "studio", 0.10, "soft corners sit well with serif headlines")

    rounded: _Score[Corners] = _Score("round", 0.0, "round corners for a friendly feel")
    rounded.add(type_pairing == "warm", 0.60, "round corners match a friendly type pairing")
    rounded.add(f.stage == "student", 0.20, "round corners suit an early-career page")

    order = {name: i for i, name in enumerate(CORNER_OPTIONS)}
    return sorted([square, soft, rounded], key=lambda o: order[o.value])


def _entries(f: Features) -> int:
    return f.n_experience + f.n_projects + f.n_education + f.n_achievements + f.n_clips


def _density_options(
    f: Features, composition: Composition, type_pairing: TypePairing
) -> list[_Score[Density]]:
    entries = _entries(f)
    airy: _Score[Density] = _Score("airy", 0.50, "plenty of room to read")
    airy.add(entries <= 6, 0.15, "little content, so it can breathe")

    compact: _Score[Density] = _Score("compact", 0.0, "tighter spacing")
    compact.add(composition == "ledger", 0.50, "a long record reads better with tighter spacing")
    compact.add(type_pairing == "technical", 0.30, "technical type suits tighter spacing")
    compact.add(entries >= COMPACT_ENTRIES, 0.25, f"{entries} entries to fit")
    compact.add(composition == "builder", 0.20, "a build log is dense by nature")

    order = {name: i for i, name in enumerate(DENSITY_OPTIONS)}
    return sorted([airy, compact], key=lambda o: order[o.value])


# ───────────────────────── graph variant ─────────────────────────


def _graph_options(
    shape: GraphShape, composition: Composition, type_pairing: TypePairing
) -> list[_Score[GraphVariant]]:
    affinity = GRAPH_AFFINITY[composition]
    return [_Score(v, affinity[v] * graph_variant_fit(v, shape, type_pairing), "") for v in GRAPH_VARIANTS]


def _graph_reason(variant: GraphVariant, shape: GraphShape) -> str:
    spread = int(shape.spread)  # rounded down: never overstates
    if variant == "focus":
        return f"{shape.n_domains} areas and {shape.n_skills} skills so far, so the graph reads as areas of focus"
    if variant == "peaks":
        return f"The top area is {spread} above the median"
    if variant == "spotlight":
        return f"One area leads the next by {shape.gap}"
    if variant == "halo":
        return f"Scores are balanced (top is {spread} above the median) and the mastery sits in the centre"
    if variant == "atlas":
        return f"{shape.n_skills} skills across {shape.n_rich} areas suit small multiples"
    if variant == "stellar":
        return f"{shape.n_domains} axes read cleanly as spears"
    return "Ranked rows stay readable at any size"


def _choose_graph(
    shape: GraphShape,
    composition: Composition,
    type_pairing: TypePairing,
    rng: random.Random,
    penalty: Mapping[str, float] | None = None,
) -> GraphVariant:
    best = _pick(_graph_options(shape, composition, type_pairing), rng, penalty)
    if best.score > 0:
        return best.value
    return "focus" if shape.sparse else "ledger"


# ───────────────────────── blocks ─────────────────────────


def _block_variant(
    kind: BlockKind, variants: tuple[str, ...], content: PortfolioContent, density: Density
) -> str:
    """The composition's default variant for a block, adjusted when the content does not suit it."""
    default = variants[0]
    count = Counter(f.kind for f in content.facts)
    if kind == "experience":
        if default == "thenNow" and count["experience"] < 2:
            return "timeline" if "timeline" in variants else variants[-1]
        if default == "timeline" and count["experience"] >= EXPERIENCE_LEDGER_MIN and "ledger" in variants:
            return "ledger"
    elif kind == "projects":
        if count["project"] >= PROJECT_LIST_MIN and "list" in variants:
            return "list"
        if count["project"] == 1 and default == "grid" and "featureList" in variants:
            return "featureList"
    elif kind == "achievements":
        numeric = sum(
            1 for f in content.facts if f.kind == "achievement" and any(c.isdigit() for c in f.title)
        )
        if default == "numbers" and numeric < 2 and "list" in variants:
            return "list"
    elif kind == "education" and (density == "compact" or count["education"] >= 3) and "compact" in variants:
        return "compact"
    return default


def _blocks(
    composition: Composition,
    graph_variant: GraphVariant,
    content: PortfolioContent,
    graph: GraphSnapshot,
    density: Density,
) -> list[BlockSpec]:
    blocks: list[BlockSpec] = []
    for kind, variants in COMPOSITIONS[composition]:
        variant = graph_variant if kind == "graph" else _block_variant(kind, variants, content, density)
        shown = kind != "openTo" and block_has_content(kind, content, graph)
        blocks.append(BlockSpec(id=kind, kind=kind, variant=variant, visible=shown, public=shown))
    return blocks


def _graph_spec(content: PortfolioContent) -> GraphSpec:
    return GraphSpec(hiddenDomainIds=[], stats=len(content.stats) >= PROOF_MIN_STATS)


def _no_privacy() -> PortfolioPrivacy:
    return PortfolioPrivacy(email=False, phone=False, grades=False)


# ───────────────────────── compose ─────────────────────────


def _design(
    f: Features,
    content: PortfolioContent,
    graph: GraphSnapshot,
    comp: _Score[Composition],
    rank: int,
    rng: random.Random,
    history: _History,
) -> PortfolioSpec | None:
    """One look for one composition, or None when every hue has been used with it already."""
    composition = comp.value
    # every look gets its own hue while there are hues left; after that only a (composition, hue) pair is unique
    taken = {fam for c, fam in history.pairs if c == composition}
    banned = set(history.families) if len(history.families) < len(FAMILIES) else taken

    if all(fam in banned for fam in FAMILIES):
        return None
    family = _pick(_family_options(f), rng, _penalty(history.families, FAMILY_REPEAT_PENALTY), banned=banned)
    type_pick = _pick(
        _type_options(f, composition, family.value), rng, _penalty(history.types, STYLE_REPEAT_PENALTY)
    )
    variant = _choose_graph(
        f.shape, composition, type_pick.value, rng, _penalty(history.graphs, GRAPH_REPEAT_PENALTY)
    )
    field_pick = _pick(
        _field_options(f, composition, type_pick.value), rng, _penalty(history.fields, STYLE_REPEAT_PENALTY)
    )
    corners = _pick(_corner_options(f, type_pick.value), rng, _penalty(history.corners, STYLE_REPEAT_PENALTY))
    density = _pick(
        _density_options(f, composition, type_pick.value),
        rng,
        _penalty(history.densities, STYLE_REPEAT_PENALTY),
    )

    history.families[family.value] += 1
    history.types[type_pick.value] += 1
    history.fields[field_pick.value] += 1
    history.corners[corners.value] += 1
    history.densities[density.value] += 1
    history.graphs[variant] += 1
    history.pairs.add((composition, family.value))

    lead = comp.because()
    lean_label = LEAN_LABELS[f.lean]
    reasons = [
        DesignReason(
            dimension="composition",
            choice=composition,
            because=lead if rank == 0 else f"A different take: {lead[:1].lower()}{lead[1:]}",
        ),
        DesignReason(dimension="graph", choice=variant, because=_graph_reason(variant, f.shape)),
        DesignReason(
            dimension="family",
            choice=family.value,
            because=f"Suits {lean_label}"
            if rank == 0
            else f"A different hue to compare; it also suits {lean_label}",
        ),
        DesignReason(dimension="type", choice=type_pick.value, because=type_pick.because(1)),
        DesignReason(dimension="field", choice=field_pick.value, because=field_pick.because(1)),
        DesignReason(dimension="corners", choice=corners.value, because=corners.because(1)),
        DesignReason(dimension="density", choice=density.value, because=density.because(1)),
    ]
    return PortfolioSpec(
        composition=composition,
        style=PortfolioStyle(
            family=family.value,
            field=field_pick.value,
            type=type_pick.value,
            corners=corners.value,
            density=density.value,
        ),
        blocks=_blocks(composition, variant, content, graph, density.value),
        graph=_graph_spec(content),
        privacy=_no_privacy(),
        reasons=reasons,
    )


def compose(
    features: Features,
    content: PortfolioContent,
    graph: GraphSnapshot,
    seed: int,
    n: int = LOOK_COUNT,
) -> list[PortfolioSpec]:
    """Up to ``n`` valid specs for one person, best first, pairwise different in composition or hue.

    Deterministic: the same features, content and seed always give the same list. Blocks that do not reach
    their minimum content are hidden, the "open to" block is always off (it is opt-in), privacy is closed and
    no domain is hidden.
    """
    rng = random.Random(seed)
    options = _composition_options(features)
    jitter = {o.value: rng.random() for o in options}
    ranked = sorted(options, key=lambda o: (-round(o.score, 6), jitter[o.value]))

    history = _History()
    specs: list[PortfolioSpec] = []
    for i in range(max(0, n)):
        spec = _design(features, content, graph, ranked[i % len(ranked)], i, rng, history)
        if spec is None:
            break
        specs.append(spec)
    return specs


# ───────────────────────── the default page ─────────────────────────


def _shape_of(graph: GraphSnapshot) -> GraphShape:
    scores = sorted((d.score for d in graph.domains), reverse=True)
    return GraphShape(
        n_domains=len(scores),
        n_skills=graph.skillCount,
        n_rich=sum(1 for d in graph.domains if len(d.skills) >= 3),
        top=scores[0] if scores else 0,
        second=scores[1] if len(scores) > 1 else 0,
        median=float(median(scores)) if scores else 0.0,
        has_mastery=graph.mastery is not None,
        single_source=len(graph.sources) <= 1,
    )


def default_spec(content: PortfolioContent, graph: GraphSnapshot) -> PortfolioSpec:
    """The safe page used when nothing else is available: Signature, the iris hue, serif type, soft corners.

    The graph variant still follows the graph's shape, so even the fallback page shows the graph well.
    """
    shape = _shape_of(graph)
    variant = _choose_graph(shape, "signature", "studio", random.Random(0))
    return PortfolioSpec(
        composition="signature",
        style=PortfolioStyle(family="iris", field="dawn", type="studio", corners="soft", density="airy"),
        blocks=_blocks("signature", variant, content, graph, "airy"),
        graph=_graph_spec(content),
        privacy=_no_privacy(),
        reasons=[
            DesignReason(
                dimension="composition",
                choice="signature",
                because=f"A safe, balanced default: the {COMPOSITION_LABELS['signature']} layout",
            ),
            DesignReason(dimension="graph", choice=variant, because=_graph_reason(variant, shape)),
            DesignReason(
                dimension="family", choice="iris", because=f"{FAMILY_LABELS['iris']} is the default hue"
            ),
        ],
    )


__all__ = ["LOOK_COUNT", "compose", "default_spec", "person_seed"]
