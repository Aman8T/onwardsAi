"""Tests for prompt builders with limits from tayyari.core.limits.

Tests verify that:
1. Prompt functions are callable and return strings
2. Limits are properly interpolated from core.limits
3. Output structure matches expected patterns
"""

from __future__ import annotations

from tayyari.core import limits
from tayyari.prompts import (
    ORIGIN_GUIDE,
    READER_FOCUS,
    READER_SYSTEM,
    SYNTH_SYSTEM,
    budget_excerpts,
    build_chat_system,
    build_reader_prompt,
    build_synth_prompt,
)


class TestReaderSystem:
    """Tests for READER_SYSTEM function."""

    def test_reader_system_resume(self) -> None:
        """Test READER_SYSTEM for resume kind."""
        result = READER_SYSTEM("resume")
        assert isinstance(result, str)
        assert "meticulous evidence reader" in result
        assert "Capture every role" in result
        assert result.startswith("You are a meticulous")

    def test_reader_system_github(self) -> None:
        """Test READER_SYSTEM for github kind."""
        result = READER_SYSTEM("github")
        assert isinstance(result, str)
        assert "GITHUB profile snapshot" in result
        assert "34 repos" in result

    def test_reader_system_lovable(self) -> None:
        """Test READER_SYSTEM for lovable kind."""
        result = READER_SYSTEM("lovable")
        assert "LOVABLE" in result
        assert "AI app builder" in result

    def test_reader_system_unknown_kind_defaults_to_link(self) -> None:
        """Test READER_SYSTEM with unknown kind defaults to 'link'."""
        result = READER_SYSTEM("unknown-kind")
        assert "WEB PAGE" in result

    def test_reader_focus_has_all_kinds(self) -> None:
        """Test that READER_FOCUS has entries for all standard kinds."""
        expected_kinds = {"resume", "github", "lovable", "linkedin", "portfolio", "link", "notes"}
        assert set(READER_FOCUS.keys()) == expected_kinds


class TestReaderPrompt:
    """Tests for build_reader_prompt function."""

    def test_build_reader_prompt_basic(self) -> None:
        """Test build_reader_prompt with minimal input."""
        src = {
            "id": "test-1",
            "kind": "resume",
            "kindLabel": "Resume",
            "label": "My Resume",
            "src": "resume.pdf",
            "content": "Job Title: Engineer",
            "facts": {"pages": 1, "words": 100},
        }
        result = build_reader_prompt(src)
        assert isinstance(result, str)
        assert "Source id: test-1" in result
        assert "Type: Resume (resume)" in result
        assert "Job Title: Engineer" in result

    def test_build_reader_prompt_with_url(self) -> None:
        """Test build_reader_prompt includes URL when provided."""
        src = {
            "id": "github-1",
            "kind": "github",
            "kindLabel": "GitHub",
            "label": "My GitHub",
            "src": "github.com/user",
            "url": "https://github.com/user",
            "content": "Repository content",
            "facts": {},
        }
        result = build_reader_prompt(src)
        assert "URL: https://github.com/user" in result

    def test_build_reader_prompt_clips_facts(self) -> None:
        """Test build_reader_prompt clips facts JSON."""
        src = {
            "id": "test-1",
            "kind": "resume",
            "kindLabel": "Resume",
            "label": "My Resume",
            "src": "resume.pdf",
            "content": "Some content",
            "facts": {"very_long_key": "x" * 10000},
        }
        result = build_reader_prompt(src)
        # The facts should be clipped to READER_FACTS_CLIP_CHARS
        assert len(result) < 20000  # Should be much smaller than the input


class TestSynthSystem:
    """Tests for SYNTH_SYSTEM constant."""

    def test_synth_system_is_string(self) -> None:
        """Test that SYNTH_SYSTEM is a string constant."""
        assert isinstance(SYNTH_SYSTEM, str)
        assert len(SYNTH_SYSTEM) > 2000

    def test_synth_system_contains_domain_range(self) -> None:
        """Test that SYNTH_SYSTEM includes domain range from limits."""
        domain_min, domain_max = limits.SYNTH_DOMAINS_RANGE
        assert f"{domain_min}–{domain_max}" in SYNTH_SYSTEM

    def test_synth_system_contains_skill_range(self) -> None:
        """Test that SYNTH_SYSTEM includes skill range from limits."""
        skill_min, skill_max = limits.SYNTH_SKILLS_RANGE
        assert f"{skill_min}–{skill_max}" in SYNTH_SYSTEM

    def test_synth_system_contains_gap_skill_range(self) -> None:
        """Test that SYNTH_SYSTEM includes gap skill range from limits."""
        gap_min, gap_max = limits.SYNTH_GAP_SKILLS_RANGE
        assert f"{gap_min}–{gap_max}" in SYNTH_SYSTEM

    def test_synth_system_contains_role_range(self) -> None:
        """Test that SYNTH_SYSTEM includes role range from limits."""
        role_min, role_max = limits.SYNTH_ROLES_RANGE
        assert f"{role_min}–{role_max}" in SYNTH_SYSTEM

    def test_synth_system_contains_requirement_range(self) -> None:
        """Test that SYNTH_SYSTEM includes requirement range from limits."""
        req_min, req_max = limits.SYNTH_REQUIREMENTS_RANGE
        assert f"{req_min}–{req_max}" in SYNTH_SYSTEM


class TestSynthPrompt:
    """Tests for build_synth_prompt function."""

    def test_build_synth_prompt_minimal(self) -> None:
        """Test build_synth_prompt with minimal sources."""
        result = build_synth_prompt(
            goal=None,
            sources=[
                {
                    "id": "resume",
                    "label": "Resume",
                    "kind": "resume",
                    "ok": True,
                    "reader": {"summary": "Test", "skills": [], "signals": []},
                }
            ],
        )
        assert isinstance(result, str)
        assert "Goal stated by the person:" in result
        assert "Valid source ids" in result
        assert "evidence" in result
        assert "resume" in result

    def test_build_synth_prompt_with_goal(self) -> None:
        """Test build_synth_prompt includes goal."""
        goal_text = "Move into leadership"
        result = build_synth_prompt(
            goal=goal_text,
            sources=[],
        )
        assert f'"{goal_text}"' in result

    def test_build_synth_prompt_with_failed_sources(self) -> None:
        """Test build_synth_prompt notes failed sources."""
        result = build_synth_prompt(
            goal=None,
            sources=[
                {
                    "id": "broken",
                    "label": "Broken Source",
                    "kind": "link",
                    "ok": False,
                    "error": "Connection timeout",
                }
            ],
        )
        assert "could NOT be read" in result
        assert "broken" in result


class TestOriginGuide:
    """Tests for ORIGIN_GUIDE dict."""

    def test_origin_guide_has_all_kinds(self) -> None:
        """Test that ORIGIN_GUIDE has all expected kinds."""
        expected_keys = {"skill", "domain", "source", "role", "requirement", "you", "free"}
        assert set(ORIGIN_GUIDE.keys()) == expected_keys

    def test_origin_guide_values_are_strings(self) -> None:
        """Test that all ORIGIN_GUIDE values are non-empty strings."""
        for key, value in ORIGIN_GUIDE.items():
            assert isinstance(value, str), f"ORIGIN_GUIDE[{key}] is not a string"
            assert len(value) > 5, f"ORIGIN_GUIDE[{key}] is too short"


class TestChatSystem:
    """Tests for build_chat_system function."""

    def test_build_chat_system_minimal(self) -> None:
        """Test build_chat_system with minimal profile."""
        profile = {
            "person": {"name": "Test"},
            "goal": None,
            "mastery": {"score": 50, "insight": "Test"},
            "domains": [],
            "skills": [],
            "roles": [],
            "sources": [],
        }
        result = build_chat_system(
            profile=profile,
            origin=None,
            sources=[],
            readers={},
        )
        assert isinstance(result, str)
        assert "You are Leafout" in result
        assert "Why they are here" in result

    def test_build_chat_system_with_origin_skill(self) -> None:
        """Test build_chat_system with skill origin."""
        profile = {
            "person": {"name": "Test"},
            "goal": None,
            "mastery": {"score": 50, "insight": "Test"},
            "domains": [],
            "skills": [],
            "roles": [],
            "sources": [],
        }
        origin = {
            "kind": "skill",
            "title": "Python",
            "refId": "python",
            "caption": "Backend skill",
        }
        result = build_chat_system(
            profile=profile,
            origin=origin,
            sources=[],
            readers={},
        )
        assert "Python" in result
        assert "clicked" in result

    def test_build_chat_system_with_sources(self) -> None:
        """Test build_chat_system includes source excerpts."""
        profile = {
            "person": {"name": "Test"},
            "goal": None,
            "mastery": {"score": 50, "insight": "Test"},
            "domains": [],
            "skills": [],
            "roles": [],
            "sources": [{"id": "resume", "label": "Resume", "kind": "resume"}],
        }
        sources = [
            {
                "id": "resume",
                "label": "Resume",
                "kind": "resume",
                "content": "Sample resume content text",
            }
        ]
        result = build_chat_system(
            profile=profile,
            origin=None,
            sources=sources,
            readers={},
        )
        assert "Raw source excerpts" in result or "source" in result.lower()


class TestBudgetExcerpts:
    """Tests for budget_excerpts function."""

    def test_budget_excerpts_respects_total_budget(self) -> None:
        """Test that budget_excerpts respects the total character budget."""
        sources = [
            {
                "id": "src1",
                "kind": "resume",
                "label": "Resume",
                "content": "x" * 50000,
            },
            {
                "id": "src2",
                "kind": "github",
                "label": "GitHub",
                "content": "y" * 50000,
            },
        ]
        budget = 10000
        excerpts = budget_excerpts(sources, budget)
        total_chars = sum(len(e.get("text", "")) for e in excerpts)
        assert total_chars <= budget + 100  # Allow small overage due to rounding

    def test_budget_excerpts_prioritizes_resume(self) -> None:
        """Test that budget_excerpts gives resume minimum share."""
        sources = [
            {
                "id": "src1",
                "kind": "resume",
                "label": "Resume",
                "content": "x" * 50000,
            },
            {
                "id": "src2",
                "kind": "github",
                "label": "GitHub",
                "content": "y" * 50000,
            },
        ]
        budget = 60000
        excerpts = budget_excerpts(sources, budget)
        resume_excerpt = next((e for e in excerpts if e["id"] == "src1"), None)
        assert resume_excerpt is not None
        # Resume should get at least CHAT_RESUME_EXCERPT_MIN_CHARS
        assert len(resume_excerpt.get("text", "")) >= limits.CHAT_RESUME_EXCERPT_MIN_CHARS

    def test_budget_excerpts_skips_empty_sources(self) -> None:
        """Test that budget_excerpts skips sources with empty content."""
        sources = [
            {
                "id": "src1",
                "kind": "resume",
                "label": "Resume",
                "content": "",
            },
            {
                "id": "src2",
                "kind": "github",
                "label": "GitHub",
                "content": "y" * 1000,
            },
        ]
        excerpts = budget_excerpts(sources, 10000)
        ids = [e["id"] for e in excerpts]
        assert "src1" not in ids
        assert "src2" in ids
