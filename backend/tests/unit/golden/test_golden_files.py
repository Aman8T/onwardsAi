"""
Test that captured golden baselines match committed files.

Runs the capture_golden script into a temporary directory and asserts
that every generated file matches the committed one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _golden_dir() -> Path:
    """Path to committed golden baseline files."""
    return Path(__file__).parent.parent.parent / "golden" / "legacy"


def _can_import_legacy() -> bool:
    """Check if tayyari.legacy can be imported."""
    try:
        import tayyari.legacy  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _can_import_legacy(), reason="tayyari.legacy not available")
def _normalize_output(output: Any) -> Any:
    """Normalize outputs by removing non-deterministic fields."""
    if isinstance(output, dict):
        # Remove non-deterministic fields from profile outputs
        output = dict(output)
        output.pop("id", None)
        output.pop("createdAt", None)
        # Recursively normalize nested structures
        for key, value in output.items():
            output[key] = _normalize_output(value)
        return output
    elif isinstance(output, list):
        return [_normalize_output(item) for item in output]
    else:
        return output


def test_golden_files_match(tmp_path: Path) -> None:
    """Run capture_golden into tmp_path and assert each file matches the committed baseline."""
    # Run capture_golden
    repo_root = Path(__file__).parent.parent.parent.parent.parent
    capture_script = repo_root / "backend" / "tools" / "capture_golden.py"

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(capture_script), "--out", str(tmp_path / "golden")],
        cwd=str(repo_root / "backend"),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        pytest.fail(f"capture_golden.py failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    # Check that each file matches
    golden_dir = _golden_dir()
    assert golden_dir.exists(), (
        f"Golden baseline directory {golden_dir} does not exist. Run capture_golden.py first."
    )

    expected_files = [
        "prompts.json",
        "clean_reader.json",
        "to_profile.json",
        "activity_lines.json",
        "humanize_error.json",
    ]

    for filename in expected_files:
        committed_path = golden_dir / filename
        generated_path = tmp_path / "golden" / filename

        assert committed_path.exists(), f"Committed file {committed_path} does not exist"
        assert generated_path.exists(), f"Generated file {generated_path} does not exist"

        # Compare as JSON to ignore whitespace differences
        committed = json.loads(committed_path.read_text(encoding="utf-8"))
        generated = json.loads(generated_path.read_text(encoding="utf-8"))

        # Normalize outputs to remove non-deterministic fields
        committed_normalized = {
            "cases": [
                {"name": c["name"], "input": c["input"], "output": _normalize_output(c["output"])}
                for c in committed.get("cases", [])
            ]
        }
        generated_normalized = {
            "cases": [
                {"name": c["name"], "input": c["input"], "output": _normalize_output(c["output"])}
                for c in generated.get("cases", [])
            ]
        }

        assert committed_normalized == generated_normalized, (
            f"Golden file {filename} does not match:\nCommitted has {len(committed.get('cases', []))} cases, generated has {len(generated.get('cases', []))} cases"
        )


def test_golden_files_have_minimum_cases() -> None:
    """Assert that each committed golden file has at least the minimum number of cases."""
    golden_dir = _golden_dir()

    if not golden_dir.exists():
        pytest.skip(f"Golden directory {golden_dir} does not exist. Run capture_golden.py first.")

    minimums = {
        "prompts.json": 15,  # at least 7 READER_SYSTEM + SYNTH_SYSTEM + a few builds
        "clean_reader.json": 5,
        "to_profile.json": 5,
        "activity_lines.json": 1,
        "humanize_error.json": 5,
    }

    for filename, min_cases in minimums.items():
        path = golden_dir / filename
        assert path.exists(), f"Golden file {path} does not exist"

        data = json.loads(path.read_text(encoding="utf-8"))
        cases = data.get("cases", [])

        assert len(cases) >= min_cases, (
            f"Golden file {filename} has {len(cases)} cases, expected at least {min_cases}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
