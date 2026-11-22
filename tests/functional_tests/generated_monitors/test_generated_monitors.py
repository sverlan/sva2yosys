"""Layer 3: generated monitors through Yosys/SBY against the trace tables (CONTRACT.md)."""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from support.reference.evaluate import evaluate
from support.reference.trace import Trace
from support.toolchain import SbyResult, SbyStatus, Tool, run_sby, run_yosys
from support.tracefile import TraceCase, load_cases, sv_source
from sva2yosys.codegen.systemverilog import monitor_module, monitor_ports
from sva2yosys.ir.directive import Directive
from sva2yosys.optimize.forms import Form, Scope, scope, state_bits
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.compile import (
    CompileError,
    compile_directive,
    compile_monitors,
    conjunct_directives,
    validate_directive,
)
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.machine import Machine, machine_size

TABLES = Path(__file__).resolve().parents[1] / "reference_semantics" / "fixtures"
MODULE = "tc"
MONITOR = "__sva2yosys_p0"
ALL_CASES = [case for path in sorted(TABLES.glob("*.trace")) for case in load_cases(path)]
ORACLE_CASES = [case for case in ALL_CASES if case.oracle_only]
ATTEMPT_CASES = [case for case in ALL_CASES if case.attempt_scope and not case.oracle_only]
# the cases of the canonical machines (the optimised forms and compile_directive apply to them)
CASES = [case for case in ALL_CASES if not case.attempt_scope]
EXPRESSIONS = Path(__file__).resolve().parent / "fixtures" / "expressions.txt"
EXPRESSION_SIGNALS = {"a": 1, "b": 1, "u": 4, "v": 4, "w": 8, "s": 4, "z": 8}
SIGNED = frozenset({"s", "z"})
EXPRESSION_SAMPLES = 24

_SBY = """[tasks]
{mode}

[options]
mode {mode}
depth {depth}

[engines]
smtbmc yices

[script]
read_verilog -formal -sv monitor.sv tb.sv
prep -top tb

[files]
monitor.sv
tb.sv
"""


def _directive(name: str, source: str) -> Directive:
    conversion = convert(export_texts([(f"{name}.sv", source)], SlangOptions(top=(MODULE,))))
    assert conversion.rejections == ()
    return conversion.directives[0]


def _machine(case: TraceCase) -> Machine:
    return compile_directive(_directive(case.name, sv_source(case, MODULE)))


def _expression_source(condition: str) -> str:
    ports = ["input logic clk"]
    for name, width in EXPRESSION_SIGNALS.items():
        signed = " signed" if name in SIGNED else ""
        ports.append(f"input logic{signed} [{width - 1}:0] {name}")
    return (
        f"module {MODULE}({', '.join(ports)});\n"
        f"  p: assert property (@(posedge clk) {condition});\n"
        "endmodule\n"
    )


def _sample(rng: random.Random, width: int) -> int:
    """Corner values half of the time, so that equalities hold now and then."""
    if rng.random() < 0.5:
        return rng.choice([0, 1, 2, 3, (1 << width) - 1, 1 << (width - 1)]) & ((1 << width) - 1)
    return rng.randrange(1 << width)


def _expression_case(line: int, name: str, condition: str) -> tuple[TraceCase, Machine]:
    """An ``assert`` case for ``condition`` on seeded random samples, with the expected
    rows from the reference evaluator, and its machine."""
    rng = random.Random(name)
    samples = tuple(
        {signal: _sample(rng, width) for signal, width in EXPRESSION_SIGNALS.items()}
        for _ in range(EXPRESSION_SAMPLES)
    )
    directive = _directive(name, _expression_source(condition))
    trace = Trace(tuple({f"{MODULE}.{k}": v for k, v in row.items()} for row in samples))
    evaluation = evaluate(directive.property, trace, directive.disable)
    case = TraceCase(
        EXPRESSIONS.name,
        line,
        name,
        condition,
        "assert",
        None,
        dict(EXPRESSION_SIGNALS),
        samples,
        frozenset(evaluation.failures),
        frozenset(evaluation.successes),
        None,
    )
    return case, compile_directive(directive)


def _expression_lines() -> list[tuple[int, str, str]]:
    entries = []
    for number, text in enumerate(EXPRESSIONS.read_text().splitlines(), start=1):
        if text.strip() and not text.startswith("#"):
            name, _, condition = text.partition(":")
            entries.append((number, name.strip(), condition.strip()))
    return entries


def _bit(flag: bool) -> str:
    return "1'b1" if flag else "1'b0"


def _testbench(
    case: TraceCase,
    machines: Sequence[Machine | AttemptMachine | ConfigMachine],
    *,
    check: bool,
    until_first_failure: bool = False,
    canonical_attempt: bool = False,
) -> str:
    lines = ["module tb(input logic clk);", "    logic [7:0] t = 8'd0;"]
    lines.append("    always @(posedge clk) if (t != 8'd255) t <= t + 8'd1;")
    for name, width in case.widths.items():
        lines.append(f"    logic [{width - 1}:0] {name};")
    lines.append("    always @* begin")
    for name in case.widths:
        lines.append(f"        {name} = '0;")
    lines.append("        case (t)")
    for t, row in enumerate(case.samples):
        values = " ".join(f"{name} = {case.widths[name]}'d{v};" for name, v in row.items())
        lines.append(f"            8'd{t}: begin {values} end")
    lines.append("            default: ;")
    lines.append("        endcase")
    lines.append("    end")
    lines.append("    logic fail, success;")
    fails = []
    for index, machine in enumerate(machines):
        name = MONITOR if len(machines) == 1 else f"{MONITOR}_c{index}"
        ports = ", ".join(
            f".i{i}({path.removeprefix(MODULE + '.')})"
            for i, path in enumerate(monitor_ports(machine))
        )
        if len(machines) == 1:
            outputs = ".fail(fail), .success(success)"
        else:
            lines.append(f"    logic fail{index}, success{index};")
            fails.append(f"fail{index}")
            outputs = f".fail(fail{index}), .success(success{index})"
        connections = f".clk(clk){', ' + ports if ports else ''}, {outputs}"
        lines.append(f"    {name} #(.CHECK({_bit(check)})) u_monitor{index} ({connections});")
    if fails:
        # a property 'and': the conjunction fails when a conjunct does; success is undefined
        lines.append(f"    assign fail = {' | '.join(fails)};")
        lines.append("    assign success = 1'b0;")
    if not check:
        lines.append("    always @* begin")
        lines.append("        case (t)")
        last = (
            min(case.fails)
            if (until_first_failure or canonical_attempt) and case.fails and case.kind != "cover"
            else len(case.samples) - 1
        )
        for t in range(last + 1):
            if canonical_attempt:
                # a canonical machine in attempt scope: fail up to the first expected failure
                # (it reports per obligation), success only for a cover (per attempt)
                checks = []
                if case.kind == "cover":
                    checks.append(f"assert (success == {_bit(t in case.passes)});")
                else:
                    checks.append(f"assert (fail == {_bit(t in case.fails)});")
                lines.append(f"            8'd{t}: begin {' '.join(checks)} end")
                continue
            checks = (
                [] if until_first_failure else [f"assert (success == {_bit(t in case.passes)});"]
            )
            if case.kind != "cover":
                checks.append(f"assert (fail == {_bit(t in case.fails)});")
            lines.append(f"            8'd{t}: begin {' '.join(checks)} end")
        lines.append("            default: ;")
        lines.append("        endcase")
        lines.append("    end")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _sby(tool: Tool, tmp_path: Path, monitor: str, tb: str, depth: int, mode: str) -> SbyResult:
    (tmp_path / "monitor.sv").write_text(monitor)
    (tmp_path / "tb.sv").write_text(tb)
    sby_file = tmp_path / "case.sby"
    sby_file.write_text(_SBY.format(depth=depth, mode=mode))
    return run_sby(tool, sby_file, mode, tmp_path / "work")


def _run(
    tool: Tool,
    tmp_path: Path,
    case: TraceCase,
    machine: Machine,
    *,
    check: bool,
    mode: str = "bmc",
    form: Form = Form.DIRECT,
) -> SbyResult:
    monitor = monitor_module(machine, MONITOR, form)
    bench = _testbench(
        case, (machine,), check=check, until_first_failure=scope(form) is not Scope.EVERY_SAMPLE
    )
    return _sby(tool, tmp_path, monitor, bench, len(case.samples), mode)


def _and_run(tool: Tool, tmp_path: Path, case: TraceCase, *, check: bool) -> SbyResult:
    """A property 'and': one monitor per conjunct, their failures OR-ed, checked up to and
    including the first failure only."""
    machines = tuple(
        compile_directive(part)
        for part in conjunct_directives(_directive(case.name, sv_source(case, MODULE)))
    )
    monitors = "\n".join(
        monitor_module(machine, f"{MONITOR}_c{index}") for index, machine in enumerate(machines)
    )
    bench = _testbench(case, machines, check=check, until_first_failure=True)
    return _sby(tool, tmp_path, monitors, bench, len(case.samples), "bmc")


@pytest.mark.slow
@pytest.mark.parametrize("case", CASES, ids=[f"{c.file}::{c.name}" for c in CASES])
def test_trace_table_through_yosys(sby_tool: Tool, tmp_path: Path, case: TraceCase) -> None:
    """1. A ``scope: attempt`` case runs through the monitor of its per-attempt machine."""
    if case.attempt_scope:
        result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case))
        assert result.status is SbyStatus.PASS, result.log[-3000:]
        return
    if case.first_failure_only:
        result = _and_run(sby_tool, tmp_path, case, check=False)
        assert result.status is SbyStatus.PASS, result.log[-3000:]
        return
    result = _run(sby_tool, tmp_path, case, _machine(case), check=False)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.parametrize("case", ORACLE_CASES, ids=[f"{c.file}::{c.name}" for c in ORACLE_CASES])
def test_oracle_only_cases_are_rejected(case: TraceCase) -> None:
    """1: no monitor is generated for a property that only the reference evaluator runs."""
    directive = _directive(case.name, sv_source(case, MODULE))
    declared = re.escape(case.rejected_by) if case.rejected_by is not None else None
    with pytest.raises(CompileError, match=declared):
        validate_directive(directive)
    with pytest.raises(CompileError, match=declared):
        compile_monitors(directive)


def _attempt_machine(case: TraceCase) -> Machine | AttemptMachine | ConfigMachine:
    """The single monitor ``compile_monitors`` gives a ``scope: attempt`` case."""
    (monitor,) = compile_monitors(_directive(case.name, sv_source(case, MODULE)))
    return monitor


def _attempt_run(
    tool: Tool,
    tmp_path: Path,
    case: TraceCase,
    machine: Machine | AttemptMachine | ConfigMachine,
    *,
    check: bool = False,
    mode: str = "bmc",
) -> SbyResult:
    """The case through its monitor. A per-attempt machine: ``fail`` (not for a cover) and
    ``success`` equal the table at every sample. A canonical machine: ``fail`` up to and
    including the first expected failure, and ``success`` at every sample for a cover."""
    monitor = monitor_module(machine, MONITOR)
    bench = _testbench(
        case,
        (machine,),
        check=check,
        canonical_attempt=isinstance(machine, (Machine, ConfigMachine)),
    )
    return _sby(tool, tmp_path, monitor, bench, len(case.samples), mode)


@pytest.mark.slow
@pytest.mark.parametrize("case", ATTEMPT_CASES, ids=[f"{c.file}::{c.name}" for c in ATTEMPT_CASES])
def test_attempt_case_through_attempt_monitor(
    sby_tool: Tool, tmp_path: Path, case: TraceCase
) -> None:
    """10."""
    result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case))
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.parametrize(
    "name", ["disable_in_nested_implication", "disable_at_the_decision_sample"]
)
def test_default_representatives_of_the_attempt_tables_with_disable(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """10, default representatives: the per-attempt monitor's ``fail`` and ``success`` equal
    the hand-derived table where ``disable iff`` is high at a decision sample (origin:
    mutant attempt-success-not-suppressed)."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == name]
    result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case))
    assert result.status is SbyStatus.PASS, result.log[-3000:]


def test_a_disabled_true_attempt_reports_no_success_through_the_attempt_monitor(
    sby_tool: Tool, tmp_path: Path
) -> None:
    """10, hand-derived (origin: mutant attempt-success-not-suppressed). ``a |-> (b |->
    c)`` with ``disable iff (rst)``: at sample 0 ``a``, ``b`` and ``c`` hold, so the attempt
    would be TRUE and nonvacuous at 0, but ``rst`` is high there: no success at 0. At sample
    1 (``rst`` low, the same inputs) the attempt is TRUE and nonvacuous: success at 1."""
    (base,) = [c for c in ATTEMPT_CASES if c.name == "disable_at_the_decision_sample"]
    full = {"a": 1, "b": 1, "c": 1}
    case = replace(
        base,
        name="r9_disabled_true_attempt",
        samples=({"rst": 1, **full}, {"rst": 0, **full}),
        fails=frozenset(),
        passes=frozenset({1}),
        outcomes=None,
        decisions=None,
    )
    result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case))
    assert result.status is SbyStatus.PASS, result.log[-3000:]


UNBOUNDED_FAST = [
    "delay_to_dollar_antecedent",
    "delay_to_dollar_antecedent_assumed",
    "repetition_antecedent_nonoverlapping",
    "goto_antecedent",
    "nonconsecutive_antecedent_then_a_sample",
    "always_of_an_implication_with_an_offset",
    "implication_into_always",
    "unbounded_antecedent_with_disable_and_past",
    "always_with_disable_restarts_the_window",
]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index in (0, 5) else pytest.mark.extended)
        for index, value in enumerate(UNBOUNDED_FAST)
    ],
)
def test_unbounded_antecedent_through_the_monitor(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """1: the fast representatives of the unbounded antecedents and unbounded ``always``,
    split between default and extended; the full tables are in the slow run."""
    (case,) = [c for c in CASES if c.name == name]
    assert not case.oracle_only
    result = _run(sby_tool, tmp_path, case, _machine(case), check=False)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.extended
def test_unbounded_always_assertion_fires_at_the_first_failure(
    sby_tool: Tool, tmp_path: Path
) -> None:
    """2: an unbounded ``always`` fails at the first sample where its operand does."""
    (case,) = [c for c in CASES if c.name == "always_boolean"]
    result = _run(sby_tool, tmp_path, case, _machine(case), check=True)
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    assert [hit.step for hit in result.failed_assertions] == [2]


def test_unbounded_antecedent_output_is_deterministic() -> None:
    """3."""
    (case,) = [c for c in CASES if c.name == "goto_antecedent"]
    assert monitor_module(_machine(case), MONITOR) == monitor_module(_machine(case), MONITOR)


def test_the_optimised_forms_do_not_apply_to_an_unbounded_antecedent() -> None:
    """6: no table of an unbounded antecedent is run through ``window`` or ``history``, and
    asking the generator for them says why."""
    cases = [c for c in CASES if c.name in UNBOUNDED_FAST]
    assert len(cases) == len(UNBOUNDED_FAST)
    for case in cases:
        machine = _machine(case)
        for form in (Form.WINDOW, Form.HISTORY):
            if case.name.startswith("always") or "antecedent" in case.name:
                with pytest.raises(ValueError, match="unbounded antecedent"):
                    monitor_module(machine, MONITOR, form)


CONFIG_FAST = [
    "merged_frontier_counterexample_unbounded",
    "nonoverlapping_goto_consequent_matches",
    "overlapping_attempts_one_dies_while_others_match",
    "disable_aborts_every_unbounded_obligation",
    "past_inside_an_unbounded_consequent",
    "always_over_an_unbounded_consequent",
    "delay_unbounded_consequent_decides",
    "repeat_plus_then_c_fails",
]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index in (0, 1) else pytest.mark.extended)
        for index, value in enumerate(CONFIG_FAST)
    ],
)
def test_weak_unbounded_consequent_through_the_monitor(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """12: the fast representatives of the configuration-set monitors, run in
    the default suite; the full tables are in the slow run."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == name]
    machine = _attempt_machine(case)
    assert isinstance(machine, ConfigMachine)
    result = _attempt_run(sby_tool, tmp_path, case, machine)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


def test_configuration_monitor_header_names_its_form() -> None:
    """12."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == "merged_frontier_counterexample_unbounded"]
    machine = _attempt_machine(case)
    assert "form: configurations" in monitor_module(machine, MONITOR)
    assert monitor_module(machine, MONITOR) == monitor_module(machine, MONITOR)
    for form in (Form.WINDOW, Form.HISTORY):
        with pytest.raises(ValueError, match="direct form only"):
            monitor_module(machine, MONITOR, form)


@pytest.mark.extended
def test_configuration_assertion_fires_at_the_reference_sample(
    sby_tool: Tool, tmp_path: Path
) -> None:
    """12: ``a |-> b[*1:$] ##1 c`` fails at the sample where one obligation dies while another
    (in a different configuration) matches; the merged-frontier design would miss it."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == "merged_frontier_counterexample_unbounded"]
    machine = _attempt_machine(case)
    result = _attempt_run(sby_tool, tmp_path, case, machine, check=True)
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    assert [hit.step for hit in result.failed_assertions] == [1]


@pytest.mark.extended
def test_weak_unbounded_consequent_that_cannot_fail_is_proved(
    sby_tool: Tool, tmp_path: Path
) -> None:
    """12: ``a |-> ##[1:$] b`` has no failure on any trace (weak): the checked monitor passes
    on the trace that never raises ``b``."""
    (case,) = [
        c for c in ATTEMPT_CASES if c.name == "weak_unbounded_consequent_pending_finalizes_true"
    ]
    machine = _attempt_machine(case)
    result = _attempt_run(sby_tool, tmp_path, case, machine, check=True)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


UNTIL_FAST = [
    "implication_into_until",
    "implication_into_until_with",
    "always_of_an_implication_into_until",
    "past_in_p_of_until",
    "past_in_q_of_until_with_does_not_hold_at_the_first_sample",
    "until_false_q_holds_while_p_holds",
]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index in (0, 1) else pytest.mark.extended)
        for index, value in enumerate(UNTIL_FAST)
    ],
)
def test_weak_until_through_the_monitor(sby_tool: Tool, tmp_path: Path, name: str) -> None:
    """14: the fast representatives of ``until_machine.trace`` through the
    configuration-set monitor, run in the default suite; the full tables are in the slow
    run."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == name]
    machine = _attempt_machine(case)
    assert isinstance(machine, ConfigMachine)
    assert "form: configurations" in monitor_module(machine, MONITOR)
    result = _attempt_run(sby_tool, tmp_path, case, machine)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.extended
def test_until_assertion_fires_at_the_reference_sample(sby_tool: Tool, tmp_path: Path) -> None:
    """14: ``a |-> b until c`` fails at the sample where ``b`` and ``c`` are both low."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == "implication_into_until"]
    result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case), check=True)
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    assert [hit.step for hit in result.failed_assertions] == [4]


@pytest.mark.extended
def test_a_weak_until_that_cannot_fail_is_proved(sby_tool: Tool, tmp_path: Path) -> None:
    """14: ``a |-> b until 1'b0`` on a trace where ``b`` always holds has no failure."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == "until_false_q_holds_while_p_holds"]
    result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case), check=True)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


COVER_FAST = [
    "cover_delay_attempts_matching_at_the_same_sample",
    "cover_repetition_with_a_tail_frontier_dies",
    "cover_goto_two",
    "cover_implication_into_unbounded_delay",
    "cover_nonoverlapping_into_repetition_merged_configurations",
    "cover_disable_during_a_pending_attempt",
    "cover_unbounded_delay_to_constant_false_is_never_covered",
]
COVER_REACHED_FAST = [
    "cover_delay_counts_the_first_match_only",
    "cover_nonconsecutive_two_then_c",
    "cover_identical_configurations_of_two_attempts",
    "cover_past_inside_the_unbounded_part",
]
COVER_UNREACHED_FAST = [
    "cover_unbounded_delay_to_constant_false_is_never_covered",
    "cover_implication_into_unbounded_delay_pending",
]
UNBOUNDED_COVER_CASES = [
    case
    for case in ATTEMPT_CASES
    if case.kind == "cover"
    and case.file in ("unbounded_covers.trace", "unbounded.trace", "lasso.trace")
    and isinstance(_attempt_machine(case), ConfigMachine)
]


def _unbounded_cover_case(name: str) -> TraceCase:
    (case,) = [c for c in UNBOUNDED_COVER_CASES if c.name == name]
    return case


def test_the_unbounded_cover_cases_are_the_expected_ones() -> None:
    """15: the promoted cases and the new table are all run, as covers."""
    names = {case.name for case in UNBOUNDED_COVER_CASES}
    assert {
        "cover_unbounded_delay",
        "cover_unbounded_pending_finalizes_false",
        "cover_goto",
        "cover_defaults_to_strong",
    } <= names
    assert len([c for c in UNBOUNDED_COVER_CASES if c.file == "unbounded_covers.trace"]) >= 15
    assert set(COVER_FAST + COVER_REACHED_FAST + COVER_UNREACHED_FAST) <= names


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index == 0 else pytest.mark.extended)
        for index, value in enumerate(COVER_FAST)
    ],
)
def test_unbounded_cover_through_the_monitor(sby_tool: Tool, tmp_path: Path, name: str) -> None:
    """15: ``success`` equals the table's ``pass`` row at every sample (BMC); the full set is
    in the slow run."""
    case = _unbounded_cover_case(name)
    machine = _attempt_machine(case)
    assert "form: configurations" in monitor_module(machine, MONITOR)
    result = _attempt_run(sby_tool, tmp_path, case, machine)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.slow
@pytest.mark.parametrize(
    "case", UNBOUNDED_COVER_CASES, ids=[f"{c.file}::{c.name}" for c in UNBOUNDED_COVER_CASES]
)
def test_every_unbounded_cover_case_through_the_monitor(
    sby_tool: Tool, tmp_path: Path, case: TraceCase
) -> None:
    """15."""
    result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case))
    assert result.status is SbyStatus.PASS, result.log[-3000:]


def _cover_mode(sby_tool: Tool, tmp_path: Path, case: TraceCase) -> SbyResult:
    return _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case), check=True, mode="cover")


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index < 1 else pytest.mark.extended)
        for index, value in enumerate(COVER_REACHED_FAST)
    ],
)
def test_unbounded_cover_is_reached_at_the_references_first_success(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """15: under SBY's cover mode the statement is reached exactly at the first sample of the
    table's ``pass`` row (the trace is fixed by the table)."""
    case = _unbounded_cover_case(name)
    assert case.passes
    result = _cover_mode(sby_tool, tmp_path, case)
    assert result.status is SbyStatus.PASS, result.log[-3000:]
    assert [hit.step for hit in result.reached_covers] == [min(case.passes)]
    assert result.unreached_covers == ()


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index < 1 else pytest.mark.extended)
        for index, value in enumerate(COVER_UNREACHED_FAST)
    ],
)
def test_unbounded_cover_that_never_succeeds_is_unreachable(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """15: a table with an empty ``pass`` row (a constant-false letter; a consequent that never
    arrives) gives an unreached cover statement."""
    case = _unbounded_cover_case(name)
    assert not case.passes
    result = _cover_mode(sby_tool, tmp_path, case)
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    assert result.reached_covers == ()
    assert len(result.unreached_covers) == 1


@pytest.mark.slow
@pytest.mark.parametrize(
    "case", UNBOUNDED_COVER_CASES, ids=[f"{c.file}::{c.name}" for c in UNBOUNDED_COVER_CASES]
)
def test_every_unbounded_cover_case_in_cover_mode(
    sby_tool: Tool, tmp_path: Path, case: TraceCase
) -> None:
    """15: reached at the first ``pass`` sample, or unreached when the row is empty."""
    result = _cover_mode(sby_tool, tmp_path, case)
    if case.passes:
        assert result.status is SbyStatus.PASS, result.log[-3000:]
        assert [hit.step for hit in result.reached_covers] == [min(case.passes)]
    else:
        assert result.status is SbyStatus.FAIL, result.log[-3000:]
        assert result.reached_covers == () and len(result.unreached_covers) == 1


def test_a_cover_monitor_checks_the_cover_statement_only() -> None:
    """15: the generated cover has no assertion and no assumption of the property."""
    case = _unbounded_cover_case("cover_goto_two")
    text = monitor_module(_attempt_machine(case), MONITOR)
    assert "cover (success)" in text
    assert "assert (!fail)" not in text and "assume (!fail)" not in text


def test_nested_implication_through_attempt_monitor(sby_tool: Tool, tmp_path: Path) -> None:
    """10: the fast representative of the per-attempt monitors."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == "nested_implication"]
    result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case))
    assert result.status is SbyStatus.PASS, result.log[-3000:]


def test_monitor_without_temporal_registers_through_sby(sby_tool: Tool, tmp_path: Path) -> None:
    """10: ``not (a |-> b)`` has no register at all, so the module has no state."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == "not_of_implication"]
    machine = _attempt_machine(case)
    assert isinstance(machine, AttemptMachine)
    assert machine.netlist.registers == ()
    result = _attempt_run(sby_tool, tmp_path, case, machine)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.extended
def test_canonical_temporal_case_through_the_monitor(sby_tool: Tool, tmp_path: Path) -> None:
    """10: the fast representative of the canonical monitors of ``temporal.trace``
    (``always [1:2] b``, whose attempt-scope table is checked up to the first failure)."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == "always_weak_window_one_two"]
    machine = _attempt_machine(case)
    assert isinstance(machine, Machine)
    result = _attempt_run(sby_tool, tmp_path, case, machine)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


CONSTANT_CANONICAL_FAST = [
    "constant_false_one_sample_ahead_fails_one_sample_later",
    "constant_false_one_sample_ahead_is_disabled_at_that_sample",
    "constant_false_one_sample_ahead_as_an_assumption_is_disabled_at_that_sample",
    "constant_false_at_the_trigger_sample_fails_there",
    "not_of_a_sequence_ending_in_a_constant_false_passes_when_it_is_refuted",
    "intersect_of_different_fixed_lengths_fails_at_the_trigger_sample",
]
CONSTANT_ATTEMPT_FAST = [
    "constant_false_after_an_unbounded_delay_never_fails",
    "constant_false_after_a_loop_is_disabled_at_the_decisive_sample",
    "constant_false_after_a_loop_fails_when_the_loop_ends_without_it",
    "per_attempt_or_with_a_constant_false_operand_is_decided_when_it_is_refuted",
]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index == 0 else pytest.mark.extended)
        for index, value in enumerate(CONSTANT_CANONICAL_FAST)
    ],
)
def test_constant_false_letters_through_the_canonical_monitor(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """13: small ``constants.trace`` representatives, split between
    default and extended; the full tables are in the slow run (point 1)."""
    (case,) = [c for c in CASES if c.name == name]
    result = _run(sby_tool, tmp_path, case, _machine(case), check=False)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index in (0, 3) else pytest.mark.extended)
        for index, value in enumerate(CONSTANT_ATTEMPT_FAST)
    ],
)
def test_constant_false_letters_through_the_attempt_and_configuration_monitors(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """13."""
    (case,) = [c for c in ATTEMPT_CASES if c.name == name]
    result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case))
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.extended
def test_a_constant_false_assertion_fires_one_sample_after_its_trigger(
    sby_tool: Tool, tmp_path: Path
) -> None:
    """13: ``a |-> ##1 1'b0`` reports its failure at step 1, not at the trigger (step 0)."""
    (case,) = [
        c for c in CASES if c.name == "constant_false_one_sample_ahead_fails_one_sample_later"
    ]
    result = _run(sby_tool, tmp_path, case, _machine(case), check=True)
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    assert [hit.step for hit in result.failed_assertions] == [1]


ISUNKNOWN_FAST = [
    "mixed_conjunct_is_the_directive_without_the_x_part",
    "x_part_in_a_conditional_selects_the_other_branch",
    "x_part_alone_in_the_consequent_fails_with_the_trigger",
    "x_part_in_disable_iff_never_disables",
    "x_part_in_disable_iff_keeps_the_real_condition_disabling",
    "x_part_next_to_past_keeps_the_first_sample_rule",
    "mixed_cover_of_a_sequence",
]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index < 1 else pytest.mark.extended)
        for index, value in enumerate(ISUNKNOWN_FAST)
    ],
)
def test_a_directive_with_an_x_part_is_the_directive_without_it(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """16: the fast representatives of ``isunknown.trace``, run in the default suite;
    the full tables are in the slow run (point 1)."""
    (case,) = [c for c in CASES if c.name == name]
    assert case.file == "isunknown.trace"
    result = _run(sby_tool, tmp_path, case, _machine(case), check=False)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.slow
@pytest.mark.parametrize(
    ("line", "name", "condition"), _expression_lines(), ids=[e[1] for e in _expression_lines()]
)
def test_expression_values_through_yosys(
    sby_tool: Tool, tmp_path: Path, line: int, name: str, condition: str
) -> None:
    """4."""
    case, machine = _expression_case(line, name, condition)
    assert case.fails and case.passes, "the samples should exercise both outcomes"
    result = _run(sby_tool, tmp_path, case, machine, check=False)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.parametrize(
    "name",
    [
        "not_fails_at_the_first_match",
        "cover_counts_the_first_match_of_each_attempt",
        "past_depth_two_antecedent",
        "past_antecedent_does_not_hold_at_the_first_sample",
        "past_consequent_fails_at_the_first_sample",
        "rose_at_first_sample_uses_x_default",
        "first_sample_fell_stable_changed",
    ],
)
def test_default_representatives_of_the_table_rules_through_the_monitor(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """1, default representatives of rules that otherwise only run in the slow tables:
    the swap of ``not``, the first match of a cover, the ``$past(e, n)`` gate, the first
    sample of the edge functions (origin: mutants negation-not-swapped-in-slot-monitor,
    cover-counts-later-matches, past-gate-seen-off-by-one, rose/stable-ignores-the-first-sample;
    the expected rows are the hand-derived ones of the tables)."""
    (case,) = [c for c in CASES if c.name == name]
    result = _run(sby_tool, tmp_path, case, _machine(case), check=False)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.parametrize(
    "name",
    [
        "initial_starts_one_attempt",
        "initial_cover_reports_only_the_first_attempt",
        "initial_past_at_the_first_tick",
        "initial_disable_at_first_tick_leaves_no_attempt",
    ],
)
def test_initial_directives_through_the_monitor(sby_tool: Tool, tmp_path: Path, name: str) -> None:
    """1 (IEEE 1800-2017 16.14.6): fast representatives of ``initial.trace`` through
    the monitor: one attempt at the first tick, a cover reporting that attempt only, ``$past``
    without history at the first tick, a ``disable iff`` true at it (origin: mutant
    initial-first-sample-always-true; the rows are the hand-derived ones of the table)."""
    (case,) = [c for c in ALL_CASES if c.name == name]
    assert case.file == "initial.trace" and case.procedure == "initial"
    if case.attempt_scope:
        result = _attempt_run(sby_tool, tmp_path, case, _attempt_machine(case))
    else:
        result = _run(sby_tool, tmp_path, case, _machine(case), check=False)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.parametrize(
    "name", ["unsigned_compare", "signed_negative_constant", "rose_of_vector_lsb", "stable_vector"]
)
def test_default_representatives_of_the_expression_values_through_yosys(
    sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """4, default representatives: an unsigned ``<=``, a signed comparison with a negative
    constant, ``$rose`` and ``$stable`` of a vector (origin: mutant
    emit-less-equal-as-less)."""
    ((line, _, condition),) = [e for e in _expression_lines() if e[1] == name]
    case, machine = _expression_case(line, name, condition)
    assert case.fails and case.passes, "the samples should exercise both outcomes"
    result = _run(sby_tool, tmp_path, case, machine, check=False)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


def test_less_or_equal_holds_at_equality_through_yosys(sby_tool: Tool, tmp_path: Path) -> None:
    """4, hand-derived (origin: mutant emit-less-equal-as-less). ``u <= v`` on 4-bit
    unsigned values: (3, 3) holds (equal), (2, 3) holds, (4, 3) fails, (0, 0) holds; every
    sample is its own attempt, so the failure is at sample 2 and the successes are 0, 1, 3."""
    pairs = ((3, 3), (2, 3), (4, 3), (0, 0))
    samples = tuple({"a": 0, "b": 0, "u": u, "v": v, "w": 0, "s": 0, "z": 0} for u, v in pairs)
    directive = _directive("less_or_equal", _expression_source("u <= v"))
    case = TraceCase(
        EXPRESSIONS.name,
        0,
        "less_or_equal",
        "u <= v",
        "assert",
        None,
        dict(EXPRESSION_SIGNALS),
        samples,
        frozenset({2}),
        frozenset({0, 1, 3}),
        None,
    )
    result = _run(sby_tool, tmp_path, case, compile_directive(directive), check=False)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.parametrize(
    ("name", "step"),
    [("p01_fail_in_trigger_sample", 1), ("disable_masks_an_immediate_failure", 1)],
)
def test_assertion_fires_at_the_first_failure(
    sby_tool: Tool, tmp_path: Path, name: str, step: int
) -> None:
    """2: assert."""
    (case,) = [c for c in CASES if c.name == name]
    result = _run(sby_tool, tmp_path, case, _machine(case), check=True)
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    assert [hit.step for hit in result.failed_assertions] == [step]


@pytest.mark.parametrize(
    ("name", "step"),
    [("cover_sequence_ends_once_per_sample", 2), ("disable_masks_a_cover_match", 1)],
)
def test_cover_is_reached_at_the_first_success(
    sby_tool: Tool, tmp_path: Path, name: str, step: int
) -> None:
    """2: cover."""
    (case,) = [c for c in CASES if c.name == name]
    result = _run(sby_tool, tmp_path, case, _machine(case), check=True, mode="cover")
    assert result.status is SbyStatus.PASS, result.log[-3000:]
    assert [hit.step for hit in result.reached_covers] == [step]


_ASSUME_TB = """module tb(input logic clk, input logic b);
    logic [7:0] t = 8'd0;
    always @(posedge clk) if (t != 8'd255) t <= t + 8'd1;
    logic a;
    assign a = t == 8'd{trigger};
    logic fail, success;
    {monitor} #(.CHECK({check})) u_monitor (
        .clk(clk), .i0(a), .i1(b), .fail(fail), .success(success)
    );
    always @* if (t == 8'd{target}) assert (b);
endmodule
"""


@pytest.mark.parametrize("check", [True, False])
def test_assumption_constrains_the_sampled_obligation(
    sby_tool: Tool, tmp_path: Path, check: bool
) -> None:
    """2: assume. ``assume property (a |-> ##2 b)`` with ``a`` only at sample 1 forces the
    free input ``b`` at sample 3 and nowhere else."""
    case = TraceCase(
        "assume",
        1,
        "assume",
        "a |-> ##2 b",
        "assume",
        None,
        {"a": 1, "b": 1},
        (),
        frozenset(),
        frozenset(),
        None,
    )
    machine = _machine(case)
    assert monitor_ports(machine) == (f"{MODULE}.a", f"{MODULE}.b")
    monitor = monitor_module(machine, MONITOR)
    forced = _ASSUME_TB.format(monitor=MONITOR, check=_bit(check), trigger=1, target=3)
    result = _sby(sby_tool, tmp_path, monitor, forced, 6, "bmc")
    assert result.status is (SbyStatus.PASS if check else SbyStatus.FAIL), result.log[-3000:]
    if check:
        (tmp_path / "free").mkdir()
        free = _ASSUME_TB.format(monitor=MONITOR, check=_bit(check), trigger=1, target=2)
        result = _sby(sby_tool, tmp_path / "free", monitor, free, 6, "bmc")
        assert result.status is SbyStatus.FAIL, result.log[-3000:]


def test_output_is_deterministic() -> None:
    """3."""
    case = next(c for c in CASES if not c.first_failure_only)
    assert monitor_module(_machine(case), MONITOR) == monitor_module(_machine(case), MONITOR)


SIZE_CASES = [
    ("a |-> ##[1:3] b", 5),
    ("a |-> ##4 b", 4),
    ("a |-> ##[1:32] b", 63),
    # antecedent: b; ages 1-4: {c2}, {c3, d}, {c4, d}, {d}
    ("a ##1 b |-> c [*2:4] ##1 d", 7),
]


@pytest.mark.extended
@pytest.mark.parametrize(("prop", "bits"), SIZE_CASES, ids=[c[0] for c in SIZE_CASES])
def test_only_reachable_letters_are_registered(
    yosys_tool: Tool, tmp_path: Path, prop: str, bits: int
) -> None:
    """5."""
    case = TraceCase(
        "size",
        1,
        "size",
        prop,
        "assert",
        None,
        dict.fromkeys("abcd", 1),
        (),
        frozenset(),
        frozenset(),
        None,
    )
    machine = _machine(case)
    assert machine_size(machine).temporal_bits == bits
    (tmp_path / "monitor.sv").write_text(monitor_module(machine, MONITOR))
    log = run_yosys(
        yosys_tool,
        ["read_verilog -formal -sv monitor.sv", f"prep -top {MONITOR}", "stat -width"],
        tmp_path,
    )
    stats = log[log.rfind(f"=== {MONITOR} ===") :]
    flip_flops = sum(
        int(count) * int(width)
        for count, width in re.findall(r"^\s+(\d+)\s+\$\w*dff\w*_(\d+)", stats, re.M)
    )
    assert flip_flops <= bits


def _form_cases() -> list[tuple[TraceCase, Form]]:
    cases = []
    for case in CASES:
        if case.first_failure_only:
            continue  # each conjunct of a property 'and' picks its form on its own
        if case.oracle_only:
            continue  # rejected by the pipeline: no machine, no form
        machine = _machine(case)
        for form in (Form.WINDOW, Form.HISTORY):
            if state_bits(machine, form) is not None:
                cases.append((case, form))
    return cases


FORM_CASES = _form_cases()


@pytest.mark.slow
@pytest.mark.parametrize(
    ("case", "form"), FORM_CASES, ids=[f"{f.value}:{c.file}::{c.name}" for c, f in FORM_CASES]
)
def test_trace_table_through_optimised_forms(
    sby_tool: Tool, tmp_path: Path, case: TraceCase, form: Form
) -> None:
    """6."""
    result = _run(sby_tool, tmp_path, case, _machine(case), check=False, form=form)
    assert result.status is SbyStatus.PASS, result.log[-3000:]


@pytest.mark.parametrize("kind", ["assert", "assume", "cover"])
@pytest.mark.parametrize(
    "body",
    [
        "a ##[1:2] b",
        "a |-> b ##[0:2] a",
        "a |=> b[*1:2]",
        "a[*1:$] ##1 b |-> b ##[0:2] a",
        "a ##[1:$] b |=> b[*1:2]",
    ],
)
def test_strength_does_not_change_the_monitor(kind: str, body: str) -> None:
    """7. (A cover with an unbounded antecedent is rejected.)"""
    texts = set()
    for strength in ("", "strong", "weak"):
        if "|" in body:
            left, op, right = body.partition("|->") if "|->" in body else body.partition("|=>")
            prop = f"{left}{op} {strength}({right.strip()})" if strength else body
        else:
            prop = f"{strength}({body})" if strength else body
        source = (
            f"module {MODULE}(input logic clk, a, b);\n"
            f"  p: {kind} property (@(posedge clk) {prop});\nendmodule\n"
        )
        directive = _directive("s", source)
        if kind == "cover" and "$" in body:
            with pytest.raises(CompileError, match="unbounded"):
                compile_monitors(directive)
            return
        texts.add(monitor_module(compile_directive(directive), MONITOR))
    assert len(texts) == 1


@pytest.mark.parametrize("kind", ["assert", "assume", "cover"])
@pytest.mark.parametrize(
    ("weak", "strong"),
    [
        ("nexttime [2] b", "s_nexttime [2] b"),
        ("a |-> nexttime [1] b", "a |-> s_nexttime [1] b"),
        ("always [1:2] b", "s_always [1:2] b"),
        ("a |-> always [0:2] (b ##1 a)", "a |-> s_always [0:2] (b ##1 a)"),
        ("eventually [1:2] b", "s_eventually [1:2] b"),
        ("a |-> eventually [1:2] b", "a |-> s_eventually [1:2] b"),
        ("eventually [0:1] (a |-> b)", "s_eventually [0:1] (a |-> b)"),
    ],
)
def test_strength_does_not_change_a_temporal_operator_monitor(
    kind: str, weak: str, strong: str
) -> None:
    """10: weak and strong ``nexttime`` / ``always`` / ``eventually`` give the same monitor
    text (or the same rejection, e.g. a cover the cover rule rejects)."""
    outcomes: set[tuple[str, ...]] = set()
    for prop in (weak, strong):
        source = (
            f"module {MODULE}(input logic clk, a, b);\n"
            f"  p: {kind} property (@(posedge clk) {prop});\nendmodule\n"
        )
        try:
            monitors = compile_monitors(_directive("s", source))
        except CompileError as error:
            outcomes.add(("rejected", str(error)))
        else:
            outcomes.add(tuple(monitor_module(m, MONITOR) for m in monitors))
    assert len(outcomes) == 1
    if kind != "cover":
        assert "rejected" not in next(iter(outcomes))


@pytest.mark.extended
def test_a_thousand_accepting_antecedent_letters_load_in_yosys(
    yosys_tool: Tool, tmp_path: Path
) -> None:
    """20: planning probe; the old flat OR overflowed the parser stack."""
    source = (
        f"module {MODULE}(input logic clk, a, b);\n"
        "  p: assert property (@(posedge clk) a[*1:1000] |-> b);\nendmodule\n"
    )
    (machine,) = compile_monitors(_directive("depth_regression", source))
    (tmp_path / "monitor.sv").write_text(monitor_module(machine, "mon"), encoding="utf-8")
    run_yosys(
        yosys_tool,
        [
            "read_verilog -formal -sv monitor.sv",
            "hierarchy -check -top mon",
            "proc",
            "prep -top mon",
            "async2sync",
            "opt",
            "check -assert",
        ],
        tmp_path,
    )
