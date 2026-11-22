"""Unit contract of sva2yosys.temporal.machine, compile and simulate (module docstrings)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from support.reference.trace import Trace
from support.simulate import simulate
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Binary, BinaryOp, Sampled, SampledFunction, Signal
from sva2yosys.ir.property import Implication, Property, SequenceProperty
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat, Sequence
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.automaton import ConditionTable, build_automaton
from sva2yosys.temporal.compile import CompileError, compile_directive
from sva2yosys.temporal.machine import (
    MachineError,
    MachineSize,
    MachineState,
    antecedent_reach,
    initial_state,
    machine_size,
    slot_reach,
    step,
)

SA, SB, SC, SD = (Signal(f"m.{n}", BIT) for n in "abcd")
A, B, C, D = Bool(SA), Bool(SB), Bool(SC), Bool(SD)
PAST_A = Sampled(SampledFunction.PAST, SA, BIT)


def _directive(prop: Property, disable: Signal | None = None) -> Directive:
    clock = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
    return Directive(
        DirectiveKind.ASSERT, "p", "m", clock, disable, prop, SourceRange("t", 1, 1, 1, 2)
    )


def _imp(antecedent: Sequence, consequent: Sequence, overlapping: bool = True) -> Implication:
    return Implication(antecedent, SequenceProperty(consequent), overlapping)


def _trace(**rows: str) -> Trace:
    length = len(next(iter(rows.values())))
    return Trace(
        tuple({f"m.{n}": int(rows.get(n, "0" * length)[t]) for n in "abcd"} for t in range(length))
    )


# --- compile ---------------------------------------------------------------------------------


def test_sequence_property_has_no_antecedent() -> None:
    machine = compile_directive(_directive(SequenceProperty(SeqConcat(A, Delay(1, 2), B))))
    assert machine.antecedent is None
    assert machine.slots == 2


def test_nonoverlapping_implication_extends_the_antecedent() -> None:
    # a |=> c is (a ##1 1) |-> c (§16.12.7): the consequent starts at the next tick.
    machine = compile_directive(_directive(_imp(A, B, overlapping=False)))
    table = ConditionTable()
    assert machine.antecedent == build_automaton(SeqConcat(A, Delay(1, 1), TRUE), table)
    assert machine.consequent == build_automaton(B, table)
    assert machine.slots == 0


def test_nonoverlapping_implication_with_a_nullable_antecedent_is_rejected() -> None:
    # (a ##1 1) |-> c would let an empty antecedent match trigger, which IEEE excludes, and
    # a |-> ##1 c would decide an impossible consequent a tick early: neither is exact.
    nullable = Repeat(A, 0, 1)
    with pytest.raises(CompileError, match="can match empty"):
        compile_directive(_directive(_imp(nullable, B, overlapping=False)))


def test_condition_order_and_past_gating() -> None:
    prop = _imp(Bool(Binary(BinaryOp.LOGICAL_AND, PAST_A, SB, BIT)), C)
    machine = compile_directive(_directive(prop, disable=SD))
    assert machine.conditions[0] == SD
    assert machine.disable == 0
    assert machine.past_depth == (0, 1, 0)


@pytest.mark.parametrize(
    ("prop", "fragment"),
    [
        (Implication(A, _imp(B, C), overlapping=True), "nested"),
        (_imp(A, SeqConcat(B, Delay(1, None), C)), "unbounded"),
        (_imp(Bool(Binary(BinaryOp.LOGICAL_OR, PAST_A, SB, BIT)), C), "first sample"),
        (_imp(A, Repeat(B, 0, 2)), "empty match"),
        (SequenceProperty(Repeat(B, 0, 1)), "empty match"),
    ],
)
def test_compile_errors(prop: Property, fragment: str) -> None:
    with pytest.raises(CompileError, match=fragment):
        compile_directive(_directive(prop))


def test_machine_size() -> None:
    machine = compile_directive(_directive(_imp(SeqConcat(A, Delay(1, 1), B), Repeat(C, 1, 3))))
    # a ##1 b: the registered frontier only ever holds b's letter. c[*1:3]: age 1 can
    # only be at the second c, age 2 at the third.
    assert antecedent_reach(machine) == frozenset({1})
    assert slot_reach(machine) == (frozenset({1}), frozenset({2}))
    assert machine_size(machine) == MachineSize(
        conditions=3,
        antecedent_letters=2,
        consequent_letters=3,
        slots=2,
        temporal_bits=3,
        canonical_bits=8,
    )


def test_wide_windows_prune_to_linear_size() -> None:
    machine = compile_directive(_directive(_imp(A, SeqConcat(TRUE, Delay(1, 32), B))))
    size = machine_size(machine)
    assert size.canonical_bits > 1000
    assert size.temporal_bits < 70


# --- step priorities ------------------------------------------------------------------------


def test_initial_state_is_empty() -> None:
    machine = compile_directive(_directive(_imp(A, SeqConcat(TRUE, Delay(1, 3), B))))
    assert initial_state(machine) == MachineState(frozenset(), (frozenset(),) * 3)


def test_disable_comes_first_and_consults_nothing_else() -> None:
    machine = compile_directive(_directive(_imp(A, B), disable=SD))
    asked: list[int] = []

    def raw(index: int) -> bool:
        asked.append(index)
        return True

    state, events = step(machine, initial_state(machine), 1, raw)
    assert asked == [0]
    assert (events.disabled, events.triggered, events.success, events.failure) == (
        True,
        False,
        False,
        False,
    )
    assert state == initial_state(machine)


def test_past_gated_condition_is_not_consulted_at_the_first_sample() -> None:
    machine = compile_directive(_directive(_imp(Bool(PAST_A), B)))
    asked: list[int] = []

    def raw(index: int) -> bool:
        asked.append(index)
        return True

    _, events = step(machine, initial_state(machine), 0, raw)
    assert asked == []
    assert not events.triggered


def test_acceptance_discharges_the_whole_obligation() -> None:
    # a |-> b ##[0:2] c with a, b, c in sample 0: passes at 0, nothing stays open.
    simulation = simulate(
        compile_directive(_directive(_imp(A, SeqConcat(B, Delay(0, 2), C)))),
        _trace(a="100", b="100", c="100"),
    )
    assert simulation.successes == (0,)
    assert simulation.failures == ()
    assert simulation.pending == 0


def test_antecedent_keeps_later_endpoints() -> None:
    simulation = simulate(
        compile_directive(_directive(_imp(SeqConcat(A, Delay(1, 2), B), C))),
        _trace(a="1000", b="0110", c="0100"),
    )
    assert [s.events.triggered for s in simulation.snapshots] == [False, True, True, False]
    assert simulation.failures == (2,)


def test_an_accepting_antecedent_letter_keeps_its_continuation() -> None:
    # origin: mutant antecedent-drops-later-endpoints.
    # a ##1 b[*1:2] with a = 1 at sample 0 and b = 0 1 1 matches ending at 1 (b once) and at
    # 2 (b twice): the letter that ends the first match is also the start of the second, so it
    # must keep its continuation. With c = 0 1 0: the endpoint at 1 passes (c), the one at 2
    # fails (c low): the failure is at sample 2.
    antecedent = SeqConcat(A, Delay(1, 1), Repeat(B, 1, 2))
    simulation = simulate(
        compile_directive(_directive(_imp(antecedent, C))),
        _trace(a="1000", b="0110", c="0100"),
    )
    assert [s.events.triggered for s in simulation.snapshots] == [False, True, True, False]
    assert simulation.successes == (1,)
    assert simulation.failures == (2,)


def test_slot_bound_is_enforced() -> None:
    machine = compile_directive(_directive(_imp(A, SeqConcat(TRUE, Delay(1, 3), B))))
    too_small = replace(machine, slots=1)
    with pytest.raises(MachineError, match="slot"):
        simulate(too_small, _trace(a="1000", b="0000"))


def test_slot_bound_is_tight() -> None:
    # a |-> ##[1:5] b: the obligation from 0 is still open in its last slot before sample 5.
    machine = compile_directive(_directive(_imp(A, SeqConcat(TRUE, Delay(1, 5), B))))
    simulation = simulate(machine, _trace(a="100000", b="000000"))
    assert machine.slots == 5
    assert simulation.snapshots[4].after.slots[4]
    assert simulation.failures == (5,)


# --- simulate -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("b_at", "verdict"), [(12, "pass"), (None, "fail")])
def test_long_lifetime_uses_the_last_slot(b_at: int | None, verdict: str) -> None:
    # a |-> ##[1:12] b: twelve registered ages; the obligation from 0 is decided at 12.
    machine = compile_directive(_directive(_imp(A, SeqConcat(TRUE, Delay(1, 12), B))))
    b = "".join("1" if t == b_at else "0" for t in range(14))
    simulation = simulate(machine, _trace(a="1" + "0" * 13, b=b))
    assert machine.slots == 12
    assert simulation.snapshots[11].after.slots[11]
    decided = simulation.successes if verdict == "pass" else simulation.failures
    assert decided == (12,)


def test_snapshots_chain_states() -> None:
    machine = compile_directive(_directive(_imp(A, SeqConcat(TRUE, Delay(1, 1), B))))
    simulation = simulate(machine, _trace(a="110", b="010"))
    assert [s.sample for s in simulation.snapshots] == [0, 1, 2]
    assert simulation.snapshots[0].before == initial_state(machine)
    for earlier, later in zip(simulation.snapshots, simulation.snapshots[1:], strict=False):
        assert later.before == earlier.after
    assert simulation.successes == (1,)
    assert simulation.failures == (2,)
    assert simulation.pending == 0
