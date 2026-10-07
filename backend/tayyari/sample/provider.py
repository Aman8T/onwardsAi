"""Provider for the bundled sample persona: fixture loading and profile management."""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

from tayyari import contract
from tayyari.core import paths
from tayyari.sample import loader

__all__ = ["SampleProvider"]


class SampleProvider:
    """Loads the bundled sample persona (Riya Kapoor) from local fixtures.

    The sample persona is a source of truth for testing and demonstration. Its fixtures
    (resume.md, github.md, etc.) are fed to real readers and synthesizer; the pre-computed
    profile and context are served as the canonical "Try a sample profile" reference.
    """

    def __init__(self, sample_dir: Path | None = None) -> None:
        """Initialize the sample provider.

        Args:
            sample_dir: Path to the sample fixtures directory. Defaults to the bundled sample directory.
        """
        self._sample_dir = sample_dir or paths.SAMPLE_DIR

    def has_sample(self) -> bool:
        """Check if sample fixtures are present.

        Returns:
            True if sample-profile.json and sample-context.json exist in the sample directory.
        """
        profile_path = self._sample_dir / "sample-profile.json"
        context_path = self._sample_dir / "sample-context.json"
        return profile_path.exists() and context_path.exists()

    @cached_property
    def sample_profile(self) -> contract.Profile | None:
        """Load and validate the sample profile.

        Returns:
            The parsed and validated Profile, or None if fixtures are missing or invalid.
        """
        if not self.has_sample():
            return None

        try:
            text = loader.load_fixture("sample-profile.json", self._sample_dir)
            if text is None:
                return None
            data = json.loads(text)
            return contract.Profile.from_dict(data)
        except (json.JSONDecodeError, Exception):
            return None

    def sample_profile_id(self) -> str | None:
        """Get the sample profile ID.

        Returns:
            The profile ID if the sample profile exists, None otherwise.
        """
        profile = self.sample_profile
        return profile.id if profile else None

    def is_sample_profile(self, profile_id: str) -> bool:
        """Check if a profile ID belongs to the sample persona.

        Args:
            profile_id: The profile ID to check.

        Returns:
            True if the profile_id matches the sample profile's ID.
        """
        sample_id = self.sample_profile_id()
        return sample_id is not None and profile_id == sample_id

    @cached_property
    def sample_context(self) -> contract.StoredContext:
        """Load the sample context (sources and reader outputs).

        Returns:
            A StoredContext with sources and readers. Returns an empty context if fixtures
            are missing or invalid, failing gracefully.
        """
        if not self.has_sample():
            return contract.StoredContext()

        try:
            text = loader.load_fixture("sample-context.json", self._sample_dir)
            if text is None:
                return contract.StoredContext()
            data = json.loads(text)
            return contract.StoredContext.model_validate(data)
        except (json.JSONDecodeError, Exception):
            return contract.StoredContext()

    def sample_profile_path(self) -> Path | None:
        """Get the path to the sample profile JSON file.

        Returns:
            The Path to sample-profile.json, or None if it doesn't exist.
        """
        profile_path = self._sample_dir / "sample-profile.json"
        return profile_path if profile_path.exists() else None
