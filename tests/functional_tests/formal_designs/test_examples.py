"""Canonical educational designs: local admission and opt-in formal outcomes.

The expectations are derived in CONTRACT.md; this module must never capture
expectations from tool output.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.toolchain import SbyStatus, Tool, run_sby
from sva2yosys.cli import main

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Example:
    """Canonical directory/top, hand-counted directives and reachable covers."""

    name: str
    directives: int
    covers: int


EXAMPLES = (Example("pulse", 4, 1), Example("counter", 4, 1), Example("fifo", 6, 2))


def _lower(example: Example, work: Path, capsys: pytest.CaptureFixture[str], *, bug: bool) -> None:
    """Check and lower identical canonical sources without copying their RTL."""
    work.mkdir(parents=True, exist_ok=True)
    source = ROOT / "examples" / example.name
    options = [
        str(source / "props" / f"{example.name}_props.sv"),
        "-y",
        str(source / "rtl"),
        "--top",
        example.name,
    ]
    if bug:
        options.extend(("-D", "EXAMPLE_BUG"))
    assert main(options) == 0
    captured = capsys.readouterr()
    assert (
        f"{example.directives} assertion(s): {example.directives} accepted, 0 rejected"
        in captured.out
    ), captured.out + captured.err
    assert main([*options, "-o", str(work / "build")]) == 0
    captured = capsys.readouterr()
    assert not captured.err, captured.err


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: example.name)
def test_example_normal_and_buggy_sources_lower(
    example: Example, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both variants retain all independently specified verification directives."""
    for bug in (False, True):
        _lower(example, tmp_path / ("bug" if bug else "normal"), capsys, bug=bug)


@pytest.mark.extended
@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: example.name)
def test_example_proves_covers_and_exposes_its_bug(
    example: Example, tmp_path: Path, capsys: pytest.CaptureFixture[str], sby_tool: Tool
) -> None:
    """The shipped config proves/reaches the normal design and refutes its mutation."""
    for bug in (False, True):
        work = tmp_path / ("bug" if bug else "normal")
        _lower(example, work, capsys, bug=bug)
        config = work / f"{example.name}.sby"
        shutil.copyfile(ROOT / "examples" / example.name / config.name, config)
        proved = run_sby(sby_tool, config, "prove", work / "prove")
        expected = SbyStatus.FAIL if bug else SbyStatus.PASS
        assert proved.status is expected, proved.log[-4000:]
        if not bug:
            covered = run_sby(sby_tool, config, "cover", work / "cover")
            assert covered.status is SbyStatus.PASS, covered.log[-4000:]
            assert len(covered.reached_covers) == example.covers, covered.log[-4000:]
