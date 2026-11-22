"""Layer 3: strong ``assume`` (a fairness constraint) of generated monitors through Yosys/SBY
(CONTRACT.md, point 19).

The generated monitors of an ``assume`` and of an ``assert`` are instantiated in a design whose
inputs are free (the SBY top inputs), and two SBY tasks run on it: ``prove`` (no define: the
safety monitors only) and ``live`` (``-D SVA2YOSYS_LIVE``, suprove: the liveness blocks). In the
live task the assumption's block is a fairness constraint, ``assume property (s_eventually
!busy)``, and the assertion's block is checked on the fair traces only. A design with only
assumptions and no assertion is an SBY error (yosys_contract point 12), so every design here has
an assertion. The statuses are hand-derived.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from support.toolchain import SbyStatus, Tool, run_sby
from sva2yosys.codegen.systemverilog import monitor_module, monitor_ports
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import compile_monitors

MODULE = "tc"
SIGNALS = ("a", "b", "req", "ack", "d")
_PASS, _FAIL = SbyStatus.PASS, SbyStatus.FAIL

_SBY = """[tasks]
prove
live

[options]
prove: mode prove
live: mode live

[engines]
prove: abc pdr
live: aiger suprove

[script]
prove: read_verilog -formal -sv design.sv
live: read_verilog -formal -sv -D SVA2YOSYS_LIVE design.sv
prep -top top

[files]
design.sv
"""

_FAIRNESS_BLOCK = "assume property (s_eventually !busy);"


def _monitor(index: int, kind: str, prop: str, disable: str | None = None) -> tuple[str, str]:
    """The monitor module of ``<kind> property (prop)`` named ``m<index>`` and its instance in
    the top module (all inputs of the monitor are top-level wires of the same names)."""
    guard = f"disable iff ({disable}) " if disable is not None else ""
    decls = ", ".join(f"input logic {name}" for name in SIGNALS)
    source = (
        f"module {MODULE}(input logic clk, {decls});\n"
        f"  p: {kind} property (@(posedge clk) {guard}{prop});\nendmodule\n"
    )
    conversion = convert(export_texts([(f"m{index}.sv", source)], SlangOptions(top=(MODULE,))))
    assert conversion.rejections == ()
    (monitor,) = compile_monitors(conversion.directives[0])
    name = f"m{index}"
    text = monitor_module(monitor, name)
    ports = "".join(
        f", .i{i}({path.removeprefix(MODULE + '.')})"
        for i, path in enumerate(monitor_ports(monitor))
    )
    return text, f"  {name} u{index} (.clk(clk){ports}, .fail(), .success());\n"


def _design(
    monitors: list[tuple[str, str]],
    internal: str = "",
    driven: frozenset[str] = frozenset(),
) -> str:
    """The top module: free inputs for the signals the monitors read, except the ``driven`` ones
    (declared by ``internal``), then the monitor instances."""
    inputs = "".join(f", input logic {name}" for name in SIGNALS if name not in driven)
    instances = "".join(instance for _, instance in monitors)
    modules = "".join(text for text, _ in monitors)
    return f"{modules}module top(input logic clk{inputs});\n{internal}{instances}endmodule\n"


def _run(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path, design: str
) -> tuple[SbyStatus, SbyStatus]:
    (tmp_path / "design.sv").write_text(design)
    sby_file = tmp_path / "fair.sby"
    sby_file.write_text(_SBY)
    prove = run_sby(sby_tool, sby_file, "prove", tmp_path / "work_prove")
    live = run_sby(sby_live_tool, sby_file, "live", tmp_path / "work_live")
    return prove.status, live.status


def _fairness_as_invariant(text: str) -> str:
    """The mutant of the fairness block: the invariant ``assume (!busy)`` at every sample
    instead of ``assume property (s_eventually !busy)``."""
    assert _FAIRNESS_BLOCK in text, "the assume monitor must carry the fairness constraint"
    return text.replace(_FAIRNESS_BLOCK, "assume (!busy);")


# (assume property, assert property, expected live status): hand-derived on the free inputs
FAIR_CASES = {
    # every request is answered on a fair trace: the assertion holds
    "assume-answers-the-assert": (
        "req |-> s_eventually ack",
        "req |-> s_eventually ack",
        _PASS,
    ),
    # fair traces include one with req once and ack only later: the stronger assertion fails
    "assume-does-not-answer-a-stronger-assert": (
        "req |-> s_eventually ack",
        "req |-> s_eventually (req && ack)",
        _FAIL,
    ),
    # not (a until b) with its fairness: every attempt dies (!a && !b at some sample), so a drops
    "not-until-assume-makes-a-drop-eventual": (
        "not (a until b)",
        "s_eventually (!a || b)",
        _PASS,
    ),
}
# the same assertions without the assume monitor: free inputs can answer nothing
UNASSUMED_CASES = {
    "the-assert-alone-fails": ("req |-> s_eventually ack", _FAIL),
    "the-stronger-assert-alone-fails": ("req |-> s_eventually (req && ack)", _FAIL),
    "the-drop-assert-alone-fails": ("s_eventually (!a || b)", _FAIL),
}


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index < 2 else pytest.mark.extended)
        for index, value in enumerate(FAIR_CASES)
    ],
)
def test_a_fairness_assume_decides_the_live_task_and_leaves_prove_alone(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path, name: str
) -> None:
    """19: with the assume monitor the ``live`` task proves or refutes the assertion on fair
    traces only; the ``prove`` task passes (neither monitor has a safety failure)."""
    assumed, asserted, live = FAIR_CASES[name]
    design = _design([_monitor(0, "assume", assumed), _monitor(1, "assert", asserted)])
    assert _run(sby_tool, sby_live_tool, tmp_path, design) == (_PASS, live)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index < 1 else pytest.mark.extended)
        for index, value in enumerate(UNASSUMED_CASES)
    ],
)
def test_without_the_assume_monitor_the_liveness_assertion_fails(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path, name: str
) -> None:
    """19: the controls of the cases above (the assume monitor removed)."""
    asserted, live = UNASSUMED_CASES[name]
    design = _design([_monitor(1, "assert", asserted)])
    assert _run(sby_tool, sby_live_tool, tmp_path, design) == (_PASS, live)


@pytest.mark.extended
def test_mutant_invariant_reading_of_the_fairness_block_is_caught(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path
) -> None:
    """19: with ``assume (!busy)`` at every sample instead of the fairness constraint, every
    ``req`` would force ``ack`` in the same sample, so ``req |-> s_eventually (req && ack)`` passes
    although a fair trace with ``req`` once and ``ack`` only later refutes it."""
    assumed, asserted, live = FAIR_CASES["assume-does-not-answer-a-stronger-assert"]
    assert live is _FAIL
    assume_text, assume_instance = _monitor(0, "assume", assumed)
    mutant = (_fairness_as_invariant(assume_text), assume_instance)
    design = _design([mutant, _monitor(1, "assert", asserted)])
    assert _run(sby_tool, sby_live_tool, tmp_path, design)[1] is _PASS  # the mutant is wrong


# the whole liveness part of a monitor: its per-monitor switch and the block it guards
_LIVE_BLOCK = re.compile(
    r"(?:`ifdef SVA2YOSYS_LIVE(?:_FAIR)?\n`define SVA2YOSYS_LIVE_\w+\n`endif\n)+"
    r"`ifdef SVA2YOSYS_LIVE_\w+\n.*?`endif\n",
    re.DOTALL,
)


@pytest.mark.extended
def test_mutant_fairness_block_missing_is_caught(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path
) -> None:
    """19: without the fairness block (the safety assume alone) the liveness assertion that the
    fairness proves fails."""
    assumed, asserted, _ = FAIR_CASES["assume-answers-the-assert"]
    assume_text, assume_instance = _monitor(0, "assume", assumed)
    stripped = _LIVE_BLOCK.sub("", assume_text)
    assert stripped != assume_text
    design = _design([(stripped, assume_instance), _monitor(1, "assert", asserted)])
    assert _run(sby_tool, sby_live_tool, tmp_path, design) == (_PASS, _FAIL)


@pytest.mark.extended
def test_the_prove_task_sees_no_fairness_constraint(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path
) -> None:
    """19: the safety assertion ``!req`` fails in ``prove`` although every fair trace satisfying
    ``assume !ack`` and ``assume req |-> s_eventually ack`` has no ``req`` (the documented
    conservative result); the result is the one of the design with the liveness block removed."""
    fair_text, fair_instance = _monitor(0, "assume", "req |-> s_eventually ack")
    stripped = _LIVE_BLOCK.sub("", fair_text)
    assert stripped != fair_text
    others = [_monitor(1, "assume", "!ack"), _monitor(2, "assert", "!req")]
    full = _design([(fair_text, fair_instance), *others])
    plain = _design([(stripped, fair_instance), *others])
    (tmp_path / "full").mkdir()
    (tmp_path / "plain").mkdir()
    status_full = _prove(sby_tool, tmp_path / "full", full)
    status_plain = _prove(sby_tool, tmp_path / "plain", plain)
    assert status_full is _FAIL and status_plain is status_full


def _prove(sby_tool: Tool, tmp_path: Path, design: str) -> SbyStatus:
    (tmp_path / "design.sv").write_text(design)
    sby_file = tmp_path / "fair.sby"
    sby_file.write_text(_SBY)
    return run_sby(sby_tool, sby_file, "prove", tmp_path / "work_prove").status


# the boundary cases (plan review), hand-derived; the inputs are driven inside the design


def _toggle_and_pulse() -> str:
    """``req`` high and ``ack`` low for ever; ``d`` toggling (``recurring``) or high in the first
    sample only (``once``)."""
    return (
        "  logic first = 1'b1;\n"
        "  always @(posedge clk) first <= 1'b0;\n"
        "  logic toggle = 1'b0;\n"
        "  always @(posedge clk) toggle <= !toggle;\n"
        "  wire req = 1'b1;\n"
        "  wire ack = 1'b0;\n"
    )


@pytest.mark.extended
@pytest.mark.parametrize(
    ("d_signal", "live"),
    [
        # every obligation is aborted by the next d: the trace is admitted, so the assertion
        # `s_eventually ack` (which it violates, ack is never high) fails
        ("toggle", _FAIL),
        # a single d at the first sample only: the later obligations are pending for ever, the
        # fairness assumption rules the trace out, and the assertion holds vacuously
        ("first", _PASS),
    ],
)
def test_disable_recurring_is_admitted_and_a_single_pulse_is_not(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path, d_signal: str, live: SbyStatus
) -> None:
    """19: ``assume property (disable iff (d) req |-> s_eventually ack)`` with ``req = 1`` and
    ``ack = 0`` for ever: ``d`` recurring admits the trace (each obligation is aborted by the next
    ``d``), a single ``d`` does not (the obligations launched after it stay pending for ever)."""
    internal = _toggle_and_pulse() + f"  wire d = {d_signal};\n"
    design = _design(
        [
            _monitor(0, "assume", "req |-> s_eventually ack", disable="d"),
            _monitor(1, "assert", "s_eventually ack"),
        ],
        internal,
        frozenset({"req", "ack", "d"}),
    )
    assert _run(sby_tool, sby_live_tool, tmp_path, design) == (_PASS, live)


@pytest.mark.extended
def test_a_past_antecedent_never_triggers_at_the_first_sample(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path
) -> None:
    """19: ``assume property ($past(req) |-> s_eventually ack)`` with ``req = 0`` for ever has no
    trigger (not at the first sample, where ``$past`` is unknown), so the trace is admitted and
    the assertion ``s_eventually ack`` (``ack`` never high) fails."""
    internal = "  wire req = 1'b0;\n  wire ack = 1'b0;\n"
    design = _design(
        [
            _monitor(0, "assume", "$past(req) |-> s_eventually ack"),
            _monitor(1, "assert", "s_eventually ack"),
        ],
        internal,
        frozenset({"req", "ack"}),
    )
    assert _run(sby_tool, sby_live_tool, tmp_path, design) == (_PASS, _FAIL)
