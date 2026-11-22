"""Import boundaries of the product package (CONTRIBUTING.md, *Rules that decide a change*).

- Only ``sva2yosys.slang`` imports ``pyslang``.
- Nothing in ``src/`` imports test infrastructure (``support``, ``tests``): the test
  oracles (reference evaluator, machine simulator) stay independent of the product.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "sva2yosys"
MODULES = sorted(SRC.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_the_package_is_found() -> None:
    assert len(MODULES) > 20


@pytest.mark.parametrize("path", MODULES, ids=[str(p.relative_to(SRC)) for p in MODULES])
def test_import_boundaries(path: Path) -> None:
    imported = _imports(path)
    if path.relative_to(SRC).parts[0] != "slang":
        assert "pyslang" not in imported
    assert not imported & {"support", "tests"}


def test_the_reference_does_not_import_the_machinery_it_checks() -> None:
    """The oracle in tests/support/reference imports IR types only: never the temporal
    lowering and machines, the optimised forms or the code generator."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "support" / "reference"
    pattern = re.compile(r"^\s*(?:from|import)\s+sva2yosys\.(temporal|optimize|codegen)\b", re.M)
    offenders = [
        path.name for path in root.glob("*.py") if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
