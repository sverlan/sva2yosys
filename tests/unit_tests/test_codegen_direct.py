"""Unit contract of the automaton-selected compact DIRECT layout."""

from dataclasses import replace

import pytest

from sva2yosys.codegen.direct import DirectKind, DirectLayout, layout
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Signal
from sva2yosys.ir.property import SequenceProperty
from sva2yosys.ir.sequence import Bool
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.automaton import Automaton, Letter
from sva2yosys.temporal.machine import Machine, slot_reach


def _machine(automaton: Automaton, slots: int) -> Machine:
    a, b = Signal("m.a", BIT), Signal("m.b", BIT)
    directive = Directive(
        DirectiveKind.ASSERT,
        "p",
        "m",
        Clock(Signal("m.clk", BIT), Edge.POSEDGE),
        None,
        SequenceProperty(Bool(a)),  # Deliberately unrelated to the actual automaton.
        SourceRange("unit.sv", 1, 1, 1, 2),
    )
    return Machine(directive, (a, b), (0, 0), None, None, automaton, slots)


CHAIN = Automaton(
    (Letter((0,), (2,), False), Letter((1,), (), True), Letter((), (1,), False)),
    (0,),
    False,
)
WINDOW = Automaton(
    (
        Letter((), (1, 2), False),
        Letter((1,), (), True),
        Letter((), (1, 3), False),
        Letter((), (1,), False),
    ),
    (0,),
    False,
)


@pytest.mark.parametrize("kind", list(DirectiveKind))
@pytest.mark.parametrize(
    ("automaton", "slots", "expected"),
    [
        (CHAIN, 2, DirectLayout(DirectKind.CHAIN, ((0,), (2,), (1,)))),
        (WINDOW, 3, DirectLayout(DirectKind.WINDOW, ((0,), (1, 2), (1, 3), (1,)))),
        (
            Automaton((Letter((), (), True),), (0,), False),
            0,
            DirectLayout(DirectKind.CHAIN, ((0,),)),
        ),
    ],
)
def test_layout_uses_the_automaton_and_keeps_canonical_state_bits(
    kind: DirectiveKind, automaton: Automaton, slots: int, expected: DirectLayout
) -> None:
    machine = _machine(automaton, slots)
    machine = replace(machine, directive=replace(machine.directive, kind=kind))
    found = layout(machine)
    assert found == expected
    assert found.ages[1:] == tuple(tuple(sorted(age)) for age in slot_reach(machine))


@pytest.mark.parametrize(
    "automaton",
    [
        Automaton((), (), True),
        Automaton((Letter((), (0,), True),), (0,), False),  # A cycle.
        Automaton((Letter((0,), (), True), Letter((1,), (), True)), (0, 1), False),
        Automaton(
            (Letter((0,), (1, 2), False), Letter((1,), (), True), Letter((), (), True)),
            (0,),
            False,
        ),  # Guarded branching is not a Boolean delay window.
        Automaton(
            (
                Letter((), (1, 2), False),
                Letter((0,), (), True),
                Letter((), (3,), False),
                Letter((), (1,), False),
            ),
            (0,),
            False,
        ),  # Matching ages 1 and 3, with a hole at 2.
    ],
)
def test_other_shapes_retain_the_dense_fallback(automaton: Automaton) -> None:
    assert layout(_machine(automaton, 3)) is None


def test_an_accepting_chain_keeps_later_structural_slots_but_discharges_at_runtime() -> None:
    automaton = Automaton((Letter((0,), (1,), True), Letter((0,), (), True)), (0,), False)
    assert layout(_machine(automaton, 1)) == DirectLayout(DirectKind.CHAIN, ((0,), (1,)))


def test_first_sample_is_the_complement_of_the_init_bit() -> None:
    """``FirstSample`` is ``!valid``, and the bit is emitted without any ``$past``."""
    from sva2yosys.codegen.systemverilog import monitor_module
    from sva2yosys.ir.expr import FirstSample
    from sva2yosys.ir.property import Implication
    from sva2yosys.ir.sequence import Delay, SeqConcat
    from sva2yosys.temporal.compile import compile_directive

    a, b = Bool(Signal("m.a", BIT)), Bool(Signal("m.b", BIT))
    prop = Implication(SeqConcat(Bool(FirstSample(BIT)), Delay(0, 0), a), SequenceProperty(b), True)
    source = SourceRange("t", 1, 1, 1, 2)
    clock = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
    directive = Directive(DirectiveKind.ASSERT, "p", "m", clock, None, prop, source)
    text = monitor_module(compile_directive(directive), "mon")
    assert "= !valid;" in text
    assert "logic valid = 1'b0;" in text
    assert "always @(posedge clk) valid <= 1'b1;" in text
