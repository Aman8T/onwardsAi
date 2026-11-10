"""The layering rule, enforced by scanning every import under ``tayyari/``.

    core < contract < llm < decisions < analysis / chat < api

A package may import itself and the packages it is allowed to see (`RULES`); nothing may import upwards, and
the top-level services (analysis, chat, portfolio) never import each other. The rest:

- ``storage`` depends on ``core`` and ``contract`` only (services see it through the Protocols in ``storage.ports``).
- ``decisions`` may import ``storage.ports`` (the decision log Protocol), nothing else of storage.
- ``guard`` may import ``storage.ports`` and ``llm.errors`` (it raises the spend-ceiling error), nothing else.
- ``discover`` is a self-contained service like ``billing``: ``core``, ``contract`` and ``storage.ports`` only.
- ``jobsearch`` (search with AI) sees what the other model-calling services see, plus ``discover``'s pure parts.
- ``weekly`` (The Leafout Weekly) sees the same, plus ``jobsearch``'s narrowing and checks and ``auth.email`` (the message type).
- ``container`` and ``cli`` are composition roots and may import everything; so may ``api``, which builds on them.
- ``legacy`` and ``canary`` (the replaced app, deleted in Wave 4) stay excluded in case an old checkout has them.

The scan reads the syntax tree, so imports inside functions and under ``if TYPE_CHECKING`` count too: a
lazy import is still a dependency.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from tayyari.core.paths import PKG_DIR

ROOT = PKG_DIR.name  # "tayyari"

EXCLUDED = ("legacy", "canary")
"""Top-level names that are not scanned (the live legacy app and the Wave 0c canary routes)."""

COMPOSITION_ROOTS = ("container", "cli")
"""May import anything: they are where the pieces are put together."""

_LOWER = ("core", "contract", "llm")
_SERVICES = (
    *_LOWER,
    "decisions",
    "prompts",
    "ingest",
    "sample",
    "storage.ports",
)

RULES: dict[str, tuple[str, ...]] = {
    "core": (),
    "contract": ("core",),
    "storage": ("core", "contract"),
    "llm": ("core", "contract"),
    "prompts": ("core", "contract"),
    "sample": ("core", "contract", "ingest"),
    # ingest reads the sample fixtures for the bundled persona.
    "ingest": ("core", "sample"),
    "guard": ("core", "llm.errors", "storage.ports"),
    "decisions": ("core", "contract", "llm", "storage.ports"),
    "analysis": _SERVICES,
    "chat": _SERVICES,
    "portfolio": _SERVICES,
    # accounts: Google and email sign-in, sessions; reaches storage only through its Protocols.
    "auth": ("core", "contract", "storage.ports"),
    # payments and entitlements: Razorpay, Stripe, the ledger rules; reaches storage only through its Protocols.
    "billing": ("core", "contract", "storage.ports"),
    # job discovery over employers' own boards: a self-contained service (its own tables, settings and HTTP client); it
    # never imports match, analysis or any other service, and reaches storage only through its Protocols.
    "discover": ("core", "contract", "storage.ports"),
    # posting match reuses the portfolio's line-cited fact extractor and its sentence anchors (no fork).
    "match": (*_SERVICES, "portfolio"),
    # search with AI reuses Discover's board fetcher, parsing, places, vocabulary and quick fit (no fork), and the
    # portfolio's sentence anchors to check a referral note; it never imports match or analysis, and discover never
    # imports it.
    "jobsearch": (*_SERVICES, "discover", "portfolio"),
    # practice interviews read a role from search with AI (through a Protocol), its evidence lines and quote checks, and
    # the portfolio's sentence anchors to check a "Say" line; nothing imports interview except api and the container.
    "interview": (*_SERVICES, "jobsearch", "discover", "portfolio"),
    # speech for practice interviews (Sarvam AI through its SDK): a self-contained service like discover, with its own
    # settings and meter; it reaches storage only through its Protocols and never imports interview or any other service.
    "speech": ("core", "contract", "storage.ports"),
    # prepare for a role sits with the model-calling services and reuses the posting match's schema helpers (`schema_of`,
    # `rows_of`; no fork); nothing imports prepare except api and the container.
    "prepare": (*_SERVICES, "match"),
    # the hand-off from the marketing site: a self-contained service like auth; it reaches storage only through its Protocols.
    "handoff": ("core", "contract", "storage.ports"),
    # the radar card: a self-contained service like the hand-off (no model); it reaches storage only through its Protocols.
    "card": ("core", "contract", "storage.ports"),
    # The Leafout Weekly sits with the model-calling services: it reuses the job search's narrowing, judge prompt and checks
    # and Discover's quick fit (no fork), and the email message type of auth (a sender is handed in; it imports no other part of
    # auth). Nothing imports weekly except api and the container; the spend guard is handed in as a callable, not imported.
    "weekly": (*_SERVICES, "discover", "jobsearch", "auth.email"),
    # auto-apply sits with the model-calling services: the SSRF-guarded fetch of ingest, the Claude client for field mapping,
    # screening answers and email stages, and the portfolio's sentence anchors to check an answer against the facts it cites.
    # A match reaches it through a Protocol the api layer implements (it never imports match); nothing imports apply except
    # api, the container and the CLI.
    "apply": (*_SERVICES, "portfolio"),
    # /resume (one set of blocks, many resumes) sits beside match and reuses its fact book, selection, verifier, export and
    # lint (no fork); nothing imports resume except api and the container.
    "resume": (*_SERVICES, "match"),
    # api sits on top: everything except the CLI (and the legacy app).
    "api": (
        "core",
        "contract",
        "llm",
        "decisions",
        "analysis",
        "chat",
        "portfolio",
        "auth",
        "billing",
        "match",
        "discover",
        "jobsearch",
        "interview",
        "speech",
        "prepare",
        "handoff",
        "weekly",
        "card",
        "apply",
        "resume",
        "guard",
        "ingest",
        "prompts",
        "sample",
        "storage",
        "container",
    ),
}
"""Top-level package -> the module prefixes (below ``tayyari.``) it may import besides itself."""


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    importer: str
    imported: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.importer} must not import {self.imported}"


# ───────────────────────────── scanning ─────────────────────────────


def module_files(package_root: Path) -> list[Path]:
    return sorted(package_root.rglob("*.py"))


def module_name(package_root: Path, path: Path) -> str:
    parts = list(path.relative_to(package_root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def known_modules(package_root: Path) -> set[str]:
    return {module_name(package_root, path) for path in module_files(package_root)}


def imported_modules(package_root: Path, path: Path, known: set[str]) -> list[tuple[int, str]]:
    """``(line, absolute module name)`` for every first-party import in ``path``.

    ``from tayyari.storage import ports`` names the submodule ``tayyari.storage.ports`` (when every imported
    name is a submodule), so a rule about ``storage.ports`` sees what the line really loads.
    """
    root = package_root.name
    me = module_name(package_root, path)
    package_parts = me.split(".") if path.name == "__init__.py" else me.split(".")[:-1]
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                target = ".".join([*base, *([node.module] if node.module else [])])
            else:
                target = node.module or ""
            submodules = [
                f"{target}.{alias.name}" for alias in node.names if f"{target}.{alias.name}" in known
            ]
            if submodules and len(submodules) == len(node.names):
                found.extend((node.lineno, name) for name in submodules)
            else:
                found.append((node.lineno, target))
                found.extend((node.lineno, name) for name in submodules)
    return [(line, name) for line, name in found if name == root or name.startswith(f"{root}.")]


def top_level(root: str, module: str) -> str | None:
    """``core`` for ``tayyari.core.settings``; None for the root package itself."""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == root else None


def allowed(root: str, importer_top: str, imported: str) -> bool:
    target_top = top_level(root, imported)
    if target_top is None or target_top == importer_top:  # the root package, or its own package
        return True
    if importer_top in COMPOSITION_ROOTS:
        return True
    return any(
        imported == f"{root}.{prefix}" or imported.startswith(f"{root}.{prefix}.")
        for prefix in RULES[importer_top]
    )


def scan(package_root: Path) -> list[Violation]:
    """Every import under ``package_root`` that the rules do not allow."""
    root = package_root.name
    known = known_modules(package_root)
    violations: list[Violation] = []
    for path in module_files(package_root):
        importer = module_name(package_root, path)
        importer_top = top_level(root, importer)
        if importer_top is None:  # tayyari/__init__.py: the root package
            importer_top = "__root__"
        elif importer_top in EXCLUDED:
            continue
        if importer_top not in RULES and importer_top not in COMPOSITION_ROOTS and importer_top != "__root__":
            violations.append(
                Violation(str(path), 0, importer, f"(no layering rule for package {importer_top!r})")
            )
            continue
        for line, imported in imported_modules(package_root, path, known):
            if importer_top == "__root__":
                ok = top_level(root, imported) in (None, "core")
            else:
                ok = allowed(root, importer_top, imported)
            if not ok:
                violations.append(
                    Violation(str(path.relative_to(package_root.parent)), line, importer, imported)
                )
    return violations


# ───────────────────────────── the real tree ─────────────────────────────


def test_the_package_respects_the_layering_rule() -> None:
    violations = scan(PKG_DIR)
    assert not violations, "layering violations:\n" + "\n".join(str(v) for v in violations)


def test_the_scan_actually_sees_the_package() -> None:
    """Guards against a vacuous pass (a wrong path or a broken parser would find nothing to complain about)."""
    known = known_modules(PKG_DIR)
    assert len(known) > 100
    assert {"tayyari.core.settings", "tayyari.storage.ports", "tayyari.api.app", "tayyari.container"} <= known

    app = PKG_DIR / "api" / "app.py"
    imports = {name for _, name in imported_modules(PKG_DIR, app, known)}
    assert "tayyari.container" in imports  # absolute import
    assert "tayyari.api.routes" in imports

    lazy = {name for _, name in imported_modules(PKG_DIR, PKG_DIR / "container.py", known)}
    assert "tayyari.analysis.service" in lazy  # an import inside a function counts


def test_every_package_has_a_rule() -> None:
    tops = {
        path.name
        for path in PKG_DIR.iterdir()
        if (path.is_dir() and (path / "__init__.py").exists())
        or (path.suffix == ".py" and path.stem != "__init__")
    }
    names = {name.removesuffix(".py") for name in tops}
    unruled = names - set(RULES) - set(COMPOSITION_ROOTS) - set(EXCLUDED)
    assert not unruled, f"add a layering rule for: {sorted(unruled)}"


def test_the_rules_only_name_packages_that_exist() -> None:
    tops = {path.name.removesuffix(".py") for path in PKG_DIR.iterdir()}
    for importer, prefixes in RULES.items():
        assert importer in tops, importer
        for prefix in prefixes:
            assert prefix.split(".")[0] in tops, f"{importer} may import {prefix}, which does not exist"


def test_no_lower_layer_may_see_a_higher_one() -> None:
    """The stated order, checked against the table itself (not only against today's imports)."""
    order = ["core", "contract", "llm", "decisions", "analysis", "chat", "api"]
    rank = {"core": 0, "contract": 1, "llm": 2, "decisions": 3, "analysis": 4, "chat": 4, "api": 5}
    for importer in order:
        for prefix in RULES[importer]:
            target = prefix.split(".")[0]
            if target in rank and target != importer:
                assert rank[target] < rank[importer], f"{importer} may import {prefix}"
    assert "chat" not in RULES["analysis"]
    assert "analysis" not in RULES["chat"]
    assert "cli" not in RULES["api"]
    assert not any(prefix.split(".")[0] == "api" for prefixes in RULES.values() for prefix in prefixes)


# ───────────────────────────── the checker itself ─────────────────────────────


def make_package(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / ROOT
    for relative, source in {"__init__.py": "", **files}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


def violations_of(tmp_path: Path, files: dict[str, str]) -> list[str]:
    root = make_package(tmp_path, files)
    return [f"{v.importer} -> {v.imported}" for v in scan(root)]


def test_the_checker_flags_an_upward_import(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "core/__init__.py": "",
            "core/a.py": "from tayyari.llm import x\n",
            "llm/__init__.py": "",
            "llm/x.py": "",
        },
    )
    assert found == ["tayyari.core.a -> tayyari.llm.x"]


def test_the_checker_resolves_relative_imports(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "contract/__init__.py": "",
            "contract/a.py": "from ..api import routes\nfrom . import b\nfrom .b import thing\n",
            "contract/b.py": "thing = 1\n",
            "api/__init__.py": "",
            "api/routes.py": "",
        },
    )
    assert found == ["tayyari.contract.a -> tayyari.api.routes"]


def test_the_checker_sees_imports_inside_functions_and_type_checking_blocks(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "llm/__init__.py": "",
            "llm/a.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    from tayyari.decisions import spec\n"
                "def f():\n"
                "    import tayyari.api.app\n"
            ),
            "decisions/__init__.py": "",
            "decisions/spec.py": "",
            "api/__init__.py": "",
            "api/app.py": "",
        },
    )
    assert sorted(found) == ["tayyari.llm.a -> tayyari.api.app", "tayyari.llm.a -> tayyari.decisions.spec"]


def test_decisions_and_guard_may_use_storage_ports_but_not_the_rest_of_storage(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "storage/__init__.py": "",
            "storage/ports.py": "",
            "storage/repos.py": "",
            "decisions/__init__.py": "",
            "decisions/recorder.py": "from tayyari.storage.ports import X\nfrom tayyari.storage import ports\n",
            "decisions/bad.py": "from tayyari.storage.repos import Y\n",
            "guard/__init__.py": "",
            "guard/spend.py": "from ..storage.ports import X\nfrom tayyari.llm.errors import E\n",
            "guard/bad.py": "from tayyari.llm.claude import C\nfrom tayyari.storage import repos\n",
            "llm/__init__.py": "",
            "llm/errors.py": "",
            "llm/claude.py": "",
        },
    )
    assert sorted(found) == [
        "tayyari.decisions.bad -> tayyari.storage.repos",
        "tayyari.guard.bad -> tayyari.llm.claude",
        "tayyari.guard.bad -> tayyari.storage.repos",
    ]


def test_storage_may_not_import_llm_or_decisions(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "storage/__init__.py": "",
            "storage/a.py": "from tayyari.core import x\nfrom tayyari.contract import y\nfrom tayyari.llm import z\n",
            "core/__init__.py": "",
            "core/x.py": "",
            "contract/__init__.py": "",
            "contract/y.py": "",
            "llm/__init__.py": "",
            "llm/z.py": "",
        },
    )
    assert found == ["tayyari.storage.a -> tayyari.llm.z"]


def test_analysis_and_chat_do_not_import_each_other(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "analysis/__init__.py": "",
            "analysis/a.py": "from tayyari.chat import b\nfrom tayyari.analysis import c\n",
            "analysis/c.py": "",
            "chat/__init__.py": "",
            "chat/b.py": "from tayyari.analysis import a\n",
        },
    )
    assert sorted(found) == ["tayyari.analysis.a -> tayyari.chat.b", "tayyari.chat.b -> tayyari.analysis.a"]


def test_composition_roots_and_api_may_import_everything_below_them(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "container.py": "from tayyari.analysis import a\nfrom tayyari.storage import s\nfrom tayyari.api import app\n",
            "cli/__init__.py": "",
            "cli/run.py": "from tayyari.container import Container\nfrom tayyari.api.app import create_app\n",
            "api/__init__.py": "",
            "api/app.py": "from tayyari.container import Container\nfrom tayyari.guard import g\nfrom tayyari.cli import run\n",
            "analysis/__init__.py": "",
            "analysis/a.py": "",
            "storage/__init__.py": "",
            "storage/s.py": "",
            "guard/__init__.py": "",
            "guard/g.py": "",
        },
    )
    assert found == ["tayyari.api.app -> tayyari.cli.run"]


def test_discover_sees_only_core_contract_and_the_storage_protocols(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "core/__init__.py": "",
            "core/clock.py": "",
            "contract/__init__.py": "",
            "contract/discover.py": "",
            "storage/__init__.py": "",
            "storage/ports.py": "",
            "storage/repos.py": "",
            "match/__init__.py": "",
            "match/service.py": "",
            "discover/__init__.py": "",
            "discover/ok.py": (
                "from tayyari.core import clock\n"
                "from tayyari.contract import discover\n"
                "from tayyari.storage.ports import X\n"
                "from tayyari.storage import ports\n"
            ),
            "discover/bad_storage.py": "from tayyari.storage.repos import Y\n",
            "discover/bad_match.py": "from tayyari.match import service\n",
        },
    )
    assert sorted(found) == [
        "tayyari.discover.bad_match -> tayyari.match.service",
        "tayyari.discover.bad_storage -> tayyari.storage.repos",
    ]


def test_nothing_below_the_composition_roots_may_import_them(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "analysis/__init__.py": "",
            "analysis/a.py": "from tayyari.container import Container\n",
            "container.py": "",
        },
    )
    assert found == ["tayyari.analysis.a -> tayyari.container"]


def test_the_legacy_app_and_the_canary_are_not_scanned(tmp_path: Path) -> None:
    found = violations_of(
        tmp_path,
        {
            "legacy/__init__.py": "",
            "legacy/server.py": "from tayyari.api import app\nfrom ..ingest import x\n",
            "canary.py": "from tayyari.storage import s\n",
            "api/__init__.py": "",
            "api/app.py": "",
            "ingest/__init__.py": "",
            "ingest/x.py": "",
            "storage/__init__.py": "",
            "storage/s.py": "",
        },
    )
    assert found == []


def test_a_new_top_level_package_without_a_rule_is_reported(tmp_path: Path) -> None:
    found = violations_of(tmp_path, {"mystery/__init__.py": "", "mystery/a.py": "x = 1\n"})
    assert found and all("no layering rule for package 'mystery'" in entry for entry in found)


def test_the_root_package_may_only_import_core(tmp_path: Path) -> None:
    root = make_package(tmp_path, {"core/__init__.py": "", "core/logging.py": "", "llm/__init__.py": ""})
    (root / "__init__.py").write_text("from .core.logging import x\nfrom .llm import y\n", encoding="utf-8")
    assert [(v.importer, v.imported) for v in scan(root)] == [("tayyari", "tayyari.llm")]


@pytest.mark.parametrize("package", sorted(RULES))
def test_every_rule_names_an_importer_that_is_a_package(package: str) -> None:
    assert (PKG_DIR / package / "__init__.py").exists()
