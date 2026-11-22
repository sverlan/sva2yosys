"""Unit tests of ``sva2yosys.temporal.liveness`` (the documented contract of its module
docstring): the liveness kind of a configuration machine and the witness step.

The expectations are derived by hand from the rules of ``witness_step``; the conditions of
the property ``a |-> strong(##[1:$] b)`` are indexed ``a`` = 0, ``b`` = 1 (``disable iff
(d)`` gives ``d`` = 0, ``a`` = 1, ``b`` = 2). Its consequent automaton has the unguarded
start letter ``1`` (the leading ``##[1:$]`` filler), whose successors are the unguarded
loop letter and the accepting ``b`` letter: two configurations, ``{start}`` and
``{loop, b}`` (so the machine is a witness machine).
"""

from __future__ import annotations

import pytest

from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Sampled, SampledFunction, Signal
from sva2yosys.ir.property import (
    Eventually,
    Implication,
    Not,
    Property,
    SequenceProperty,
    Strength,
    Until,
)
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.compile import (
    CompileError,
    compile_configuration_machine,
    compile_monitors,
    never_fails_warning,
)
from sva2yosys.temporal.configurations import ConfigMachine, never_fails
from sva2yosys.temporal.liveness import Liveness, liveness_kind, witness_step
from sva2yosys.temporal.machine import StepEvents

_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("t.sv", 1, 1, 1, 2)
_A, _B, _C, _D = (Signal(f"m.{n}", BIT) for n in "abcd")
_STRONG = Strength.STRONG


def _machine(
    prop: Property,
    kind: DirectiveKind = DirectiveKind.ASSERT,
    disable: Signal | None = None,
) -> ConfigMachine:
    directive = Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE)
    return compile_configuration_machine(directive)


def _delayed_b(low: int = 1) -> SequenceProperty:
    return SequenceProperty(SeqConcat(TRUE, Delay(low, None), Bool(_B)), _STRONG)


def _events(
    disabled: bool = False, triggered: bool = False, success: bool = False, failure: bool = False
) -> StepEvents:
    return StepEvents(disabled, triggered, success, failure)


def _values(*high: int):  # type: ignore[no-untyped-def]
    return lambda condition: condition in high


# --- the kind --------------------------------------------------------------------------------


def test_the_kind_is_the_pending_bit_for_at_most_one_configuration() -> None:
    assert liveness_kind(0) is Liveness.PENDING
    assert liveness_kind(1) is Liveness.PENDING
    assert liveness_kind(2) is Liveness.WITNESS
    assert liveness_kind(7) is Liveness.WITNESS


_S_EVENTUALLY_B = SequenceProperty(SeqConcat(Repeat(TRUE, 0, None), Delay(1, 1), Bool(_B)), _STRONG)


@pytest.mark.parametrize(
    ("prop", "count", "kind"),
    [
        # one configuration: the start frontier is a fixed point (a loop letter at the start)
        (SequenceProperty(Repeat(Bool(_B), 1, None), _STRONG), 1, Liveness.PENDING),
        (
            Implication(Bool(_A), SequenceProperty(Repeat(Bool(_B), 1, None), _STRONG), True),
            1,
            Liveness.PENDING,
        ),
        (Until(_A, _B, True, False), 1, Liveness.PENDING),
        (Until(_A, _B, True, True), 1, Liveness.PENDING),
        # strong(1[*0:$] ##1 b), the lowering of s_eventually b: the start frontier {loop, b}
        # is its own successor
        (_S_EVENTUALLY_B, 1, Liveness.PENDING),
        (Implication(Bool(_A), _S_EVENTUALLY_B, True), 1, Liveness.PENDING),
        (Eventually(0, None, True, SequenceProperty(Bool(_B))), 1, Liveness.PENDING),
        # strong(##[1:$] b) after a start letter: two configurations
        (Implication(Bool(_A), _delayed_b(1), True), 2, Liveness.WITNESS),
        (Implication(Bool(_A), _delayed_b(2), True), 3, Liveness.WITNESS),
        (
            SequenceProperty(SeqConcat(Bool(_A), Delay(1, None), Bool(_B)), _STRONG),
            2,
            Liveness.WITNESS,
        ),
    ],
)
def test_the_machine_kind_follows_the_number_of_configurations(
    prop: Property, count: int, kind: Liveness
) -> None:
    machine = _machine(prop)
    assert len(machine.configurations) == count
    assert machine.liveness is kind


def test_there_is_no_liveness_part_for_a_weak_consequent_or_a_cover() -> None:
    weak = SequenceProperty(SeqConcat(TRUE, Delay(1, None), Bool(_B)), Strength.WEAK)
    assert _machine(Implication(Bool(_A), weak, True)).liveness is None
    default = SequenceProperty(SeqConcat(TRUE, Delay(1, None), Bool(_B)))
    assert _machine(Implication(Bool(_A), default, True)).liveness is None
    strong = Implication(Bool(_A), _delayed_b(1), True)
    assert _machine(strong, DirectiveKind.COVER).liveness is None
    assert _machine(strong).liveness is Liveness.WITNESS


def test_compile_monitors_gives_the_strong_assert_a_liveness_kind() -> None:
    directive = Directive(
        DirectiveKind.ASSERT,
        "p",
        "m",
        _CLOCK,
        None,
        Implication(Bool(_A), _delayed_b(), True),
        _SOURCE,
    )
    (monitor,) = compile_monitors(directive)
    assert isinstance(monitor, ConfigMachine) and monitor.liveness is Liveness.WITNESS


def test_a_strong_assume_has_a_pending_bit_liveness_part_with_one_configuration() -> None:
    """``assume a |-> s_eventually b`` (one configuration, ``{loop, b}``) gets the
    pending bit as its liveness part, as the assert does; a ``cover`` still gets none."""
    prop = Implication(Bool(_A), Eventually(0, None, True, SequenceProperty(Bool(_B))), True)
    machine = _machine(prop, DirectiveKind.ASSUME)
    assert len(machine.configurations) == 1
    assert machine.liveness is Liveness.PENDING


def test_a_strong_assume_needing_a_witness_is_rejected() -> None:
    """``assume a |-> strong(##[1:$] b)`` has two configurations: a witness would
    pick one obligation, so as an assumption it would be weaker than the property."""
    prop = Implication(Bool(_A), _delayed_b(), True)
    with pytest.raises(CompileError, match=r"a strong unbounded property \(liveness\) in 'assume'"):
        _machine(prop, DirectiveKind.ASSUME)
    directive = Directive(DirectiveKind.ASSUME, "p", "m", _CLOCK, None, prop, _SOURCE)
    with pytest.raises(CompileError, match=r"\(liveness\) in 'assume'"):
        compile_monitors(directive)


# --- witness_step ----------------------------------------------------------------------------

_M = _machine(Implication(Bool(_A), _delayed_b(), True))
_START, _LOOPING = _M.configurations  # {start}, {loop, b}


def test_an_idle_witness_stays_idle_without_a_trigger_or_a_pick() -> None:
    assert witness_step(_M, None, _events(), True, 3, _values(0, 1)) is None
    assert witness_step(_M, None, _events(triggered=True), False, 3, _values(0, 1)) is None


def test_an_idle_witness_picks_at_the_trigger_and_evaluates_the_obligation_there() -> None:
    # the start letter has no guard: b is not looked at in the trigger sample
    after = witness_step(_M, None, _events(triggered=True), True, 3, _values(0))
    assert after == _LOOPING
    # also when b happens to hold there: the first letter is the delay filler, not b
    assert witness_step(_M, None, _events(triggered=True), True, 3, _values(0, 1)) == _LOOPING


def test_a_busy_witness_follows_its_frontier_and_is_freed_by_a_match() -> None:
    assert witness_step(_M, _LOOPING, _events(), False, 4, _values()) == _LOOPING
    assert witness_step(_M, _LOOPING, _events(), False, 4, _values(1)) is None


def test_a_witness_freed_in_a_sample_does_not_pick_in_that_sample() -> None:
    """The mutant that re-picks: a, b high with the witness busy gives idle, not busy."""
    events = _events(triggered=True, success=True)
    assert witness_step(_M, _LOOPING, events, True, 4, _values(0, 1)) is None
    # a busy witness that is not freed ignores the pick too
    assert witness_step(_M, _LOOPING, _events(triggered=True), True, 4, _values(0)) == _LOOPING


def test_disable_clears_a_busy_witness_and_blocks_a_pick() -> None:
    machine = _machine(Implication(Bool(_A), _delayed_b(), True), disable=_D)
    assert machine.disable == 0
    looping = machine.configurations[1]
    disabled = _events(disabled=True, triggered=True)
    assert witness_step(machine, looping, disabled, True, 4, _values(0)) is None
    assert witness_step(machine, None, disabled, True, 4, _values(0)) is None
    assert witness_step(machine, looping, _events(), False, 4, _values()) == looping


def test_a_dead_frontier_frees_the_witness_the_failure_is_the_machines() -> None:
    """``a |-> strong(b[*1:$] ##1 c)``: with ``b`` low the obligation dies; in the witness it
    is idle (the configuration machine reports the failure)."""
    sequence = SeqConcat(Repeat(Bool(_B), 1, None), Delay(1, 1), Bool(_C))
    machine = _machine(Implication(Bool(_A), SequenceProperty(sequence, _STRONG), True))
    assert machine.liveness is Liveness.WITNESS
    # conditions: a = 0, b = 1, c = 2
    assert witness_step(machine, None, _events(triggered=True), True, 3, _values(0)) is None
    picked = witness_step(machine, None, _events(triggered=True), True, 3, _values(0, 1))
    assert picked is not None
    assert witness_step(machine, picked, _events(), False, 4, _values()) is None
    assert witness_step(machine, picked, _events(), False, 4, _values(1)) == picked
    assert witness_step(machine, picked, _events(), False, 4, _values(2)) is None


def test_conditions_with_past_depth_are_gated_like_the_configuration_step() -> None:
    """``a |-> strong(##[1:$] $past(b))``: ``$past(b)`` does not hold while ``elapsed < 1``."""
    past_b = Sampled(SampledFunction.PAST, _B, BIT)
    sequence = SeqConcat(TRUE, Delay(1, None), Bool(past_b))
    machine = _machine(Implication(Bool(_A), SequenceProperty(sequence, _STRONG), True))
    looping = machine.configurations[1]
    assert machine.past_depth == (0, 1)
    assert witness_step(machine, looping, _events(), False, 0, _values(1)) == looping
    assert witness_step(machine, looping, _events(), False, 1, _values(1)) is None


def test_a_strong_consequent_that_keeps_a_live_letter_gets_no_never_fails_warning() -> None:
    """The consequent can never fail by dying (``never_fails``), but it can fail its liveness
    check, so the 4e warning is not given; the weak form keeps it."""
    assert never_fails(_M)
    assert never_fails_warning(_M, 0, 1) is None
    weak = SequenceProperty(SeqConcat(TRUE, Delay(1, None), Bool(_B)), Strength.WEAK)
    weak_machine = _machine(Implication(Bool(_A), weak, True))
    assert never_fails_warning(weak_machine, 0, 1) is not None


# --- not of an unbounded sequence ----------------------------------------------


def test_the_liveness_kind_of_a_negated_consequent_follows_the_strength_after_negation() -> None:
    """``not weak(s)`` is strong (a liveness part, the kind by the number of configurations),
    ``not strong(s)`` weak (none); the default strength is weak."""
    sequence = SeqConcat(TRUE, Delay(1, None), Bool(_B))
    for strength, kind in (
        (Strength.DEFAULT, Liveness.WITNESS),
        (Strength.WEAK, Liveness.WITNESS),
        (Strength.STRONG, None),
    ):
        machine = _machine(Implication(Bool(_A), Not(SequenceProperty(sequence, strength)), True))
        assert machine.negated and len(machine.configurations) == 2
        assert machine.liveness is kind, strength
    pending = _machine(Not(Until(_A, _B, False, False)))
    assert pending.negated and len(pending.configurations) == 1
    assert pending.liveness is Liveness.PENDING
    strong_until = _machine(Not(Until(_A, _B, True, False)))
    assert strong_until.negated and strong_until.liveness is None


def test_a_plain_machine_is_not_negated() -> None:
    plain = _machine(Implication(Bool(_A), _delayed_b(1), True))
    assert plain.negated is False and plain.liveness is Liveness.WITNESS


def test_a_negated_consequent_gets_no_never_fails_warning() -> None:
    """``not strong(1 ##[1:$] b)`` cannot die, but it fails where ``b`` holds: no warning
    (``never_fails`` is about frontiers that cannot die); the weak machine of the same
    sequence keeps its warning."""
    sequence = SeqConcat(TRUE, Delay(1, None), Bool(_B))
    negated = _machine(Implication(Bool(_A), Not(SequenceProperty(sequence, _STRONG)), True))
    assert negated.negated and not never_fails(negated)
    assert never_fails_warning(negated, 0, 1) is None
    weak = _machine(Implication(Bool(_A), SequenceProperty(sequence, Strength.WEAK), True))
    assert never_fails_warning(weak, 0, 1) is not None
