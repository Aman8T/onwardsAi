from __future__ import annotations

from tayyari.core import paths


def test_folders_are_where_the_layout_says() -> None:
    assert paths.PKG_DIR.name == "tayyari"
    assert (paths.PKG_DIR / "core" / "paths.py").is_file()
    assert paths.BACKEND_DIR == paths.PKG_DIR.parent
    assert (paths.BACKEND_DIR / "main.py").is_file()
    assert (paths.BACKEND_DIR / "pyproject.toml").is_file()
    assert paths.REPO_ROOT == paths.BACKEND_DIR.parent
    assert paths.STATIC_DIR == paths.BACKEND_DIR / "static"
    assert paths.SAMPLE_DIR == paths.PKG_DIR / "sample"


def test_paths_are_absolute() -> None:
    for path in (paths.PKG_DIR, paths.BACKEND_DIR, paths.REPO_ROOT, paths.STATIC_DIR, paths.SAMPLE_DIR):
        assert path.is_absolute()
