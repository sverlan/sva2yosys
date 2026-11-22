"""The original assertion in the generated code (CONTRACT.md, point 20)."""

from __future__ import annotations

from pathlib import Path

from support.toolchain import SbyStatus, Tool, run_sby
from sva2yosys.rewrite import MONITORS_FILE, Rewrite, rewrite, syntax_items
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

_MULTI_LINE = (
    "module m(input logic clk, rst, a, b);\n"
    "  default clocking @(posedge clk); endclocking\n"
    "  default disable iff (rst);\n"
    "  lab: assert property (\n"
    "      a\n"
    "      |=> b   // ok next\n"
    "  ); logic after_assert;\n"
    "  assert property (@(posedge clk) a |-> a); assume property (@(posedge clk) b |-> b);\n"
    "  cover property (a ##1 b);\n"
    "endmodule\n"
)


def _rewrite(text: str, top: str = "m") -> Rewrite:
    sources = [("v.sv", text)]
    document = export_texts(sources, SlangOptions(top=(top,)))
    return rewrite(sources, frozenset({"v.sv"}), syntax_items(document), convert(document))


def test_instance_carries_the_assertion_as_written_and_lines_are_kept() -> None:
    """20."""
    result = _rewrite(_MULTI_LINE)
    (output,) = [f.text for f in result.files]
    lines = output.split("\n")
    assert len(lines) == len(_MULTI_LINE.split("\n"))
    # The text is as written, folded to one line; a `//` comment inside it is harmless in
    # a block comment, and the code that followed the assertion stays on the line.
    instance_line = next(line for line in lines if "__sva2yosys_i0" in line)
    assert "/* lab: assert property (" in instance_line
    assert lines[6].endswith("logic after_assert;")  # still on its original line


def test_code_after_an_assertion_on_its_line_survives_two_assertions_per_line() -> None:
    """20."""
    result = _rewrite(_MULTI_LINE)
    (output,) = [f.text for f in result.files]
    line = next(row for row in output.split("\n") if "__sva2yosys_i1" in row)
    assert "__sva2yosys_i2" in line
    assert line.count("/*") == 2 and line.count("*/") == 2
    assert "/* assert property (@(posedge clk) a |-> a); */" in line
    assert "/* assume property (@(posedge clk) b |-> b); */" in line


def test_header_shows_the_assertion_and_the_effective_clock_and_disable() -> None:
    """20."""
    monitors = _rewrite(_MULTI_LINE).monitors
    first = monitors.split("endmodule")[0]
    assert "// as written: lab: assert property ( a |=> b // ok next );" in first
    assert "// clock: posedge clk\n" in first  # from the default clocking
    assert "// disable iff: rst\n" in first  # from the default disable iff
    second = monitors.split("endmodule")[1]
    assert "// clock: posedge clk\n" in second
    assert "// disable iff: rst\n" in second
    third = monitors.split("endmodule")[2]
    assert "// as written: assume property (@(posedge clk) b |-> b);" in third


def test_header_without_disable_and_for_an_unnamed_assertion() -> None:
    """20."""
    text = "module m(input logic clk, a);\n  assert property (@(posedge clk) a);\nendmodule\n"
    result = _rewrite(text)
    (output,) = [f.text for f in result.files]
    assert "/* assert property (@(posedge clk) a); */" in output
    assert "// as written: assert property (@(posedge clk) a);\n" in result.monitors
    assert "// clock: posedge clk\n" in result.monitors
    assert "// disable iff:" not in result.monitors


def test_conjuncts_of_one_property_and_share_one_comment() -> None:
    """20."""
    text = (
        "module m(input logic clk, a, b);\n"
        "  assert property (@(posedge clk) (a |-> a) and (b |-> b));\n"
        "endmodule\n"
    )
    (output,) = [f.text for f in _rewrite(text).files]
    assert output.count("/*") == 1 and "__sva2yosys_i0_c1" in output


def test_monitors_with_the_comments_still_prove(sby_tool: Tool, tmp_path: Path) -> None:
    """20: the commented output elaborates and proves."""
    text = (
        "module m(input logic clk, input logic a);\n"
        "  logic b;\n"
        "  always_ff @(posedge clk) b <= a;\n"
        "  p: assert property (\n"
        "      @(posedge clk)\n"
        "      a |=> b\n"
        "  ); logic after_assert;\n"
        "endmodule\n"
    )
    result = _rewrite(text)
    (output,) = result.files
    (tmp_path / "v.sv").write_text(output.text)
    (tmp_path / MONITORS_FILE).write_text(result.monitors)
    sby = tmp_path / "t.sby"
    sby.write_text(
        "[tasks]\nbmc\n\n[options]\nmode bmc\ndepth 6\n\n[engines]\nsmtbmc yices\n\n[script]\n"
        f"read_verilog -formal -sv v.sv\nread_verilog -formal -sv {MONITORS_FILE}\n"
        f"prep -top m\n\n[files]\nv.sv\n{MONITORS_FILE}\n"
    )
    checked = run_sby(sby_tool, sby, "bmc", tmp_path / "work")
    assert checked.status is SbyStatus.PASS, checked.log[-2000:]
