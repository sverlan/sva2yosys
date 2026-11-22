"""Unit contract of sva2yosys.passes.validate (module docstring)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace

import pytest

from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import (
    BIT,
    Binary,
    BinaryOp,
    BitSelect,
    Concat,
    Conditional,
    Const,
    Convert,
    Expr,
    FirstSample,
    RangeSelect,
    Replicate,
    Sampled,
    SampledFunction,
    Signal,
    Unary,
    UnaryOp,
    ValueType,
)
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
    Strength,
    Until,
)
from sva2yosys.ir.sequence import (
    Bool,
    Delay,
    Goto,
    NonConsecutive,
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
from sva2yosys.passes.validate import validation_problems
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.bounds import sequence_bounds
from sva2yosys.temporal.compile import (
    CompileError,
    compile_attempt_machine,
    compile_directive,
    compile_monitors,
    conjunct_directives,
)
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.liveness import Liveness
from sva2yosys.temporal.machine import Machine

SA, SB, SC = Signal("m.a", BIT), Signal("m.b", BIT), Signal("m.c", BIT)
S4 = ValueType(4, signed=True)
V4 = Signal("m.v", ValueType(4))
CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)


def _directive(prop: Property, clock: Clock = CLOCK) -> Directive:
    source = SourceRange("t", 1, 1, 1, 2)
    return Directive(DirectiveKind.ASSERT, "p", "m", clock, None, prop, source)


def _on(condition: Expr) -> Directive:
    return _directive(Implication(Bool(SA), SequenceProperty(Bool(condition)), True))


def test_first_sample_is_a_one_bit_unsigned_value() -> None:
    assert validation_problems(_on(FirstSample(BIT))) == ()
    assert validation_problems(_on(Binary(BinaryOp.LOGICAL_AND, FirstSample(BIT), SB, BIT))) == ()
    wide = _on(FirstSample(ValueType(2)))
    assert any("first-sample" in problem for problem in validation_problems(wide))
    signed = _on(FirstSample(ValueType(1, signed=True)))
    assert any("first-sample" in problem for problem in validation_problems(signed))
    inside = _on(Sampled(SampledFunction.PAST, FirstSample(BIT), BIT))
    assert any("sampled-value" in problem for problem in validation_problems(inside))


def test_valid_directive_has_no_problems() -> None:
    prop = Implication(
        SeqConcat(Bool(SA), Delay(1, 2), Bool(SB)), SequenceProperty(Repeat(Bool(SB), 1, 3)), False
    )
    assert validation_problems(_directive(prop)) == ()


@pytest.mark.parametrize(
    ("directive", "fragment"),
    [
        (_directive(SequenceProperty(Bool(SA)), replace(CLOCK, edge=Edge.NEGEDGE)), "posedge"),
        (_directive(SequenceProperty(Bool(SA)), replace(CLOCK, enable=SB)), "iff"),
        (_directive(SequenceProperty(Bool(SA)), replace(CLOCK, signal=V4)), "1-bit"),
        (
            _directive(
                PropOr(
                    (
                        SequenceProperty(Bool(SA)),
                        SequenceProperty(SeqConcat(Bool(SA), Delay(1, None), Bool(SB))),
                    )
                )
            ),  # (not of it is accepted)
            "unbounded",
        ),
        (
            _on(Sampled(SampledFunction.ROSE, Sampled(SampledFunction.PAST, SA, BIT), BIT)),
            "nested sampled-value",
        ),
        (_on(BitSelect(V4, 4, BIT)), "bit select"),
        (
            _on(
                Binary(
                    BinaryOp.EQUALITY,
                    RangeSelect(V4, 1, 2, ValueType(2)),
                    Const(0, ValueType(2)),
                    BIT,
                )
            ),
            "part select",
        ),
        (
            _on(
                Binary(
                    BinaryOp.EQUALITY,
                    RangeSelect(V4, 5, 2, ValueType(4)),
                    Const(0, ValueType(4)),
                    BIT,
                )
            ),
            "part select",
        ),
        (_on(Binary(BinaryOp.EQUALITY, Replicate(0, SA, BIT), SA, BIT)), "replication"),
        (_on(Binary(BinaryOp.EQUALITY, Concat((), BIT), SA, BIT)), "concatenation"),
        (_on(Const(2, BIT)), "constant"),
        (_on(Conditional(SA, BitSelect(V4, 9, BIT), SB, BIT)), "bit select"),
        (
            _on(
                Binary(
                    BinaryOp.EQUALITY,
                    Concat((V4, BitSelect(V4, 7, BIT)), ValueType(5)),
                    Const(0, ValueType(5)),
                    BIT,
                )
            ),
            "bit select",
        ),
        (
            _on(Binary(BinaryOp.LOGICAL_OR, Sampled(SampledFunction.PAST, SA, BIT), SB, BIT)),
            "first sample",
        ),
        (
            _on(Binary(BinaryOp.EQUALITY, Binary(BinaryOp.DIVIDE, V4, V4, ValueType(4)), V4, BIT)),
            "divisor",
        ),
        (
            _on(
                Binary(
                    BinaryOp.EQUALITY,
                    Binary(BinaryOp.MOD, V4, Const(0, ValueType(4)), ValueType(4)),
                    V4,
                    BIT,
                )
            ),
            "divisor",
        ),
        (
            _on(
                Binary(
                    BinaryOp.EQUALITY,
                    Binary(
                        BinaryOp.DIVIDE,
                        V4,
                        Convert(Const(16, ValueType(5)), ValueType(4)),
                        ValueType(4),
                    ),
                    V4,
                    BIT,
                )
            ),
            "divisor",
        ),
    ],
)
def test_problems(directive: Directive, fragment: str) -> None:
    problems = validation_problems(directive)
    assert problems, "expected a problem"
    assert any(fragment in problem for problem in problems), problems


def test_compiler_rejects_invalid_directives() -> None:
    nested = _on(Sampled(SampledFunction.ROSE, Sampled(SampledFunction.PAST, SA, BIT), BIT))
    with pytest.raises(CompileError, match="nested sampled-value"):
        compile_directive(nested)


@pytest.mark.parametrize(
    "divisor",
    [Const(3, ValueType(4)), Const(15, S4)],
)
def test_constant_divisors_are_accepted(divisor: Expr) -> None:
    for op in (BinaryOp.DIVIDE, BinaryOp.MOD):
        quotient = Binary(op, V4, divisor, ValueType(4))
        assert validation_problems(_on(Binary(BinaryOp.EQUALITY, quotient, V4, BIT))) == ()


@pytest.mark.parametrize(
    "divisor",
    [
        Binary(BinaryOp.SUBTRACT, Const(1, ValueType(4)), Const(1, ValueType(4)), ValueType(4)),
        Binary(BinaryOp.DIVIDE, Const(1, ValueType(4)), Const(0, ValueType(4)), ValueType(4)),
        Sampled(SampledFunction.PAST, V4, ValueType(4)),
        # Unfolded constant expressions: the exporter folds them; validation fails closed.
        Convert(Const(3, ValueType(2)), ValueType(4)),
        Unary(UnaryOp.MINUS, Const(1, S4), S4),
    ],
)
def test_divisors_that_may_be_zero_are_rejected(divisor: Expr) -> None:
    quotient = Binary(BinaryOp.DIVIDE, V4, divisor, ValueType(4))
    problems = validation_problems(_on(Binary(BinaryOp.EQUALITY, quotient, V4, BIT)))
    assert any("divisor" in problem for problem in problems), problems


_NULLABLE = Repeat(Bool(SA), 0, 1)


@pytest.mark.parametrize(
    "sequence",
    [
        SeqAnd(_NULLABLE, Bool(SB)),
        SeqAnd(Bool(SB), _NULLABLE),
        SeqIntersect(_NULLABLE, Bool(SB)),
        SeqIntersect(Bool(SB), _NULLABLE),
        Within(_NULLABLE, Bool(SB)),
        Within(Bool(SB), _NULLABLE),
        SeqConcat(Bool(SB), Delay(1, 1), SeqAnd(Bool(SA), Throughout(SB, _NULLABLE))),
    ],
    ids=[
        "and-left",
        "and-right",
        "intersect-left",
        "intersect-right",
        "within-inner",
        "within-outer",
        "nested-throughout",
    ],
)
def test_nullable_operands_of_products_are_rejected(sequence: Sequence) -> None:
    directive = _directive(Implication(Bool(SA), SequenceProperty(sequence), True))
    assert any("can match empty" in p for p in validation_problems(directive))


def test_a_nullable_throughout_operand_is_accepted() -> None:
    sequence = SeqConcat(Throughout(SB, _NULLABLE), Delay(1, 1), Bool(SA))
    directive = _directive(Implication(Bool(SA), SequenceProperty(sequence), True))
    assert validation_problems(directive) == ()


def test_cover_of_an_implication_with_several_antecedent_lengths_is_accepted() -> None:
    """The per-attempt machine decides it (every antecedent match's consequent must pass)."""
    varying = Implication(Repeat(Bool(SA), 1, 2), SequenceProperty(Bool(SB)), True)
    for kind in (DirectiveKind.COVER, DirectiveKind.ASSERT):
        assert validation_problems(replace(_directive(varying), kind=kind)) == ()
    cover = replace(_directive(varying), kind=DirectiveKind.COVER)
    assert isinstance(compile_monitors(cover)[0], AttemptMachine)


def _and() -> PropAnd:
    return PropAnd(
        (
            Implication(Bool(SA), SequenceProperty(Bool(SB)), True),
            Not(SequenceProperty(Bool(SB))),
        )
    )


def test_property_and_is_valid_for_assert_and_assume_with_one_directive_per_conjunct() -> None:
    for kind in (DirectiveKind.ASSERT, DirectiveKind.ASSUME):
        directive = replace(_directive(_and()), kind=kind)
        assert validation_problems(directive) == ()
        parts = conjunct_directives(directive)
        assert [d.property for d in parts] == list(_and().operands)
        assert all(d.kind is kind for d in parts)
        for part in parts:
            compile_directive(part)
        with pytest.raises(CompileError, match="one machine per conjunct"):
            compile_directive(directive)


def test_property_and_is_accepted_for_cover_and_compiles_to_one_attempt_machine() -> None:
    directive = replace(_directive(_and()), kind=DirectiveKind.COVER)
    assert validation_problems(directive) == ()
    monitors = compile_monitors(directive)
    assert len(monitors) == 1
    assert isinstance(monitors[0], AttemptMachine)


def test_property_and_checks_every_conjunct_and_distributes_over_an_implication() -> None:
    nested = PropAnd(
        (Implication(Bool(SA), Implication(Bool(SA), SequenceProperty(Bool(SB)), True), True),)
    )
    assert any("at least two operands" in p for p in validation_problems(_directive(nested)))
    inside = Not(_and())
    assert validation_problems(_directive(inside)) == ()
    distributed = Implication(Bool(SA), PropAnd((_SA, _SB)), True)
    assert validation_problems(_directive(distributed)) == ()
    assert conjunct_directives(_directive(PropAnd((_and(), _and())))) != ()
    assert len(conjunct_directives(_directive(PropAnd((_and(), _and()))))) == 4


def test_nonoverlapping_implication_needs_an_antecedent_without_empty_match() -> None:
    nullable = Implication(Repeat(Bool(SA), 0, 1), SequenceProperty(Bool(SB)), False)
    assert any("can match empty" in p for p in validation_problems(_directive(nullable)))
    overlapping = Implication(Repeat(Bool(SA), 0, 1), SequenceProperty(Bool(SB)), True)
    assert validation_problems(_directive(overlapping)) == ()


def test_a_property_and_needs_two_operands() -> None:
    empty = _directive(PropAnd(()))
    assert any(
        "a property 'and' needs at least two operands" in p for p in validation_problems(empty)
    )


def test_a_property_or_needs_two_operands() -> None:
    for prop in (PropOr((_SA,)), Not(PropOr((_SA,))), Implication(Bool(SA), PropOr(()), True)):
        problems = validation_problems(_directive(prop))
        assert "a property 'or' needs at least two operands" in problems, prop


_SA = SequenceProperty(Bool(SA))
_SB = SequenceProperty(Bool(SB))
_UNBOUNDED = SequenceProperty(SeqConcat(Bool(SA), Delay(1, None), Bool(SB)))
_STRONG_UNBOUNDED = SequenceProperty(_UNBOUNDED.sequence, Strength.STRONG)


@pytest.mark.parametrize(
    "prop",
    [
        Implication(Bool(SA), Implication(Bool(SB), _UNBOUNDED, True), True),
        Implication(Bool(SA), PropAnd((_SA, Implication(Bool(SB), _UNBOUNDED, True))), True),
        PropOr((_SA, _UNBOUNDED)),
        PropIf(SA, _SA, _STRONG_UNBOUNDED),
    ],
)
def test_sequences_anywhere_in_the_property_are_checked(prop: Property) -> None:
    assert any("unbounded" in p for p in validation_problems(_directive(prop)))
    # Unsupported nested shapes remain rejected for an assume too.
    assume = replace(_directive(prop), kind=DirectiveKind.ASSUME)
    assert any("unbounded" in p for p in validation_problems(assume))
    inner = PropIf(SA, Implication(Bool(SB), _STRONG_UNBOUNDED, True), None)
    # This conditional lowers to an accepted shape; its L2 part is rejected by the compiler.
    inner_assume = replace(assume, property=inner)
    assert validation_problems(inner_assume) == ()
    with pytest.raises(CompileError, match=re.escape(_ASSUME_LIVENESS)):
        compile_monitors(inner_assume)


def test_if_condition_is_checked_as_an_expression() -> None:
    bad = PropIf(BitSelect(V4, 4, BIT), _SA, None)
    assert any("bit select" in p for p in validation_problems(_directive(bad)))


def test_nullable_nonoverlapping_antecedent_is_found_at_any_depth() -> None:
    nullable = Repeat(Bool(SA), 0, 1)
    nested = Implication(Bool(SA), Implication(nullable, _SB, False), True)
    problems = validation_problems(_directive(nested))
    assert any("can match empty" in p for p in problems)
    under_if = PropIf(SB, Implication(nullable, _SB, False), None)
    assert any("can match empty" in p for p in validation_problems(_directive(under_if)))


_IMP_AB = Implication(Bool(SA), _SB, True)
_IMP_CD = Implication(Bool(SB), _SA, True)
_SC = Signal("m.c", BIT)
_E = Signal("m.e", BIT)
_CD = Implication(Bool(_SC), _SB, True)


@pytest.mark.parametrize(
    "prop",
    [
        Implication(Bool(SA), Implication(Bool(SB), _SB, True), True),
        Not(Implication(Bool(SA), _SB, True)),
        Not(Not(_SA)),
        Implication(Bool(SA), Not(Not(_SA)), True),
        PropOr((_SA, _SB)),
        PropOr((_IMP_AB, _CD)),
        Implication(Bool(SA), PropOr((_SA, _SB)), True),
        PropIf(SA, Implication(Bool(SB), _SB, True), _SA),
        PropIf(SA, SequenceProperty(Bool(SB), Strength.STRONG), _SA),
        PropIf(SA, _SA, Not(_SB)),
        PropIf(SA, PropOr((_SA, _SB)), None),
        PropIf(SA, Implication(Bool(SB), _SB, True), PropOr((_SA, _SB))),
        PropIf(SA, _SB, None),
        PropIf(SA, Implication(Bool(SB), _SB, False), None),
        PropIf(SA, PropAnd((_SA, _SB)), None),
        PropAnd((Not(PropAnd((_SA, _SB))), _SA)),
    ],
)
def test_formerly_unsupported_shapes_are_accepted_and_get_attempt_machines(prop: Property) -> None:
    d = _directive(prop)
    assert validation_problems(d) == ()
    assert all(isinstance(m, (Machine, AttemptMachine)) for m in compile_monitors(d))


@pytest.mark.parametrize(
    "prop",
    [
        Implication(Bool(SA), Implication(Bool(SB), _SB, True), True),
        Not(Implication(Bool(SA), _SB, True)),
        PropOr((_SA, _SB)),
        PropIf(SA, Implication(Bool(SB), _SB, True), _SA),
    ],
)
def test_compile_directive_still_rejects_shapes_the_canonical_machine_does_not_decide(
    prop: Property,
) -> None:
    with pytest.raises(CompileError):
        compile_directive(_directive(prop))


def test_non_canonical_shapes_compile_to_attempt_machines() -> None:
    for prop in (
        Implication(Bool(SA), Implication(Bool(SB), _SB, True), True),
        Not(Implication(Bool(SA), _SB, True)),
        PropOr((_IMP_AB, _CD)),
        PropIf(SA, Implication(Bool(SB), _SB, True), _SA),
    ):
        monitors = compile_monitors(_directive(prop))
        assert len(monitors) == 1
        assert isinstance(monitors[0], AttemptMachine), prop


def _cover(prop: Property) -> Directive:
    return replace(_directive(prop), kind=DirectiveKind.COVER)


_COVER_RULE = [
    PropOr((_IMP_AB, _CD)),
    Not(Implication(Bool(SA), Implication(Bool(SB), SequenceProperty(Bool(_SC)), True), True)),
    PropIf(_E, PropOr((_IMP_AB, _CD)), _SA),
]


@pytest.mark.parametrize("prop", _COVER_RULE)
def test_the_cover_rule_rejects_a_cover_whose_nonvacuity_comes_late(prop: Property) -> None:
    problems = validation_problems(_cover(prop))
    assert any("cover of this property is not supported" in p for p in problems), problems
    assert validation_problems(_directive(prop)) == ()  # the same property under assert
    assert validation_problems(replace(_directive(prop), kind=DirectiveKind.ASSUME)) == ()
    with pytest.raises(CompileError, match="cover of this property"):
        compile_monitors(_cover(prop))


@pytest.mark.parametrize(
    "prop",
    [
        PropAnd((_IMP_AB, _CD)),
        Implication(Repeat(Bool(SA), 1, 2), _SB, True),
        Not(_IMP_AB),
        PropIf(_E, _IMP_AB, _CD),
    ],
)
def test_the_cover_rule_accepts_these_covers(prop: Property) -> None:
    assert validation_problems(_cover(prop)) == ()
    (monitor,) = compile_monitors(_cover(prop))
    assert isinstance(monitor, (Machine, AttemptMachine))


def _past(expr: Expr) -> Expr:
    return Sampled(SampledFunction.PAST, expr, BIT)


def test_a_property_if_else_condition_with_past_is_rejected() -> None:
    message = "a property if/else whose condition uses $past is not supported"
    for shapes in (True, False):
        with_else = _directive(PropIf(_past(SA), _SA, _SB))
        assert message in validation_problems(with_else, shapes=shapes)
        nested = _directive(Implication(Bool(SA), PropIf(_past(SA), _SA, _SB), True))
        assert message in validation_problems(nested, shapes=shapes)
    with pytest.raises(CompileError, match="uses \\$past"):
        compile_monitors(_directive(PropIf(_past(SA), _SA, _SB)))
    for shapes in (True, False):
        without_else = _directive(PropIf(_past(SA), _SA, None))
        assert message not in validation_problems(without_else, shapes=shapes)
        plain = _directive(PropIf(SA, _SA, _SB))
        assert message not in validation_problems(plain, shapes=shapes)


_ONE = SequenceProperty(Bool(Const(1, BIT)))
_D = Signal("m.d", BIT)
_OR_ANTECEDENT = SeqOr(SeqConcat(Bool(SA), Delay(2, 2), Bool(SB)), Bool(_D))
_LATE_IMPLICATION = Implication(_OR_ANTECEDENT, SequenceProperty(Bool(_SC)), True)
_NULLABLE_NEXT = Implication(Repeat(Bool(SA), 0, 1), _SB, False)
_TEMPORAL: list[Callable[[Property], Property]] = [
    lambda p: Nexttime(2, False, p),
    lambda p: Nexttime(1, True, p),
    lambda p: Always(1, 3, False, p),
    lambda p: Always(0, 1, True, p),
    lambda p: Eventually(0, 2, False, p),
    lambda p: Eventually(1, 2, True, p),
]


@pytest.mark.parametrize(
    ("prop", "message"),
    [
        (Nexttime(-1, False, _SA), "nexttime count -1 must be at least 0"),
        (Always(3, 1, False, _SA), "always range [3:1] is invalid"),
        (Always(-1, 1, True, _SA), "always range [-1:1] is invalid"),
        (Eventually(2, 1, False, _SA), "eventually range [2:1] is invalid"),
        (Implication(Bool(SA), Nexttime(-2, False, _SA), True), "nexttime count -2"),
        (Not(Eventually(5, 4, True, _SA)), "eventually range [5:4] is invalid"),
        (PropIf(SA, Always(2, 0, False, _SA), None), "always range [2:0] is invalid"),
    ],
)
def test_invalid_temporal_windows_are_rejected(prop: Property, message: str) -> None:
    for shapes in (True, False):
        problems = validation_problems(_directive(prop), shapes=shapes)
        assert any(message in p for p in problems), problems
    with pytest.raises(CompileError):
        compile_monitors(_directive(prop))


@pytest.mark.parametrize("wrap", _TEMPORAL)
def test_valid_temporal_windows_are_accepted(wrap: Callable[[Property], Property]) -> None:
    assert validation_problems(_directive(wrap(_SA))) == ()
    assert validation_problems(_directive(wrap(_IMP_AB))) == ()


@pytest.mark.parametrize("wrap", _TEMPORAL)
def test_validation_recurses_into_temporal_operands(wrap: Callable[[Property], Property]) -> None:
    # (a strong unbounded sequence is supported for an assert and for an
    # assume since 5c; the unbounded weak `eventually` is not)
    unbounded = validation_problems(_directive(wrap(Eventually(0, None, False, _SA))))
    assert any("unbounded" in p for p in unbounded)
    nullable = validation_problems(_directive(wrap(_NULLABLE_NEXT)))
    assert any("can match empty" in p for p in nullable)
    past = _directive(wrap(PropIf(_past(SA), _SA, _SB)))
    message = "a property if/else whose condition uses $past is not supported"
    assert message in validation_problems(past)
    assert message in validation_problems(past, shapes=False)
    short_and = validation_problems(_directive(wrap(PropAnd(()))))
    assert "a property 'and' needs at least two operands" in short_and
    short_or = validation_problems(_directive(wrap(PropOr((_SA,)))))
    assert "a property 'or' needs at least two operands" in short_or
    bad_condition = PropIf(BitSelect(V4, 4, BIT), _SA, None)
    assert any("bit select" in p for p in validation_problems(_directive(wrap(bad_condition))))


@pytest.mark.parametrize(
    "prop",
    [
        Always(1, 3, False, _SA),
        Eventually(0, 2, False, _SA),
        Nexttime(1, False, _IMP_AB),
        Not(Always(0, 1, False, _SA)),
        Always(0, 1, False, _IMP_AB),
        Nexttime(2, True, _SA),
    ],
)
def test_the_cover_rule_accepts_temporal_covers(prop: Property) -> None:
    assert validation_problems(_cover(prop)) == ()
    (monitor,) = compile_monitors(_cover(prop))
    assert isinstance(monitor, (Machine, AttemptMachine))


@pytest.mark.parametrize(
    "prop",
    [
        Eventually(0, 1, False, _LATE_IMPLICATION),
        PropOr((_IMP_AB, Nexttime(2, False, _ONE))),
        Not(Always(0, 1, False, Not(_LATE_IMPLICATION))),
    ],
)
def test_the_cover_rule_rejects_temporal_covers_with_late_nonvacuity(prop: Property) -> None:
    problems = validation_problems(_cover(prop))
    assert any("cover of this property is not supported" in p for p in problems), problems
    assert validation_problems(_directive(prop)) == ()
    with pytest.raises(CompileError, match="cover of this property"):
        compile_monitors(_cover(prop))


# --- unbounded constructs: built by the converter, rejected for the machines -----------

_UNSUPPORTED_1C = [
    (
        SequenceProperty(Goto(SA, 2, 2), Strength.STRONG),
        "goto repetition [->] is not supported yet",
    ),
    (
        SequenceProperty(Goto(SA, 1, None), Strength.STRONG),
        "unbounded ranges ($) are not supported yet",
    ),
    (
        SequenceProperty(NonConsecutive(SA, 1, 3), Strength.STRONG),
        "nonconsecutive repetition [=] is not supported yet",
    ),
    (
        SequenceProperty(NonConsecutive(SA, 0, None), Strength.STRONG),
        "unbounded ranges ($) are not supported yet",
    ),
    (
        Eventually(0, None, True, _SA),
        "unbounded 's_eventually' (no upper bound) is not supported yet",
    ),
    (Until(SA, SB, True, False), "'s_until' is not supported yet"),
    (Until(SA, SB, True, True), "'s_until_with' is not supported yet"),
]


_ASSUME_LIVENESS = "a strong unbounded property (liveness) in 'assume'"


def _assert_strong_assume_outcome(prop: Property) -> None:
    """validation treats a strong ``assume`` like the ``assert`` of the same
    property (no liveness problem any more); the compiler accepts it, with a ``PENDING``
    liveness part, exactly when the configuration machine of the ``assert`` has the pending-bit
    kind (at most one configuration), and rejects it with the liveness message otherwise."""
    asserted = _directive(prop)
    assume = replace(asserted, kind=DirectiveKind.ASSUME)
    for shapes in (True, False):
        assert validation_problems(assume, shapes=shapes) == validation_problems(
            asserted, shapes=shapes
        )
    last = compile_monitors(asserted)[-1]
    assert isinstance(last, ConfigMachine) and last.liveness is not None
    if last.liveness is Liveness.PENDING:
        accepted = compile_monitors(assume)[-1]
        assert isinstance(accepted, ConfigMachine) and accepted.liveness is Liveness.PENDING
    else:
        with pytest.raises(CompileError, match=re.escape(_ASSUME_LIVENESS)):
            compile_monitors(assume)


@pytest.mark.parametrize(("prop", "message"), _UNSUPPORTED_1C)
@pytest.mark.parametrize("kind", [DirectiveKind.ASSUME, DirectiveKind.COVER])
@pytest.mark.parametrize("shapes", [True, False])
def test_unbounded_constructs_are_rejected(
    prop: Property, message: str, kind: DirectiveKind, shapes: bool
) -> None:
    """Every construct of the list is strong and unbounded: a supported ``assert``
    (liveness), an ``assume`` of the one-configuration class (the others
    are rejected by the compiler with the liveness message), and rejected as written for a
    ``cover`` unless it is a strong sequence."""
    directive = replace(_directive(prop), kind=kind)
    problems = validation_problems(directive, shapes=shapes)
    if kind is DirectiveKind.ASSUME:
        assert problems == validation_problems(_directive(prop), shapes=shapes)
        if (
            isinstance(prop, SequenceProperty)
            and isinstance(prop.sequence, NonConsecutive)
            and prop.sequence.minimum == 0
        ):
            # This sequence is nullable, independently of its strength and directive kind.
            for directive_kind in (DirectiveKind.ASSERT, DirectiveKind.ASSUME):
                with pytest.raises(CompileError, match="empty match"):
                    compile_monitors(replace(directive, kind=directive_kind))
        else:
            _assert_strong_assume_outcome(prop)
        return
    if kind is DirectiveKind.COVER and isinstance(prop, SequenceProperty):
        # (a strong unbounded sequence is a supported cover)
        assert problems == ()
        return
    assert any(message in p for p in problems), problems
    with pytest.raises(CompileError):
        compile_monitors(directive)


@pytest.mark.parametrize(("prop", "message"), _UNSUPPORTED_1C)
@pytest.mark.parametrize(
    "wrap",
    [
        lambda p: Implication(Bool(SA), p, True),
        lambda p: Nexttime(1, False, p),
        lambda p: PropAnd((_SA, p)),
        lambda p: PropOr((_SA, p)),
        lambda p: PropIf(SA, p, None),
        lambda p: PropIf(SA, _SA, p),
    ],
)
def test_unbounded_constructs_are_found_anywhere(
    prop: Property, message: str, wrap: Callable[[Property], Property]
) -> None:
    """In an ``assume``, as in an ``assert`` (accepted where a weak one is accepted;
    for an ``assume`` in the one-configuration class): where the wrapper lowers to an accepted
    shape no problem (validation reports no liveness problem), else the construct's message."""
    directive = replace(_directive(wrap(prop)), kind=DirectiveKind.ASSUME)
    problems = validation_problems(directive)
    assert problems == validation_problems(_directive(wrap(prop)))
    assert not any(_ASSUME_LIVENESS in p for p in problems), problems
    if problems:
        assert any(message in p for p in problems), problems


# --- unbounded antecedents and unbounded always -------------------------------

_ANTECEDENTS = [
    SeqConcat(Bool(SA), Delay(1, None), Bool(SB)),
    SeqConcat(Bool(SA), Delay(0, None), Bool(SB)),
    Repeat(Bool(SA), 1, None),
    Repeat(Bool(SA), 0, None),
    SeqConcat(Repeat(Bool(SA), 2, None), Delay(1, 1), Bool(SB)),
    Goto(SA, 2, 2),
    Goto(SA, 1, None),
    NonConsecutive(SA, 0, 3),
    NonConsecutive(SA, 2, None),
    SeqConcat(NonConsecutive(SA, 2, 2), Delay(1, 1), Bool(SB)),
    SeqIntersect(Repeat(Bool(SA), 1, None), Repeat(Bool(SB), 2, None)),
    Throughout(SA, Repeat(Bool(SB), 1, None)),
]


@pytest.mark.parametrize("antecedent", _ANTECEDENTS)
@pytest.mark.parametrize("kind", [DirectiveKind.ASSERT, DirectiveKind.ASSUME])
@pytest.mark.parametrize("shapes", [True, False])
def test_an_unbounded_antecedent_into_a_bounded_consequent_is_accepted(
    antecedent: Sequence, kind: DirectiveKind, shapes: bool
) -> None:
    for overlapping in (True, False):
        if not overlapping and sequence_can_be_empty(antecedent):
            continue
        for consequent in (_SB, Not(SequenceProperty(SeqConcat(Bool(SB), Delay(1, 2), Bool(SA))))):
            directive = replace(
                _directive(Implication(antecedent, consequent, overlapping)), kind=kind
            )
            assert validation_problems(directive, shapes=shapes) == ()
            assert compile_monitors(directive)


def sequence_can_be_empty(sequence: Sequence) -> bool:
    """The antecedents of _ANTECEDENTS that can match empty (written out here, not computed
    with the code under test)."""
    return sequence in (Repeat(Bool(SA), 0, None), NonConsecutive(SA, 0, 3))


@pytest.mark.parametrize("antecedent", _ANTECEDENTS)
@pytest.mark.parametrize("shapes", [True, False])
def test_a_cover_of_an_unbounded_antecedent_is_rejected(antecedent: Sequence, shapes: bool) -> None:
    directive = _cover(Implication(antecedent, _SB, True))
    problems = validation_problems(directive, shapes=shapes)
    assert any("a cover of an implication with an unbounded antecedent" in p for p in problems), (
        problems
    )
    with pytest.raises(CompileError):
        compile_monitors(directive)


@pytest.mark.parametrize(
    "prop",
    [
        Implication(Repeat(Bool(SA), 1, None), Implication(Bool(SB), _SA, True), True),
        Implication(Repeat(Bool(SA), 1, None), PropOr((_SA, _SB)), True),
        Not(Implication(Repeat(Bool(SA), 1, None), _SB, True)),
        PropOr((_SA, Implication(Repeat(Bool(SA), 1, None), _SB, True))),
        PropIf(SA, _SA, Implication(Repeat(Bool(SA), 1, None), _SB, True)),
    ],
)
@pytest.mark.parametrize("shapes", [True, False])
def test_every_other_unbounded_property_is_rejected(prop: Property, shapes: bool) -> None:
    problems = validation_problems(_directive(prop), shapes=shapes)
    assert problems, prop
    with pytest.raises(CompileError):
        compile_monitors(_directive(prop))


def test_a_nonoverlapping_unbounded_antecedent_that_can_match_empty_is_rejected() -> None:
    for antecedent in (Repeat(Bool(SA), 0, None), NonConsecutive(SA, 0, None)):
        prop = Implication(antecedent, _SB, False)
        problems = validation_problems(_directive(prop))
        assert "'|=>' with an antecedent that can match empty is not supported" in problems


def test_unbounded_always_is_accepted_where_it_flattens_into_one_implication() -> None:
    imply = Implication(Bool(SA), _SB, True)
    for prop in (
        Always(0, None, False, _SA),
        Always(2, None, False, _SA),
        Always(0, None, False, Not(_SA)),
        Always(1, None, False, imply),
        Always(0, None, False, Implication(Bool(SA), _SB, False)),
        Implication(Bool(SA), Always(0, None, False, _SB), True),
        Implication(Bool(SA), Always(1, None, False, imply), False),
        PropAnd((_SA, Always(0, None, False, _SB))),
        PropIf(SA, Always(0, None, False, _SB), None),
        Always(0, None, False, PropAnd((_SA, _SB))),
    ):
        for kind in (DirectiveKind.ASSERT, DirectiveKind.ASSUME):
            for shapes in (True, False):
                directive = replace(_directive(prop), kind=kind)
                assert validation_problems(directive, shapes=shapes) == (), prop


@pytest.mark.parametrize(
    "prop",
    [
        Always(0, None, False, _SA),
        Always(1, None, False, Implication(Bool(SA), _SB, True)),
        Implication(Bool(SA), Always(0, None, False, _SB), True),
    ],
)
@pytest.mark.parametrize("shapes", [True, False])
def test_unbounded_always_is_rejected_in_a_cover(prop: Property, shapes: bool) -> None:
    problems = validation_problems(_cover(prop), shapes=shapes)
    assert any("unbounded 'always' (no upper bound) is not supported yet" in p for p in problems)


@pytest.mark.parametrize(
    "prop",
    [
        Not(Always(0, None, False, _SA)),
        Nexttime(1, False, Always(0, None, False, _SA)),
        PropOr((_SA, Always(0, None, False, _SB))),
        PropIf(SA, _SA, Always(0, None, False, _SB)),
        Always(0, None, False, Always(0, None, False, Eventually(0, 2, False, _SA))),
        Always(0, None, False, PropOr((_SA, _SB))),  # an operand that is not flattened
        Always(
            0, None, False, Implication(Bool(SA), Always(0, 2, False, PropOr((_SA, _SB))), True)
        ),
        Always(0, None, True, _SA),  # strong: IEEE has no s_always [m:$]
        Always(0, None, True, Until(SA, SB, False, False)),
    ],
)
@pytest.mark.parametrize("shapes", [True, False])
def test_unbounded_always_elsewhere_or_over_other_operands_is_rejected(
    prop: Property, shapes: bool
) -> None:
    problems = validation_problems(_directive(prop), shapes=shapes)
    assert problems, prop
    with pytest.raises(CompileError):
        compile_monitors(_directive(prop))


def test_conditions_of_new_constructs_are_checked_as_expressions() -> None:
    bad = BitSelect(V4, 4, BIT)
    for prop in (
        SequenceProperty(Goto(bad, 1, 1)),
        SequenceProperty(NonConsecutive(bad, 1, 1)),
        Until(bad, SB, False, False),
        Until(SA, bad, True, True),
    ):
        problems = validation_problems(_directive(prop))
        assert any("bit select" in p for p in problems), problems


def test_unbounded_windows_with_a_negative_start_are_invalid() -> None:
    problems = validation_problems(_directive(Always(-1, None, False, _SA)))
    assert any("always range [-1:$] is invalid" in p for p in problems), problems
    problems = validation_problems(_directive(Eventually(-1, None, True, _SA)))
    assert any("eventually range [-1:$] is invalid" in p for p in problems), problems


def test_bounded_windows_are_still_accepted() -> None:
    assert validation_problems(_directive(Always(1, 3, False, _SA))) == ()
    assert validation_problems(_directive(Eventually(0, 2, True, _SA))) == ()


# --- the first-sample rule sees the negations that goto / nonconsecutive repetition add -------


def _unsafe_negation() -> Expr:
    """``a && $past(b)``: definitely false at the first sample (IEEE: 0 && X), but its negation
    ``!(a && $past(b))`` is true there (the machine would gate it false)."""
    return Binary(BinaryOp.LOGICAL_AND, SA, _past(SB), BIT)


@pytest.mark.parametrize("repetition", [Goto, NonConsecutive])
@pytest.mark.parametrize("counts", [(1, 1), (2, None), (0, 2)])
def test_a_goto_whose_negation_is_not_first_sample_safe_is_rejected(
    repetition: type[Goto] | type[NonConsecutive], counts: tuple[int, int | None]
) -> None:
    antecedent = SeqConcat(Bool(SA), Delay(0, 0), repetition(_unsafe_negation(), *counts))
    directive = _directive(Implication(antecedent, SequenceProperty(Bool(Const(0, BIT))), True))
    problems = validation_problems(directive)
    assert any("known true at the first sample" in p for p in problems), problems
    with pytest.raises(CompileError):
        compile_monitors(directive)


@pytest.mark.parametrize("repetition", [Goto, NonConsecutive])
@pytest.mark.parametrize(
    "condition", [SB, _past(SB), Binary(BinaryOp.LOGICAL_OR, SA, _past(SB), BIT)]
)
def test_a_goto_whose_negation_is_safe_stays_accepted(
    repetition: type[Goto] | type[NonConsecutive], condition: Expr
) -> None:
    """No ``$past`` (nothing to gate), or ``$past(b)`` alone (X in both polarities, so
    false in both, as in IEEE), or ``a || $past(b)`` is rejected by the written rule already
    (checked here against the plain form)."""
    directive = _directive(
        Implication(repetition(condition, 1, 1), SequenceProperty(Bool(Const(0, BIT))), True)
    )
    written = validation_problems(
        _directive(Implication(Bool(condition), SequenceProperty(Bool(Const(0, BIT))), True))
    )
    assert validation_problems(directive) == written


def test_an_unbounded_antecedent_with_an_unsupported_consequent_says_so() -> None:
    antecedent = SeqConcat(Bool(SA), Delay(1, None), Bool(SB))
    later = Nexttime(1, False, _SA)
    window = Always(1, 3, False, _SA)
    for consequent in (later, window, Implication(Bool(SB), _SA, True), PropOr((_SA, _SB))):
        problems = validation_problems(_directive(Implication(antecedent, consequent, True)))
        assert any("unbounded antecedent needs a bounded sequence" in p for p in problems), (
            consequent,
            problems,
        )
    # (an unbounded consequent after an unbounded antecedent is supported, weak or strong)
    for unbounded in (_UNBOUNDED, _STRONG_UNBOUNDED):
        assert validation_problems(_directive(Implication(antecedent, unbounded, True))) == ()


# --- weak unbounded consequents ------------------------------------------------

_LOOP_B = SequenceProperty(Repeat(Bool(SB), 1, None))
_FALSE_BOOL = Bool(Const(0, BIT))


@pytest.mark.parametrize(
    "prop",
    [
        Implication(Bool(SA), _UNBOUNDED, True),
        Implication(Bool(SA), _UNBOUNDED, False),
        Implication(Repeat(Bool(SA), 1, None), _UNBOUNDED, True),
        Implication(Bool(SA), SequenceProperty(Goto(SB, 2, 2)), True),
        Implication(Bool(SA), SequenceProperty(NonConsecutive(SB, 0, 1)), False),
        Implication(Bool(SA), SequenceProperty(Repeat(Bool(SB), 1, None), Strength.WEAK), True),
        _UNBOUNDED,
        SequenceProperty(Repeat(Bool(SB), 1, None), Strength.WEAK),
        Always(0, None, False, Implication(Bool(SA), _LOOP_B, True)),
        Always(2, None, False, _LOOP_B),
        Implication(Bool(SA), Always(0, None, False, _LOOP_B), True),
        PropAnd((_SA, Implication(Bool(SB), _UNBOUNDED, True))),
        PropIf(SA, _SA, _UNBOUNDED),  # lowered to a weak sequence: (a ##0 s1) or (!a ##0 s2)
        PropIf(SA, Implication(Bool(SB), _UNBOUNDED, True), None),
        Nexttime(1, False, _UNBOUNDED),  # lowered to (1 ##1 1) |-> s
        Always(0, None, False, Always(0, None, False, Implication(Bool(SA), _UNBOUNDED, True))),
    ],
)
@pytest.mark.parametrize("kind", [DirectiveKind.ASSERT, DirectiveKind.ASSUME])
@pytest.mark.parametrize("shapes", [True, False])
def test_weak_unbounded_consequents_are_accepted(
    prop: Property, kind: DirectiveKind, shapes: bool
) -> None:
    directive = replace(_directive(prop), kind=kind)
    assert validation_problems(directive, shapes=shapes) == (), prop


@pytest.mark.parametrize(
    "prop",
    [
        Implication(Bool(SA), Implication(Bool(SB), _LOOP_B, True), True),
        Implication(Bool(SA), PropOr((_SA, _LOOP_B)), True),
        PropOr((_SA, _UNBOUNDED)),
    ],
)
@pytest.mark.parametrize("shapes", [True, False])
def test_other_unbounded_consequents_stay_rejected(prop: Property, shapes: bool) -> None:
    assert validation_problems(_directive(prop), shapes=shapes), prop
    with pytest.raises(CompileError):
        compile_monitors(_directive(prop))


@pytest.mark.parametrize(
    "prop",
    [
        Always(0, None, False, _LOOP_B),
        Not(_LOOP_B),
        PropOr((_SA, _LOOP_B)),
        PropAnd((_SA, _LOOP_B)),
        Nexttime(1, False, _LOOP_B),
        PropIf(SA, _LOOP_B, None),
        Implication(Bool(SA), Implication(Bool(SB), _LOOP_B, True), True),
        Implication(Bool(SA), Not(_LOOP_B), True),
    ],
)
def test_a_cover_of_any_other_unbounded_shape_stays_rejected(prop: Property) -> None:
    problems = validation_problems(_cover(prop))
    assert problems, prop
    with pytest.raises(CompileError):
        compile_monitors(_cover(prop))


@pytest.mark.parametrize(
    "consequent",
    [
        SeqConcat(Bool(SA), Delay(1, None), _FALSE_BOOL),
        SeqConcat(Repeat(Bool(SB), 1, None), Delay(1, 1), _FALSE_BOOL),
        SeqOr(Repeat(Bool(SB), 1, None), _FALSE_BOOL),
        Goto(Const(0, BIT), 1, 1),
        NonConsecutive(Const(0, BIT), 1, None),
    ],
)
def test_a_constant_false_condition_in_an_unbounded_consequent_is_accepted(
    consequent: Sequence,
) -> None:
    """the constant is an ordinary letter evaluated at its own sample (an unknown
    future sample satisfies it), so the consequent is a legal weak unbounded one."""
    for prop in (
        Implication(Bool(SA), SequenceProperty(consequent), True),
        SequenceProperty(consequent),
        Always(0, None, False, SequenceProperty(consequent)),
    ):
        assert validation_problems(_directive(prop)) == (), prop
        compile_monitors(_directive(prop))


def test_a_constant_false_condition_in_a_bounded_consequent_or_an_antecedent_is_accepted() -> None:
    bounded = Implication(
        Bool(SA), SequenceProperty(SeqConcat(Bool(SA), Delay(1, 2), _FALSE_BOOL)), True
    )
    antecedent = Implication(
        SeqConcat(Bool(SA), Delay(1, None), _FALSE_BOOL), SequenceProperty(Bool(SB)), True
    )
    assert validation_problems(_directive(bounded)) == ()
    assert validation_problems(_directive(antecedent)) == ()


def test_the_first_sample_rule_applies_to_the_conditions_of_an_unbounded_consequent() -> None:
    risky = Binary(BinaryOp.LOGICAL_OR, _past(SA), SB, BIT)
    prop = Implication(
        Bool(SA), SequenceProperty(SeqConcat(Bool(SB), Delay(1, None), Bool(risky))), True
    )
    assert validation_problems(_directive(prop))
    goto = Implication(
        Bool(SA),
        SequenceProperty(Goto(Binary(BinaryOp.LOGICAL_AND, SA, _past(SB), BIT), 1, 1)),
        True,
    )
    assert validation_problems(_directive(goto))


_TRUE_CONST = Const(1, BIT)


@pytest.mark.parametrize(
    "prop",
    [
        # the expansions of goto / nonconsecutive negate a constant-true condition
        SequenceProperty(
            SeqIntersect(
                NonConsecutive(_TRUE_CONST, 1, 1), SeqConcat(Bool(SA), Delay(1, None), Bool(SB))
            )
        ),
        SequenceProperty(
            SeqIntersect(Goto(_TRUE_CONST, 2, 2), SeqConcat(Bool(SA), Delay(2, None), Bool(SB)))
        ),
        Implication(
            Bool(SA),
            SequenceProperty(
                SeqConcat(
                    Bool(_TRUE_CONST),
                    Delay(1, None),
                    SeqIntersect(
                        NonConsecutive(_TRUE_CONST, 1, 1), Repeat(Bool(_TRUE_CONST), 2, 2)
                    ),
                )
            ),
            True,
        ),
    ],
)
def test_a_constant_false_letter_introduced_by_an_expansion_is_accepted(prop: Property) -> None:
    """The negation of a constant-true goto/nonconsecutive condition is a constant-false letter:
    an ordinary letter, so nothing is rejected."""
    assert validation_problems(replace(_directive(prop), disable=SB)) == ()
    compile_monitors(_directive(prop))


@pytest.mark.parametrize(
    "consequent",
    [
        Repeat(Bool(_TRUE_CONST), 1, None),
        Goto(SB, 2, 2),
        NonConsecutive(SB, 1, None),
        SeqIntersect(NonConsecutive(SB, 1, 1), Repeat(Bool(_TRUE_CONST), 2, 2)),
    ],
)
def test_constant_true_and_non_constant_expansions_stay_accepted(consequent: Sequence) -> None:
    prop = Implication(Bool(SA), SequenceProperty(consequent), True)
    assert validation_problems(_directive(prop)) == ()


# --- weak until and until_with -------------------------------------------------

_UNTIL = Until(SA, SB, False, False)
_UNTIL_WITH = Until(SA, SB, False, True)


@pytest.mark.parametrize("until", [_UNTIL, _UNTIL_WITH], ids=["until", "until_with"])
@pytest.mark.parametrize("kind", [DirectiveKind.ASSERT, DirectiveKind.ASSUME])
@pytest.mark.parametrize("shapes", [True, False])
@pytest.mark.parametrize(
    "wrap",
    [
        lambda u: u,
        lambda u: Implication(Bool(SC), u, True),
        lambda u: Implication(Bool(SC), u, False),
        lambda u: Implication(Repeat(Bool(SC), 1, None), u, True),
        lambda u: Always(0, None, False, u),
        lambda u: Always(2, None, False, u),
        lambda u: Always(0, None, False, Implication(Bool(SC), u, True)),
        lambda u: Always(0, None, False, Implication(Bool(SC), u, False)),
        lambda u: Implication(Bool(SC), Always(0, None, False, u), True),
        lambda u: Nexttime(1, False, u),
        lambda u: Nexttime(2, False, u),
        lambda u: Always(0, 2, False, u),
        lambda u: PropIf(SC, u, None),
        lambda u: PropIf(SC, u, SequenceProperty(Bool(SA), Strength.WEAK)),
        lambda u: PropAnd((_SA, u)),
    ],
)
def test_weak_until_is_accepted_where_a_weak_unbounded_sequence_is(
    until: Until,
    kind: DirectiveKind,
    shapes: bool,
    wrap: Callable[[Property], Property],
) -> None:
    directive = replace(_directive(wrap(until)), kind=kind)
    assert validation_problems(directive, shapes=shapes) == ()
    assert compile_monitors(directive)


@pytest.mark.parametrize(
    "prop",
    [
        PropOr((_SA, _UNTIL)),
        Implication(Bool(SC), PropOr((_SA, _UNTIL_WITH)), True),
        Implication(Bool(SC), Implication(Bool(SA), _UNTIL, True), True),
        Implication(Bool(SC), Nexttime(2, False, _UNTIL), True),  # a nested implication
        PropIf(SC, _UNTIL, _SA),  # strengths differ: stays a property if
        Always(0, None, False, PropOr((_SA, _UNTIL))),
        PropIf(SC, _UNTIL, Implication(Bool(SA), _SB, True)),
    ],
)
@pytest.mark.parametrize("shapes", [True, False])
def test_other_until_shapes_stay_rejected(prop: Property, shapes: bool) -> None:
    problems = validation_problems(_directive(prop), shapes=shapes)
    assert any("until" in p for p in problems), (prop, problems)
    with pytest.raises(CompileError):
        compile_monitors(_directive(prop))


@pytest.mark.parametrize("until", [_UNTIL, _UNTIL_WITH], ids=["until", "until_with"])
@pytest.mark.parametrize(
    "wrap",
    [
        lambda u: u,
        lambda u: Implication(Bool(SC), u, True),
        lambda u: Always(0, None, False, u),
    ],
)
def test_a_cover_containing_until_stays_rejected(
    until: Until, wrap: Callable[[Property], Property]
) -> None:
    problems = validation_problems(_cover(wrap(until)))
    assert any("until" in p for p in problems), problems
    with pytest.raises(CompileError):
        compile_monitors(_cover(wrap(until)))


def test_the_first_sample_rule_runs_on_the_generated_conjunction_of_until_with() -> None:
    """``p && q`` is a condition of the lowered property: ``$past(a) || b`` as ``p`` is a
    problem for the first-sample rule in ``until`` and in ``until_with`` alike."""
    unsafe = Binary(BinaryOp.LOGICAL_OR, Sampled(SampledFunction.PAST, SA, BIT), SB, BIT)
    for inclusive in (False, True):
        for prop in (Until(unsafe, SB, False, inclusive), Until(SB, unsafe, False, inclusive)):
            assert validation_problems(_directive(prop)), prop
    safe = Sampled(SampledFunction.PAST, SA, BIT)
    for inclusive in (False, True):
        for prop in (Until(safe, SB, False, inclusive), Until(SB, safe, False, inclusive)):
            assert validation_problems(_directive(prop)) == (), prop


# --- covers of unbounded sequences -------------------------------------------------

_DELAY_TO_B = SeqConcat(Bool(SA), Delay(1, None), Bool(SB))
_REPEAT_THEN_B = SeqConcat(Repeat(Bool(SA), 2, None), Delay(1, 1), Bool(SB))
_GOTO_B = Goto(SB, 2, 2)
_NONCONSECUTIVE_THEN_C = SeqConcat(NonConsecutive(SB, 2, 2), Delay(1, 1), Bool(SC))
_LENGTH_TWO = SeqConcat(Bool(SA), Delay(1, 1), Bool(SB))

_ACCEPTED_COVERS: list[Property] = [
    SequenceProperty(_DELAY_TO_B),
    SequenceProperty(_DELAY_TO_B, Strength.STRONG),
    SequenceProperty(_REPEAT_THEN_B),
    SequenceProperty(_GOTO_B),
    SequenceProperty(_NONCONSECUTIVE_THEN_C),
    SequenceProperty(SeqOr(_DELAY_TO_B, Bool(SC))),
    Implication(
        Bool(SA), SequenceProperty(SeqConcat(Bool(Const(1, BIT)), Delay(1, None), Bool(SB))), True
    ),
    Implication(Bool(SA), SequenceProperty(_LOOP_B.sequence), False),
    Implication(Bool(SA), SequenceProperty(_GOTO_B, Strength.STRONG), True),
    Implication(_LENGTH_TWO, SequenceProperty(Repeat(Bool(SC), 2, None)), True),
    Implication(_LENGTH_TWO, SequenceProperty(_LOOP_B.sequence), False),
    Implication(Bool(SC), SequenceProperty(_NONCONSECUTIVE_THEN_C), True),
    # (the empty match of a nullable antecedent is ignored: one length, as for bounded covers)
    Implication(Repeat(Bool(SA), 0, 1), SequenceProperty(_LOOP_B.sequence), True),
]


@pytest.mark.parametrize("prop", _ACCEPTED_COVERS)
@pytest.mark.parametrize("shapes", [True, False])
def test_a_cover_of_an_unbounded_sequence_is_accepted(prop: Property, shapes: bool) -> None:
    assert validation_problems(_cover(prop), shapes=shapes) == (), prop


@pytest.mark.parametrize("prop", _ACCEPTED_COVERS)
def test_an_accepted_unbounded_cover_compiles_to_one_configuration_machine(prop: Property) -> None:
    (monitor,) = compile_monitors(_cover(prop))
    assert isinstance(monitor, ConfigMachine)
    assert monitor.directive.kind is DirectiveKind.COVER
    with pytest.raises(CompileError):
        compile_attempt_machine(_cover(prop))
    with pytest.raises(CompileError):
        compile_directive(_cover(prop))


_WEAK_MESSAGE = "a cover of a weak unbounded sequence is not supported"


@pytest.mark.parametrize(
    "prop",
    [
        SequenceProperty(_DELAY_TO_B, Strength.WEAK),
        SequenceProperty(_GOTO_B, Strength.WEAK),
        SequenceProperty(_REPEAT_THEN_B, Strength.WEAK),
        Implication(Bool(SA), SequenceProperty(_LOOP_B.sequence, Strength.WEAK), True),
        Implication(_LENGTH_TWO, SequenceProperty(_NONCONSECUTIVE_THEN_C, Strength.WEAK), False),
    ],
)
@pytest.mark.parametrize("shapes", [True, False])
def test_an_explicitly_weak_unbounded_cover_is_rejected(prop: Property, shapes: bool) -> None:
    problems = validation_problems(_cover(prop), shapes=shapes)
    assert any(_WEAK_MESSAGE in p for p in problems), problems
    assert not any("unbounded ranges" in p for p in problems), problems  # (explained once)
    # the same property is accepted under assert, where weak is the default
    assert validation_problems(_directive(prop), shapes=shapes) == ()
    with pytest.raises(CompileError, match=_WEAK_MESSAGE):
        compile_monitors(_cover(prop))


def test_a_weak_bounded_cover_is_unchanged() -> None:
    """``weak(a ##[1:2] b)`` has no unbounded part: strength never changes its monitor."""
    wide = SeqConcat(Bool(SA), Delay(1, 2), Bool(SB))
    weak = _cover(SequenceProperty(wide, Strength.WEAK))
    plain = _cover(SequenceProperty(wide))
    assert validation_problems(weak) == ()
    assert isinstance(compile_monitors(weak)[0], Machine)
    assert isinstance(compile_monitors(plain)[0], Machine)


@pytest.mark.parametrize("until", [_UNTIL, _UNTIL_WITH], ids=["until", "until_with"])
@pytest.mark.parametrize(
    "wrap",
    [
        lambda u: u,
        lambda u: Implication(Bool(SC), u, True),
        lambda u: Implication(Bool(SC), u, False),
        lambda u: Always(0, None, False, u),
    ],
)
@pytest.mark.parametrize("shapes", [True, False])
def test_a_cover_of_a_weak_until_is_rejected_with_its_name(
    until: Until, wrap: Callable[[Property], Property], shapes: bool
) -> None:
    name = "until_with" if until.inclusive else "until"
    message = f"a cover of '{name}' is not supported"
    problems = validation_problems(_cover(wrap(until)), shapes=shapes)
    assert any(message in p for p in problems), problems
    with pytest.raises(CompileError, match=message):
        compile_monitors(_cover(wrap(until)))


def test_a_cover_of_a_strong_until_stays_not_supported_yet() -> None:
    strong = Until(SA, SB, True, False)
    problems = validation_problems(_cover(strong))
    assert any("'s_until' is not supported yet" in p for p in problems), problems


@pytest.mark.parametrize(
    "antecedent",
    [
        SeqConcat(Bool(SA), Delay(1, 2), Bool(SB)),  # two lengths
        SeqOr(Bool(SA), SeqConcat(Bool(SA), Delay(1, 1), Bool(SB))),
        Repeat(Bool(SA), 0, 2),  # lengths 1 and 2 (the empty match is ignored)
        SeqConcat(Repeat(Bool(SA), 1, 2), Delay(1, 1), Bool(SB)),
    ],
)
@pytest.mark.parametrize("overlapping", [True, False])
def test_an_antecedent_of_several_lengths_with_an_unbounded_consequent_is_rejected(
    antecedent: Sequence, overlapping: bool
) -> None:
    prop = Implication(antecedent, _LOOP_B, overlapping)
    problems = validation_problems(_cover(prop))
    assert any("a single match length" in p for p in problems), problems
    with pytest.raises(CompileError, match="a single match length"):
        compile_monitors(_cover(prop))
    # a bounded consequent keeps the per-attempt machine, as before
    bounded = Implication(antecedent, _SB, overlapping)
    if overlapping or not sequence_bounds(antecedent).nullable:
        assert isinstance(compile_monitors(_cover(bounded))[0], AttemptMachine)


def test_a_cover_with_an_antecedent_that_never_matches_is_a_single_length() -> None:
    """An antecedent with no match at all counts as single-length (nothing is ever covered)."""
    dead = SeqIntersect(Bool(SA), SeqConcat(Bool(SA), Delay(1, 1), Bool(SB)))
    prop = Implication(dead, _LOOP_B, True)
    assert validation_problems(_cover(prop)) == ()


@pytest.mark.parametrize("kind", [DirectiveKind.ASSERT, DirectiveKind.ASSUME])
def test_assert_and_assume_are_unchanged_by_the_cover_rules(kind: DirectiveKind) -> None:
    for prop in (
        SequenceProperty(_DELAY_TO_B),
        SequenceProperty(_DELAY_TO_B, Strength.WEAK),
        Implication(Bool(SA), _LOOP_B, True),
        Implication(Repeat(Bool(SA), 1, 2), _LOOP_B, True),
    ):
        assert validation_problems(replace(_directive(prop), kind=kind)) == ()


# --- strong unbounded properties: liveness for assert --------------------------

_S_EVENTUALLY = Eventually(0, None, True, _SB)
_STRONG_WRAPS: list[Callable[[Property], Property]] = [
    lambda p: p,
    lambda p: Implication(Bool(SC), p, True),
    lambda p: Implication(Bool(SC), p, False),
    lambda p: Implication(Repeat(Bool(SC), 1, None), p, True),
    lambda p: Always(0, None, False, p),
    lambda p: Always(2, None, False, p),
    lambda p: Always(0, None, False, Implication(Bool(SC), p, True)),
    lambda p: Always(0, None, False, Implication(Bool(SC), p, False)),
    lambda p: Implication(Bool(SC), Always(0, None, False, p), True),
    lambda p: Nexttime(1, False, p),
    lambda p: Always(0, 2, False, p),
    lambda p: PropIf(SC, p, None),
    lambda p: PropAnd((_SA, p)),
]
_STRONG_FORMS: list[Property] = [
    _STRONG_UNBOUNDED,
    SequenceProperty(Goto(SB, 2, 2), Strength.STRONG),
    _S_EVENTUALLY,
    Eventually(2, None, True, _SB),
    Until(SA, SB, True, False),
    Until(SA, SB, True, True),
]


@pytest.mark.parametrize("form", _STRONG_FORMS)
@pytest.mark.parametrize("wrap", _STRONG_WRAPS)
@pytest.mark.parametrize("shapes", [True, False])
def test_a_strong_unbounded_property_is_accepted_for_assert_where_a_weak_one_is(
    form: Property, wrap: Callable[[Property], Property], shapes: bool
) -> None:
    """``strong(...)``, ``s_eventually`` and ``s_until`` are decided where a
    weak unbounded sequence is (the whole property, a consequent, under ``always``, ...)."""
    directive = _directive(wrap(form))
    assert validation_problems(directive, shapes=shapes) == ()
    monitors = compile_monitors(directive)
    assert monitors
    assert all(isinstance(m, ConfigMachine) and m.liveness is not None for m in monitors[-1:])


@pytest.mark.parametrize("form", _STRONG_FORMS)
@pytest.mark.parametrize("wrap", _STRONG_WRAPS)
def test_a_strong_unbounded_property_is_accepted_for_assume_in_the_one_configuration_class(
    form: Property, wrap: Callable[[Property], Property]
) -> None:
    """validation no longer reports a strong ``assume`` part; the compiler accepts
    it with a pending-bit fairness part, or rejects it (several configurations) with the
    liveness message."""
    _assert_strong_assume_outcome(wrap(form))


def test_the_bare_strong_forms_split_into_accepted_and_rejected_assumes() -> None:
    """hand-derived (configurations as in ``test_liveness_machine.KINDS``):
    ``s_eventually b`` and ``b s_until(_with) c`` have one configuration (accepted), ``strong(a
    ##[1:$] b)``, ``strong(b[->2])`` and ``s_eventually [2:$] b`` need a witness (rejected)."""
    accepted: list[Property] = [
        _S_EVENTUALLY,
        Until(SA, SB, True, False),
        Until(SA, SB, True, True),
    ]
    for prop in accepted:
        (monitor,) = compile_monitors(replace(_directive(prop), kind=DirectiveKind.ASSUME))
        assert isinstance(monitor, ConfigMachine) and monitor.liveness is Liveness.PENDING
    rejected: list[Property] = [
        _STRONG_UNBOUNDED,
        SequenceProperty(Goto(SB, 2, 2), Strength.STRONG),
        Eventually(2, None, True, _SB),
    ]
    for prop in rejected:
        with pytest.raises(CompileError, match=re.escape(_ASSUME_LIVENESS)):
            compile_monitors(replace(_directive(prop), kind=DirectiveKind.ASSUME))


@pytest.mark.parametrize(
    ("prop", "fragment"),
    [
        # `not` of an unbounded sequence is accepted; these keep their messages
        (Not(Eventually(0, None, False, _SB)), "unbounded 's_eventually' (no upper bound)"),
        (
            Not(Eventually(0, None, True, Implication(Bool(SA), _SB, True))),
            "unbounded 's_eventually' (no upper bound)",
        ),
        (Not(Eventually(0, None, True, _UNBOUNDED)), "unbounded 's_eventually' (no upper bound)"),
        (Eventually(0, None, False, _SB), "unbounded 's_eventually' (no upper bound)"),
        (
            Eventually(0, None, True, Implication(Bool(SA), _SB, True)),
            "unbounded 's_eventually' (no upper bound)",
        ),
        (Eventually(0, None, True, _UNBOUNDED), "unbounded 's_eventually' (no upper bound)"),
        (PropOr((_SA, _S_EVENTUALLY)), "unbounded"),
    ],
)
def test_other_strong_shapes_keep_their_messages(prop: Property, fragment: str) -> None:
    problems = validation_problems(_directive(prop))
    assert any(fragment in p for p in problems), problems


# --- `not` of an unbounded sequence --------------------------------------------

# strong after the negation (the operand is weak): a liveness part for an assert, rejected for an
# assume with the strong-assume message
_NOT_WEAK_FORMS: list[Property] = [
    _UNBOUNDED,
    SequenceProperty(_UNBOUNDED.sequence, Strength.WEAK),
    Until(SA, SB, False, False),
    Until(SA, SB, False, True),
]
# weak after the negation (the operand is strong): safety only, valid in an assert and an assume
_NOT_STRONG_FORMS: list[Property] = _STRONG_FORMS


@pytest.mark.parametrize("form", [*_NOT_WEAK_FORMS, *_NOT_STRONG_FORMS])
@pytest.mark.parametrize("wrap", _STRONG_WRAPS)
@pytest.mark.parametrize("shapes", [True, False])
def test_not_of_an_unbounded_sequence_is_accepted_for_assert_where_the_sequence_is(
    form: Property, wrap: Callable[[Property], Property], shapes: bool
) -> None:
    """``not`` of a weak or strong unbounded sequence is decided where the sequence
    itself is (the whole property, a consequent, under ``always``, ...), by a negated
    configuration machine; it has a liveness part exactly when the operand is weak."""
    directive = _directive(wrap(Not(form)))
    assert validation_problems(directive, shapes=shapes) == ()
    monitors = compile_monitors(directive)
    last = monitors[-1]
    assert isinstance(last, ConfigMachine) and last.negated
    assert (last.liveness is not None) == (form in _NOT_WEAK_FORMS)


@pytest.mark.parametrize("form", _NOT_WEAK_FORMS)
@pytest.mark.parametrize("wrap", _STRONG_WRAPS)
def test_not_of_a_weak_unbounded_sequence_assume_follows_the_configuration_count(
    form: Property, wrap: Callable[[Property], Property]
) -> None:
    """``not weak(s)`` is strong, a liveness assumption; accepted
    with one configuration (``not (a until b)``) and rejected by the compiler otherwise."""
    _assert_strong_assume_outcome(wrap(Not(form)))


@pytest.mark.parametrize("form", _NOT_STRONG_FORMS)
@pytest.mark.parametrize("wrap", _STRONG_WRAPS)
@pytest.mark.parametrize("shapes", [True, False])
def test_not_of_a_strong_unbounded_sequence_is_accepted_for_assume(
    form: Property, wrap: Callable[[Property], Property], shapes: bool
) -> None:
    """``not strong(s)`` is weak: safety only, a valid assumption."""
    directive = replace(_directive(wrap(Not(form))), kind=DirectiveKind.ASSUME)
    assert validation_problems(directive, shapes=shapes) == ()
    last = compile_monitors(directive)[-1]
    assert isinstance(last, ConfigMachine) and last.negated and last.liveness is None


@pytest.mark.parametrize("form", [*_NOT_WEAK_FORMS, *_NOT_STRONG_FORMS])
def test_a_cover_of_not_of_an_unbounded_sequence_stays_rejected(form: Property) -> None:
    """A cover is judged on the property as written (the cover rule)."""
    for prop in (Not(form), Implication(Bool(SA), Not(form), True)):
        assert validation_problems(_cover(prop)), prop
        with pytest.raises(CompileError):
            compile_monitors(_cover(prop))


def test_a_cover_of_s_eventually_and_s_until_is_judged_as_written() -> None:
    for prop in (_S_EVENTUALLY, Until(SA, SB, True, False)):
        problems = validation_problems(_cover(prop))
        assert problems, prop


@pytest.mark.parametrize("negated", [False, True])
def test_a_nullable_unbounded_consequent_is_rejected_by_the_compiler(negated: bool) -> None:
    """``a |=> b[=0:1]`` and ``a |=> not (b[=0:1])``: the consequent can match empty
    (Slang rejects it in source, ``SeqEmptyMatch``); the compiler refuses it, with or
    without ``not``."""
    consequent: Property = SequenceProperty(NonConsecutive(SB, 0, 1))
    if negated:
        consequent = Not(consequent)
    with pytest.raises(CompileError, match="empty match"):
        compile_monitors(_directive(Implication(Bool(SA), consequent, False)))


@pytest.mark.parametrize(
    "prop",
    [
        Not(_UNBOUNDED),
        Implication(Bool(SA), Not(_LOOP_B), True),
        Not(_UNTIL),
        Not(_UNTIL_WITH),
        Implication(Bool(SC), Not(_UNTIL), True),
        Nexttime(1, False, Not(_UNTIL)),
        Always(0, None, False, Not(_UNTIL)),
    ],
)
@pytest.mark.parametrize("shapes", [True, False])
def test_not_of_an_unbounded_sequence_is_accepted_in_an_assert(
    prop: Property, shapes: bool
) -> None:
    """``not`` of a weak unbounded sequence (strong after negation) in an ``assert``,
    in the positions where the sequence itself is accepted; an ``assume`` of it is a fairness
    assumption (one configuration) or rejected by the compiler."""
    assert validation_problems(_directive(prop), shapes=shapes) == (), prop
    assert compile_monitors(_directive(prop))
    _assert_strong_assume_outcome(prop)
