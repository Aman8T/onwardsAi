#!/usr/bin/env python3
"""
Capture golden baselines from the legacy code.

Run: `uv run python tools/capture_golden.py` to generate files in tests/golden/legacy/,
or `--out DIR` to write elsewhere.

Freezes the current behaviour of legacy code so later units can port it and prove they match.
Deterministic and idempotent: patches datetime and random functions, then imports legacy modules.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

try:
    from datetime import UTC
except ImportError:
    UTC = UTC  # type: ignore

# Ensure we can import tayyari
sys.path.insert(0, str(Path(__file__).parent.parent))


def _patched_datetime_now(tz: Any = None) -> datetime:
    """Fixed datetime for deterministic prompts: 2026-10-07."""
    return datetime(2026, 10, 7, 12, 30, 45, tzinfo=UTC)


def _patched_today_str() -> str:
    """Fixed date string for prompts."""
    return "2026-10-07"


@dataclass
class GoldenCase:
    """A single test case: input → output."""

    name: str
    input: Any
    output: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capture_prompts() -> list[GoldenCase]:
    """Capture 1. prompts.json: READER_SYSTEM(kind), build_reader_prompt, SYNTH_SYSTEM, build_synth_prompt, build_chat_system."""
    # Patch datetime at import time
    with patch("tayyari.legacy.prompts.datetime") as mock_dt:
        mock_dt.now.side_effect = _patched_datetime_now
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)

        # Also patch timezone
        mock_dt.timezone = timezone

        from tayyari.legacy import prompts
        from tayyari.sample import load_sample_sources

        # Patch the _today function in the module
        original_today = prompts._today
        prompts._today = _patched_today_str

        try:
            cases: list[GoldenCase] = []

            # 1. READER_SYSTEM for all 7 kinds
            kinds = ["resume", "github", "lovable", "linkedin", "portfolio", "link", "notes"]
            for kind in kinds:
                cases.append(
                    GoldenCase(
                        name=f"READER_SYSTEM({kind})",
                        input={"kind": kind},
                        output=prompts.READER_SYSTEM(kind),
                    )
                )

            # 2. build_reader_prompt for each sample source
            sources = load_sample_sources()
            for src in sources:
                src_dict = {
                    "id": src.id,
                    "kind": src.kind,
                    "label": src.label,
                    "kindLabel": src.kind_label,
                    "url": src.url,
                    "src": src.src,
                    "content": src.text or "",
                    "facts": src.meta or {},
                }
                cases.append(
                    GoldenCase(
                        name=f"build_reader_prompt({src.id})",
                        input=src_dict,
                        output=prompts.build_reader_prompt(src_dict),
                    )
                )

            # 3. SYNTH_SYSTEM (constant)
            cases.append(
                GoldenCase(
                    name="SYNTH_SYSTEM",
                    input=None,
                    output=prompts.SYNTH_SYSTEM,
                )
            )

            # 4. build_synth_prompt with sample data
            import json as json_module

            sample_profile = json_module.loads(
                (Path(__file__).parent.parent / "tayyari" / "sample" / "sample-profile.json").read_text()
            )
            sample_context = json_module.loads(
                (Path(__file__).parent.parent / "tayyari" / "sample" / "sample-context.json").read_text()
            )

            synth_sources = sample_context.get("sources", [])
            synth_prompt = prompts.build_synth_prompt(
                goal="Move into AI product engineering within a year",
                sources=synth_sources,
                resume_text=next((s.get("content") for s in synth_sources if s.get("kind") == "resume"), ""),
                notes_text=next((s.get("content") for s in synth_sources if s.get("kind") == "notes"), ""),
            )
            cases.append(
                GoldenCase(
                    name="build_synth_prompt(sample)",
                    input={
                        "goal": "Move into AI product engineering within a year",
                        "sources_count": len(synth_sources),
                    },
                    output=synth_prompt,
                )
            )

            # 5. build_chat_system for each origin kind
            readers = sample_context.get("readers", {})
            origin_kinds = ["skill", "domain", "source", "role", "requirement", "you", "free"]
            for origin_kind in origin_kinds:
                origin = {
                    "kind": origin_kind,
                    "title": f"Test {origin_kind}",
                    "refId": f"test-{origin_kind}",
                    "caption": "Test caption",
                }

                chat_system = prompts.build_chat_system(
                    profile=sample_profile,
                    origin=origin,
                    sources=synth_sources,
                    readers=readers,
                    budget=60000,
                )
                cases.append(
                    GoldenCase(
                        name=f"build_chat_system({origin_kind})",
                        input={"origin_kind": origin_kind},
                        output=chat_system,
                    )
                )

            return cases

        finally:
            prompts._today = original_today


def capture_clean_reader() -> list[GoldenCase]:
    """Capture 2. clean_reader.json: test on 8-12 crafted reader outputs."""
    from tayyari.legacy.pipeline import clean_reader

    cases: list[GoldenCase] = []

    test_inputs = [
        # Normal case
        {
            "name": "normal",
            "input": {
                "sub": "4 roles · 23 skills",
                "summary": "Found evidence across multiple projects",
                "person": {
                    "name": "Alice Smith",
                    "currentRole": "Senior Frontend Engineer",
                    "yearsExperience": 5.5,
                    "location": "San Francisco, CA",
                },
                "signals": [
                    {"label": "React", "detail": "5 repos"},
                    {"label": "TypeScript", "detail": "primary language"},
                ],
                "skills": [
                    {"name": "React", "evidence": "Shipped at Acme Corp", "strength": 4},
                    {"name": "Node.js", "evidence": "Personal projects", "strength": 3},
                ],
                "notes": ["Strong testing practices", "Good mentoring skills"],
            },
        },
        # Overlong strings
        {
            "name": "overlong_strings",
            "input": {
                "sub": "A" * 100 + " roles and more skills than should fit",
                "summary": "X" * 500,
                "skills": [
                    {
                        "name": "A" * 50,
                        "evidence": "B" * 200,
                        "strength": 3,
                    }
                ],
                "notes": ["C" * 200, "D" * 200],
            },
        },
        # Too many items
        {
            "name": "too_many_items",
            "input": {
                "sub": "sub text",
                "summary": "summary",
                "signals": [{"label": f"Signal{i}", "detail": "detail"} for i in range(20)],
                "skills": [{"name": f"Skill{i}", "evidence": "evidence", "strength": 3} for i in range(40)],
                "notes": [f"Note{i}" for i in range(10)],
            },
        },
        # Missing fields
        {
            "name": "missing_fields",
            "input": {
                "summary": "summary only",
                # no sub, person, signals, skills, notes
            },
        },
        # Bad strength values
        {
            "name": "bad_strength",
            "input": {
                "sub": "sub",
                "summary": "summary",
                "skills": [
                    {"name": "Skill1", "evidence": "evidence", "strength": 0},  # too low
                    {"name": "Skill2", "evidence": "evidence", "strength": 10},  # too high
                    {"name": "Skill3", "evidence": "evidence", "strength": "invalid"},  # non-numeric
                    {"name": "Skill4", "evidence": "evidence"},  # missing
                ],
            },
        },
        # Wrong types (some fields get coerced)
        {
            "name": "wrong_types",
            "input": {
                "sub": 123,  # will be coerced to string
                "summary": None,  # will be coerced to string
                "signals": None,  # will default to []
                "skills": None,  # will default to []
                "notes": None,  # will default to []
            },
        },
        # Unicode and special characters
        {
            "name": "unicode",
            "input": {
                "sub": "Python · Go · TypeScript",
                "summary": "Émojis: 🚀 💻 🎯 and accents: naïve résumé café",
                "person": {
                    "name": "François Müller",
                    "currentRole": "Senior SWE",
                    "yearsExperience": 3.2,
                    "location": "São Paulo, Brasil",
                },
                "skills": [
                    {"name": "C++", "evidence": "Built high-perf systems", "strength": 4},
                ],
            },
        },
        # Nested person without data
        {
            "name": "empty_person",
            "input": {
                "sub": "sub",
                "summary": "summary",
                "person": {"name": "", "currentRole": None},
            },
        },
    ]

    for test in test_inputs:
        output = clean_reader(test["input"])
        cases.append(
            GoldenCase(
                name=test["name"],
                input=test["input"],
                output=output,
            )
        )

    return cases


def capture_to_profile() -> list[GoldenCase]:
    """Capture 3. to_profile.json: test on 8-12 crafted cases."""
    import secrets
    import time

    from tayyari.ingest import PlannedSource
    from tayyari.legacy.pipeline import ReadResult, to_profile

    # Patch time and secrets for deterministic ID generation
    original_time = time.time
    original_urandom = secrets.token_hex

    time.time = lambda: 1728304245.123  # Fixed timestamp

    def patched_token_hex(n: int = 16) -> str:
        """Generate a deterministic hex string."""
        base = "abcdef" * ((n // 6) + 1)
        return base[:n]

    secrets.token_hex = patched_token_hex

    try:
        cases: list[GoldenCase] = []

        # Helper to create a ReadResult
        def make_result(
            source_id: str,
            source_kind: str,
            reader_output: dict[str, Any] | None,
            error: str | None = None,
        ) -> ReadResult:
            from tayyari.ingest import SourceDoc

            planned = PlannedSource(
                id=source_id,
                kind=source_kind,
                label=f"{source_kind.title()} {source_id}",
                kind_label=f"{source_kind.title()} source",
                src=f"src_{source_id}",
                url=None,
                ingest=None,  # type: ignore
            )
            raw = (
                SourceDoc(
                    id=source_id,
                    kind=source_kind,
                    label=f"{source_kind.title()} {source_id}",
                    kind_label=f"{source_kind.title()} source",
                    src=f"src_{source_id}",
                    text="Sample text for testing",
                    meta={},
                )
                if not error
                else None
            )
            return ReadResult(
                planned=planned,
                raw=raw,
                reader=reader_output,
                error=error,
            )

        # Normal case
        synth_normal = {
            "person": {
                "name": "Test User",
                "currentRole": "Frontend Engineer",
                "yearsExperience": 3.0,
                "headline": "Frontend Engineer · 3 yrs · NYC",
            },
            "mastery": {"score": 75, "insight": "Strong UI skills"},
            "domains": [
                {"id": "frontend", "name": "Frontend", "short": "Frontend", "score": 80, "summary": "UI/UX"},
                {"id": "backend", "name": "Backend", "short": "Backend", "score": 40, "summary": "APIs"},
            ],
            "skills": [
                {
                    "id": "react",
                    "name": "React",
                    "domainId": "frontend",
                    "score": 85,
                    "evidence": [{"sourceId": "resume", "text": "Built React apps"}],
                    "unlocks": ["Senior Frontend Engineer"],
                },
                {
                    "id": "node",
                    "name": "Node.js",
                    "domainId": "backend",
                    "score": 45,
                    "evidence": [],
                    "unlocks": [],
                },
            ],
            "roles": [
                {
                    "id": "senior-fe",
                    "title": "Senior Frontend Engineer",
                    "alignment": 75,
                    "eta": "~6 months",
                    "why": "Strong React skills",
                    "requirements": [
                        {"name": "React", "skillId": "react", "have": True, "current": 85, "target": 90},
                        {"name": "Testing", "have": False, "current": 40, "target": 70},
                    ],
                },
            ],
            "sources": [
                {"id": "resume", "sub": "2 roles · 15 skills", "notes": ["Good writing"]},
            ],
        }

        results_normal = [
            make_result("resume", "resume", {"sub": "2 roles · 15 skills", "summary": "Found evidence"}),
        ]

        cases.append(
            GoldenCase(
                name="normal",
                input={"synth_domains": 2, "synth_skills": 2, "synth_roles": 1},
                output=to_profile(synth_normal, results_normal, "Test goal", False),
            )
        )

        # Duplicate domains/skills/roles
        synth_dups = {
            "person": {"name": "User", "currentRole": "Engineer"},
            "mastery": {"score": 50, "insight": ""},
            "domains": [
                {"id": "d1", "name": "Frontend", "short": "FE", "score": 75, "summary": ""},
                {"id": "d2", "name": "frontend", "short": "Frontend", "score": 70, "summary": ""},  # dup
            ],
            "skills": [
                {"id": "s1", "name": "React", "domainId": "d1", "score": 80, "evidence": [], "unlocks": []},
                {
                    "id": "s2",
                    "name": "react",
                    "domainId": "d1",
                    "score": 75,
                    "evidence": [],
                    "unlocks": [],
                },  # dup
            ],
            "roles": [
                {
                    "id": "r1",
                    "title": "Senior FE",
                    "alignment": 70,
                    "eta": "~1 year",
                    "why": "Good",
                    "requirements": [],
                },
                {
                    "id": "r2",
                    "title": "senior fe",
                    "alignment": 65,
                    "eta": "~1 year",
                    "why": "Good",
                    "requirements": [],
                },  # dup
            ],
            "sources": [],
        }

        results_dups = []

        cases.append(
            GoldenCase(
                name="duplicate_names",
                input={"synth_domains": 2, "synth_skills": 2, "synth_roles": 2},
                output=to_profile(synth_dups, results_dups, None, False),
            )
        )

        # Unknown domainIds
        synth_unknown_domain = {
            "person": {"name": "User", "currentRole": "Engineer"},
            "mastery": {"score": 50, "insight": ""},
            "domains": [{"id": "frontend", "name": "Frontend", "short": "FE", "score": 80, "summary": ""}],
            "skills": [
                {
                    "id": "react",
                    "name": "React",
                    "domainId": "unknown-domain",
                    "score": 75,
                    "evidence": [],
                    "unlocks": [],
                },
            ],
            "roles": [],
            "sources": [],
        }

        cases.append(
            GoldenCase(
                name="unknown_domainIds",
                input={},
                output=to_profile(synth_unknown_domain, [], None, False),
            )
        )

        # Evidence sourceIds (resolvable and unresolvable)
        synth_evidence = {
            "person": {"name": "User", "currentRole": "Engineer"},
            "mastery": {"score": 50, "insight": ""},
            "domains": [{"id": "frontend", "name": "Frontend", "short": "FE", "score": 80, "summary": ""}],
            "skills": [
                {
                    "id": "react",
                    "name": "React",
                    "domainId": "frontend",
                    "score": 75,
                    "evidence": [
                        {"sourceId": "resume", "text": "React work"},  # resolvable
                        {"sourceId": "unknown-id", "text": "Evidence"},  # unresolvable
                        {"sourceId": "", "text": "No source"},  # empty
                    ],
                    "unlocks": [],
                }
            ],
            "roles": [],
            "sources": [],
        }

        results_evidence = [
            make_result("resume", "resume", {"sub": "sub", "summary": "summary"}),
        ]

        cases.append(
            GoldenCase(
                name="evidence_resolution",
                input={},
                output=to_profile(synth_evidence, results_evidence, None, False),
            )
        )

        # More than 7 domains / 28 skills / 6 roles
        synth_excess = {
            "person": {"name": "User", "currentRole": "Engineer"},
            "mastery": {"score": 50, "insight": ""},
            "domains": [
                {"id": f"d{i}", "name": f"Domain{i}", "short": f"D{i}", "score": 50 + i, "summary": ""}
                for i in range(10)
            ],
            "skills": [
                {
                    "id": f"s{i}",
                    "name": f"Skill{i}",
                    "domainId": "d0",
                    "score": 50 + (i % 50),
                    "evidence": [],
                    "unlocks": [],
                }
                for i in range(40)
            ],
            "roles": [
                {
                    "id": f"r{i}",
                    "title": f"Role{i}",
                    "alignment": 50 + i,
                    "eta": "~1 year",
                    "why": "",
                    "requirements": [],
                }
                for i in range(10)
            ],
            "sources": [],
        }

        cases.append(
            GoldenCase(
                name="excess_items",
                input={"synth_domains": 10, "synth_skills": 40, "synth_roles": 10},
                output=to_profile(synth_excess, [], None, False),
            )
        )

        # Missing person (fallback to reader)
        synth_no_person = {
            "person": None,
            "mastery": {"score": 50, "insight": ""},
            "domains": [{"id": "frontend", "name": "Frontend", "short": "FE", "score": 80, "summary": ""}],
            "skills": [],
            "roles": [],
            "sources": [],
        }

        results_reader_person = [
            make_result(
                "resume",
                "resume",
                {
                    "sub": "2 roles",
                    "summary": "summary",
                    "person": {
                        "name": "Reader Person",
                        "currentRole": "Engineer",
                        "yearsExperience": 5.0,
                        "location": "NYC",
                    },
                },
            ),
        ]

        cases.append(
            GoldenCase(
                name="missing_person",
                input={},
                output=to_profile(synth_no_person, results_reader_person, None, False),
            )
        )

        # Requirements with and without skillId
        synth_reqs = {
            "person": {"name": "User", "currentRole": "Engineer"},
            "mastery": {"score": 50, "insight": ""},
            "domains": [{"id": "frontend", "name": "Frontend", "short": "FE", "score": 80, "summary": ""}],
            "skills": [
                {
                    "id": "react",
                    "name": "React",
                    "domainId": "frontend",
                    "score": 80,
                    "evidence": [],
                    "unlocks": [],
                },
            ],
            "roles": [
                {
                    "id": "senior-fe",
                    "title": "Senior FE",
                    "alignment": 70,
                    "eta": "~1 year",
                    "why": "",
                    "requirements": [
                        {"name": "React", "skillId": "react", "have": True, "current": 80, "target": 90},
                        {"name": "System Design", "have": False, "current": 30, "target": 70},  # no skillId
                    ],
                }
            ],
            "sources": [],
        }

        cases.append(
            GoldenCase(
                name="requirements_with_and_without_skillId",
                input={},
                output=to_profile(synth_reqs, [], None, False),
            )
        )

        # Failed sources (errors)
        synth_with_failed = {
            "person": {"name": "User", "currentRole": "Engineer"},
            "mastery": {"score": 50, "insight": ""},
            "domains": [{"id": "frontend", "name": "Frontend", "short": "FE", "score": 80, "summary": ""}],
            "skills": [],
            "roles": [],
            "sources": [
                {"id": "resume", "sub": "2 roles", "notes": []},
                {"id": "github", "sub": "34 repos", "notes": []},
            ],
        }

        results_with_failed = [
            make_result("resume", "resume", {"sub": "2 roles", "summary": "Read OK"}),
            make_result("github", "github", None, error="Could not read GitHub"),
        ]

        cases.append(
            GoldenCase(
                name="failed_sources",
                input={},
                output=to_profile(synth_with_failed, results_with_failed, None, False),
            )
        )

        # Goal set vs none
        synth_goal_set = {
            "person": {"name": "User", "currentRole": "Engineer"},
            "mastery": {"score": 50, "insight": ""},
            "domains": [{"id": "frontend", "name": "Frontend", "short": "FE", "score": 80, "summary": ""}],
            "skills": [],
            "roles": [],
            "sources": [],
        }

        cases.append(
            GoldenCase(
                name="goal_set",
                input={},
                output=to_profile(synth_goal_set, [], "Move to backend", False),
            )
        )

        cases.append(
            GoldenCase(
                name="goal_none",
                input={},
                output=to_profile(synth_goal_set, [], None, False),
            )
        )

        # Sample mode (ids prefixed with "sample-")
        cases.append(
            GoldenCase(
                name="sample_mode",
                input={},
                output=to_profile(synth_goal_set, [], None, True),
            )
        )

        return cases

    finally:
        time.time = original_time
        secrets.token_hex = original_urandom


def capture_activity_lines() -> list[GoldenCase]:
    """Capture 4. activity_lines.json: test for each sample source kind."""
    from tayyari.legacy.pipeline import activity_lines
    from tayyari.sample import load_sample_sources

    cases: list[GoldenCase] = []

    sources = load_sample_sources()
    for src in sources:
        lines = activity_lines(src)
        cases.append(
            GoldenCase(
                name=f"activity_lines({src.kind})",
                input={"source_id": src.id, "source_kind": src.kind},
                output=lines,
            )
        )

    return cases


def capture_humanize_error() -> list[GoldenCase]:
    """Capture 5. humanize_error.json: test on various errors."""
    from tayyari.legacy.pipeline import humanize_error

    cases: list[GoldenCase] = []

    test_errors = [
        # Auth errors
        ("auth_key", ValueError("401 Unauthorized")),
        ("auth_invalid_key", ValueError("invalid api key")),
        ("auth_token_expired", ValueError("token has expired")),
        # Rate limit / overload
        ("rate_limited", ValueError("rate_limited: 429")),
        ("overloaded", ValueError("Service overloaded (529)")),
        # Network errors
        ("timeout", ValueError("Request timed out after 30s")),
        ("connection_error", ValueError("Could not connect to the server")),
        # Incomplete answer
        ("incomplete", ValueError("failed validation: no JSON object")),
        # Generic unknown
        ("unknown_long_text", ValueError("X" * 300)),
        ("unknown_short_text", ValueError("Something went wrong")),
    ]

    for name, error in test_errors:
        msg = humanize_error(error)
        cases.append(
            GoldenCase(
                name=name,
                input={"error_type": type(error).__name__, "error_message": str(error)},
                output=msg,
            )
        )

    # Also test with string errors (not exceptions)
    cases.append(
        GoldenCase(
            name="string_error",
            input={"error_message": "401 Unauthorized"},
            output=humanize_error("401 Unauthorized"),
        )
    )

    # Cancelled error
    import asyncio

    cases.append(
        GoldenCase(
            name="cancelled",
            input={"error_type": "CancelledError"},
            output=humanize_error(asyncio.CancelledError()),
        )
    )

    return cases


def main() -> None:
    """Capture all golden baselines and write to JSON files."""
    parser = argparse.ArgumentParser(description="Capture legacy code baselines")
    parser.add_argument(
        "--out", default="tests/golden/legacy", help="Output directory (default: tests/golden/legacy)"
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Capturing golden baselines to {out_dir}...")  # noqa: T201

    # Capture all golden data
    captures = [
        ("prompts.json", capture_prompts),
        ("clean_reader.json", capture_clean_reader),
        ("to_profile.json", capture_to_profile),
        ("activity_lines.json", capture_activity_lines),
        ("humanize_error.json", capture_humanize_error),
    ]

    for filename, capture_func in captures:
        print(f"  {filename}...", end=" ", flush=True)  # noqa: T201
        try:
            cases = capture_func()
            output = {"cases": [c.to_dict() for c in cases]}
            path = out_dir / filename
            path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"✓ ({len(cases)} cases)")  # noqa: T201
        except Exception as e:
            print(f"✗ {e}")  # noqa: T201
            raise

    print(f"\nDone! {len(captures)} files written to {out_dir}/")  # noqa: T201


if __name__ == "__main__":
    main()
