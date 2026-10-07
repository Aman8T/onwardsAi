"""Tests for the sample provider."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tayyari import contract
from tayyari.sample.provider import SampleProvider


@pytest.fixture
def sample_provider() -> SampleProvider:
    """Get the provider with real sample fixtures."""
    from tayyari.core import paths

    return SampleProvider(sample_dir=paths.SAMPLE_DIR)


@pytest.fixture
def temp_sample_dir() -> Path:
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestSampleProviderWithRealFixtures:
    """Tests against the real bundled sample fixtures."""

    def test_has_sample(self, sample_provider: SampleProvider) -> None:
        """The provider detects when real fixtures are present."""
        assert sample_provider.has_sample() is True

    def test_sample_profile_loads(self, sample_provider: SampleProvider) -> None:
        """The sample profile loads and is valid."""
        profile = sample_provider.sample_profile
        assert profile is not None
        assert isinstance(profile, contract.Profile)
        assert profile.id.startswith("sample-")
        assert "Riya" in profile.person.name

    def test_sample_profile_is_cached(self, sample_provider: SampleProvider) -> None:
        """The sample profile is cached after first access."""
        profile1 = sample_provider.sample_profile
        profile2 = sample_provider.sample_profile
        assert profile1 is profile2

    def test_sample_profile_id(self, sample_provider: SampleProvider) -> None:
        """The profile ID can be retrieved."""
        profile_id = sample_provider.sample_profile_id()
        assert profile_id is not None
        assert profile_id.startswith("sample-")

    def test_is_sample_profile_with_matching_id(self, sample_provider: SampleProvider) -> None:
        """is_sample_profile returns True for the sample profile ID."""
        profile_id = sample_provider.sample_profile_id()
        assert sample_provider.is_sample_profile(profile_id) is True

    def test_is_sample_profile_with_non_matching_id(self, sample_provider: SampleProvider) -> None:
        """is_sample_profile returns False for other IDs."""
        assert sample_provider.is_sample_profile("some-random-id") is False
        assert sample_provider.is_sample_profile("sample-different-id") is False

    def test_sample_context_loads(self, sample_provider: SampleProvider) -> None:
        """The sample context loads and has sources and readers."""
        context = sample_provider.sample_context
        assert isinstance(context, contract.StoredContext)
        assert len(context.sources) > 0
        assert len(context.readers) > 0

    def test_sample_context_has_expected_sources(self, sample_provider: SampleProvider) -> None:
        """The sample context has the expected sources."""
        context = sample_provider.sample_context
        source_ids = {s.id for s in context.sources}
        expected_ids = {"resume", "github", "lovable-1", "portfolio-1"}
        assert expected_ids.issubset(source_ids)

    def test_sample_context_sources_have_content(self, sample_provider: SampleProvider) -> None:
        """All sources in the sample context have content."""
        context = sample_provider.sample_context
        for source in context.sources:
            assert source.content
            assert len(source.content) > 100

    def test_sample_context_readers_have_outputs(self, sample_provider: SampleProvider) -> None:
        """The sample context has reader outputs for key sources."""
        context = sample_provider.sample_context
        # At least resume reader should be present
        assert "resume" in context.readers or len(context.readers) > 0

    def test_sample_profile_path(self, sample_provider: SampleProvider) -> None:
        """The profile path points to the correct file."""
        path = sample_provider.sample_profile_path()
        assert path is not None
        assert path.exists()
        assert path.name == "sample-profile.json"

    def test_profile_validates(self, sample_provider: SampleProvider) -> None:
        """The loaded profile validates against the contract."""
        profile = sample_provider.sample_profile
        assert profile is not None
        # Profile should have required fields
        assert profile.id
        assert profile.createdAt
        assert profile.person
        assert profile.mastery
        assert profile.sources
        assert isinstance(profile.sources, list)


class TestSampleProviderWithMissingFixtures:
    """Tests with missing or invalid fixtures."""

    def test_has_sample_returns_false_for_missing_dir(self, temp_sample_dir: Path) -> None:
        """has_sample returns False when the directory doesn't exist."""
        missing_dir = temp_sample_dir / "nonexistent"
        provider = SampleProvider(sample_dir=missing_dir)
        assert provider.has_sample() is False

    def test_sample_profile_returns_none_for_missing_fixtures(self, temp_sample_dir: Path) -> None:
        """sample_profile returns None when fixtures are missing."""
        provider = SampleProvider(sample_dir=temp_sample_dir)
        assert provider.sample_profile is None

    def test_sample_profile_id_returns_none_for_missing_fixtures(self, temp_sample_dir: Path) -> None:
        """sample_profile_id returns None when fixtures are missing."""
        provider = SampleProvider(sample_dir=temp_sample_dir)
        assert provider.sample_profile_id() is None

    def test_is_sample_profile_returns_false_for_missing_fixtures(self, temp_sample_dir: Path) -> None:
        """is_sample_profile returns False when fixtures are missing."""
        provider = SampleProvider(sample_dir=temp_sample_dir)
        assert provider.is_sample_profile("any-id") is False

    def test_sample_context_returns_empty_for_missing_fixtures(self, temp_sample_dir: Path) -> None:
        """sample_context returns an empty context when fixtures are missing."""
        provider = SampleProvider(sample_dir=temp_sample_dir)
        context = provider.sample_context
        assert isinstance(context, contract.StoredContext)
        assert len(context.sources) == 0
        assert len(context.readers) == 0

    def test_sample_profile_path_returns_none_for_missing_file(self, temp_sample_dir: Path) -> None:
        """sample_profile_path returns None when the file doesn't exist."""
        provider = SampleProvider(sample_dir=temp_sample_dir)
        assert provider.sample_profile_path() is None

    def test_sample_profile_returns_none_for_invalid_json(self, temp_sample_dir: Path) -> None:
        """sample_profile returns None when JSON is invalid."""
        profile_file = temp_sample_dir / "sample-profile.json"
        context_file = temp_sample_dir / "sample-context.json"
        # Create both files
        profile_file.write_text("{invalid json")
        context_file.write_text("{}")

        provider = SampleProvider(sample_dir=temp_sample_dir)
        assert provider.sample_profile is None

    def test_sample_context_returns_empty_for_invalid_json(self, temp_sample_dir: Path) -> None:
        """sample_context returns empty context when JSON is invalid."""
        profile_file = temp_sample_dir / "sample-profile.json"
        context_file = temp_sample_dir / "sample-context.json"
        # Create both files
        profile_file.write_text("{}")
        context_file.write_text("{invalid json")

        provider = SampleProvider(sample_dir=temp_sample_dir)
        context = provider.sample_context
        assert isinstance(context, contract.StoredContext)
        assert len(context.sources) == 0


class TestLegacyImports:
    """Tests that legacy imports still work."""

    def test_legacy_imports_from_init(self) -> None:
        """Legacy names are still importable from tayyari.sample."""
        from tayyari.sample import SAMPLE_DIR, SAMPLE_GOAL, SAMPLE_INGEST_LOGS, load_sample_sources

        assert SAMPLE_DIR.exists()
        assert SAMPLE_DIR.name == "sample"
        assert isinstance(SAMPLE_GOAL, str)
        assert "AI product" in SAMPLE_GOAL
        assert isinstance(SAMPLE_INGEST_LOGS, dict)
        assert "resume" in SAMPLE_INGEST_LOGS
        assert callable(load_sample_sources)

    def test_load_sample_sources(self) -> None:
        """load_sample_sources returns a list of SourceDoc objects."""
        from tayyari.ingest.classify import SourceDoc
        from tayyari.sample import load_sample_sources

        sources = load_sample_sources()
        assert len(sources) == 4
        assert all(isinstance(s, SourceDoc) for s in sources)
        assert sources[0].id == "resume"
        assert sources[1].id == "github"
        assert sources[2].id == "lovable-1"
        assert sources[3].id == "portfolio-1"
