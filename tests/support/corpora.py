"""Test support: fetch the external SVA corpora used for surveys and the opt-in corpus tests.

The corpora are third-party repositories, kept outside this repository (never committed).
This module holds a manifest of them (pinned to full commit hashes) and a command-line
entry point that clones them with git::

    uv run python tests/support/corpora.py <dest> [--latest] [--update] [name ...]

Contract
--------
* ``plan_commands`` is pure: it returns the git commands (argument tuples, without the
  leading ``git``) for one entry and never runs anything or touches the file system.
* A new clone is partial (``--filter=blob:none``), without checkout; sparse entries are
  restricted to their paths (cone mode); then the pinned commit (or, with ``latest``, the
  head of the remote default branch) is checked out detached.
* An existing clone is skipped (no commands), unless ``update`` is set: then it is fetched
  (the pinned commit explicitly, or, with ``latest``, the remote HEAD is refreshed) and
  checked out again. ``main`` refuses to update a clone with local changes
  (``dirty_check_command`` output non-empty) and never resets or cleans it.
* Options and corpus names may be intermixed on the command line.
* No option weakening certificate checks is ever passed to git.
* Nothing here imports sva2yosys; ``src/`` never imports this module.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_FULL_COMMIT = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Corpus:
    """One external corpus: where it comes from and which revision is pinned."""

    name: str
    """Directory name under the destination."""
    url: str
    """https clone URL."""
    commit: str
    """Pinned full 40-hex commit hash."""
    sparse_paths: tuple[str, ...]
    """Sparse-checkout paths (cone mode); empty for a full checkout."""
    license_note: str
    """Licence of the corpus, as observed in the checkout."""
    purpose: str
    """What the corpus is used for here."""


CORPORA: tuple[Corpus, ...] = (
    Corpus(
        name="yosys",
        url="https://github.com/YosysHQ/yosys.git",
        commit="ba8e952566fc97260ac6884e6ab7b9ed86e1dbe0",
        sparse_paths=("tests/sva",),
        license_note="ISC",
        purpose=(
            "Yosys SVA tests; tests/functional_tests/external/yosys_corpus "
            "(SVA2YOSYS_YOSYS_SVA_TESTS)"
        ),
    ),
    Corpus(
        name="ebmc",
        url="https://github.com/diffblue/hw-cbmc.git",
        commit="2a3263c66d042364769353ea0b068ea1f5eec54e",
        sparse_paths=("regression/verilog/SVA",),
        license_note="BSD-3-Clause (LICENSE; tag ebmc-6.1)",
        purpose=(
            "EBMC regression tests with expected answers; "
            "tests/functional_tests/external/ebmc_corpus (SVA2YOSYS_EBMC_TESTS)"
        ),
    ),
    Corpus(
        name="sby",
        url="https://github.com/YosysHQ/sby.git",
        commit="79fc9eddca5e33a28cc2f2cd19c9825efd9a7255",
        sparse_paths=("docs/examples", "tests"),
        license_note="ISC (COPYING)",
        purpose=(
            "SymbiYosys examples and tests with expected results; "
            "tests/functional_tests/external/sby_examples (SVA2YOSYS_SBY_EXAMPLES)"
        ),
    ),
    Corpus(
        name="SVA-AXI4-FVIP",
        url="https://github.com/YosysHQ-GmbH/SVA-AXI4-FVIP.git",
        commit="250f1ffd47fc1cdc4b4dd1670c6e1df58dec1b12",
        sparse_paths=(),
        license_note="ISC (COPYING)",
        purpose="AXI4 assertion library, survey of real-world SVA usage",
    ),
    Corpus(
        name="sv-tests",
        url="https://github.com/chipsalliance/sv-tests.git",
        commit="f3f572c1cb9527272c2d1769c7217cffb1b4dd47",
        sparse_paths=(),
        license_note="ISC (LICENSE); individual tests carry their own headers",
        purpose="SystemVerilog conformance tests, survey of assertion constructs",
    ),
    Corpus(
        name="common_cells",
        url="https://github.com/pulp-platform/common_cells.git",
        commit="e73baaec2ca665cd80c3c384e9258e35242b829c",
        sparse_paths=(),
        license_note="Solderpad Hardware License 0.51 (LICENSE)",
        purpose="RTL with embedded assertions, survey",
    ),
    Corpus(
        name="wb2axip",
        url="https://github.com/ZipCPU/wb2axip.git",
        commit="2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b",
        sparse_paths=(),
        license_note="Apache 2 per README; check file headers",
        purpose="Bus RTL with formal properties, survey",
    ),
    Corpus(
        name="assertion_data_for_LLM",
        url="https://github.com/achieve-lab/assertion_data_for_LLM.git",
        commit="07840fbbfe882b67fb7c549bdb7fe1328aadd6d2",
        sparse_paths=(),
        license_note=(
            "no repository licence; paper arXiv 2406.18627 (CC BY-SA 4.0), NAACL 2025 "
            "Findings (CC BY 4.0); designs from OpenCores under their own licences; fetched "
            "for the survey, never redistributed; "
            "the repository's published formal verdicts are never used"
        ),
        purpose="AssertionBench (Pulavarthi, Nandal, Dan, Pal): acceptance survey only",
    ),
    Corpus(
        name="opentitan",
        url="https://github.com/lowRISC/opentitan.git",
        commit="cfcefae2db13015b6178c40044d9f1c93070bb8c",
        sparse_paths=(
            "hw/ip/gpio/rtl",
            "hw/ip/hmac/rtl",
            "hw/ip/i2c/rtl",
            "hw/ip/prim/rtl",
            "hw/ip/prim_generic/rtl",
            "hw/ip/prim_xilinx/rtl",
            "hw/ip/rv_timer/rtl",
            "hw/ip/spi_host/rtl",
            "hw/ip/tlul/rtl",
            "hw/ip/uart/rtl",
        ),
        license_note="Apache 2.0 (LICENSE)",
        purpose="Production RTL with assertions, survey",
    ),
)

GitCommand = tuple[str, ...]
"""Arguments of one git invocation, without the leading ``git``."""


def plan_commands(
    corpus: Corpus, dest: Path, *, latest: bool, exists: bool, update: bool
) -> tuple[GitCommand, ...]:
    """Return the git commands that bring ``dest/<corpus.name>`` to the wanted revision.

    ``exists`` says whether the clone directory is already there. Pure: nothing is run.
    """
    target = str(dest / corpus.name)
    if exists and not update:
        return ()
    commands: list[GitCommand] = []
    if exists:
        commands.append(("-C", target, "fetch", "--filter=blob:none", "origin"))
        if latest:
            commands.append(("-C", target, "remote", "set-head", "origin", "--auto"))
        else:
            commands.append(("-C", target, "fetch", "--filter=blob:none", "origin", corpus.commit))
    else:
        commands.append(("clone", "--filter=blob:none", "--no-checkout", corpus.url, target))
    if corpus.sparse_paths:
        commands.append(("-C", target, "sparse-checkout", "set", "--cone", *corpus.sparse_paths))
    revision = "origin/HEAD" if latest else corpus.commit
    commands.append(("-C", target, "checkout", "--detach", revision))
    return tuple(commands)


def dirty_check_command(corpus: Corpus, dest: Path) -> GitCommand:
    """The command whose non-empty output means the clone has local changes (tracked or not)."""
    return ("-C", str(dest / corpus.name), "status", "--porcelain", "--untracked-files=all")


def env_line(dest: Path) -> str:
    """The ``.env`` line naming the Yosys SVA tests under ``dest`` (forward slashes).

    The value is single-quoted (uv's dotenv parser takes it literally); a path containing a
    single quote is rejected, since no quoting keeps it literal.

    Raises:
        ValueError: the path contains a single quote.
    """
    path = (dest / "yosys" / "tests" / "sva").resolve().as_posix()
    if "'" in path:
        raise ValueError(f"the corpus path {path!r} contains a single quote; choose another")
    return f"SVA2YOSYS_YOSYS_SVA_TESTS='{path}'"


def select(names: Sequence[str]) -> tuple[Corpus, ...]:
    """The manifest entries called ``names`` (all when empty); ``ValueError`` if unknown."""
    if not names:
        return CORPORA
    by_name = {c.name: c for c in CORPORA}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(f"unknown corpus: {', '.join(unknown)}; known: {', '.join(by_name)}")
    return tuple(by_name[n] for n in names)


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description="Fetch the external SVA corpora with git.")
    parser.add_argument("dest", type=Path, help="directory receiving one clone per corpus")
    parser.add_argument("names", nargs="*", help="corpora to fetch (default: all)")
    parser.add_argument("--latest", action="store_true", help="use the default branch head")
    parser.add_argument(
        "--update", action="store_true", help="fetch and re-checkout existing clones"
    )
    args = parser.parse_intermixed_args(argv)
    try:
        chosen = select(args.names)
    except ValueError as error:
        parser.error(str(error))
    dest: Path = args.dest
    dest.mkdir(parents=True, exist_ok=True)
    for corpus in chosen:
        exists = (dest / corpus.name / ".git").exists()
        commands = plan_commands(
            corpus, dest, latest=args.latest, exists=exists, update=args.update
        )
        if not commands:
            print(f"{corpus.name}: exists, skipped (use --update)")
            continue
        if exists:
            status = subprocess.run(
                ["git", *dirty_check_command(corpus, dest)],
                check=False,
                capture_output=True,
                text=True,
            )
            if status.returncode != 0 or status.stdout.strip():
                print(
                    f"{corpus.name}: clone is dirty or unreadable; refusing to update. "
                    f"Delete {dest / corpus.name} and fetch it again.",
                    file=sys.stderr,
                )
                return 1
        print(f"{corpus.name}: {corpus.url}")
        for command in commands:
            completed = subprocess.run(["git", *command], check=False)
            if completed.returncode != 0:
                print(
                    f"{corpus.name}: git {' '.join(command)} failed; the pinned "
                    "commit or default branch may be unreachable",
                    file=sys.stderr,
                )
                return completed.returncode
    print()
    try:
        line = env_line(dest)
    except ValueError as error:
        print(f"No .env line: {error}", file=sys.stderr)
        return 1
    print("Add to .env:")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
