"""Test matrix page: area x tier x external tools x external corpora of the test suite.

Test-support module (never imported by ``src/``). Its output is ``docs/test-matrix.md``, a
generated, committed page; ``tests/functional_tests/reference_semantics/
test_suite_matrix_page.py`` fails when the page is stale.

Contract.

1. Collection. :func:`collect` collects the whole suite without running it, in a subprocess
   (``python -m pytest --collect-only -q -p support.suite_matrix`` from the repository root,
   with this module loaded as a plugin that writes one JSON record per collected test).
   Collection needs no external tool and no environment variable: :func:`collection_command`
   passes ``--external-tools=none --external-corpora=none`` explicitly, and
   :func:`collection_environment` removes the resource variables and also ``PYTEST_ADDOPTS``
   and ``PYTEST_PLUGINS`` from the child's environment (inherited options could filter the
   page to a part of the suite or select resources that are then missing). A collection
   error raises ``RuntimeError``.
2. Record. Each collected test becomes a :class:`TestRecord`:

   * ``area``: ``unit_tests``, ``coverage_tests``, ``functional_tests/<area>`` or
     ``functional_tests/external/<area>`` (the folder of the test file under ``tests/``;
     any other location is its first path component);
   * ``tier``: ``slow`` when the test (or its class or module) carries the ``slow`` marker,
     else ``extended`` when it carries ``extended``, else ``default`` (slow wins, as in
     ``support.tiers``);
   * ``tools``: the names of :data:`support.selection.TOOLS` whose fixture (``yosys_tool``,
     ``sby_tool``, ``sby_live_tool``, ``slang_tool``, ``ebmc_tool``, ``spot_dir``) is in the
     test's closure of fixtures (``item.fixturenames``, transitive);
   * ``corpora``: likewise for :data:`support.selection.CORPORA` (``yosys_sva_tests``).

   Parametrized cases count one test each.
3. Aggregation. :func:`aggregate` turns records into one :class:`Row` per area, sorted by
   area name, with the number of tests per tier and, for each tool and corpus, the number of
   tests that need it; plus a totals row. A test needing two tools counts under both, so the
   tool columns do not add up to a total.
4. Page. :func:`render` writes a short generated-file header (how to regenerate it) and one
   Markdown table: columns area, default, extended, slow, then every tool and corpus in
   :data:`support.selection.TOOLS` / :data:`support.selection.CORPORA` order (a corpus whose
   name is also a tool's, ``ebmc``, is headed ``<name> (corpus)``), then the totals row.
   Deterministic: no timestamp, sorted rows.

Command line (from the repository root)::

    uv run python tests/support/suite_matrix.py            # print the page
    uv run python tests/support/suite_matrix.py --write    # rewrite docs/test-matrix.md
    uv run python tests/support/suite_matrix.py --check    # exit 1 if the page differs
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if __package__ in (None, ""):  # run as a script: make the `support` package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.selection import CORPORA, TOOLS
from support.toolchain import (
    ENV_SBY,
    ENV_SBY_LIVE,
    ENV_SLANG,
    ENV_YOSYS,
    ENV_YOSYS_SVA_TESTS,
)
from support.verilator import ENV_BUILD_PATH as ENV_VERILATOR_BUILD_PATH
from support.verilator import ENV_ROOT as ENV_VERILATOR_ROOT
from support.verilator import ENV_SHELL as ENV_VERILATOR_SHELL
from support.verilator import ENV_VERILATOR

if TYPE_CHECKING:
    import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
PAGE = REPOSITORY / "docs" / "test-matrix.md"
TIERS: tuple[str, ...] = ("default", "extended", "slow")
TOTALS = "total"

_ENV_OUT = "SVA2YOSYS_TEST_MATRIX_OUT"
_TOOL_FIXTURES: dict[str, str] = {t.name: t.fixture for t in TOOLS}
_CORPUS_FIXTURES: dict[str, str] = {c.name: c.fixture for c in CORPORA}
_PYTEST_VARIABLES = ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")
_RESOURCE_VARIABLES = (
    ENV_YOSYS,
    ENV_SBY,
    ENV_SBY_LIVE,
    ENV_SLANG,
    ENV_YOSYS_SVA_TESTS,
    ENV_VERILATOR,
    ENV_VERILATOR_SHELL,
    ENV_VERILATOR_ROOT,
    ENV_VERILATOR_BUILD_PATH,
)


@dataclass(frozen=True)
class TestRecord:
    """One collected test."""

    __test__ = False  # not a pytest test class

    area: str
    tier: str
    tools: frozenset[str] = frozenset()
    corpora: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Row:
    """The counts of one area (or the totals)."""

    area: str
    tiers: dict[str, int] = field(default_factory=dict)
    tools: dict[str, int] = field(default_factory=dict)
    corpora: dict[str, int] = field(default_factory=dict)


# --- plugin (runs inside the collecting subprocess) -------------------------------------------


def area_of(relative_path: str) -> str:
    """The area of a test file path relative to ``tests/`` (forward slashes)."""
    parts = relative_path.split("/")
    if parts[0] == "functional_tests" and len(parts) > 2:
        if parts[1] == "external" and len(parts) > 3:
            return "/".join(parts[:3])
        return "/".join(parts[:2])
    return parts[0]


def pytest_collection_finish(session: pytest.Session) -> None:
    """Plugin hook: write the records of the collected items when the output variable is set."""
    target = os.environ.get(_ENV_OUT)
    if not target:
        return
    root = session.config.rootpath / "tests"
    records: list[dict[str, object]] = []
    for item in session.items:
        if item.get_closest_marker("slow") is not None:
            tier = "slow"
        elif item.get_closest_marker("extended") is not None:
            tier = "extended"
        else:
            tier = "default"
        names = set(item.fixturenames)  # type: ignore[attr-defined]
        records.append(
            {
                "area": area_of(item.path.relative_to(root).as_posix()),
                "tier": tier,
                "tools": sorted(n for n, f in _TOOL_FIXTURES.items() if f in names),
                "corpora": sorted(n for n, f in _CORPUS_FIXTURES.items() if f in names),
            }
        )
    Path(target).write_text(json.dumps(records), encoding="utf-8")


# --- collection -------------------------------------------------------------------------------


def collection_command() -> list[str]:
    """The collection command: the default selection is passed explicitly."""
    return [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "support.suite_matrix",
        "--external-tools=none",
        "--external-corpora=none",
    ]


def collection_environment(base: Mapping[str, str], out: Path) -> dict[str, str]:
    """The environment of the collection from ``base``: without the resource variables and
    without ``PYTEST_ADDOPTS`` / ``PYTEST_PLUGINS`` (a filter would give a partial page, an
    inherited ``--external-tools=all`` would fail), with ``tests`` on ``PYTHONPATH`` and the
    output file ``out``."""
    environment = {
        k: v for k, v in base.items() if k not in (*_RESOURCE_VARIABLES, *_PYTEST_VARIABLES)
    }
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPOSITORY / "tests"), environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    environment[_ENV_OUT] = str(out)
    return environment


def collect() -> tuple[TestRecord, ...]:
    """Collect the suite in a subprocess and return one record per test.

    Raises:
        RuntimeError: The collection failed.
    """
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "records.json"
        result = subprocess.run(
            collection_command(),
            cwd=REPOSITORY,
            env=collection_environment(os.environ, out),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not out.exists():
            raise RuntimeError(f"test collection failed:\n{result.stdout}\n{result.stderr}")
        raw = json.loads(out.read_text(encoding="utf-8"))
    return tuple(
        TestRecord(r["area"], r["tier"], frozenset(r["tools"]), frozenset(r["corpora"]))
        for r in raw
    )


# --- aggregation and rendering ----------------------------------------------------------------


def aggregate(records: tuple[TestRecord, ...] | list[TestRecord]) -> tuple[Row, ...]:
    """One row per area, sorted by area, then the totals row (area ``total``)."""
    by_area: dict[str, list[TestRecord]] = {}
    for record in records:
        by_area.setdefault(record.area, []).append(record)
    rows = [_row(area, by_area[area]) for area in sorted(by_area)]
    rows.append(_row(TOTALS, list(records)))
    return tuple(rows)


def _row(area: str, records: list[TestRecord]) -> Row:
    tiers = Counter(r.tier for r in records)
    tools = Counter(t for r in records for t in r.tools)
    corpora = Counter(c for r in records for c in r.corpora)
    return Row(
        area,
        {t: tiers[t] for t in TIERS},
        {t.name: tools[t.name] for t in TOOLS},
        {c.name: corpora[c.name] for c in CORPORA},
    )


def render(records: tuple[TestRecord, ...] | list[TestRecord] | None = None) -> str:
    """The Markdown page for ``records`` (default: :func:`collect`)."""
    rows = aggregate(collect() if records is None else records)
    tool_names = {t.name for t in TOOLS}
    header = [
        "area",
        *TIERS,
        *(t.name for t in TOOLS),
        *(f"{c.name} (corpus)" if c.name in tool_names else c.name for c in CORPORA),
    ]
    out = [
        "# Test matrix",
        "",
        "This page is generated; do not edit it. It counts the collected tests of each area "
        "per tier (`default`, `extended`, `slow`: the tier of its markers, `slow` wins) and "
        "the tests that need each external tool or corpus (a test needing several counts under "
        "each, so those columns do not add up to the total). The tools and corpora are "
        "selected with `--external-tools` and `--external-corpora`. Regenerate it with "
        "`uv run python tests/support/suite_matrix.py --write`; a test fails when it is stale.",
        "",
        "| " + " | ".join(header) + " |",
        "|---|" + "---:|" * (len(header) - 1),
    ]
    for row in rows:
        cells = [
            *(str(row.tiers[t]) for t in TIERS),
            *(str(row.tools[t.name]) for t in TOOLS),
            *(str(row.corpora[c.name]) for c in CORPORA),
        ]
        label = f"**{row.area}**" if row.area == TOTALS else f"`{row.area}`"
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Print the page, or with ``--write`` rewrite it, or with ``--check`` verify it."""
    parser = argparse.ArgumentParser(description="Generate docs/test-matrix.md.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite docs/test-matrix.md")
    mode.add_argument("--check", action="store_true", help="exit 1 if the page differs")
    args = parser.parse_args(argv)
    page = render()
    if args.write:
        PAGE.write_text(page, encoding="utf-8", newline="\n")
    elif args.check:
        if not PAGE.exists() or PAGE.read_text(encoding="utf-8") != page:
            print("docs/test-matrix.md is stale: run tests/support/suite_matrix.py --write")
            return 1
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
