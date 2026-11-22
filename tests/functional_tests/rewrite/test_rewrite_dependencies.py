"""Functional contract of the selection by the dependencies (CONTRACT.md, points 21 and 22).

The expectations are hand-derived: an instance parameter that the monitor cannot depend on does
not select the monitor; one that sets a signal width does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from support.toolchain import SbyStatus, Tool, run_sby
from sva2yosys.ir.directive import uncomparable_message
from sva2yosys.rewrite import MONITORS_FILE, Rewrite, RewriteError, Severity, rewrite, syntax_items
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

_FILE = "v.sv"


def _translate(text: str, top: str) -> Rewrite:
    sources = [(_FILE, text)]
    document = export_texts(sources, SlangOptions(top=(top,)))
    return rewrite(
        sources, frozenset({_FILE}), syntax_items(document), convert(document), Severity.ERROR
    )


def _errors(text: str, top: str) -> list[str]:
    try:
        _translate(text, top)
    except RewriteError as error:
        return [str(d) for d in error.diagnostics if d.severity is Severity.ERROR]
    return []


def _check_mode(text: str, top: str) -> list[str]:
    document = export_texts([(_FILE, text)], SlangOptions(top=(top,)))
    return [r.message for r in convert(document).rejections]


def _code(text: str) -> str:
    """``text`` without its block comments (those quote the assertions as written)."""
    return re.sub(r"/\*.*?\*/", "", text)


def _monitor_lines(result: Rewrite) -> list[str]:
    """The code of the lines that instantiate monitors."""
    (edited,) = [f for f in result.files if f.source == _FILE]
    return [_code(line) for line in edited.text.split("\n") if "__sva2yosys_" in line]


# --- the AXI checker case ------------------------------------------------------------------

_CHECKER = """typedef struct { bit en_a; bit en_b; } cfg_t;
module chk #(parameter cfg_t cfg = '{1'b1, 1'b1}) (input logic clk, valid, ready);
  if (cfg.en_a) begin : ga
    pa: assert property (@(posedge clk) valid && !ready |=> valid);
  end
  if (cfg.en_b) begin : gb
    pb: assert property (@(posedge clk) valid |-> ##1 ready);
  end
endmodule
"""


def test_a_struct_parameter_used_only_in_generate_conditions_generates_one_monitor_each() -> None:
    text = _CHECKER + (
        "module top(input logic clk, valid, ready);\n"
        "  chk u(.clk(clk), .valid(valid), .ready(ready));\n"
        "endmodule\n"
    )
    result = _translate(text, "top")
    assert result.monitors.count("endmodule") == 2
    lines = _monitor_lines(result)
    assert len(lines) == 2
    for line in lines:
        assert "cfg" not in line
        assert "==" not in line
        assert "generate" not in line
        assert " else " not in line
    assert sorted(result.forms) == ["__sva2yosys_p0", "__sva2yosys_p1"]


def test_instances_with_different_struct_values_share_the_monitor_they_both_have() -> None:
    """u2 lacks pb (en_b is 0); pa has the same monitor and connections in u1 and u2."""
    text = _CHECKER + (
        "module top(input logic clk, valid, ready);\n"
        "  chk u1(.clk(clk), .valid(valid), .ready(ready));\n"
        "  chk #(.cfg('{1'b1, 1'b0})) u2(.clk(clk), .valid(valid), .ready(ready));\n"
        "endmodule\n"
    )
    result = _translate(text, "top")
    assert result.monitors.count("endmodule") == 2
    lines = _monitor_lines(result)
    assert len(lines) == 2
    assert all("cfg" not in line and "==" not in line for line in lines)


# --- an unread integer parameter does not select the monitor ---------------------------------


def test_instances_that_differ_only_in_an_unread_parameter_share_one_monitor() -> None:
    text = (
        "module c #(parameter int N = 1) (input logic clk, a, b);\n"
        "  x: assert property (@(posedge clk) a |-> b);\n"
        "endmodule\n"
        "module p(input logic clk, a, b);\n"
        "  c #(1) u1(.clk(clk), .a(a), .b(b));\n"
        "  c #(2) u2(.clk(clk), .a(a), .b(b));\n"
        "endmodule\n"
    )
    result = _translate(text, "p")
    assert result.monitors.count("endmodule") == 1
    (line,) = _monitor_lines(result)
    assert not re.search(r"\bN\b", line)
    assert "unexpected" not in line


def test_instances_differing_in_a_width_parameter_are_selected_by_that_parameter_only() -> None:
    """W sets the width of the signal read; U changes nothing: u1 and u3 share the W == 4 branch."""
    text = (
        "module c #(parameter int W = 4, parameter int U = 1)"
        " (input logic clk, b, input logic [W-1:0] v);\n"
        "  x: assert property (@(posedge clk) (v == '0) |-> b);\n"
        "endmodule\n"
        "module p(input logic clk, b, input logic [3:0] v4, input logic [1:0] v2);\n"
        "  c #(4, 1) u1(.clk(clk), .b(b), .v(v4));\n"
        "  c #(2, 3) u2(.clk(clk), .b(b), .v(v2));\n"
        "  c #(4, 5) u3(.clk(clk), .b(b), .v(v4));\n"
        "endmodule\n"
    )
    result = _translate(text, "p")
    assert result.monitors.count("endmodule") == 2
    (line,) = _monitor_lines(result)
    assert "if ((W == 4)) begin : __sva2yosys_p0_g0 " in line
    assert "else if ((W == 2)) begin : __sva2yosys_p0_g1 " in line
    assert not re.search(r"\bU\b", line)


# --- check mode and generation refuse the same directives ------------------------------------

_P_HEADER = "typedef struct { bit en; int n; } cfg_t;\n"

BAD = [
    pytest.param(
        _P_HEADER + "module p #(parameter cfg_t cfg = '{1'b1, 2}) (input logic clk, a, b);\n"
        "  p0: assert property (@(posedge clk) a |-> ##(cfg.n) b);\n"
        "endmodule\n",
        "cfg",
        id="struct-in-a-delay",
    ),
    pytest.param(
        _P_HEADER + "module p #(parameter cfg_t cfg = '{1'b1, 2})"
        " (input logic clk, a, b, input logic [cfg.n-1:0] v);\n"
        "  p0: assert property (@(posedge clk) v[0] |-> b);\n"
        "endmodule\n",
        "cfg",
        id="struct-in-the-width-of-a-read-port",
    ),
    pytest.param(
        "module p #(parameter type T = logic) (input logic clk, a, b, input T s);\n"
        "  p0: assert property (@(posedge clk) s |-> b);\n"
        "endmodule\n",
        "T",
        id="type-parameter-of-a-read-port",
    ),
]

GOOD = [
    pytest.param(
        _P_HEADER + "module p #(parameter cfg_t cfg = '{1'b1, 2}) (input logic clk, a, b);\n"
        "  if (cfg.en) begin : g\n"
        "    p0: assert property (@(posedge clk) a |-> b);\n"
        "  end\n"
        "endmodule\n",
        id="struct-in-a-generate-condition",
    ),
    pytest.param(
        "module p #(parameter type T = logic, parameter real R = 1.5)"
        " (input logic clk, a, b);\n"
        "  T unread;\n"
        "  p0: assert property (@(posedge clk) a |-> b);\n"
        "endmodule\n",
        id="unread-type-and-real-parameters",
    ),
]


@pytest.mark.parametrize(("text", "name"), BAD)
def test_check_mode_and_generation_refuse_a_parameter_inside_the_dependencies(
    text: str, name: str
) -> None:
    expected = uncomparable_message((name,))
    assert _check_mode(text, "p") == [expected]
    assert any(expected in e for e in _errors(text, "p"))


@pytest.mark.parametrize("text", GOOD)
def test_check_mode_and_generation_both_accept_a_parameter_outside_the_dependencies(
    text: str,
) -> None:
    assert _check_mode(text, "p") == []
    assert _errors(text, "p") == []


# --- behaviour under SBY: the shared monitor serves an unseen value ------------------------------

_SBY = """[tasks]
bmc

[options]
mode bmc
depth 5

[engines]
smtbmc yices

[script]
{reads}
prep -top {top}

[files]
{files}
"""

_SHARED = """module c #(parameter int N = 1) (input logic clk, a);
  logic q = 1'b0;
  always @(posedge clk) q <= (N == 1) ? a : 1'b0;
  x: assert property (@(posedge clk) a |=> q);
endmodule
module top_a(input logic clk, a);
  c #(1) u(.clk(clk), .a(a));
endmodule
module top_b(input logic clk, a);
  c #(2) u(.clk(clk), .a(a));
endmodule
"""


@pytest.mark.parametrize(("top", "status"), [("top_a", SbyStatus.PASS), ("top_b", SbyStatus.FAIL)])
def test_the_monitor_seen_with_one_value_of_an_unread_parameter_serves_another(
    sby_tool: Tool, tmp_path: Path, top: str, status: SbyStatus
) -> None:
    """The assertion reads only ``a`` and ``q``: with N = 1 the register copies ``a`` and
    ``a |=> q`` holds; with N = 2 it stays 0 and ``a`` high at the first step refutes it. The
    rewrite elaborated only top_a (N = 1), and N does not select the monitor, so the same
    rewritten files check top_b."""
    result = _translate(_SHARED, "top_a")
    (line,) = _monitor_lines(result)
    assert not re.search(r"\bN\b", line)
    (edited,) = [f for f in result.files if f.source == _FILE]
    names = [edited.name, MONITORS_FILE]
    (tmp_path / edited.name).write_text(edited.text)
    (tmp_path / MONITORS_FILE).write_text(result.monitors)
    reads = "\n".join(f"read_verilog -formal -sv {name}" for name in names)
    sby_file = tmp_path / "bmc.sby"
    sby_file.write_text(_SBY.format(reads=reads, files="\n".join(names), top=top))
    checked = run_sby(sby_tool, sby_file, "bmc", tmp_path / "work_bmc")
    assert checked.status is status, checked.log[-3000:]
