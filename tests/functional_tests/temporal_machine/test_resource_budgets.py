"""Shared early compiler guards (CONTRACT points 76-78)."""

from collections.abc import Callable
from dataclasses import replace
from unittest.mock import patch

import pytest

from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Expr, Signal, Unary, UnaryOp
from sva2yosys.ir.property import Implication, Nexttime, Not, Property, PropOr, SequenceProperty
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.attempts import AttemptMachine, build_attempt_machine
from sva2yosys.temporal.budgets import DEFAULT_LIMITS, BudgetError, ResourceLimits
from sva2yosys.temporal.compile import (
    CompileError,
    compile_attempt_machine,
    compile_configuration_machine,
    compile_directive,
    compile_monitor,
    compile_monitors,
)
from sva2yosys.temporal.machine import machine_size

LIMITS = ResourceLimits(16384, 20000000, 8, 200000)


def directive(n: int, kind: DirectiveKind = DirectiveKind.ASSERT) -> Directive:
    return Directive(
        kind,
        "p",
        "m",
        Clock(Signal("m.clk", BIT), Edge.POSEDGE),
        None,
        Implication(
            Bool(Signal("m.a", BIT)),
            SequenceProperty(SeqConcat(TRUE, Delay(n, n), Bool(Signal("m.b", BIT)))),
            True,
        ),
        SourceRange("resource.sv", 2, 3, 2, 4),
    )


@pytest.mark.parametrize("kind", list(DirectiveKind))
@pytest.mark.parametrize(
    "entry",
    [
        compile_directive,
        compile_monitor,
        compile_monitors,
        compile_attempt_machine,
        compile_configuration_machine,
    ],
)
def test_all_compiler_routes_reject_before_lowering(
    entry: Callable[[Directive], object], kind: DirectiveKind
) -> None:
    # The sentinel makes an absent preflight fail quickly, without expanding the range.
    with (
        patch("sva2yosys.temporal.compile.lower", side_effect=AssertionError("lower ran")),
        pytest.raises(CompileError, match="expansion"),
    ):
        entry(directive(10**100, kind))


def test_low_level_attempt_builder_checks_before_lowering() -> None:
    with (
        patch("sva2yosys.temporal.attempts.lower", side_effect=AssertionError("lower ran")),
        pytest.raises(BudgetError, match="expansion"),
    ):
        build_attempt_machine(directive(10**100))


def test_canonical_reachable_state_admits_the_boundary() -> None:
    machine = compile_directive(directive(8), limits=LIMITS)
    assert machine_size(machine).temporal_bits == 8
    assert machine_size(machine).canonical_bits > 8
    with pytest.raises(CompileError, match="7 temporal state bits"):
        compile_directive(directive(8), limits=replace(LIMITS, state_bits=7))


def test_deep_written_property_becomes_a_compile_diagnostic() -> None:
    # Origin: review; recursive estimation must not escape its boundary.
    prop: Property = directive(1).property
    for _ in range(3000):
        prop = Not(prop)
    with pytest.raises(CompileError, match="nesting exceeds compiler capacity"):
        compile_directive(replace(directive(1), property=prop))


def test_deep_plain_condition_becomes_a_compile_diagnostic() -> None:
    expr: Expr = Signal("m.b", BIT)
    for _ in range(3000):
        expr = Unary(UnaryOp.LOGICAL_NOT, expr, BIT)
    with pytest.raises(CompileError, match="nesting exceeds compiler capacity"):
        compile_directive(replace(directive(1), property=SequenceProperty(Bool(expr))))


@pytest.mark.parametrize("allowance", [200000, 65000])
def test_optimized_frontiers_admit_work_without_a_dense_probe_charge(allowance: int) -> None:
    # CONTRACT79/82. The 6c implementation charged 681892 visits for this shape.
    # The sparse implementation needs about 61000, including netlist finalization.
    prop = Implication(
        Repeat(Bool(Signal("m.a", BIT)), 1, 64),
        PropOr(
            (
                SequenceProperty(Bool(Signal("m.b", BIT))),
                Nexttime(1, False, SequenceProperty(Bool(Signal("m.c", BIT)))),
            )
        ),
        True,
    )
    monitor = compile_monitor(
        replace(directive(1), property=prop), limits=replace(DEFAULT_LIMITS, work=allowance)
    )
    assert isinstance(monitor, AttemptMachine)
