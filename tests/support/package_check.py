"""Check the built distribution files before they are released or trusted.

Usage (CI, from the repository root, after ``uv build --out-dir dist``)::

    uv run python tests/support/package_check.py dist

Contract of :func:`package_problems`. ``dist`` holds the files of one build. It is acceptable
when:

* it holds exactly one source archive ``sva2yosys-*.tar.gz`` and exactly one wheel
  ``sva2yosys-*.whl``;
* it holds nothing but the package: the source archive only ``LICENSE``, ``README.md``,
  ``pyproject.toml`` (and the ``pyproject.toml.orig`` that a newer uv writes), ``PKG-INFO`` and
  the folder ``src/sva2yosys``; the wheel only the folder ``sva2yosys`` and its ``.dist-info``
  folder (an allow-list, so no other file can slip in);
* the source archive holds ``LICENSE``, ``README.md`` and ``pyproject.toml``;
* the wheel holds the package (``sva2yosys/__init__.py``) and its entry points.

Each unmet condition is one message; no message means the files are acceptable. :func:`main`
prints the messages and returns 1 when there is one. It reads ``dist`` only and runs no tool.
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

# pyproject.toml.orig: a newer uv writes its normalised pyproject.toml, keeping the original
_SDIST_TOP = ("LICENSE", "README.md", "pyproject.toml", "pyproject.toml.orig", "PKG-INFO")


def _unexpected_in_sdist(member: str) -> bool:
    inner = member.replace("\\", "/").split("/", 1)[-1] if "/" in member else ""
    return inner not in ("", "src", *_SDIST_TOP) and not inner.startswith("src/sva2yosys")


def _unexpected_in_wheel(member: str) -> bool:
    top = member.replace("\\", "/").split("/", 1)[0]
    return top != "sva2yosys" and not top.endswith(".dist-info")


def package_problems(dist: Path) -> list[str]:
    """The reasons why the files of ``dist`` must not be released; empty when they may."""
    sdists = sorted(dist.glob("sva2yosys-*.tar.gz"))
    wheels = sorted(dist.glob("sva2yosys-*.whl"))
    problems: list[str] = []
    if len(sdists) != 1:
        problems.append(f"expected one source archive in {dist}, found {len(sdists)}")
    if len(wheels) != 1:
        problems.append(f"expected one wheel in {dist}, found {len(wheels)}")
    if problems:
        return problems
    with tarfile.open(sdists[0]) as archive:
        source = archive.getnames()
    with zipfile.ZipFile(wheels[0]) as wheel:
        built = wheel.namelist()
    problems.extend(
        f"the source archive holds an unexpected file: {n}"
        for n in source
        if _unexpected_in_sdist(n)
    )
    problems.extend(
        f"the wheel holds an unexpected file: {n}" for n in built if _unexpected_in_wheel(n)
    )
    stems = {name.split("/", 1)[1] for name in source if "/" in name}
    problems.extend(
        f"the source archive has no {required}"
        for required in ("LICENSE", "README.md", "pyproject.toml")
        if required not in stems
    )
    if "sva2yosys/__init__.py" not in built:
        problems.append("the wheel has no sva2yosys/__init__.py")
    if not any(name.endswith(".dist-info/entry_points.txt") for name in built):
        problems.append("the wheel has no entry points")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """Print the problems of the ``dist`` folder named on the command line; 1 when any."""
    parser = argparse.ArgumentParser(description="check the built distribution files")
    parser.add_argument("dist", type=Path, help="the folder of the build, for example dist")
    arguments = parser.parse_args(argv)
    problems = package_problems(arguments.dist)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
