"""The app uses the core only through `gradeflow_core` and `gradeflow_core.editing`."""

import ast
import pkgutil
from pathlib import Path

import gradeflow_core
import gradeflow_core.editing

PUBLIC = {"gradeflow_core", "gradeflow_core.editing"}
APP = Path(__file__).parents[1]
SUBMODULES = {
    f"{package.__name__}.{module.name}"
    for package in (gradeflow_core, gradeflow_core.editing)
    for module in pkgutil.iter_modules(package.__path__)
}


def imported_modules(tree: ast.AST) -> list[str]:
    """Each core module a file imports, counting `from gradeflow_core import x` when `x` is a submodule."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
            found += [name for alias in node.names if (name := f"{node.module}.{alias.name}") in SUBMODULES]
    return [module for module in found if module.split(".")[0] == "gradeflow_core"]


def test_the_app_imports_only_the_cores_public_modules() -> None:
    files = [path for folder in ("src", "tests", "sandbox") for path in (APP / folder).rglob("*.py")]
    internal = [
        f"{path.relative_to(APP)}: {module}"
        for path in files
        for module in imported_modules(ast.parse(path.read_text()))
        if module not in PUBLIC
    ]
    assert not internal
