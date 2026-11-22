"""Selected-layout emission work, CONTRACT point 22."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from sva2yosys.codegen.systemverilog import monitor_module
from sva2yosys.ir.expr import BIT, Expr, Sampled, SampledFunction, Signal, Unary, UnaryOp
from sva2yosys.ir.property import Not, Property
from sva2yosys.optimize.forms import Form
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.budgets import DEFAULT_LIMITS, BudgetError, ResourceLimits
from sva2yosys.temporal.compile import Monitor, compile_monitors
from sva2yosys.temporal.machine import Machine


def _monitor(prop: str, limits: ResourceLimits = DEFAULT_LIMITS) -> Monitor:
    text = (Path(__file__).parent / "fixtures" / "depth.sv").read_text(encoding="utf-8")
    text = text.replace("@KIND@", "assert").replace("@PROPERTY@", prop)
    conversion = convert(export_texts([("resources.sv", text)], SlangOptions(top=("tc",))))
    (directive,) = conversion.directives
    (monitor,) = compile_monitors(directive, limits=limits)
    return monitor


def _machine(prop: str) -> Machine:
    machine = _monitor(prop)
    assert isinstance(machine, Machine)
    return machine


def test_compact_work_is_admitted_when_history_replay_exceeds_the_limit() -> None:
    machine = _machine("a |-> ##64 b")
    limits = ResourceLimits(16384, 20000000, 8192, 5000)
    compact = monitor_module(machine, "mon", limits=limits)
    assert compact == monitor_module(machine, "mon")
    with pytest.raises(BudgetError, match="emission"):
        monitor_module(machine, "mon", Form.HISTORY, limits=limits)


def test_manually_built_machine_cannot_bypass_the_span_guard() -> None:
    machine = replace(_machine("a |-> ##1 b"), slots=10**100)
    with (
        patch("sva2yosys.codegen.direct.layout", side_effect=AssertionError("layout ran")),
        pytest.raises(BudgetError, match="expansion"),
    ):
        monitor_module(machine, "mon")


def test_manual_machine_histories_are_checked_independently_of_its_source() -> None:
    # Origin: accounting review requires an independent low-level emitter guard.
    machine = _machine("a |-> b")
    conditions = tuple(
        Sampled(SampledFunction.PAST, Signal(f"tc.history{i}", BIT), BIT, depth=20)
        for i in range(2)
    )
    manual = replace(machine, conditions=conditions, past_depth=(20, 20))
    with pytest.raises(BudgetError, match="sampled history"):
        monitor_module(manual, "mon", limits=replace(DEFAULT_LIMITS, expansion=32))


def test_manual_deep_source_reports_resource_exhaustion() -> None:
    machine = _machine("a |-> b")
    prop: Property = machine.directive.property
    for _ in range(3000):
        prop = Not(prop)
    manual = replace(machine, directive=replace(machine.directive, property=prop))
    with pytest.raises(BudgetError, match="nesting exceeds compiler capacity"):
        monitor_module(manual, "mon")


def test_manual_deep_condition_reports_resource_exhaustion() -> None:
    # Origin: follow-up review, plain expressions have the same hash hazard.
    machine = _machine("a |-> b")
    expr: Expr = Signal("tc.a", BIT)
    for _ in range(3000):
        expr = Unary(UnaryOp.LOGICAL_NOT, expr, BIT)
    with pytest.raises(BudgetError, match="nesting exceeds compiler capacity"):
        monitor_module(replace(machine, conditions=(expr,)), "mon")


@pytest.mark.parametrize("prop", ["a |-> b", "a |-> ##[1:$] b", "(a |-> b) or (a |-> ##1 b)"])
def test_compilation_preserves_the_policy_until_emission(prop: str) -> None:
    limits = replace(DEFAULT_LIMITS, emission=1)
    monitor = _monitor(prop, limits)
    assert monitor.limits is limits
    with pytest.raises(BudgetError, match="emission"):
        monitor_module(monitor, "mon")
    # An explicit later override is allowed and changes no generated semantics/text.
    assert monitor_module(monitor, "mon", limits=DEFAULT_LIMITS) == monitor_module(
        _monitor(prop), "mon"
    )


@pytest.mark.slow
def test_sparse_attempt_compiles_but_default_emission_allowance_rejects() -> None:
    # Origin: capacity measurements; rejection phase changes after optimization.
    monitor = _monitor("a[*1:2044] |-> (b or nexttime c)")
    with pytest.raises(BudgetError, match="200000 emission references"):
        monitor_module(monitor, "mon")
    assert monitor_module(monitor, "mon", limits=replace(DEFAULT_LIMITS, emission=300000))
