"""Default sampled values on the command line (CONTRACT.md, point 21)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sva2yosys.cli import main


def test_a_known_default_is_rejected_at_the_directive(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """21: ``$stable`` of a 2-state variable (IEEE 1800 section 16.5.1: its default is 0, not
    X) is a located rejection. Origin: reviews (D1)."""
    source = tmp_path / "d.sv"
    source.write_text(
        "module d(input logic clk);\n"
        "  bit a;\n"
        "  q: assert property (@(posedge clk) $stable(a));\n"
        "endmodule\n"
    )
    status = main([str(source)])
    out = capsys.readouterr().out
    assert status == 1
    (line,) = [x for x in out.splitlines() if x.startswith("rejected")]
    assert line.startswith(f"rejected  {source}:3:")
    assert "d.q:" in line
    assert "before the first clock tick is not unknown" in line
    assert "does not depend on a 'disable iff'" in line


def test_an_unknown_default_is_accepted(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """21: the same property over an input port of 4-state type is accepted."""
    source = tmp_path / "d.sv"
    source.write_text(
        "module d(input logic clk, a);\n"
        "  q: assert property (@(posedge clk) $stable(a));\n"
        "endmodule\n"
    )
    assert main([str(source)]) == 0
    assert "1 assertion(s): 1 accepted, 0 rejected" in capsys.readouterr().out
