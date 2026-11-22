"""Unit tests of ``sva2yosys.temporal.configurations`` (the documented contract of its
module docstring): the static configuration sets, the step and the size metrics."""

from __future__ import annotations

from dataclasses import replace

import pytest

from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Const, Signal
from sva2yosys.ir.property import (
    Always,
    Implication,
    Not,
    Property,
    SequenceProperty,
    Strength,
)
from sva2yosys.ir.sequence import (
    TRUE,
    Bool,
    Delay,
    Goto,
    Repeat,
    SeqConcat,
    SeqIntersect,
    SeqOr,
    Sequence,
)
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.automaton import Automaton, ConditionTable, build_automaton
from sva2yosys.temporal.budgets import BudgetError
from sva2yosys.temporal.compile import (
    Monitor,
    compile_configuration_machine,
    compile_monitor,
    never_fails_warning,
)
from sva2yosys.temporal.configurations import (
    ConfigMachine,
    ConfigState,
    configuration_sets,
    configuration_step,
    initial_config_state,
    never_fails,
)

_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("t.sv", 1, 1, 1, 2)
A, B, C = (Bool(Signal(f"m.{n}", BIT)) for n in "abc")


def _automaton(sequence: Sequence) -> Automaton:
    return build_automaton(sequence, ConditionTable())


def _cat(left: Sequence, low: int, high: int | None, right: Sequence) -> SeqConcat:
    return SeqConcat(left, Delay(low, high), right)


def test_a_plain_loop_has_one_configuration() -> None:
    """``b[*1:$]``: from {b}, b held matches (discharged); b not held fails."""
    found = configuration_sets(_automaton(Repeat(B, 1, None)), 100)
    assert len(found) == 1 and found[0] == frozenset(_automaton(Repeat(B, 1, None)).start)


def test_a_loop_with_a_tail_has_two_configurations() -> None:
    """``b[*1:$] ##1 c``: {b} and, after b, {b, c}."""
    automaton = _automaton(_cat(Repeat(B, 1, None), 1, 1, C))
    found = configuration_sets(automaton, 100)
    assert found[0] == frozenset(automaton.start)
    assert len(found) == 2
    assert found[1] == frozenset({0, 1})


def test_the_start_frontier_is_entry_zero_and_entries_are_distinct_and_non_empty() -> None:
    automaton = _automaton(_cat(_cat(TRUE, 1, None, B), 3, 3, C))
    found = configuration_sets(automaton, 1000)
    assert found[0] == frozenset(automaton.start)
    assert len(set(found)) == len(found) and all(found)


def test_independent_guards_give_exponentially_many_configurations() -> None:
    """``##[1:$] b ##n c``: 2**n configurations of the tail positions (and then some)."""
    counts = [
        len(configuration_sets(_automaton(_cat(_cat(TRUE, 1, None, B), n, n, C)), 10_000))
        for n in (1, 2, 3, 4)
    ]
    assert counts == sorted(counts) and counts[3] >= 2 * counts[2] - 1


def test_exceeding_the_limit_raises_a_budget_error() -> None:
    automaton = _automaton(_cat(_cat(TRUE, 1, None, B), 8, 8, C))
    with pytest.raises(BudgetError, match="configurations"):
        configuration_sets(automaton, 20)
    assert len(configuration_sets(automaton, 10_000)) > 20


def test_an_empty_automaton_has_no_configurations() -> None:
    assert configuration_sets(Automaton((), (), False), 5) == ()


def _machine(prop: Implication | SequenceProperty):  # type: ignore[no-untyped-def]
    return compile_configuration_machine(
        Directive(DirectiveKind.ASSERT, "p", "m", _CLOCK, None, prop, _SOURCE)
    )


def test_step_discharges_on_a_match_fails_on_a_dead_frontier_and_merges_identical_ones() -> None:
    """``a |-> b[*1:$] ##1 c``: conditions in order a, b, c (indices 0, 1, 2)."""
    machine = _machine(Implication(A, SequenceProperty(_cat(Repeat(B, 1, None), 1, 1, C)), True))
    state = initial_config_state()
    assert state == ConfigState(frozenset(), frozenset())

    def run(values: dict[int, bool], at: ConfigState) -> tuple[ConfigState, bool, bool]:
        after, events = configuration_step(machine, at, 5, lambda i: values.get(i, False))
        return after, events.failure, events.success

    # a, not b: the new obligation dies at once
    _, failure, success = run({0: True}, state)
    assert failure and not success
    # a and b: it moves to {b, c}
    after, failure, success = run({0: True, 1: True}, state)
    assert not failure and not success and len(after.live) == 1
    # a and b again while one is live in {b, c}: one live bit, two obligations with
    # different configurations ({b} for the new, {b, c} for the old) become one {b, c}
    again, failure, success = run({0: True, 1: True}, after)
    assert again.live == after.live and not failure and not success
    # not a, c only: the {b, c} obligation matches
    done, failure, success = run({2: True}, after)
    assert success and not failure and not done.live
    # not a, nothing holds: it fails
    _, failure, success = run({}, after)
    assert failure and not success


def test_a_new_obligation_and_an_old_one_in_different_configurations_stay_separate() -> None:
    machine = _machine(Implication(A, SequenceProperty(_cat(Repeat(B, 1, None), 1, 1, C)), True))
    after, _ = configuration_step(machine, initial_config_state(), 0, lambda i: i in (0, 1))
    # sample 1: a high, b low, c high: the old obligation ({b, c}) matches, the new one ({b})
    # fails; a merged frontier would hide the failure
    _, events = configuration_step(machine, after, 1, lambda i: i in (0, 2))
    assert events.success and events.failure


def test_disable_clears_the_state_and_reports_nothing() -> None:
    directive = Directive(
        DirectiveKind.ASSERT,
        "p",
        "m",
        _CLOCK,
        Signal("m.d", BIT),
        Implication(A, SequenceProperty(Repeat(B, 1, None)), True),
        _SOURCE,
    )
    machine = compile_configuration_machine(directive)
    assert machine.disable == 0
    live = ConfigState(frozenset(), frozenset({0}))
    after, events = configuration_step(machine, live, 3, lambda i: i == machine.disable)
    assert events.disabled and not events.failure and not events.success
    assert after == initial_config_state()


# --- never_fails -----------------------------------------------------------------

D = Bool(Signal("m.d", BIT))
_LOOP = _cat(TRUE, 1, None, B)  # ##[1:$] b: the filler letter has no guard and loops


def _directive(
    prop: Property, kind: DirectiveKind = DirectiveKind.ASSERT, disable: Signal | None = None
) -> Directive:
    return Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE)


def _never_fails(prop: Property, kind: DirectiveKind = DirectiveKind.ASSERT) -> bool:
    return never_fails(compile_configuration_machine(_directive(prop, kind)))


def _implies(antecedent: Sequence, consequent: Sequence, overlapping: bool = True) -> Implication:
    return Implication(antecedent, SequenceProperty(consequent), overlapping)


@pytest.mark.parametrize(
    ("name", "prop"),
    [
        # the start frontier is the always-held filler, which loops forever
        ("a |-> ##[1:$] b", _implies(A, _LOOP)),
        # every configuration holds the filler or the loop letter (no guard) with successors
        ("a |-> ##[1:$] b ##1 c", _implies(A, _cat(_LOOP, 1, 1, C))),
        # a constant-false letter is a guarded letter that never holds; the filler still loops
        ("a |-> ##[1:$] 1'b0", _implies(A, _cat(TRUE, 1, None, Bool(Const(0, BIT))))),
        ("a |=> ##[1:$] b", _implies(A, _LOOP, False)),
        ("##[1:$] b", SequenceProperty(_LOOP)),
        ("weak(##[1:$] b)", SequenceProperty(_LOOP, Strength.WEAK)),
        ("always ##[1:$] b", Always(0, None, False, SequenceProperty(_LOOP))),
        ("a[*1:$] |-> ##[1:$] b", _implies(Repeat(A, 1, None), _LOOP)),
        # an alternative that is guarded does not matter: the other branch keeps the filler
        ("a |-> (##[1:$] b) or c", _implies(A, SeqOr(_LOOP, C))),
    ],
)
def test_consequents_that_always_keep_a_live_letter_never_fail(name: str, prop: Property) -> None:
    assert _never_fails(prop), name


@pytest.mark.parametrize(
    ("name", "prop"),
    [
        # b is guarded: with b false the frontier dies at once
        ("a |-> b[*1:$]", _implies(A, Repeat(B, 1, None))),
        ("a |-> b[*1:$] ##1 c", _implies(A, _cat(Repeat(B, 1, None), 1, 1, C))),
        # b must hold at the trigger sample
        ("a |-> b ##1 (c ##[1:$] d)", _implies(A, _cat(B, 1, 1, _cat(C, 1, None, D)))),
        ("a |-> b ##[1:$] c", _implies(A, _cat(B, 1, None, C))),
        ("a |-> b until c", _implies(A, _cat(Repeat(B, 0, None), 1, 1, C))),
        # b[->1] = !b[*0:$] ##1 b: every letter is guarded, so "not proved" (conservative:
        # the guards !b and b are complementary and the real answer is that it never fails)
        ("a |-> b[->1]", _implies(A, Goto(Signal("m.b", BIT), 1, 1))),
        ("a |-> b[*1:$] or c", _implies(A, SeqOr(Repeat(B, 1, None), C))),
        ("b[*1:$] as a property", SequenceProperty(Repeat(B, 1, None))),
    ],
)
def test_consequents_whose_frontier_can_die_are_not_proved(name: str, prop: Property) -> None:
    assert not _never_fails(prop), name


def test_the_answer_does_not_depend_on_antecedent_or_disable() -> None:
    plain = _implies(A, _LOOP)
    unbounded = _implies(_cat(A, 1, None, B), _LOOP)
    disabled = compile_configuration_machine(_directive(plain, disable=Signal("m.d", BIT)))
    assert never_fails(disabled)
    assert _never_fails(unbounded)


def test_assume_is_judged_like_assert() -> None:
    assert _never_fails(_implies(A, _LOOP), DirectiveKind.ASSUME)
    assert not _never_fails(_implies(A, Repeat(B, 1, None)), DirectiveKind.ASSUME)


def test_a_machine_without_configurations_can_fail() -> None:
    """An empty consequent fails at its trigger."""
    machine = compile_configuration_machine(_directive(_implies(A, _LOOP)))
    assert never_fails(machine)
    assert not never_fails(replace(machine, configurations=()))


def test_one_failing_configuration_is_enough() -> None:
    """``b ##[0:$] c``: the configuration after b keeps a looping filler, but the start
    configuration {b, ...} holds only guarded letters, so it can die."""
    machine = compile_configuration_machine(_directive(_implies(A, _cat(B, 0, None, C))))
    assert not never_fails(machine)


def _monitor(prop: Property, kind: DirectiveKind = DirectiveKind.ASSERT) -> Monitor:
    return compile_monitor(_directive(prop, kind))


def test_the_warning_text_of_an_assert_and_an_assume() -> None:
    prop = _implies(A, _LOOP)
    assert never_fails_warning(_monitor(prop), 0, 1) == (
        "this assert can never fail: its weak unbounded consequent always keeps a live match "
        "(a liveness check needs strong(...) or s_eventually, see docs/usage.md)"
    )
    assert never_fails_warning(_monitor(prop, DirectiveKind.ASSUME), 0, 1) == (
        "this assume can never fail, so it constrains nothing: its weak unbounded consequent "
        "always keeps a live match (a fairness constraint needs a strong assume, see "
        "docs/usage.md)"
    )


def test_a_sequence_property_says_sequence_and_conjuncts_are_named() -> None:
    message = never_fails_warning(_monitor(SequenceProperty(_LOOP)), 0, 1)
    assert message is not None and "its weak unbounded sequence" in message
    named = never_fails_warning(_monitor(_implies(A, _LOOP)), 1, 3)
    assert named is not None and named.startswith("conjunct 2 of 3 of this assert can never fail")


def test_no_warning_for_a_monitor_that_can_fail_or_is_not_a_configuration_machine() -> None:
    assert never_fails_warning(_monitor(_implies(A, Repeat(B, 1, None))), 0, 1) is None
    assert never_fails_warning(_monitor(_implies(A, B)), 0, 1) is None  # canonical machine
    assert never_fails_warning(_monitor(_implies(A, _cat(B, 1, 3, C))), 0, 1) is None
    cover = _monitor(SequenceProperty(_LOOP), DirectiveKind.COVER)
    assert never_fails_warning(cover, 0, 1) is None


def test_a_negated_machine_swaps_the_events_of_the_step() -> None:
    """``a |-> not (b[*1:$] ##1 c)`` (conditions a, b, c = 0, 1, 2): the
    configurations and the step are those of the sequence, a match is a failure and a dead
    frontier a success."""
    sequence = _cat(Repeat(B, 1, None), 1, 1, C)
    plain = _machine(Implication(A, SequenceProperty(sequence), True))
    negated = _machine(Implication(A, Not(SequenceProperty(sequence)), True))
    assert negated.negated and not plain.negated
    assert negated.configurations == plain.configurations

    def run(
        machine: ConfigMachine, values: set[int], at: ConfigState
    ) -> tuple[ConfigState, bool, bool]:
        after, events = configuration_step(machine, at, 5, lambda i: i in values)
        return after, events.failure, events.success

    state = initial_config_state()
    # a, not b: the new obligation dies at once (plain: failure; negated: success)
    assert run(plain, {0}, state)[1:] == (True, False)
    assert run(negated, {0}, state)[1:] == (False, True)
    # a and b: both move to {b, c}; no event
    moved_plain, *_ = run(plain, {0, 1}, state)
    moved_negated, failure, success = run(negated, {0, 1}, state)
    assert moved_negated == moved_plain and (failure, success) == (False, False)
    # c only: the obligation matches (plain: success; negated: failure) and is discharged
    done, failure, success = run(negated, {2}, moved_negated)
    assert (failure, success) == (True, False) and not done.live
    assert run(plain, {2}, moved_plain)[1:] == (False, True)


def test_a_negated_machine_with_no_configuration_succeeds_at_the_trigger() -> None:
    """The consequent that never matches (``intersect`` of lengths that cannot meet): the plain
    machine fails at the trigger, the negated one succeeds there and never fails."""
    never = SeqIntersect(_cat(B, 1, None, C), A)
    plain = _machine(Implication(A, SequenceProperty(never), True))
    negated = _machine(Implication(A, Not(SequenceProperty(never)), True))
    assert plain.configurations == negated.configurations == ()
    _, events = configuration_step(plain, initial_config_state(), 0, lambda i: i == 0)
    assert events.triggered and events.failure and not events.success
    _, events = configuration_step(negated, initial_config_state(), 0, lambda i: i == 0)
    assert events.triggered and events.success and not events.failure
    _, events = configuration_step(negated, initial_config_state(), 0, lambda i: False)
    assert not events.triggered and not events.success and not events.failure


def test_never_fails_is_false_for_a_negated_machine() -> None:
    """A negated machine fails on a match, whatever its frontier can do."""
    loop = _cat(TRUE, 1, None, B)
    plain = _machine(Implication(A, SequenceProperty(loop), True))
    negated = _machine(Implication(A, Not(SequenceProperty(loop, Strength.STRONG)), True))
    assert never_fails(plain) and not never_fails(negated)
    assert never_fails(replace(negated, negated=False))
    assert never_fails_warning(negated, 0, 1) is None
