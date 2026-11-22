"""Contract points 13 and 37: what the generated driver presents at each rising edge (opt-in).

The formal form of two hand-written cases, with its assertion removed, is simulated by Verilator
(``--external-tools=verilator``): a testbench prints the value of every signal at each rising edge
before the non-blocking updates of the edge (what a property samples) and right after them (what a
``disable iff`` condition reads, IEEE 1800-2017 16.12). It needs no concurrent-assertion support and
the expectations are written by hand from the trace rows below.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support.toolchain import Tool
from support.trace_suite import render_formal
from support.tracefile import TraceCase, load_cases
from support.verilator import Config, Event, Status, configuration, run

FINITE = """\
== finite_wide_disable
property: a |-> ##1 b
disable: r == 4'd9
scope: attempt
signals: a b r:4
a : 1 . 1 . 1
b : . 1 . . .
r : 0 9 0 3 9
fail : . . . . .
pass : . . . . .
clause: 16.12
class: normative

== lasso_disable
property: a |-> s_eventually b
disable: r
scope: attempt
loop: 2
signals: a b r
a : 1 . . .
b : . . . 1
r : 0 0 1 0
outcome : F F F F
decided : . . . .
inf-outcome : F F F F
inf-decided : . . . .
clause: 16.12
class: normative
"""

EDGES = 10  # a few edges past the written samples: the hold of the last row and the lasso wrap


def _cases(tmp_path: Path) -> dict[str, TraceCase]:
    path = tmp_path / "timing.trace"
    path.write_text(FINITE, encoding="utf-8")
    return {case.name: case for case in load_cases(path)}


def _row(case: TraceCase, edge: int) -> int:
    """The row selected at ``edge`` (hand-derived rule: written samples, then the loop repeating
    forever for a lasso, the last row held for a finite trace)."""
    count = len(case.samples)
    if case.loop is None:
        return min(edge, count - 1)
    return edge if edge < count else case.loop + (edge - count) % (count - case.loop)


def _testbench(case: TraceCase, naive: bool = False) -> str:
    # the property is removed: only the driver is simulated; ``naive`` is the control without the
    # saved counter (every signal selected by the live counter, as a plain rising-edge driver)
    text = render_formal(case)
    if naive:
        text = text.replace("*row_idx_prev)", "*row_idx)")  # only the decoders of the rows
    lines = [
        line for line in text.split("\n") if not line.lstrip().startswith(("p:", "initial p:"))
    ]
    assert "assert property" not in "\n".join(lines)
    names = list(case.widths)
    pre = " ".join(f'$display("SVA2YOSYS_PROBE pre_{n} %0d", dut.{n});' for n in names)
    post = " ".join(f'$display("SVA2YOSYS_PROBE post_{n} %0d", dut.{n});' for n in names)
    return "\n".join(
        [
            *lines,
            "module top;",
            "  logic clk = 0;",
            "  sva_case dut(.clk(clk));",
            "  always #5 clk = ~clk;",
            f"  always @(posedge clk) begin {pre} end",  # Active region: before the updates
            f"  always @(posedge clk) begin #1; {post} end",  # after the updates
            "  initial begin",
            f"    #{10 * EDGES};",
            '    $display("SVA2YOSYS_PROBE done 1");',
            "    $finish;",
            "  end",
            "endmodule",
            "",
        ]
    )


def _series(events: tuple[Event, ...], label: str) -> list[int]:
    return [event.value for event in events if event.label == label]


@pytest.fixture(scope="module")
def verilator(verilator_tool: Tool) -> Config:
    return configuration(os.environ)


def _simulate(
    case: TraceCase, config: Config, tmp_path: Path, naive: bool = False
) -> tuple[Event, ...]:
    source = tmp_path / "top.sv"
    source.write_text(_testbench(case, naive), encoding="utf-8")
    result = run(config, source, tmp_path / "work")
    assert result.status is Status.FINISHED, result.detail
    return result.events


@pytest.mark.extended
@pytest.mark.parametrize("name", ["finite_wide_disable", "lasso_disable"])
def test_the_property_signals_show_row_k_before_edge_k_and_the_disable_signal_after_it(
    name: str, verilator: Config, tmp_path: Path
) -> None:
    case = _cases(tmp_path)[name]
    events = _simulate(case, verilator, tmp_path)
    disable_only = "r"
    for signal in case.widths:
        values = [sample[signal] for sample in case.samples]
        pre = _series(events, f"pre_{signal}")
        post = _series(events, f"post_{signal}")
        assert len(pre) == len(post) == EDGES, signal
        if signal == disable_only:
            # a disable iff condition reads current values, after the updates: row k at edge k
            assert post == [values[_row(case, k)] for k in range(EDGES)], (name, signal, post)
        else:
            # a property samples the values from before the updates: row k at edge k, and the
            # signal moves to row k + 1 right after the edge
            assert pre == [values[_row(case, k)] for k in range(EDGES)], (name, signal, pre)
            assert post == [values[_row(case, k + 1)] for k in range(EDGES)], (name, signal, post)


@pytest.mark.extended
def test_the_control_without_the_saved_counter_shows_the_disable_signal_the_next_row(
    verilator: Config, tmp_path: Path
) -> None:
    """The test above can see the defect it guards against: a plain rising-edge driver gives the
    ``disable iff`` condition row k + 1 at edge k."""
    case = _cases(tmp_path)["finite_wide_disable"]
    events = _simulate(case, verilator, tmp_path, naive=True)
    values = [sample["r"] for sample in case.samples]
    assert _series(events, "post_r") == [values[_row(case, k + 1)] for k in range(EDGES)]
    assert _series(events, "post_r") != [values[_row(case, k)] for k in range(EDGES)]
