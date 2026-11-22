"""Optimised monitor forms against the direct monitor, proven by SBY (CONTRACT.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings

from support.ir_strategies import directives
from support.toolchain import SbyStatus, Tool, run_sby, run_yosys
from sva2yosys.codegen.equivalence import equivalence_check
from sva2yosys.codegen.systemverilog import monitor_module
from sva2yosys.ir.directive import Directive
from sva2yosys.optimize.forms import Form, Strategy, choose, state_bits
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import compile_directive
from sva2yosys.temporal.machine import Machine

# Generated IR values have long reprs; the warning carries no information here.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Generating overly large repr:hypothesis.errors.HypothesisWarning"
)

BOTH = [
    "a |-> ##[1:8] b",
    "a |=> ##[2:5] b",
    "a ##1 b |-> ##[1:3] c",
    "##[1:4] a",
    "a |-> ##[1:3] $past(b)",
    "$rose(a) |-> ##[2:6] b",
]
HISTORY_ONLY = [
    "a |-> ##[1:4] b ##[1:4] c",
    "a |-> b[*1:3] ##1 c",
    "a |-> b ##0 c ##[1:2] d",
    "a |-> ##[1:3] b",
]
COVER = "a ##[1:3] b ##[1:2] c"


def _machine(prop: str, kind: str = "assert") -> Machine:
    text = (
        "module m(input logic clk, rst, a, b, c, d);\n"
        f"  p: {kind} property (@(posedge clk) disable iff (rst) {prop});\nendmodule\n"
    )
    (directive,) = convert(export_texts([("m.sv", text)], SlangOptions(top=("m",)))).directives
    return compile_directive(directive)


def _prove(tool: Tool, directory: Path, files: dict[str, str]) -> SbyStatus:
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (directory / name).write_text(content)
    result = run_sby(tool, directory / "equivalence.sby", "prove", directory / "work")
    return result.status


CASES = [(p, Form.WINDOW) for p in BOTH] + [(p, Form.HISTORY) for p in BOTH + HISTORY_ONLY]


@pytest.mark.parametrize(
    ("prop", "form"),
    [
        pytest.param(*value, marks=() if index in (0, len(BOTH)) else pytest.mark.extended)
        for index, value in enumerate(CASES)
    ],
    ids=[f"{f.value}:{p}" for p, f in CASES],
)
def test_form_equals_the_direct_monitor(
    sby_tool: Tool, tmp_path: Path, prop: str, form: Form
) -> None:
    """1."""
    machine = _machine(prop)
    assert state_bits(machine, form) is not None
    assert _prove(sby_tool, tmp_path, equivalence_check(machine, "mon", form)) is SbyStatus.PASS


def test_history_form_of_a_cover(sby_tool: Tool, tmp_path: Path) -> None:
    """1: a cover (window does not apply to covers)."""
    machine = _machine(COVER, "cover")
    assert state_bits(machine, Form.WINDOW) is None
    files = equivalence_check(machine, "mon", Form.HISTORY)
    assert _prove(sby_tool, tmp_path, files) is SbyStatus.PASS


MUTANTS = [
    # (property, form, text in the optimised monitor, replacement); here c2 is b.
    ("a |-> ##[1:4] b", Form.WINDOW, "!c2 & (win_oldest == 3'd4)", "!c2 & (win_oldest == 3'd3)"),
    ("a |-> ##[1:4] b", Form.WINDOW, "(c2 || win_oldest == 3'd0", "(1'b0 || win_oldest == 3'd0"),
    ("a |-> ##[1:3] b ##1 c", Form.HISTORY, "his_c2[2]", "his_c2[1]"),
    # Only success is wrong: the window check compares it up to the first failure too.
    (
        "a |-> ##[1:4] b",
        Form.WINDOW,
        "assign success = !dis & (win_accept);",
        "assign success = 1'b0;",
    ),
]


@pytest.mark.parametrize(
    ("prop", "form", "old", "new"),
    [
        pytest.param(*value, marks=() if index == 0 else pytest.mark.extended)
        for index, value in enumerate(MUTANTS)
    ],
    ids=["early", "no-clear", "short", "no-success"],
)
def test_wrong_monitors_fail_the_check(
    sby_tool: Tool, tmp_path: Path, prop: str, form: Form, old: str, new: str
) -> None:
    """2."""
    files = equivalence_check(_machine(prop), "mon", form)
    direct, separator, optimised = files["monitors.sv"].partition("module mon #(")
    assert separator and old in optimised, optimised
    files["monitors.sv"] = direct + separator + optimised.replace(old, new)
    assert _prove(sby_tool, tmp_path, files) is SbyStatus.FAIL


@pytest.mark.slow
@settings(
    max_examples=25,
    deadline=None,
    derandomize=True,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
        HealthCheck.filter_too_much,
    ],
)
@given(directive=directives())
def test_random_properties(sby_tool: Tool, tmp_path: Path, directive: Directive) -> None:
    """3."""
    machine = compile_directive(directive)
    assume(state_bits(machine, Form.HISTORY) is not None)  # something to prove
    for form in (Form.WINDOW, Form.HISTORY):
        if state_bits(machine, form) is None:
            continue
        files = equivalence_check(machine, "mon", form)
        directory = tmp_path / f"{form.value}_{abs(hash(directive))}"
        assert _prove(sby_tool, directory, files) is SbyStatus.PASS, files["monitors.sv"]


def test_equivalence_jobs_are_self_contained() -> None:
    """4."""
    machine = _machine("a |-> ##[1:3] b")
    files = equivalence_check(machine, "mon", Form.WINDOW)
    assert sorted(files) == ["equivalence.sby", "miter.sv", "monitors.sv"]
    assert "module mon_direct" in files["monitors.sv"] and "module mon #(" in files["monitors.sv"]
    assert "prep -top mon_equiv" in files["equivalence.sby"]
    with pytest.raises(ValueError, match="reference"):
        equivalence_check(machine, "mon", Form.DIRECT)
    with pytest.raises(ValueError, match="does not apply"):
        equivalence_check(_machine("a |-> b ##1 c"), "mon", Form.WINDOW)


_ASSUME_TB = """module tb(input logic clk, input logic b);
    logic [3:0] t = 4'd0;
    always @(posedge clk) if (t != 4'd15) t <= t + 4'd1;
    logic rst, a, seen = 1'b0;
    assign rst = 1'b0;
    assign a = t == 4'd0;
    always @(posedge clk) if (t >= 4'd1 && t <= 4'd3 && b) seen <= 1'b1;
    logic fail, success;
    mon u_monitor (.clk(clk), .i0(rst), .i1(a), .i2(b), .fail(fail), .success(success));
    always @* if (t == 4'd4) assert (seen);
    always @* cover (t == 4'd6);
endmodule
"""

_ASSUME_SBY = """[tasks]
{mode}

[options]
mode {mode}
depth 8

[engines]
smtbmc yices

[script]
read_verilog -formal -sv monitor.sv tb.sv
prep -top tb

[files]
monitor.sv
tb.sv
"""


@pytest.mark.parametrize("form", [Form.DIRECT, Form.WINDOW])
def test_an_optimised_assumption_constrains_like_the_direct_one(
    sby_tool: Tool, tmp_path: Path, form: Form
) -> None:
    """5."""
    machine = _machine("a |-> ##[1:3] b", "assume")
    assert state_bits(machine, Form.WINDOW) is not None
    (tmp_path / "monitor.sv").write_text(monitor_module(machine, "mon", form))
    (tmp_path / "tb.sv").write_text(_ASSUME_TB)
    for mode in ("bmc", "cover"):
        sby_file = tmp_path / f"{mode}.sby"
        sby_file.write_text(_ASSUME_SBY.format(mode=mode))
        result = run_sby(sby_tool, sby_file, mode, tmp_path / f"work_{mode}")
        assert result.status is SbyStatus.PASS, result.log[-3000:]


# --- 6: model size --------------------------------------------------------------------------

SIZE_WINDOWS = [f"a |-> ##[1:{n}] b" for n in (1, 2, 4, 6, 8, 10, 12, 16, 32)] + [
    f"a |-> ##[{m}:{n}] b" for m, n in ((2, 4), (3, 4), (4, 8), (8, 8), (2, 6), (6, 12), (4, 32))
]
SIZE_CHAINS = [
    "a |-> b ##[1:4] c",
    "a |-> ##[1:4] b ##[1:4] c",
    "a ##1 b |-> ##[2:6] c ##1 d[*1:3]",
]


def _gates(tool: Tool, directory: Path, machine: Machine, form: Form) -> int:
    """AIG gates (``$_AND_`` and ``$_NOT_`` after ``aigmap``) of the monitor alone."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "mon.sv").write_text(monitor_module(machine, "mon", form))
    run_yosys(
        tool,
        [
            "read_verilog -formal -sv mon.sv",
            "prep -top mon",
            "flatten",
            "opt",
            "techmap",
            "opt -fast",
            "aigmap",
            "opt_clean",
            "tee -q -o stat.json stat -json",
        ],
        directory,
    )
    cells = json.loads((directory / "stat.json").read_text())["modules"]["\\mon"]
    counts: dict[str, int] = cells["num_cells_by_type"]
    return counts.get("$_AND_", 0) + counts.get("$_NOT_", 0)


@pytest.mark.slow
@pytest.mark.parametrize("prop", SIZE_WINDOWS)
def test_auto_never_picks_more_logic_than_direct(
    yosys_tool: Tool, tmp_path: Path, prop: str
) -> None:
    """6."""
    machine = _machine(prop)
    direct = _gates(yosys_tool, tmp_path / "direct", machine, Form.DIRECT)
    window = _gates(yosys_tool, tmp_path / "window", machine, Form.WINDOW)
    chosen = choose(machine, Strategy.AUTO)
    print(f"{prop}: direct {direct} gates, window {window} gates, auto {chosen.value}")
    if chosen is Form.WINDOW:
        assert window < direct
    else:
        assert chosen is Form.DIRECT


@pytest.mark.slow
@pytest.mark.parametrize("prop", [*SIZE_CHAINS, "a |-> ##[1:16] b"])
def test_history_has_more_logic_than_direct(yosys_tool: Tool, tmp_path: Path, prop: str) -> None:
    """6."""
    machine = _machine(prop)
    direct = _gates(yosys_tool, tmp_path / "direct", machine, Form.DIRECT)
    history = _gates(yosys_tool, tmp_path / "history", machine, Form.HISTORY)
    print(f"{prop}: direct {direct} gates, history {history} gates")
    assert history > direct
