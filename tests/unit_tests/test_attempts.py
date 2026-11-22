"""Unit contract of ``sva2yosys.temporal.attempts`` and of the analyses split out of it:
``life`` (``temporal.life``), the nonvacuity predicates and the cover rule
(``temporal.nonvacuity``), ``letter_estimate`` and the budgets (``temporal.budgets``), and
``step`` / ``attempt_size`` (``docs/semantics.md`` §9.4, §9.5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given, settings

from support.ir_strategies import property_trees
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Const, Expr, Sampled, SampledFunction, Signal
from sva2yosys.ir.property import (
    Always,
    Eventually,
    Implication,
    Nexttime,
    Not,
    PropAnd,
    Property,
    PropIf,
    PropOr,
    SequenceProperty,
)
from sva2yosys.ir.sequence import (
    TRUE,
    Bool,
    Delay,
    Repeat,
    SeqAnd,
    SeqConcat,
    SeqIntersect,
    SeqOr,
    Sequence,
    Throughout,
    Within,
)
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.attempts import (
    attempt_size,
    build_attempt_machine,
    initial_attempt_state,
    step,
)
from sva2yosys.temporal.automaton import ConditionTable, build_automaton
from sva2yosys.temporal.budgets import (
    DEFAULT_BUDGETS,
    BudgetError,
    Budgets,
    check_letter_budget,
    letter_estimate,
    property_sequences,
)
from sva2yosys.temporal.life import life
from sva2yosys.temporal.netlist import NAnd, NNot, NOr
from sva2yosys.temporal.nonvacuity import cover_problem, launch_nv, nv_at_false, nv_at_true


def sig(name: str) -> Expr:
    return Signal(f"m.{name}", BIT)


def seq(name: str) -> Sequence:
    return Bool(sig(name))


def then(left: Sequence, low: int, high: int, right: Sequence) -> Sequence:
    return SeqConcat(left, Delay(low, high), right)


def sp(sequence: Sequence) -> Property:
    return SequenceProperty(sequence)


A = sp(seq("a"))
B = sp(seq("b"))
LATE = sp(then(seq("c"), 2, 2, seq("d")))  # c ##2 d
IMP = Implication(seq("a"), B, True)  # a |-> b
E = sig("e")


# --- life ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        (A, 1),
        (sp(then(seq("a"), 1, 3, seq("b"))), 4),
        (sp(Bool(Const(0, BIT))), 1),
        (Implication(seq("a"), sp(then(TRUE, 2, 2, seq("b"))), True), 3),  # a |-> ##2 b
        (Implication(seq("a"), B, False), 2),  # a |=> b
        (Implication(seq("a"), B, True), 1),
        # a ##[0:2] b |-> (c |-> ##1 d): antecedent ends at offset 2, the inner implication
        # lives 2 samples
        (
            Implication(
                then(seq("a"), 0, 2, seq("b")),
                Implication(seq("c"), sp(then(TRUE, 1, 1, seq("d"))), True),
                True,
            ),
            4,
        ),
        (Not(sp(then(seq("a"), 1, 3, seq("b")))), 4),
        (Not(Not(A)), 1),
        (PropAnd((A, sp(then(seq("a"), 1, 3, seq("b"))))), 4),
        (PropOr((sp(then(seq("a"), 1, 2, seq("b"))), sp(then(seq("a"), 1, 3, seq("b"))))), 4),
        (PropIf(E, A, sp(then(seq("a"), 1, 3, seq("b")))), 4),
        (PropIf(E, sp(then(seq("a"), 1, 3, seq("b"))), A), 4),
        (PropIf(E, sp(then(seq("a"), 1, 3, seq("b"))), None), 4),
        (PropIf(E, Not(A), None), 1),
        (Implication(seq("a"), Implication(seq("b"), LATE, False), False), 1 + 1 + 3),
        (Nexttime(0, False, A), 1),
        (Nexttime(2, False, A), 3),
        (Nexttime(2, True, LATE), 2 + 3),
        (Always(1, 3, False, A), 4),
        (Always(0, 2, True, LATE), 2 + 3),
        (Eventually(0, 2, False, A), 3),
        (Eventually(1, 4, True, Nexttime(1, False, A)), 4 + 2),
    ],
)
def test_life(prop: Property, expected: int) -> None:
    assert life(prop) == expected


def test_life_of_an_unbounded_sequence_is_an_error() -> None:
    with pytest.raises(ValueError):
        life(sp(SeqConcat(seq("a"), Delay(1, None), seq("b"))))


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(prop=property_trees(3))
def test_life_is_at_least_one(prop: Property) -> None:
    assert life(prop) >= 1


# --- nonvacuity predicates -------------------------------------------------------------------

NODES: list[tuple[str, Property, tuple[bool, bool, bool]]] = [
    # name, property, (launch_nv, nv_at_true, nv_at_false)
    ("sequence", A, (True, True, True)),
    ("not sequence", Not(A), (True, True, True)),
    ("not implication", Not(IMP), (False, True, True)),
    ("not not implication", Not(Not(IMP)), (False, True, True)),
    ("implication", IMP, (False, True, True)),
    ("implication of implication", Implication(seq("a"), IMP, True), (False, True, False)),
    (
        "not implication of implication",
        Not(Implication(seq("a"), IMP, True)),
        (False, False, True),
    ),
    ("and, or and implication", PropAnd((PropOr((IMP, IMP)), IMP)), (False, False, False)),
    (
        "if else, implication of implication",
        PropIf(E, Implication(seq("a"), IMP, True), A),
        (False, True, False),
    ),
    (
        "implication of or of implications",
        Implication(seq("a"), PropOr((IMP, IMP)), True),
        (False, False, False),
    ),
    ("and, implication and sequence", PropAnd((IMP, A)), (True, True, True)),
    ("and, two implications", PropAnd((IMP, IMP)), (False, True, False)),
    ("or, two implications", PropOr((IMP, IMP)), (False, False, True)),
    ("or, implication and sequence", PropOr((IMP, LATE)), (True, True, True)),
    (
        "or, implication of implications",
        PropOr((Implication(seq("a"), IMP, True), Implication(seq("b"), IMP, True))),
        (False, False, False),
    ),
    ("if else, two implications", PropIf(E, IMP, IMP), (False, True, True)),
    ("if without else, implication", PropIf(E, IMP, None), (False, True, True)),
    ("if without else, bad branch", PropIf(E, PropOr((IMP, IMP)), None), (False, False, True)),
    ("if else, bad then", PropIf(E, PropOr((IMP, IMP)), A), (False, False, True)),
    ("if else, sequences", PropIf(E, A, B), (False, True, True)),
    ("nexttime sequence", Nexttime(2, False, A), (False, True, True)),
    ("nexttime implication", Nexttime(1, True, IMP), (False, True, True)),
    (
        "nexttime implication of implication",
        Nexttime(1, False, Implication(seq("a"), IMP, True)),
        (False, True, False),
    ),
    ("always sequence", Always(1, 3, False, A), (False, True, True)),
    ("always implication", Always(0, 1, True, IMP), (False, True, False)),
    ("eventually sequence", Eventually(0, 2, False, A), (False, True, True)),
    ("eventually implication", Eventually(1, 2, True, IMP), (False, False, True)),
    ("not always implication", Not(Always(0, 1, False, IMP)), (False, False, True)),
    ("not eventually implication", Not(Eventually(0, 1, False, IMP)), (False, True, False)),
]


@pytest.mark.parametrize(("name", "prop", "expected"), NODES, ids=[n[0] for n in NODES])
def test_nonvacuity_predicates(
    name: str, prop: Property, expected: tuple[bool, bool, bool]
) -> None:
    assert (launch_nv(prop), nv_at_true(prop), nv_at_false(prop)) == expected, name


def test_a_launch_nv_node_is_known_at_both_decisions() -> None:
    for _, prop, _ in NODES:
        if launch_nv(prop):
            assert nv_at_true(prop) and nv_at_false(prop)


@pytest.mark.extended
@settings(max_examples=300, deadline=None, derandomize=True)
@given(prop=property_trees(3))
def test_launch_nv_implies_known_at_both_decisions_on_generated_trees(prop: Property) -> None:
    if launch_nv(prop):
        assert nv_at_true(prop) and nv_at_false(prop)


@pytest.mark.extended
@settings(max_examples=300, deadline=None, derandomize=True)
@given(prop=property_trees(3))
def test_cover_rule_is_nonvacuity_at_the_true_decision(prop: Property) -> None:
    assert (cover_problem(prop) is None) is nv_at_true(prop)


@pytest.mark.parametrize(
    "prop",
    [
        PropOr((IMP, LATE)),  # (a |-> b) or (c ##2 d): the sequence operand is launch_nv
        PropAnd((IMP, Implication(seq("c"), sp(seq("d")), True))),
        Not(IMP),
        Not(Not(IMP)),
        IMP,
        Implication(seq("a"), IMP, True),
        PropIf(E, IMP, Implication(seq("c"), sp(seq("d")), True)),
        A,
    ],
)
def test_covers_that_are_accepted(prop: Property) -> None:
    assert cover_problem(prop) is None


@pytest.mark.parametrize(
    "prop",
    [
        PropOr((IMP, Implication(seq("c"), sp(seq("d")), True))),  # (a |-> b) or (c |-> d)
        # stand-in for (a |-> b) or nexttime [2] 1: an implication is not launch_nv either
        PropOr((IMP, Implication(TRUE, sp(then(TRUE, 2, 2, TRUE)), True))),
        Not(Implication(seq("a"), IMP, True)),
        Implication(seq("a"), PropOr((IMP, IMP)), True),
    ],
)
def test_covers_that_are_rejected(prop: Property) -> None:
    message = cover_problem(prop)
    assert isinstance(message, str) and message


@pytest.mark.parametrize(
    "prop",
    [
        Nexttime(2, False, A),
        Always(1, 3, True, A),
        Eventually(0, 2, False, A),
        Nexttime(1, False, IMP),
        Always(0, 1, False, IMP),
        Not(Always(0, 1, False, A)),
        Not(Eventually(0, 1, False, IMP)),
        PropOr((LATE, Always(1, 2, False, IMP))),
    ],
)
def test_temporal_covers_that_are_accepted(prop: Property) -> None:
    assert cover_problem(prop) is None


@pytest.mark.parametrize(
    "prop",
    [
        Eventually(0, 1, False, IMP),
        Not(Always(0, 1, False, IMP)),
        PropOr((IMP, Nexttime(2, False, sp(Bool(Const(1, BIT)))))),
        Nexttime(1, False, Not(Implication(seq("a"), IMP, True))),
        Not(Nexttime(1, False, Implication(seq("a"), IMP, True))),
        PropOr((IMP, Always(1, 2, False, A))),
    ],
)
def test_temporal_covers_that_are_rejected(prop: Property) -> None:
    message = cover_problem(prop)
    assert isinstance(message, str) and message


# --- letter_estimate -------------------------------------------------------------------------

LIMIT = 4096
HUGE = 1_000_000_000


def test_property_sequences_include_the_implicit_windows() -> None:
    window = then(TRUE, 1, 3, TRUE)
    assert property_sequences(Always(1, 3, False, A)) == [window, seq("a")]
    assert property_sequences(Eventually(1, 3, True, A)) == [window, seq("a")]
    assert property_sequences(Nexttime(2, False, A)) == [then(TRUE, 2, 2, TRUE), seq("a")]
    assert property_sequences(Nexttime(0, False, A)) == [seq("a")]
    assert property_sequences(Always(0, 0, False, A)) == [seq("a")]
    nested = property_sequences(Nexttime(1, False, Always(0, 2, False, IMP)))
    assert nested == [then(TRUE, 1, 1, TRUE), then(TRUE, 0, 2, TRUE), seq("a"), seq("b")]


@pytest.mark.parametrize(
    "prop",
    [
        Always(0, 1_000_000, False, A),
        Eventually(1, 1_000_000, True, A),
        Nexttime(1_000_000, False, A),
        Nexttime(1, False, Always(0, HUGE, False, IMP)),
    ],
)
def test_a_huge_temporal_window_exceeds_the_letter_budget_by_name(prop: Property) -> None:
    with pytest.raises(BudgetError, match="letters"):
        check_letter_budget(prop)


def test_a_small_temporal_window_fits_the_letter_budget() -> None:
    check_letter_budget(Always(0, 8, False, A))
    check_letter_budget(Nexttime(5, True, Eventually(0, 4, False, IMP)))


def test_building_an_attempt_machine_for_a_huge_window_is_a_budget_error() -> None:
    directive = Directive(
        DirectiveKind.ASSERT,
        "p",
        "m",
        Clock(Signal("m.clk", BIT), Edge.POSEDGE),
        None,
        Always(0, 1_000_000, False, A),
        SourceRange("attempts.sv", 1, 1, 1, 2),
    )
    with pytest.raises(BudgetError, match="letters"):
        build_attempt_machine(directive)


def test_letter_estimate_of_a_boolean_is_one() -> None:
    assert letter_estimate(seq("a"), LIMIT) == 1


def test_letter_estimate_saturates_on_a_huge_delay_range() -> None:
    assert letter_estimate(then(TRUE, 1, HUGE, seq("b")), LIMIT) == LIMIT + 1


def test_letter_estimate_saturates_on_a_huge_range_under_an_empty_repetition() -> None:
    inner = then(TRUE, 1, HUGE, TRUE)
    assert letter_estimate(Repeat(inner, 0, 0), LIMIT) == LIMIT + 1


def test_letter_estimate_saturates_on_a_huge_repetition() -> None:
    assert letter_estimate(Repeat(seq("a"), 1, HUGE), LIMIT) == LIMIT + 1


def test_letter_estimate_never_exceeds_the_limit_plus_one() -> None:
    nested = Repeat(Repeat(then(TRUE, 0, 50, seq("a")), 1, 50), 1, 50)
    assert letter_estimate(nested, LIMIT) == LIMIT + 1
    assert 1 <= letter_estimate(seq("a"), 3) <= 4


_A, _B, _C = seq("a"), seq("b"), seq("c")
ESTIMATE_SEQUENCES: list[Sequence] = [
    _A,
    then(_A, 1, 1, _B),
    then(_A, 0, 0, _B),
    then(_A, 0, 2, _B),
    then(_A, 1, 3, then(_B, 1, 2, _C)),
    Repeat(_A, 1, 3),
    Repeat(_A, 0, 2),
    Repeat(then(_A, 1, 1, _B), 2, 3),
    SeqOr(_A, then(_B, 1, 1, _C)),
    SeqAnd(then(_A, 1, 1, _B), _C),
    SeqAnd(then(_A, 0, 2, _B), then(_B, 1, 1, _C)),
    SeqIntersect(then(_A, 1, 2, _B), then(_B, 1, 2, _C)),
    Throughout(sig("d"), then(_A, 1, 1, _B)),
    Within(_A, then(_B, 1, 3, _C)),
]


@pytest.mark.parametrize(
    "sequence", ESTIMATE_SEQUENCES, ids=[str(i) for i in range(len(ESTIMATE_SEQUENCES))]
)
def test_letter_estimate_is_an_upper_bound_of_the_automaton(sequence: Sequence) -> None:
    letters = len(build_automaton(sequence, ConditionTable()).letters)
    assert letter_estimate(sequence, LIMIT) >= letters


# --- budgets ---------------------------------------------------------------------------------


def test_default_budgets() -> None:
    assert (Budgets().letters, Budgets().instances, Budgets().registers, Budgets().gates) == (
        4096,
        20000,
        8192,
        200000,
    )
    assert Budgets() == DEFAULT_BUDGETS


def test_budgets_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        DEFAULT_BUDGETS.letters = 1  # type: ignore[misc]


def test_budget_error_is_a_value_error() -> None:
    assert issubclass(BudgetError, ValueError)


# --- machine: step and size ------------------------------------------------------------------

_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("attempts.sv", 1, 1, 1, 2)


def directive(
    prop: Property, kind: DirectiveKind = DirectiveKind.ASSERT, disable: Expr | None = None
) -> Directive:
    return Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE)


def raw_of(machine_conditions: tuple[Expr, ...], values: dict[str, int]) -> Callable[[int], bool]:
    def raw(condition: int) -> bool:
        expr = machine_conditions[condition]
        if isinstance(expr, Const):
            return expr.value != 0
        assert isinstance(expr, Signal), expr
        return values[expr.path.removeprefix("m.")] != 0

    return raw


def test_a_step_reports_the_failure_of_the_attempt_at_its_decision_sample() -> None:
    machine = build_attempt_machine(
        directive(Implication(seq("a"), sp(then(TRUE, 1, 1, seq("b"))), True))
    )
    assert machine.life == 2
    state = initial_attempt_state(machine)
    assert not any(state)
    state, first = step(machine, state, 0, raw_of(machine.conditions, {"a": 1, "b": 0}))
    assert (first.disabled, first.failure, first.success) == (False, False, False)
    assert len(first.ages) == machine.life
    assert first.ages[0].pending is True  # root age 0 is the attempt starting now
    state, second = step(machine, state, 1, raw_of(machine.conditions, {"a": 0, "b": 0}))
    assert second.failure is True and second.success is False
    assert second.ages[1].false is True and second.ages[1].true is False
    assert not any(state)


def test_a_step_reports_success_for_a_nonvacuous_true_attempt() -> None:
    machine = build_attempt_machine(directive(Implication(seq("a"), B, True), DirectiveKind.COVER))
    state, events = step(
        machine, initial_attempt_state(machine), 0, raw_of(machine.conditions, {"a": 1, "b": 1})
    )
    assert (events.success, events.failure) == (True, False)
    assert events.ages[0].true and events.ages[0].nonvacuous
    state, events = step(machine, state, 1, raw_of(machine.conditions, {"a": 0, "b": 0}))
    assert (events.success, events.failure) == (False, False)  # true but vacuous
    assert events.ages[0].true and not events.ages[0].nonvacuous


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        (
            ((1, 0, 0), (1, 0, 0), (1, 1, 0), (0, 0, 1), (0, 0, 0)),
            ((False, False), (True, False), (True, False), (False, True), (False, False)),
        ),
        (
            ((1, 1, 0), (1, 1, 0), (1, 1, 0), (0, 0, 0), (0, 0, 0)),
            ((False, False), (False, False), (False, True), (False, True), (False, False)),
        ),
    ],
)
def test_accepting_continuations_and_overlapping_attempts_keep_their_own_decisions(
    samples: tuple[tuple[int, int, int], ...], expected: tuple[tuple[bool, bool], ...]
) -> None:
    # Origin: hand derivation. Each a triggers b now or c next sample.
    # The first trace fails roots 0/1 at 1/2; root 2 succeeds at 3 after a falls.
    # In the second trace root 0 reaches its last allowed endpoint at 2; roots 1/2
    # close at 3 when a falls. Earlier accepting endpoints must keep continuing.
    prop = Implication(Repeat(seq("a"), 1, 3), PropOr((B, Nexttime(1, False, sp(seq("c"))))), True)
    machine = build_attempt_machine(directive(prop))
    state = initial_attempt_state(machine)
    actual = []
    for tick, (a, b, c) in enumerate(samples):
        state, events = step(
            machine, state, tick, raw_of(machine.conditions, {"a": a, "b": b, "c": c})
        )
        actual.append((events.failure, events.success))
    assert tuple(actual) == expected


def test_disable_clears_the_state_and_suppresses_every_other_event() -> None:
    machine = build_attempt_machine(
        directive(Implication(seq("a"), sp(then(TRUE, 1, 1, seq("b"))), True), disable=sig("d"))
    )
    state, _ = step(
        machine,
        initial_attempt_state(machine),
        0,
        raw_of(machine.conditions, {"a": 1, "b": 0, "d": 0}),
    )
    assert any(state)
    state, events = step(machine, state, 1, raw_of(machine.conditions, {"a": 1, "b": 0, "d": 1}))
    assert events.disabled is True
    assert (events.failure, events.success) == (False, False)
    assert not any(state)
    assert all(not (age.true or age.false or age.pending) for age in events.ages)


def test_a_condition_is_not_read_while_its_past_depth_is_not_reached() -> None:
    past = Sampled(SampledFunction.PAST, sig("a"), BIT)
    machine = build_attempt_machine(directive(sp(Bool(past))))
    assert machine.past_depth == (1,)

    def raw(condition: int) -> bool:
        raise AssertionError("raw must not be called before the history exists")

    state, events = step(machine, initial_attempt_state(machine), 0, raw)
    assert events.failure is True  # $past(a) is not true at the first sample
    _, events = step(machine, state, 1, lambda condition: True)
    assert events.failure is False


def test_attempt_size_reports_the_netlist() -> None:
    machine = build_attempt_machine(
        directive(Implication(seq("a"), sp(then(TRUE, 1, 2, seq("b"))), True), disable=sig("d"))
    )
    size = attempt_size(machine)
    assert size.conditions == len(machine.conditions)
    assert size.life == machine.life
    assert size.registers == len(machine.netlist.registers)
    assert size.gates == sum(isinstance(g, NNot | NAnd | NOr) for g in machine.netlist.gates)


def test_the_disable_condition_is_registered_first() -> None:
    machine = build_attempt_machine(directive(A, disable=sig("d")))
    assert machine.disable == 0
    assert machine.conditions[0] == sig("d")
    assert build_attempt_machine(directive(A)).disable is None


def test_the_machine_keeps_the_lowered_property() -> None:
    machine = build_attempt_machine(directive(PropIf(E, A, None)))
    assert not isinstance(machine.directive.property, PropIf)
