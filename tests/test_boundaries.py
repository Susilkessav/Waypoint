"""T33 - nothing under waypoint/ may import target_app.

The target app is a fixture. If Waypoint ever imports it, the claim that the
engine is independent of any one surface is false, and every generalisation
argument in REPORT.md collapses. This test is the mechanical guard on that.

See README.md, Repository tour.
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN_ROOT = "target_app"


def _imports_forbidden_module(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(
            alias.name == FORBIDDEN_ROOT or alias.name.startswith(f"{FORBIDDEN_ROOT}.")
            for alias in node.names
        )
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return module == FORBIDDEN_ROOT or module.startswith(f"{FORBIDDEN_ROOT}.")
    return False


def test_waypoint_does_not_import_target_app() -> None:
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "waypoint").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _imports_forbidden_module(node):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{getattr(node, 'lineno', '?')}")

    assert not offenders, (
        "waypoint/ must not import target_app (it is a test fixture, not a dependency). "
        f"Offending imports: {', '.join(offenders)}"
    )
