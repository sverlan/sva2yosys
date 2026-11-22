"""Unit contract of the compilation boundary of ``sva2yosys.temporal.compile``:
``machine_exact``, ``monitor_directives``, ``compile_monitor``, ``compile_monitors`` and
``compile_attempt_machine`` (``docs/semantics.md`` §9.4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from hypothesis import given, settings

from support.ir_strategies import directives
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Const, Expr, Signal
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
    conjuncts,
)
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat, Sequence
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.budgets import Budgets
from sva2yosys.temporal.compile import (
    CompileError,
    compile_attempt_machine,
    compile_directive,
    compile_monitor,
    compile_monitors,
    conjunct_directives,
    machine_exact,
    monitor_directives,
)
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.lower import lower
from sva2yosys.temporal.machine import Machine

_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("boundary.sv", 1, 1, 1, 2)
HUGE = 1_000_000_000


def sig(name: str) -> Expr:
    return Signal(f"m.{name}", BIT)


def seq(name: str) -> Sequence:
    return Bool(sig(name))


def sp(sequence: Sequence) -> Property:
    return SequenceProperty(sequence)


def then(left: Sequence, low: int, high: int | None, right: Sequence) -> Sequence:
    return SeqConcat(left, Delay(low, high), right)


def directive(
    prop: Property, kind: DirectiveKind = DirectiveKind.ASSERT, disable: Expr | None = None
) -> Directive:
    return Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE)


A, B, C = sp(seq("a")), sp(seq("b")), sp(seq("c"))
IMP = Implication(seq("a"), B, True)
IMP2 = Implication(seq("b"), C, False)
WIDE = then(seq("a"), 1, 2, seq("b"))  # a ##[1:2] b: two match lengths
UNB = sp(then(seq("a"), 1, None, seq("b")))  # a ##[1:$] b

# --- machine_exact ---------------------------------------------------------------------------

EXACT: list[tuple[str, Property, bool, bool]] = [
    # name, lowered property, exact for assert, exact for cover
    ("sequence", A, True, True),
    ("not sequence", Not(A), True, True),
    ("implication", IMP, True, True),
    ("non-overlapping implication", IMP2, True, True),
    ("implication of not sequence", Implication(seq("a"), Not(B), True), True, True),
    (
        "unbounded sequence",
        UNB,
        False,
        False,
    ),  # the configuration-set machine (covers too)
    ("unbounded consequent", Implication(seq("a"), UNB, True), False, False),
    ("unbounded consequent, wide antecedent", Implication(WIDE, UNB, True), False, False),
    ("multi-length antecedent", Implication(WIDE, B, True), True, False),
    ("multi-length antecedent, not", Implication(WIDE, Not(B), True), True, False),
    ("constant-false antecedent", Implication(Bool(Const(0, BIT)), B, True), True, True),
    ("nested implication", Implication(seq("a"), IMP, True), False, False),
    ("not of implication", Not(IMP), False, False),
    ("not not sequence", Not(Not(A)), False, False),
    ("property and", PropAnd((A, B)), False, False),
    ("property or", PropOr((A, B)), False, False),
    ("if with else", PropIf(sig("e"), A, B), False, False),
    ("if without else", PropIf(sig("e"), A, None), False, False),
]


@pytest.mark.parametrize(
    ("name", "prop", "assert_exact", "cover_exact"), EXACT, ids=[e[0] for e in EXACT]
)
def test_machine_exact(name: str, prop: Property, assert_exact: bool, cover_exact: bool) -> None:
    assert machine_exact(prop, False) is assert_exact, name
    assert machine_exact(prop, True) is cover_exact, name


# --- monitor_directives ----------------------------------------------------------------------


def test_assert_of_a_property_and_splits_into_its_conjuncts() -> None:
    d = directive(PropAnd((IMP, IMP2, A)))
    parts = monitor_directives(d)
    assert parts == conjunct_directives(d)
    assert [p.property for p in parts] == list(conjuncts(lower(d.property)))
    assert len(parts) == 3


def test_assume_splits_like_assert() -> None:
    d = directive(PropAnd((IMP, IMP2)), DirectiveKind.ASSUME)
    assert monitor_directives(d) == conjunct_directives(d)
    assert len(monitor_directives(d)) == 2


def test_cover_is_never_split_and_keeps_its_property_as_written() -> None:
    d = directive(PropAnd((IMP, IMP2)), DirectiveKind.COVER)
    assert monitor_directives(d) == (d,)
    written = directive(PropIf(sig("e"), A, None), DirectiveKind.COVER)
    assert monitor_directives(written) == (written,)
    assert isinstance(monitor_directives(written)[0].property, PropIf)


def test_assert_of_an_if_is_lowered() -> None:
    d = directive(PropIf(sig("e"), A, None))
    (part,) = monitor_directives(d)
    assert not isinstance(part.property, PropIf)
    assert part == conjunct_directives(d)[0]


# --- compile_monitor / compile_monitors ------------------------------------------------------


def test_an_exact_shape_compiles_to_the_canonical_machine() -> None:
    d = directive(IMP)
    monitor = compile_monitor(d)
    assert isinstance(monitor, Machine)
    assert monitor == compile_directive(d)


def test_a_cover_of_an_exact_shape_compiles_to_the_canonical_machine() -> None:
    d = directive(PropIf(sig("e"), A, None), DirectiveKind.COVER)
    monitor = compile_monitor(d)
    assert isinstance(monitor, Machine)
    assert monitor == compile_directive(d)


@pytest.mark.parametrize(
    ("prop", "kind"),
    [
        (Implication(seq("a"), IMP, True), DirectiveKind.ASSERT),
        (Not(IMP), DirectiveKind.ASSERT),
        (PropOr((IMP, IMP2)), DirectiveKind.ASSERT),
        (PropIf(sig("e"), IMP, IMP2), DirectiveKind.ASSERT),
        (Implication(WIDE, B, True), DirectiveKind.COVER),
        (PropAnd((IMP, IMP2)), DirectiveKind.COVER),
    ],
)
def test_any_other_shape_compiles_to_the_attempt_machine(
    prop: Property, kind: DirectiveKind
) -> None:
    assert isinstance(compile_monitor(directive(prop, kind)), AttemptMachine)


def test_non_canonical_shapes_compile_through_compile_monitors_to_attempt_machines() -> None:
    nested = directive(Implication(seq("a"), IMP, True))
    (monitor,) = compile_monitors(nested)
    assert isinstance(monitor, AttemptMachine)
    (covered,) = compile_monitors(directive(PropAnd((IMP, IMP2)), DirectiveKind.COVER))
    assert isinstance(covered, AttemptMachine)
    with pytest.raises(CompileError):
        compile_directive(nested)


def test_a_cover_the_cover_rule_rejects_is_rejected_by_compile_monitors() -> None:
    with pytest.raises(CompileError, match="cover of this property"):
        compile_monitors(directive(PropOr((IMP, IMP2)), DirectiveKind.COVER))


def test_compile_monitors_applies_every_validation_rule() -> None:
    # (`not` of an unbounded sequence is accepted; a property `or` with one stays rejected)
    unbounded = PropOr((sp(then(seq("a"), 1, None, seq("b"))), sp(seq("a"))))
    with pytest.raises(CompileError):
        compile_monitors(directive(unbounded))


def test_compile_monitors_of_a_property_and_gives_one_machine_per_conjunct() -> None:
    d = directive(PropAnd((IMP, IMP2)))
    assert compile_monitors(d) == tuple(compile_directive(p) for p in conjunct_directives(d))
    assert len(compile_monitors(d)) == 2


@pytest.mark.extended
@settings(max_examples=150, deadline=None, derandomize=True)
@given(d=directives())
def test_accepted_directives_compile_as_before_slice_3b(d: Directive) -> None:
    monitors = compile_monitors(d)
    assert all(isinstance(m, Machine) for m in monitors)
    assert monitors == tuple(compile_directive(p) for p in conjunct_directives(d))


# --- compile_attempt_machine -----------------------------------------------------------------


def test_compile_attempt_machine_builds_a_machine_for_a_nested_implication() -> None:
    machine = compile_attempt_machine(directive(Implication(seq("a"), IMP, True)))
    assert isinstance(machine, AttemptMachine)
    assert machine.life >= 1


def test_compile_attempt_machine_accepts_a_property_the_canonical_machine_cannot_decide() -> None:
    d = directive(PropOr((IMP, IMP2)), disable=sig("d"))
    assert isinstance(compile_attempt_machine(d), AttemptMachine)


def test_a_nullable_top_sequence_is_rejected() -> None:
    with pytest.raises(CompileError, match="empty match"):
        compile_attempt_machine(directive(sp(Repeat(seq("a"), 0, 1))))
    with pytest.raises(CompileError, match="empty match"):
        compile_attempt_machine(directive(Not(sp(Repeat(seq("a"), 0, 1)))))


def test_a_nullable_sequence_at_depth_is_rejected() -> None:
    nested = Implication(seq("a"), Implication(seq("b"), sp(Repeat(seq("c"), 0, 1)), True), True)
    with pytest.raises(CompileError, match="empty match"):
        compile_attempt_machine(directive(nested))
    in_or = PropOr((IMP, sp(Repeat(seq("c"), 0, 1))))
    with pytest.raises(CompileError, match="empty match"):
        compile_attempt_machine(directive(in_or))
    in_if = PropIf(sig("e"), IMP, sp(Repeat(seq("c"), 0, 1)))
    with pytest.raises(CompileError, match="empty match"):
        compile_attempt_machine(directive(in_if))


def test_a_nullable_overlapping_antecedent_is_accepted() -> None:
    nullable = Implication(Repeat(seq("b"), 0, 1), sp(seq("d")), True)
    assert isinstance(compile_attempt_machine(directive(nullable)), AttemptMachine)
    fused = Implication(then(Repeat(seq("b"), 0, 1), 1, 1, seq("c")), sp(seq("d")), True)
    assert isinstance(compile_attempt_machine(directive(fused)), AttemptMachine)


def test_a_nullable_non_overlapping_antecedent_is_rejected() -> None:
    nullable = Implication(Repeat(seq("b"), 0, 1), sp(seq("d")), False)
    with pytest.raises(CompileError):
        compile_attempt_machine(directive(nullable))


def test_an_unbounded_range_is_rejected() -> None:
    with pytest.raises(CompileError):
        compile_attempt_machine(
            directive(Implication(seq("a"), sp(then(TRUE, 1, None, seq("b"))), True))
        )


def test_a_cover_must_satisfy_the_cover_rule() -> None:
    bad = PropOr((IMP, IMP2))
    with pytest.raises(CompileError):
        compile_attempt_machine(directive(bad, DirectiveKind.COVER))
    assert isinstance(compile_attempt_machine(directive(bad)), AttemptMachine)
    good = PropOr((IMP, sp(then(seq("c"), 2, 2, seq("d")))))
    assert isinstance(compile_attempt_machine(directive(good, DirectiveKind.COVER)), AttemptMachine)


def test_a_cover_of_a_multi_length_antecedent_is_accepted() -> None:
    d = directive(Implication(WIDE, C, True), DirectiveKind.COVER)
    assert isinstance(compile_attempt_machine(d), AttemptMachine)


@pytest.mark.parametrize("operands", [(), (IMP,)])
@pytest.mark.parametrize("operator", [PropAnd, PropOr])
def test_an_operator_with_fewer_than_two_operands_is_rejected(
    operator: type[PropAnd] | type[PropOr], operands: tuple[Property, ...]
) -> None:
    with pytest.raises(CompileError):
        compile_attempt_machine(directive(operator(operands)))


def test_a_cover_and_an_assert_of_the_same_property_share_the_machine_but_the_directive() -> None:
    prop = Implication(seq("a"), IMP, True)
    asserted = compile_attempt_machine(directive(prop))
    covered = compile_attempt_machine(directive(prop, DirectiveKind.COVER))
    assert replace(covered, directive=asserted.directive) == asserted


# --- budgets ---------------------------------------------------------------------------------

BIG = Implication(
    then(seq("a"), 1, 1, seq("b")),
    sp(then(seq("c"), 1, 3, seq("d"))),
    True,
)


@pytest.mark.parametrize(
    ("budgets", "name"),
    [
        (Budgets(letters=2), "letters"),
        (Budgets(instances=1), "instances"),
        (Budgets(registers=1), "registers"),
        (Budgets(gates=2), "gates"),
    ],
)
def test_exceeding_a_budget_is_a_compile_error_naming_it(budgets: Budgets, name: str) -> None:
    with pytest.raises(CompileError, match=name):
        compile_attempt_machine(directive(BIG), budgets)
    assert isinstance(compile_attempt_machine(directive(BIG)), AttemptMachine)


def test_a_huge_delay_range_is_rejected_by_the_letter_estimate() -> None:
    prop = Implication(TRUE, sp(then(TRUE, 1, HUGE, seq("b"))), True)  # 1 |-> 1 ##[1:N] b
    with pytest.raises(CompileError, match="letters"):
        compile_attempt_machine(directive(prop))


def test_a_huge_range_under_an_empty_repetition_is_rejected_by_the_letter_estimate() -> None:
    antecedent = Repeat(then(TRUE, 1, HUGE, TRUE), 0, 0)  # (1 ##[1:N] 1)[*0] |-> b
    with pytest.raises(CompileError, match="letters"):
        compile_attempt_machine(directive(Implication(antecedent, B, True)))


# --- bounded nexttime / always / eventually --------------------------------------------------


@pytest.mark.parametrize(
    ("prop", "kind"),
    [
        (Nexttime(2, False, A), DirectiveKind.ASSERT),
        (Always(1, 3, False, A), DirectiveKind.ASSERT),
        (Nexttime(2, False, A), DirectiveKind.COVER),
    ],
)
def test_temporal_operators_with_a_canonical_lowering_get_the_canonical_machine(
    prop: Property, kind: DirectiveKind
) -> None:
    d = directive(prop, kind)
    (monitor,) = compile_monitors(d)
    assert isinstance(monitor, Machine)
    assert monitor == compile_directive(replace(d, property=lower(prop)))


@pytest.mark.parametrize(
    ("prop", "kind"),
    [
        (Always(1, 3, False, A), DirectiveKind.COVER),
        (Eventually(0, 2, False, A), DirectiveKind.ASSERT),
        (Always(0, 1, False, IMP), DirectiveKind.ASSERT),
    ],
)
def test_other_temporal_operators_get_attempt_machines(prop: Property, kind: DirectiveKind) -> None:
    (monitor,) = compile_monitors(directive(prop, kind))
    assert isinstance(monitor, AttemptMachine)


@pytest.mark.parametrize(
    "kind", [DirectiveKind.ASSERT, DirectiveKind.COVER], ids=["assert", "cover"]
)
@pytest.mark.parametrize(
    "build",
    [
        lambda strong: Nexttime(2, strong, A),
        lambda strong: Always(1, 3, strong, A),
        lambda strong: Always(0, 1, strong, IMP),
        lambda strong: Eventually(0, 2, strong, A),
    ],
    ids=["nexttime", "always", "always-implication", "eventually"],
)
def test_weak_and_strong_forms_compile_to_equal_monitors(
    build: Callable[[bool], Property], kind: DirectiveKind
) -> None:
    assert compile_monitors(directive(build(False), kind)) == compile_monitors(
        directive(build(True), kind)
    )


def test_a_huge_temporal_window_is_rejected_by_the_letter_budget() -> None:
    for prop in (
        Always(0, 1_000_000, False, A),
        Eventually(1, 1_000_000, True, A),
        Nexttime(1_000_000, False, A),
    ):
        with pytest.raises(CompileError, match="letters"):
            compile_attempt_machine(directive(prop))


def test_a_huge_range_in_a_cover_is_rejected_before_validation() -> None:
    prop = Implication(seq("a"), sp(then(TRUE, 1, HUGE, seq("b"))), True)
    with pytest.raises(CompileError, match="letters"):
        compile_attempt_machine(directive(prop, DirectiveKind.COVER))


# --- covers of unbounded sequences -------------------------------------------------


@pytest.mark.parametrize(
    "prop",
    [
        UNB,
        Implication(seq("a"), UNB, True),
        Implication(seq("a"), UNB, False),
        Implication(then(seq("a"), 1, 1, seq("b")), UNB, True),
    ],
)
def test_a_cover_of_an_unbounded_sequence_compiles_to_the_configuration_machine(
    prop: Property,
) -> None:
    covered = directive(prop, DirectiveKind.COVER)
    assert monitor_directives(covered) == (covered,)
    (monitor,) = compile_monitors(covered)
    assert isinstance(monitor, ConfigMachine)
    assert compile_monitor(covered) == monitor
    # the machine does not depend on the directive kind (a cover consumes its success)
    asserted = compile_monitors(directive(prop, DirectiveKind.ASSERT))[0]
    assert isinstance(asserted, ConfigMachine)
    assert asserted.configurations == monitor.configurations
    assert asserted.consequent == monitor.consequent and asserted.antecedent == monitor.antecedent


def test_a_cover_with_a_bounded_part_only_keeps_its_machine() -> None:
    wide_cover = directive(Implication(WIDE, B, True), DirectiveKind.COVER)
    assert isinstance(compile_monitors(wide_cover)[0], AttemptMachine)
    assert isinstance(compile_monitors(directive(IMP, DirectiveKind.COVER))[0], Machine)
