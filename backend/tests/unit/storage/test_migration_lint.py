"""Additive-only lint for migrations.

During a rollout the old and the new version of the app run against the same database, so a migration must
never remove or reshape something the old version still uses. After the initial revision a migration may
only add (tables, nullable columns, indexes). A destructive step is a deliberate second release (the
"contract phase") and its file must carry the marker comment below.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tayyari.storage.migrate import MIGRATIONS_DIR

CONTRACT_MARKER = "# contract-phase"
FORBIDDEN_EXACT = {"alter_column"}
FORBIDDEN_PREFIXES = ("drop_", "rename")
SAMPLE_NAMES = ("drop_table", "drop_column", "drop_index", "drop_constraint", "alter_column", "rename_table")


def is_initial(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign | ast.Assign)
            and ast.unparse(node.targets[0] if isinstance(node, ast.Assign) else node.target)
            == "down_revision"
            and isinstance(node.value, ast.Constant)
            and node.value.value is None
        ):
            return True
    return False


def destructive_calls(source: str) -> list[str]:
    """Names of destructive operations called in ``upgrade()`` (``op.drop_table``, ``batch_op.alter_column`` ...)."""
    tree = ast.parse(source)
    found: list[str] = []
    for func in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "upgrade"):
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in FORBIDDEN_EXACT or name.startswith(FORBIDDEN_PREFIXES):
                found.append(name)
    return found


def violations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    if is_initial(ast.parse(source)) or CONTRACT_MARKER in source:
        return []
    return [f"{path.name}: {name}" for name in destructive_calls(source)]


def test_no_migration_after_the_first_is_destructive() -> None:
    files = sorted((MIGRATIONS_DIR / "versions").glob("*.py"))
    assert files, "no migrations found"
    problems = [problem for path in files for problem in violations(path)]
    assert problems == [], (
        "migrations after 0001 must be additive; mark a deliberate contract-phase migration with "
        f"'{CONTRACT_MARKER}': {problems}"
    )


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_the_lint_flags_destructive_operations(name: str, tmp_path: Path) -> None:
    path = tmp_path / "0002_bad.py"
    path.write_text(
        f'revision = "0002"\ndown_revision = "0001"\n\ndef upgrade():\n    op.{name}("x")\n', encoding="utf-8"
    )
    assert violations(path) == [f"0002_bad.py: {name}"]


def test_the_lint_flags_batch_operations(tmp_path: Path) -> None:
    path = tmp_path / "0002_bad.py"
    path.write_text(
        "revision = '0002'\ndown_revision = '0001'\n\n"
        "def upgrade():\n    with op.batch_alter_table('t') as batch_op:\n        batch_op.drop_column('c')\n",
        encoding="utf-8",
    )
    assert violations(path) == ["0002_bad.py: drop_column"]


def test_the_lint_allows_additive_migrations(tmp_path: Path) -> None:
    path = tmp_path / "0002_ok.py"
    path.write_text(
        "revision = '0002'\ndown_revision = '0001'\n\n"
        "def upgrade():\n"
        "    op.add_column('t', sa.Column('c', sa.Text(), nullable=True))\n"
        "    op.create_index('ix', 't', ['c'])\n"
        "    op.create_table('u')\n\n"
        "def downgrade():\n    op.drop_column('t', 'c')\n",
        encoding="utf-8",
    )
    assert violations(path) == []  # downgrade() may undo what upgrade() added


def test_the_lint_respects_the_contract_phase_marker(tmp_path: Path) -> None:
    path = tmp_path / "0003_contract.py"
    path.write_text(
        f"{CONTRACT_MARKER}: the old column has been unused since release 12\n"
        "revision = '0003'\ndown_revision = '0002'\n\ndef upgrade():\n    op.drop_column('t', 'old')\n",
        encoding="utf-8",
    )
    assert violations(path) == []


def test_the_initial_revision_is_exempt(tmp_path: Path) -> None:
    path = tmp_path / "0001_initial.py"
    path.write_text(
        "revision: str = '0001'\ndown_revision: str | None = None\n\ndef upgrade():\n    op.drop_table('x')\n",
        encoding="utf-8",
    )
    assert violations(path) == []
