"""Saturating resource accounting and range-independent preflight contracts."""

from dataclasses import FrozenInstanceError

import pytest

from sva2yosys.ir.expr import BIT, Expr, Sampled, SampledFunction, Signal, Unary, UnaryOp, ValueType
from sva2yosys.ir.property import Always, Eventually, Nexttime, Not, Property, SequenceProperty
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat
from sva2yosys.temporal.automaton import ConditionTable, build_automaton
from sva2yosys.temporal.budgets import (
    BudgetError,
    ResourceLimits,
    Work,
    check_expressions,
    preflight,
)
from sva2yosys.temporal.netlist import NetlistBuilder

LIMITS = ResourceLimits(expansion=32, work=16, state_bits=8, emission=64)
B = Bool(Signal("m.b", BIT))


def test_resource_limits_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        LIMITS.work = 1  # type: ignore[misc]


@pytest.mark.parametrize("values", [(-1, 1, 1, 1), (1, -1, 1, 1), (1, 1, -1, 1), (1, 1, 1, -1)])
def test_resource_limits_reject_negative_fields(values: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        ResourceLimits(*values)


def test_work_admits_its_exact_boundary_and_saturates_on_exhaustion() -> None:
    work = Work(LIMITS)
    work.charge(16)
    assert work.used == 16
    with pytest.raises(BudgetError, match="16 construction"):
        work.charge(10**100)
    assert work.used == 17


def test_negative_work_is_invalid() -> None:
    with pytest.raises(ValueError, match="negative"):
        Work(LIMITS).charge(-1)


def test_emission_references_have_a_separate_saturating_counter() -> None:
    work = Work(LIMITS)
    work.emit(64)
    assert work.emitted == 64
    assert work.used == 0
    with pytest.raises(BudgetError, match="64 emission"):
        work.emit(10**100)
    assert work.emitted == 65


def test_intermediate_letters_are_charged_before_work() -> None:
    limits = ResourceLimits(2, 100, 8, 64)
    work = Work(limits)
    work.letter(1)
    work.letter(0)
    assert (work.letters, work.used) == (2, 3)
    with pytest.raises(BudgetError, match="2 expanded letters"):
        work.letter(0)
    assert work.letters == 3


@pytest.mark.parametrize(
    "prop",
    [
        SequenceProperty(SeqConcat(TRUE, Delay(10**100, 10**100), B)),
        SequenceProperty(Repeat(B, 1, 10**100)),
        Nexttime(10**100, False, SequenceProperty(B)),
        Always(0, 10**100, False, SequenceProperty(B)),
        Always(10**100, None, False, SequenceProperty(B)),
        Eventually(10**100, None, True, SequenceProperty(B)),
    ],
)
def test_preflight_rejects_huge_ranges_without_enumeration(prop: Property) -> None:
    # A mistaken range walk cannot complete this test; arithmetic must saturate.
    with pytest.raises(BudgetError, match="32 expansion"):
        preflight(prop, LIMITS)


def test_preflight_has_an_inclusive_boundary() -> None:
    preflight(SequenceProperty(SeqConcat(TRUE, Delay(29, 29), B)), LIMITS)
    with pytest.raises(BudgetError, match="expansion"):
        preflight(SequenceProperty(SeqConcat(TRUE, Delay(30, 30), B)), LIMITS)


def test_unbounded_range_keeps_its_loop_and_checks_only_the_prefix() -> None:
    preflight(SequenceProperty(SeqConcat(TRUE, Delay(1, None), B)), LIMITS)
    with pytest.raises(BudgetError, match="expansion"):
        preflight(SequenceProperty(SeqConcat(TRUE, Delay(10**100, None), B)), LIMITS)


def test_sampled_history_is_checked_without_building_or_classifying_it() -> None:
    past = Sampled(SampledFunction.PAST, Signal("m.b", BIT), BIT, depth=10**100)
    with pytest.raises(BudgetError, match="expansion"):
        check_expressions((past,), LIMITS)


def test_sampled_history_accounts_for_its_value_width() -> None:
    word = Signal("m.word", ValueType(4))
    check_expressions((Sampled(SampledFunction.PAST, word, word.type, depth=8),), LIMITS)
    with pytest.raises(BudgetError, match="expansion"):
        check_expressions((Sampled(SampledFunction.PAST, word, word.type, depth=9),), LIMITS)


def test_distinct_sampled_histories_share_the_expansion_allowance() -> None:
    limits = ResourceLimits(32, 1000, 8, 64)
    first = Sampled(SampledFunction.PAST, Signal("m.a", BIT), BIT, depth=20)
    second = Sampled(SampledFunction.PAST, Signal("m.b", BIT), BIT, depth=20)
    check_expressions((first, first), limits)
    with pytest.raises(BudgetError, match="sampled history"):
        check_expressions((first, second), limits)


def test_deep_sampled_operand_is_charged_before_structural_hashing() -> None:
    # Origin: review; hashing Sampled first bypassed the small work cap.
    operand: Expr = Signal("m.a", BIT)
    for _ in range(3000):
        operand = Unary(UnaryOp.LOGICAL_NOT, operand, BIT)
    with pytest.raises(BudgetError, match="construction"):
        check_expressions((Sampled(SampledFunction.PAST, operand, BIT),), LIMITS)


def test_deep_written_property_reports_resource_exhaustion() -> None:
    # Origin: review of the recursive estimate after iterative preflight.
    prop: Property = SequenceProperty(B)
    for _ in range(3000):
        prop = Not(prop)
    with pytest.raises(BudgetError):
        preflight(prop, ResourceLimits(16384, 100000000, 8192, 200000))


def test_deep_plain_expression_reports_resource_exhaustion() -> None:
    operand: Expr = Signal("m.a", BIT)
    for _ in range(3000):
        operand = Unary(UnaryOp.LOGICAL_NOT, operand, BIT)
    with pytest.raises(BudgetError, match="nesting exceeds compiler capacity"):
        check_expressions((operand,), ResourceLimits(16384, 100000000, 8192, 200000))


def test_automaton_accounts_for_intermediate_letters_even_after_pruning() -> None:
    work = Work(ResourceLimits(32, 1000, 32, 1000))
    # Fusion creates two operand letters and a fused letter, then prunes the operands.
    automaton = build_automaton(SeqConcat(B, Delay(0, 0), B), ConditionTable(), work=work)
    assert len(automaton.letters) == 1
    assert work.letters == 3


def test_netlist_accounts_for_folded_operands_not_only_surviving_gates() -> None:
    work = Work(ResourceLimits(32, 16, 32, 1000))
    netlist = NetlistBuilder(on_work=work.charge)
    neutral = netlist.const(True)
    # The result folds to a constant, but inspecting a long operand list is still work.
    with pytest.raises(BudgetError, match="construction"):
        netlist.and_([neutral] * 100)
