"""Layer 3: strong liveness of generated monitors through Yosys/SBY on lassos (CONTRACT.md,
points 17 and 18).

A test bench drives the monitor of a strong directive from a looping ROM (the samples of a
lasso, the counter running back to ``loop`` after the last one) and two SBY tasks run: ``prove``
(no define: the safety monitor only) and ``live`` (``-D SVA2YOSYS_LIVE``, suprove: the liveness
check). The expectation comes from the reference (``lasso_attempt_results``) on the unrolled
lasso: ``prove`` fails exactly when some attempt is FALSE at a finite sample; when it passes,
``live`` fails exactly when some attempt is FALSE (only in the limit). When ``prove`` fails,
``live`` is run too and passes: live mode turns the safety assertions into assumptions, so one
combined ``live`` task would hide the failure.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.reference.evaluate import Outcome
from support.reference.lasso import Lasso, lasso_attempt_results
from support.reference.lasso import _depth as _condition_depth
from support.toolchain import SbyStatus, Tool, run_sby
from support.tracefile import TraceCase, load_cases, sv_source
from sva2yosys.codegen.emit_configurations import live_define
from sva2yosys.codegen.systemverilog import monitor_module, monitor_ports
from sva2yosys.ir.directive import Directive
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import CompileError, compile_monitors
from sva2yosys.temporal.configurations import ConfigMachine

TABLES = Path(__file__).resolve().parents[1] / "reference_semantics" / "fixtures"
MODULE = "tc"
MONITOR = "__sva2yosys_p0"

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
prove: read_verilog -formal -sv monitor.sv tb.sv
live: read_verilog -formal -sv -D SVA2YOSYS_LIVE monitor.sv tb.sv
prep -top tb

[files]
monitor.sv
tb.sv
"""

_LOOP_T = """module loop_t #(parameter N = 1, parameter K = 0) (input clk, output reg [7:0] t);
  initial t = 8'd0;
  always @(posedge clk) t <= (t == N - 1) ? K : t + 8'd1;
endmodule
"""


# --- building directives, machines and benches ------------------------------------------------


def _from_source(
    name: str, source: str, with_liveness: bool = True
) -> tuple[Directive, ConfigMachine] | None:
    """The directive and its single configuration machine with a liveness part (with
    ``with_liveness`` false: a negated machine of a strong sequence, ``not strong(s)``, which
    has none), or ``None``."""
    conversion = convert(export_texts([(name, source)], SlangOptions(top=(MODULE,))))
    assert conversion.rejections == ()
    directive = conversion.directives[0]
    try:
        monitors = compile_monitors(directive)
    except CompileError:
        return None
    if len(monitors) != 1:
        return None
    (monitor,) = monitors
    if not isinstance(monitor, ConfigMachine):
        return None
    if with_liveness and monitor.liveness is None:
        return None
    if not with_liveness and (monitor.liveness is not None or not monitor.negated):
        return None
    return directive, monitor


def _property_source(prop: str, disable: str | None = None) -> str:
    guard = f"disable iff ({disable}) " if disable is not None else ""
    return (
        f"module {MODULE}(input logic clk, input logic a, input logic b, input logic c, "
        f"input logic d);\n  p: assert property (@(posedge clk) {guard}{prop});\nendmodule\n"
    )


@dataclass(frozen=True)
class LassoCase:
    """A lasso run: the directive, its monitor, the signal widths, the written samples (signal
    name to value) and the loop index."""

    name: str
    directive: Directive
    machine: ConfigMachine
    widths: dict[str, int]
    rows: tuple[dict[str, int], ...]
    loop: int


def _adhoc(
    prop: str,
    rows: list[dict[str, int]],
    loop: int,
    disable: str | None = None,
    with_liveness: bool = True,
) -> LassoCase:
    loaded = _from_source("adhoc.sv", _property_source(prop, disable), with_liveness)
    assert loaded is not None
    directive, machine = loaded
    widths = {name: 1 for name in ("a", "b", "c", "d")}
    full = tuple({name: row.get(name, 0) for name in widths} for row in rows)
    return LassoCase(f"{prop} {rows} loop {loop}", directive, machine, widths, full, loop)


def _table(case: TraceCase, with_liveness: bool = True) -> LassoCase | None:
    assert case.loop is not None
    loaded = _from_source(f"{case.name}.sv", sv_source(case, MODULE), with_liveness)
    if loaded is None:
        return None
    directive, machine = loaded
    rows = tuple(dict(row) for row in case.samples)
    return LassoCase(
        f"{case.file}::{case.name}", directive, machine, dict(case.widths), rows, case.loop
    )


def _bench(case: LassoCase) -> str:
    lines = [
        "module tb(input logic clk);",
        "    wire [7:0] t;",
        f"    loop_t #(.N({len(case.rows)}), .K({case.loop})) lp (.clk(clk), .t(t));",
    ]
    for name, width in case.widths.items():
        lines.append(f"    logic [{width - 1}:0] {name};")
    lines.append("    always @* begin")
    lines.extend(f"        {name} = '0;" for name in case.widths)
    lines.append("        case (t)")
    for index, row in enumerate(case.rows):
        values = " ".join(f"{name} = {case.widths[name]}'d{v};" for name, v in row.items())
        lines.append(f"            8'd{index}: begin {values} end")
    lines.append("            default: ;")
    lines.append("        endcase")
    lines.append("    end")
    ports = "".join(
        f", .i{i}({path.removeprefix(MODULE + '.')})"
        for i, path in enumerate(monitor_ports(case.machine))
    )
    lines.append(f"    {MONITOR} u_monitor (.clk(clk){ports}, .fail(), .success());")
    lines.append("endmodule")
    return "\n".join(lines) + "\n" + _LOOP_T


# --- the reference expectation ----------------------------------------------------------------


def _extended(lasso: Lasso, case: LassoCase) -> Lasso:
    """The same infinite trace with the loop unrolled far enough that every class of attempt
    start is a written start (as ``temporal_machine/test_liveness_machine.py``)."""
    depth = max(
        (*case.machine.past_depth, *(_condition_depth(c) for c in case.machine.conditions)),
        default=0,
    )
    wanted = lasso.loop + depth + 2 * lasso.period + 1
    samples = list(lasso.samples)
    while len(samples) < wanted:
        samples.extend(lasso.samples[lasso.loop :])
    return Lasso(tuple(samples), lasso.loop)


def _expected(case: LassoCase) -> tuple[bool, bool]:
    """``(prove fails, live fails when prove passes)`` from the reference."""
    lasso = Lasso(
        tuple({f"{MODULE}.{n}": v for n, v in row.items()} for row in case.rows), case.loop
    )
    results = lasso_attempt_results(
        case.directive.property, _extended(lasso, case), case.directive.disable
    )
    false = [r for r in results if r.outcome is Outcome.FALSE]
    return any(r.decided is not None for r in false), bool(false)


# --- running ---------------------------------------------------------------------------------


def _run(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path, case: LassoCase
) -> tuple[SbyStatus, SbyStatus]:
    """The statuses of the ``prove`` task and of the ``live`` task of ``case``."""
    (tmp_path / "monitor.sv").write_text(monitor_module(case.machine, MONITOR))
    (tmp_path / "tb.sv").write_text(_bench(case))
    sby_file = tmp_path / "lasso.sby"
    sby_file.write_text(_SBY)
    prove = run_sby(sby_tool, sby_file, "prove", tmp_path / "work_prove")
    live = run_sby(sby_live_tool, sby_file, "live", tmp_path / "work_live")
    return prove.status, live.status


def _check(sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path, case: LassoCase) -> None:
    prove_fails, any_false = _expected(case)
    prove, live = _run(sby_tool, sby_live_tool, tmp_path, case)
    assert prove is (SbyStatus.FAIL if prove_fails else SbyStatus.PASS), f"prove: {case.name}"
    if prove_fails:
        # live mode turns the safety assertions into assumptions: the failure is hidden there,
        # which is why a strong directive holds only when both tasks pass
        assert live is SbyStatus.PASS, f"live (safety fails): {case.name}"
    else:
        assert live is (SbyStatus.FAIL if any_false else SbyStatus.PASS), f"live: {case.name}"


# --- fast representatives ---------------------------------------------------------------------

_ROW = dict[str, int]


def _r(**values: int) -> _ROW:
    return dict(values)


FAST = {
    # (property, rows, loop, expected prove status, expected live status): hand-derived
    "pending-true": ("a |-> s_eventually b", [_r(a=1, b=0), _r(a=0, b=1)], 0, "PASS", "PASS"),
    "pending-false-in-the-limit": ("a |-> s_eventually b", [_r(a=1, b=0)], 0, "PASS", "FAIL"),
    "witness-same-sample-no-repick": (
        "a |-> strong(##[1:$] b)",
        [_r(a=1, b=1)],
        0,
        "PASS",
        "PASS",
    ),
    "witness-false-in-the-limit": ("a |-> strong(##[1:$] b)", [_r(a=1, b=0)], 0, "PASS", "FAIL"),
    "safety-fails-and-live-hides-it": (
        "a[*1:2] |-> strong(c ##[1:$] b)",
        [_r(a=1, c=1, b=0), _r(a=1, c=0, b=0), _r(a=0, c=0, b=0)],
        2,
        "FAIL",
        "PASS",
    ),
    # not weak(s) is strong, its liveness part is that of the strong forms (L1 for a single
    # configuration, L2 otherwise); the safety part fails at a match
    "not-until-pending-for-ever": ("not (a until b)", [_r(a=1, b=0)], 0, "PASS", "FAIL"),
    "not-until-matches": ("not (a until b)", [_r(a=1, b=1)], 0, "FAIL", "PASS"),
    "not-until-dies": ("not (a until b)", [_r(a=0, b=0)], 0, "PASS", "PASS"),
    "not-delay-pending-for-ever": ("not (a ##[1:$] b)", [_r(a=1, b=0)], 0, "PASS", "FAIL"),
    "not-delay-matches": ("not (a ##[1:$] b)", [_r(a=1, b=1)], 0, "FAIL", "PASS"),
    "not-delay-dies": ("not (a ##[1:$] b)", [_r(a=0, b=0)], 0, "PASS", "PASS"),
    "not-delay-pending-only-in-every-other-period": (
        "not (a ##[1:$] b)",
        [_r(a=1, b=0), _r(a=0, b=0)],
        0,
        "PASS",
        "FAIL",
    ),
}


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index in (0, 1, 2, 3, 4, 5, 8) else pytest.mark.extended)
        for index, value in enumerate(FAST)
    ],
)
def test_fast_representatives_through_the_prove_and_live_tasks(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path, name: str
) -> None:
    """17: PENDING true and false in the limit, WITNESS (no re-pick; false in the limit), and
    the combined-task mutant (``prove`` fails, ``live`` passes). The statuses are hand-derived
    and agree with the reference."""
    prop, rows, loop, prove, live = FAST[name]
    case = _adhoc(prop, rows, loop)
    expected_prove, expected_live = SbyStatus[prove], SbyStatus[live]
    prove_fails, any_false = _expected(case)
    assert prove_fails is (expected_prove is SbyStatus.FAIL)
    if not prove_fails:
        assert any_false is (expected_live is SbyStatus.FAIL)
    got_prove, got_live = _run(sby_tool, sby_live_tool, tmp_path, case)
    assert (got_prove, got_live) == (expected_prove, expected_live)


# not strong(s) is weak: no liveness block, the prove task decides it
FAST_NOT_STRONG = {
    "not-strong-delay-pending-for-ever-is-true": (
        "not strong(a ##[1:$] b)",
        [_r(a=1, b=0)],
        0,
        "PASS",
    ),
    "not-strong-delay-matches": ("not strong(a ##[1:$] b)", [_r(a=1, b=1)], 0, "FAIL"),
    "not-s-until-pending-for-ever-is-true": ("not (a s_until b)", [_r(a=1, b=0)], 0, "PASS"),
    "not-s-eventually-b": ("not s_eventually b", [_r(b=0), _r(b=0), _r(b=1)], 2, "FAIL"),
}


def _run_prove(sby_tool: Tool, tmp_path: Path, case: LassoCase) -> SbyStatus:
    (tmp_path / "monitor.sv").write_text(monitor_module(case.machine, MONITOR))
    (tmp_path / "tb.sv").write_text(_bench(case))
    sby_file = tmp_path / "lasso.sby"
    sby_file.write_text(_SBY)
    return run_sby(sby_tool, sby_file, "prove", tmp_path / "work_prove").status


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index < 2 else pytest.mark.extended)
        for index, value in enumerate(FAST_NOT_STRONG)
    ],
)
def test_not_strong_is_decided_by_the_prove_task_alone(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """18: ``not strong(s)`` has no liveness block; ``prove`` fails exactly at a match (a
    pending-for-ever obligation is TRUE). The statuses are hand-derived and agree with the
    reference."""
    prop, rows, loop, prove = FAST_NOT_STRONG[name]
    case = _adhoc(prop, rows, loop, with_liveness=False)
    assert "SVA2YOSYS_LIVE" not in monitor_module(case.machine, MONITOR)
    prove_fails, any_false = _expected(case)
    assert prove_fails is (prove == "FAIL") and any_false is prove_fails
    assert _run_prove(sby_tool, tmp_path, case) is SbyStatus[prove]


# --- every lasso case of the trace tables -----------------------------------------------------

_LASSO_CASES = [
    case
    for path in sorted(TABLES.glob("*.trace"))
    for case in load_cases(path)
    if case.loop is not None and case.kind == "assert" and not case.oracle_only
]


@functools.cache
def _table_cases() -> list[LassoCase]:
    return [lasso for case in _LASSO_CASES if (lasso := _table(case)) is not None]


def _ids() -> list[str]:
    return [case.name for case in _table_cases()]


@pytest.mark.slow
@pytest.mark.parametrize("case", _table_cases(), ids=_ids())
def test_every_lasso_case_through_the_prove_and_live_tasks(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path, case: LassoCase
) -> None:
    """17: ``prove`` fails exactly when an attempt is FALSE at a finite sample, ``live`` exactly
    when one is FALSE in the limit only, and passes when ``prove`` fails."""
    _check(sby_tool, sby_live_tool, tmp_path, case)


def test_the_lasso_cases_include_the_promoted_and_the_new_ones() -> None:
    names = {case.name for case in _table_cases()}
    files = {name.split("::")[0] for name in names}
    assert {
        "lasso.trace",
        "constants.trace",
        "strong_liveness.trace",
        "not_liveness.trace",
    } <= files
    assert "strong_liveness.trace" in files and len(names) >= 20
    assert len([n for n in names if n.startswith("not_liveness.trace::")]) >= 16


@functools.cache
def _negated_strong_cases() -> list[LassoCase]:
    return [
        lasso
        for case in _LASSO_CASES
        if case.file == "not_liveness.trace"
        and (lasso := _table(case, with_liveness=False)) is not None
    ]


@pytest.mark.slow
@pytest.mark.parametrize("case", _negated_strong_cases(), ids=lambda c: c.name)
def test_every_negated_strong_lasso_case_through_the_prove_task(
    sby_tool: Tool, tmp_path: Path, case: LassoCase
) -> None:
    """18: ``prove`` fails exactly when an attempt is FALSE at a finite sample, and no attempt
    is FALSE only in the limit (nothing for ``live`` to find)."""
    prove_fails, any_false = _expected(case)
    assert any_false is prove_fails
    assert _run_prove(sby_tool, tmp_path, case) is (
        SbyStatus.FAIL if prove_fails else SbyStatus.PASS
    )


def test_the_negated_strong_lasso_cases_are_the_expected_ones() -> None:
    assert len(_negated_strong_cases()) == 8


# --- the define block -------------------------------------------------------------------------

# the per-monitor switch and the liveness block it guards: SVA2YOSYS_LIVE (all monitors)
# defines SVA2YOSYS_LIVE_p0 (this monitor); an assume is also switched on by SVA2YOSYS_LIVE_FAIR
_BLOCK = re.compile(
    r"(?:`ifdef SVA2YOSYS_LIVE(?:_FAIR)?\n`define SVA2YOSYS_LIVE_p0\n`endif\n)+"
    r"`ifdef SVA2YOSYS_LIVE_p0\n.*?`endif\n",
    re.DOTALL,
)


def _monitor_text(prop: str, kind: str = "assert") -> str:
    source = (
        f"module {MODULE}(input logic clk, input logic a, input logic b, input logic c);\n"
        f"  p: {kind} property (@(posedge clk) {prop});\nendmodule\n"
    )
    conversion = convert(export_texts([("t.sv", source)], SlangOptions(top=(MODULE,))))
    assert conversion.rejections == ()
    (monitor,) = compile_monitors(conversion.directives[0])
    text = monitor_module(monitor, MONITOR)
    return "".join(line for line in text.splitlines(keepends=True) if "// source:" not in line)


STRONG_AND_WEAK = [
    ("a |-> strong(##[1:$] b)", "a |-> weak(##[1:$] b)"),
    ("strong(a ##[1:$] b)", "weak(a ##[1:$] b)"),
    ("s_eventually b", "weak(1'b1[*0:$] ##1 b)"),
    ("a |-> s_eventually b", "a |-> weak(1'b1[*0:$] ##1 b)"),
    ("always (a |-> s_eventually b)", "always (a |-> weak(1'b1[*0:$] ##1 b))"),
    ("a s_until b", "a until b"),
    ("a s_until_with b", "a until_with b"),
    ("a |-> strong(b[->2])", "a |-> weak(b[->2])"),
]

# a negated weak sequence differs from the negated strong one by the define block
# only (the safety part is the same negated machine)
NOT_WEAK_AND_STRONG = [
    ("not weak(a ##[1:$] b)", "not strong(a ##[1:$] b)"),
    ("not (a ##[1:$] b)", "not strong(a ##[1:$] b)"),
    ("not (a until b)", "not (a s_until b)"),
    ("not (a until_with b)", "not (a s_until_with b)"),
    ("a |-> not (b[->2])", "a |-> not strong(b[->2])"),
    ("always (a |-> not (b until c))", "always (a |-> not (b s_until c))"),
]


@pytest.mark.parametrize(("strong", "weak"), STRONG_AND_WEAK)
def test_without_the_define_the_monitor_is_the_weak_monitor(strong: str, weak: str) -> None:
    """17: the define block is the only difference (apart from the source comment), so the
    ``prove`` task sees the safety monitor of the weak machine."""
    text = _monitor_text(strong)
    assert text.count("`ifdef SVA2YOSYS_LIVE") == 2 and text.count("`endif") == 2
    assert _BLOCK.sub("", text) == _monitor_text(weak)
    assert "SVA2YOSYS_LIVE" not in _monitor_text(weak)


@pytest.mark.parametrize(("weak", "strong"), NOT_WEAK_AND_STRONG)
def test_without_the_define_a_negated_weak_monitor_is_the_negated_strong_monitor(
    weak: str, strong: str
) -> None:
    """18: ``not weak(s)`` has a liveness block, ``not strong(s)`` has none; the rest is the
    same monitor, so the ``prove`` task sees the safety monitor of the negated machine."""
    text = _monitor_text(weak)
    assert text.count("`ifdef SVA2YOSYS_LIVE") == 2 and text.count("`endif") == 2
    assert _BLOCK.sub("", text) == _monitor_text(strong)
    assert "SVA2YOSYS_LIVE" not in _monitor_text(strong)


@pytest.mark.parametrize(
    ("prop", "kind"),
    [("a |-> ##[1:$] b", "assert"), ("a |-> ##2 b", "assert"), ("a ##[1:$] b", "cover")],
)
def test_weak_bounded_and_cover_monitors_have_no_define_block(prop: str, kind: str) -> None:
    """17."""
    assert "SVA2YOSYS_LIVE" not in _monitor_text(prop, kind)


@pytest.mark.parametrize(
    ("prop", "token"),
    [
        ("a |-> s_eventually b", "assign busy = cfg_q[0];"),
        ("a |-> strong(##[1:$] b)", "$anyseq"),
        ("not (a until b)", "assign busy = cfg_q[0];"),
        ("not (a ##[1:$] b)", "$anyseq"),
    ],
)
def test_the_define_block_declares_busy_and_the_check(prop: str, token: str) -> None:
    """17: PENDING reads the configuration register, WITNESS has the free choice; both check
    ``s_eventually !busy`` under ``CHECK``."""
    (block,) = _BLOCK.findall(_monitor_text(prop))
    assert token in block
    assert "if (CHECK)" in block and "assert property (s_eventually !busy);" in block
    assert _monitor_text(prop) == _monitor_text(prop)  # deterministic


@pytest.mark.parametrize(
    ("strong", "weak"),
    [
        ("a |-> s_eventually b", "a |-> weak(1'b1[*0:$] ##1 b)"),
        ("a s_until b", "a until b"),
        ("not (a until b)", "not (a s_until b)"),
    ],
)
def test_a_strong_assume_differs_from_the_weak_monitor_by_the_fairness_block_only(
    strong: str, weak: str
) -> None:
    """19: the define block of an ``assume`` is the fairness constraint
    ``assume property (s_eventually !busy)`` (the pending bit), not an assertion; without the
    define the monitor is the weak one, so the ``prove`` task sees the safety assumption only."""
    text = _monitor_text(strong, "assume")
    (block,) = _BLOCK.findall(text)
    assert "assign busy = cfg_q[0];" in block
    assert "assume property (s_eventually !busy);" in block
    assert "assert property" not in block
    assert _BLOCK.sub("", text) == _monitor_text(weak, "assume")
    assert "SVA2YOSYS_LIVE" not in _monitor_text(weak, "assume")


# --- the per-monitor switch -------------------------------------------------------------


def test_an_assert_is_switched_on_by_the_global_or_its_own_define_only() -> None:
    """17: ``SVA2YOSYS_LIVE`` switches every monitor on by defining the monitor's own
    ``SVA2YOSYS_LIVE_p0``, the block tests that name, and the fairness define does not reach an
    ``assert``: SBY's ``live`` mode decides a task from its first justice verdict only, so each
    liveness part needs a task of its own."""
    text = _monitor_text("a |-> s_eventually b")
    head = text[text.index("`ifdef SVA2YOSYS_LIVE") :]
    assert head.startswith("`ifdef SVA2YOSYS_LIVE\n`define SVA2YOSYS_LIVE_p0\n`endif\n")
    assert "SVA2YOSYS_LIVE_FAIR" not in text
    assert text.count("`ifdef SVA2YOSYS_LIVE_p0\n") == 1


def test_a_fairness_assume_is_also_switched_on_by_the_fairness_define() -> None:
    """19: a fairness constraint applies to every run, so ``SVA2YOSYS_LIVE_FAIR`` (defined
    in each per-monitor task) switches the ``assume`` on beside the global define."""
    text = _monitor_text("a |-> s_eventually b", "assume")
    assert "`ifdef SVA2YOSYS_LIVE_FAIR\n`define SVA2YOSYS_LIVE_p0\n`endif\n" in text
    assert text.count("`ifdef SVA2YOSYS_LIVE\n`define SVA2YOSYS_LIVE_p0\n`endif\n") == 1


@pytest.mark.parametrize(
    ("module", "define"),
    [
        ("__sva2yosys_p0", "SVA2YOSYS_LIVE_p0"),
        ("__sva2yosys_p12_c1", "SVA2YOSYS_LIVE_p12_c1"),
        ("tc", "SVA2YOSYS_LIVE_tc"),
    ],
)
def test_the_per_monitor_define_is_named_after_the_module(module: str, define: str) -> None:
    """17."""
    assert live_define(module) == define
