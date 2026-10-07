"""The port against the legacy code it replaces, on thousands of random inputs.

The golden files pin a handful of hand-made cases; these tests feed the same random inputs to the legacy functions in
`tayyari.legacy.pipeline` and to `analysis.normalise` and demand identical results. The only allowed differences in
`to_profile` are the profile id and `createdAt` (the id is now random hex, the time is injected).

The legacy package is deleted in the last rebuild wave; this file goes with it (it skips when it is missing).
"""

from __future__ import annotations

import math
import random
from typing import Any

import pytest

from tayyari.analysis import normalise
from tayyari.contract import ModelsUsed
from tayyari.ingest import PlannedSource, SourceDoc

legacy = pytest.importorskip("tayyari.legacy.pipeline")
legacy_config = pytest.importorskip("tayyari.legacy.config")

SEEDS = range(400)
NAMES = [
    "React",
    "react",
    " React  ",
    "Node.js",
    "node-js",
    "C++",
    "C#",
    "Rust",
    "UI/UX",
    "Testing",
    "Tests",
    "System Design",
    "Design Systems",
    "REST & GraphQL",
    "rest-graphql",
    "Frontend Engineering",
    "Accessibility",
    "A very long skill name that goes well past the limit",
    "Senior Frontend Engineer",
    "senior frontend engineer",
    "Staff Engineer",
    "Engineering Manager (Platform Team)",
    "Ünïcode Naïve Café",
    "",
    "   ",
]
IDS = ["react", "node", "ui", "d1", "d2", "frontend", "backend", "r1", "r2", "", "Weird ID!", "x" * 60]
TEXTS = [
    "Built it",
    "Built it",
    "Shipped the thing at Acme",
    "x" * 200,
    "",
    "  ",
    "Sentence one. Sentence two.",
]
KINDS = ["resume", "github", "lovable", "linkedin", "portfolio", "link", "notes"]


def junk(rng: random.Random) -> Any:
    return rng.choice(
        [
            None,
            "",
            " ",
            "text",
            " spaced   text ",
            0,
            1,
            50,
            99.5,
            -5,
            1e9,
            True,
            False,
            [],
            {},
            "72",
            "abc",
            "12.5",
            ["x"],
        ]
    )


def number(rng: random.Random) -> Any:
    return rng.choice([rng.randint(-20, 130), rng.random() * 120, str(rng.randint(0, 100)), None, junk(rng)])


def pick_name(rng: random.Random) -> Any:
    return rng.choice(NAMES) if rng.random() < 0.9 else junk(rng)


def gen_domains(rng: random.Random) -> list[Any]:
    return [
        {
            "id": rng.choice(IDS),
            "name": pick_name(rng),
            "short": pick_name(rng),
            "score": number(rng),
            "summary": rng.choice(TEXTS),
        }
        for _ in range(rng.randint(0, 9))
    ]


def gen_skills(
    rng: random.Random, domains: list[Any], source_refs: list[str], titles: list[str]
) -> list[Any]:
    domain_refs = [d["id"] for d in domains] + [d["name"] for d in domains] + ["", "nope", "frontend"]
    skills = []
    for _ in range(rng.randint(0, 35)):
        evidence: list[Any] = [
            {"sourceId": rng.choice(source_refs), "text": rng.choice(TEXTS)}
            if rng.random() < 0.85
            else junk(rng)
            for _ in range(rng.randint(0, 6))
        ]
        skills.append(
            {
                "id": rng.choice(IDS),
                "name": pick_name(rng),
                "domainId": rng.choice(domain_refs) if rng.random() < 0.9 else junk(rng),
                "score": number(rng),
                "evidence": evidence,
                "unlocks": [rng.choice(titles + NAMES) for _ in range(rng.randint(0, 5))],
            }
        )
    return skills


def gen_roles(rng: random.Random, skills: list[Any]) -> tuple[list[Any], list[str]]:
    roles = []
    skill_refs = [s["id"] for s in skills] + [s["name"] for s in skills] + ["", "ghost", "REACT", "tests"]
    for _ in range(rng.randint(0, 9)):
        reqs: list[Any] = []
        for _ in range(rng.randint(0, 10)):
            if rng.random() < 0.1:
                reqs.append(junk(rng))
                continue
            reqs.append(
                {
                    "name": pick_name(rng),
                    "skillId": rng.choice(skill_refs) if rng.random() < 0.6 else None,
                    "have": rng.choice([True, False, None, "yes", 0]),
                    "current": number(rng),
                    "target": number(rng),
                }
            )
        roles.append(
            {
                "id": rng.choice(IDS),
                "title": rng.choice(NAMES),
                "alignment": number(rng),
                "eta": rng.choice(["", "~4 months", None, "  ~1 year "]),
                "why": rng.choice(TEXTS),
                "requirements": reqs,
            }
        )
    return roles, [r["title"] for r in roles]


def gen_person(rng: random.Random) -> Any:
    if rng.random() < 0.2:
        return junk(rng)
    return {
        k: v
        for k, v in {
            "name": rng.choice(["Test User", "you", "You", "", None, "  Ada  Lovelace "]),
            "headline": rng.choice(["Engineer · 3 yrs", "", None, "h" * 120]),
            "currentRole": rng.choice(["Frontend Engineer", "", None, "R" * 70]),
            "location": rng.choice(["Pune, India", "", None, "NYC"]),
            "yearsExperience": rng.choice([3, 2.25, -1, None, "4", True, 0, 7.04]),
            "summary": rng.choice(TEXTS),
        }.items()
        if rng.random() < 0.9
    }


def gen_case(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    n_sources = rng.randint(0, 5)
    planned = []
    for i in range(n_sources):
        kind = rng.choice(KINDS)
        sid = kind if kind in ("resume", "notes", "github") and i == 0 else f"{kind}-{i + 1}"
        label = rng.choice(["Resume", "GitHub", "Portfolio", "Kaggle", "Web", "Notes"])
        planned.append(
            {
                "id": sid,
                "kind": kind,
                "label": label,
                "kind_label": f"{label} source",
                "src": f"src-{i}",
                "url": rng.choice([None, f"https://x.test/{i}"]),
            }
        )
    results = []
    for p in planned:
        has_raw = rng.random() < 0.85
        raw = (
            {
                "id": p["id"],
                "kind": p["kind"],
                "label": rng.choice([p["label"], "", "Relabelled"]),
                "kind_label": p["kind_label"],
                "src": p["src"],
                "text": "text",
                "meta": rng.choice(
                    [
                        {},
                        {"repos": 12, "languages": ["a", "b"]},
                        {"pages": 4, "caseStudies": 3},
                        {"publicRepos": 1},
                        {"caseStudies": 1, "pages": 2},
                        {"repos": True},
                    ]
                ),
                "url": rng.choice([None, "https://raw.test/x"]),
            }
            if has_raw
            else None
        )
        reader = (
            {
                "sub": rng.choice(["3 roles · 9 skills", "", None, "s" * 80]),
                "summary": rng.choice(TEXTS),
                "notes": rng.choice([[], ["n1", "n2", "n3", "n4", "n5"], ["x" * 200], [" "], None]),
                "person": rng.choice(
                    [
                        None,
                        {
                            "name": "Reader Name",
                            "currentRole": "Reader Role",
                            "yearsExperience": 5.5,
                            "location": "Delhi, India",
                        },
                        {"name": "", "yearsExperience": -3},
                        {"currentRole": "Only Role"},
                    ]
                ),
            }
            if rng.random() < 0.8
            else None
        )
        error = rng.choice([None, "Could not read this source.", "Blocked"]) if reader is None else None
        results.append({"planned": p, "raw": raw, "reader": reader, "error": error})
    ids = [p["id"] for p in planned]
    labels = [p["label"] for p in planned]
    source_refs = [*ids, *(i.upper() for i in ids), *labels, *KINDS, "", "unknown-id"]
    domains = gen_domains(rng)
    domains_out: list[Any] = domains if rng.random() < 0.95 else []
    skills = gen_skills(rng, domains, source_refs, [])
    roles, titles = gen_roles(rng, skills)
    # skills' unlocks refer to role titles; regenerate with them for realism
    for s in skills:
        s["unlocks"] = (
            [rng.choice(titles + NAMES) for _ in range(rng.randint(0, 5))] if titles else s["unlocks"]
        )
    synth: dict[str, Any] = {
        "person": gen_person(rng),
        "mastery": rng.choice([{"score": number(rng), "insight": rng.choice(TEXTS)}, None, "oops"]),
        "domains": domains_out,
        "skills": skills,
        "roles": roles,
        "sources": [
            {
                "id": rng.choice(source_refs),
                "sub": rng.choice(["sub", "", None]),
                "notes": rng.choice([[], ["a", "b"], None]),
            }
            if rng.random() < 0.85
            else junk(rng)
            for _ in range(rng.randint(0, 6))
        ],
    }
    if rng.random() < 0.05:
        synth.pop("roles")
    return {
        "synth": synth,
        "results": results,
        "goal": rng.choice([None, "", "Become staff"]),
        "sample": rng.random() < 0.3,
    }


def build(cls: type, item: dict[str, Any]) -> Any:
    planned = PlannedSource(**item["planned"], ingest=None)  # type: ignore[arg-type]
    raw = SourceDoc(**item["raw"]) if item["raw"] is not None else None
    return cls(planned=planned, raw=raw, reader=item["reader"], error=item["error"])


def run_legacy(case: dict[str, Any]) -> Any:
    try:
        out = legacy.to_profile(
            case["synth"],
            [build(legacy.ReadResult, r) for r in case["results"]],
            case["goal"],
            case["sample"],
        )
    except Exception as exc:
        return type(exc).__name__
    out = dict(out)
    out.pop("id"), out.pop("createdAt")
    return out


def run_port(case: dict[str, Any]) -> Any:
    models = ModelsUsed(reader=legacy_config.MODELS["reader"], synth=legacy_config.MODELS["synth"])
    try:
        profile = normalise.to_profile(
            case["synth"],
            [build(normalise.ReadResult, r) for r in case["results"]],
            case["goal"],
            case["sample"],
            models=models,
        )
    except Exception as exc:
        return type(exc).__name__
    out = profile.to_dict()
    out.pop("id"), out.pop("createdAt")
    return out


@pytest.mark.parametrize("seed", SEEDS)
def test_to_profile_equals_legacy_on_random_inputs(seed: int) -> None:
    case = gen_case(seed)
    assert run_port(case) == run_legacy(case)


def test_the_random_cases_reach_the_interesting_branches() -> None:
    """The generator would be worthless if it only produced failures or empty graphs."""
    outcomes = [run_legacy(gen_case(seed)) for seed in SEEDS]
    graphs = [o for o in outcomes if isinstance(o, dict)]
    assert len(graphs) > 300
    assert sum(1 for g in graphs if len(g["skills"]) >= 10) > 100
    assert sum(1 for g in graphs if any(len(r["requirements"]) >= 5 for r in g["roles"])) > 50
    assert sum(1 for g in graphs if any(s["evidence"] for s in g["skills"])) > 100
    assert sum(1 for g in graphs if any(not s["ok"] for s in g["sources"])) > 50
    assert sum(1 for g in graphs if len(g["domains"]) >= 2) > 200


# ───────────────────────── clean_reader and the helpers ─────────────────────────


def gen_reader(rng: random.Random) -> dict[str, Any]:
    def sig() -> Any:
        return rng.choice(
            [
                {"label": rng.choice(NAMES), "detail": rng.choice(TEXTS)},
                {"label": ""},
                junk(rng),
                {"detail": "d"},
            ]
        )

    def skill() -> Any:
        return rng.choice(
            [
                {
                    "name": rng.choice(NAMES),
                    "evidence": rng.choice(TEXTS),
                    "strength": rng.choice([0, 1, 3, 5, 9, "4", None, 2.5, True, "x"]),
                },
                {"name": "", "evidence": "e"},
                junk(rng),
            ]
        )

    out: dict[str, Any] = {}
    for key, value in {
        "sub": rng.choice(["4 roles · 23 skills", "x" * 100, 123, None, ""]),
        "summary": rng.choice([*TEXTS, None, 5]),
        "person": rng.choice(
            [
                None,
                "oops",
                {
                    "name": " A  B ",
                    "currentRole": "Eng",
                    "yearsExperience": rng.choice([3, 2.25, -1, "4", None, True, 1e3]),
                    "location": "X",
                },
                {},
            ]
        ),
        "signals": rng.choice([[sig() for _ in range(rng.randint(0, 20))], None, "oops"]),
        "skills": rng.choice([[skill() for _ in range(rng.randint(0, 40))], None, "oops"]),
        "notes": rng.choice(
            [[rng.choice(TEXTS) for _ in range(rng.randint(0, 8))], None, ["a", None, 5, " "]]
        ),
    }.items():
        if rng.random() < 0.92:
            out[key] = value
    return out


@pytest.mark.parametrize("seed", SEEDS)
def test_clean_reader_equals_legacy_on_random_inputs(seed: int) -> None:
    data = gen_reader(random.Random(seed))
    try:
        expected: Any = legacy.clean_reader(data)
    except Exception as exc:
        expected = type(exc).__name__
    try:
        got: Any = normalise.clean_reader(data)
    except Exception as exc:
        got = type(exc).__name__
    assert got == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_activity_lines_equal_legacy_on_random_documents(seed: int) -> None:
    rng = random.Random(seed)
    headings = [
        rng.choice([*NAMES, "- h2: Checkout, rebuilt", "## Experience"]) for _ in range(rng.randint(0, 6))
    ]
    meta = rng.choice(
        [
            {},
            {"headings": headings},
            {"topRepos": [rng.choice(NAMES) for _ in range(8)], "languages": ["a", "b"]},
            {"routes": ["/", "/a", "/b", "/c"], "headings": [{"title": "T1"}, "T2"]},
            {"sections": headings[:2], "caseStudies": ["x", "y"]},
        ]
    )
    text = "\n".join(f"- h2: {h}" if rng.random() < 0.5 else f"## {h}" for h in headings)
    doc = SourceDoc(
        id="x", kind=rng.choice(KINDS), label="L", src=rng.choice(NAMES), kind_label="K", text=text, meta=meta
    )
    assert normalise.activity_lines(doc) == legacy.activity_lines(doc)


@pytest.mark.parametrize("seed", SEEDS)
def test_small_helpers_equal_legacy(seed: int) -> None:
    rng = random.Random(seed)
    value = rng.choice([junk(rng), number(rng), math.nan, math.inf, -math.inf, rng.random() * 200 - 50])
    lo, hi, fb = rng.choice([(0, 100, 0), (1, 5, 2), (0, 100, 70)])
    assert normalise.clamp(value, lo, hi, fb) == legacy.clamp(value, lo, hi, fb)
    assert normalise.is_num(value) == legacy._is_num(value)
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        assert normalise.round1(float(value)) == legacy._round1(float(value))

    signal = f"{rng.choice(NAMES)} · {rng.choice(TEXTS)}"
    assert normalise.split_signal(signal, "fb") == legacy.split_signal(signal, "fb")
    assert normalise.split_signal("no separator", "fb") == legacy.split_signal("no separator", "fb")

    out = {
        "sub": rng.choice(["a · b · c · d", "3 skills · x", "", "34 repos · 9 languages"]),
        "skills": [1] * rng.randint(0, 3),
    }
    assert normalise.result_line(out) == legacy.result_line(out)

    lines = ["- h2: Heading", "## 1. Sub", "### Page text", "- h3: " + "y" * 80, "plain"]
    text = chr(10).join(rng.choice(lines) for _ in range(rng.randint(0, 14)))
    assert normalise.content_headings(text) == legacy.content_headings(text)

    facts = {"k": [rng.choice(NAMES), {"name": rng.choice(NAMES)}, {"title": rng.choice(NAMES)}, 3]}
    assert normalise.fact_strings(facts, ["k"]) == legacy.fact_strings(facts, ["k"])

    kind = rng.choice(KINDS)
    doc = SourceDoc(id="x", kind=kind, label=rng.choice(NAMES), src="s", kind_label="K")
    assert normalise.quick_detail(doc) == legacy.quick_detail(doc)
