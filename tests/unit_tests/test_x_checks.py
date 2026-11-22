"""Unit contract of ``sva2yosys.passes.x_checks``: constant folding of the
expressions of a directive, and the sound structural criteria "can never fail" and "can
never succeed" (see the module docstring)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from support.reference.evaluate import Outcome, evaluate_attempts, finalize_attempts
from support.reference.trace import Trace
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
    TRUE,
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
from sva2yosys.passes.x_checks import (
    fold_expr,
    fold_property,
    never_fails,
    never_matches,
    never_succeeds,
    truth,
)

A = Signal("a", BIT)
B = Signal("b", BIT)
V = Signal("v", ValueType(4))
ONE = Const(1, BIT)
ZERO = Const(0, BIT)


def _not(e: Expr) -> Expr:
    return Unary(UnaryOp.LOGICAL_NOT, e, BIT)


def _and(left: Expr, right: Expr) -> Expr:
    return Binary(BinaryOp.LOGICAL_AND, left, right, BIT)


def _or(left: Expr, right: Expr) -> Expr:
    return Binary(BinaryOp.LOGICAL_OR, left, right, BIT)


def _seq(e: Expr) -> SequenceProperty:
    return SequenceProperty(Bool(e))


# --- truth and folding ---------------------------------------------------------------------


def test_truth_of_constants_only() -> None:
    assert truth(ONE) is True
    assert truth(Const(8, ValueType(4))) is True
    assert truth(ZERO) is False
    assert truth(A) is None
    assert truth(_not(A)) is None


@pytest.mark.parametrize(
    ("expr", "folded"),
    [
        (_not(ZERO), ONE),
        (_not(ONE), ZERO),
        (_not(Const(4, ValueType(4))), ZERO),
        (_and(A, ZERO), ZERO),
        (_and(ZERO, A), ZERO),
        (_and(A, ONE), A),
        (_and(ONE, A), A),
        (_and(ONE, ONE), ONE),
        (_or(A, ONE), ONE),
        (_or(ONE, A), ONE),
        (_or(A, ZERO), A),
        (_or(ZERO, A), A),
        (_or(ZERO, ZERO), ZERO),
        # the x-check shapes: !$isunknown(c) is !0
        (_and(A, _not(ZERO)), A),
        (_or(A, ZERO), A),
        (Binary(BinaryOp.LOGICAL_IMPLICATION, ZERO, A, BIT), ONE),
        (Binary(BinaryOp.LOGICAL_IMPLICATION, A, ONE, BIT), ONE),
        (Binary(BinaryOp.LOGICAL_IMPLICATION, ONE, A, BIT), A),
        (Binary(BinaryOp.LOGICAL_IMPLICATION, A, ZERO, BIT), _not(A)),
        (Binary(BinaryOp.LOGICAL_EQUIVALENCE, ONE, A, BIT), A),
        (Binary(BinaryOp.LOGICAL_EQUIVALENCE, ZERO, A, BIT), _not(A)),
        (Binary(BinaryOp.LOGICAL_EQUIVALENCE, ZERO, ZERO, BIT), ONE),
        (Conditional(ZERO, A, B, BIT), B),
        (Conditional(ONE, A, B, BIT), A),
        (Conditional(_not(ONE), A, B, BIT), B),
        (
            Unary(UnaryOp.BITWISE_NOT, Const(0b0101, ValueType(4)), ValueType(4)),
            Const(0b1010, ValueType(4)),
        ),
        (Unary(UnaryOp.REDUCE_OR, Const(0b0100, ValueType(4)), BIT), ONE),
        (Unary(UnaryOp.REDUCE_AND, Const(0b0100, ValueType(4)), BIT), ZERO),
        (Unary(UnaryOp.REDUCE_XOR, Const(0b0111, ValueType(4)), BIT), ONE),
        (
            Convert(Const(0b1000, ValueType(4, signed=True)), ValueType(8, signed=True)),
            Const(0xF8, ValueType(8, signed=True)),
        ),
        (Convert(Const(0b1000, ValueType(4)), ValueType(8)), Const(8, ValueType(8))),
        (Convert(Const(0x1F, ValueType(8)), ValueType(4)), Const(0xF, ValueType(4))),
        (BitSelect(Const(0b0100, ValueType(4)), 2, BIT), ONE),
        (RangeSelect(Const(0b0110, ValueType(4)), 2, 1, ValueType(2)), Const(0b11, ValueType(2))),
        (Concat((ONE, Const(0, ValueType(2))), ValueType(3)), Const(0b100, ValueType(3))),
        (Replicate(3, ONE, ValueType(3)), Const(7, ValueType(3))),
        (
            Binary(BinaryOp.BITWISE_AND, V, Const(0, ValueType(4)), ValueType(4)),
            Const(0, ValueType(4)),
        ),
        (Binary(BinaryOp.BITWISE_AND, V, Const(15, ValueType(4)), ValueType(4)), V),
        (
            Binary(BinaryOp.BITWISE_OR, V, Const(15, ValueType(4)), ValueType(4)),
            Const(15, ValueType(4)),
        ),
        (Binary(BinaryOp.BITWISE_OR, Const(0, ValueType(4)), V, ValueType(4)), V),
    ],
)
def test_fold_expr_values(expr: Expr, folded: Expr) -> None:
    assert fold_expr(expr) == folded


def test_fold_keeps_the_first_sample_value() -> None:
    first = FirstSample(BIT)
    assert fold_expr(first) == first
    assert truth(first) is None  # not a constant


def test_fold_keeps_what_it_does_not_recognise() -> None:
    kept = Binary(BinaryOp.ADD, V, Const(1, ValueType(4)), ValueType(4))
    assert fold_expr(kept) == kept
    wide = Binary(BinaryOp.LOGICAL_AND, V, ONE, BIT)  # a 4-bit operand is not the bit result
    assert fold_expr(wide) == wide


def test_fold_reaches_inside_sampled_functions_and_keeps_them() -> None:
    sampled = Sampled(SampledFunction.PAST, _and(A, _not(ZERO)), BIT)
    assert fold_expr(sampled) == Sampled(SampledFunction.PAST, A, BIT)
    assert fold_expr(Sampled(SampledFunction.ROSE, _not(ZERO), BIT)) == Sampled(
        SampledFunction.ROSE, ONE, BIT
    )


def test_fold_property_folds_every_expression_position() -> None:
    folded_b = _and(B, _not(ZERO))
    prop = PropAnd(
        (
            Implication(Bool(folded_b), _seq(_and(A, ONE)), True),
            PropIf(_and(A, ONE), Until(_or(A, ZERO), _or(B, ZERO), False, False), None),
            SequenceProperty(
                SeqConcat(Throughout(_and(A, ONE), Bool(B)), Delay(1, 1), Goto(_and(A, ONE), 1, 2))
            ),
            SequenceProperty(NonConsecutive(_and(A, ONE), 1, 1)),
        )
    )
    assert fold_property(prop) == PropAnd(
        (
            Implication(Bool(B), _seq(A), True),
            PropIf(A, Until(A, B, False, False), None),
            SequenceProperty(SeqConcat(Throughout(A, Bool(B)), Delay(1, 1), Goto(A, 1, 2))),
            SequenceProperty(NonConsecutive(A, 1, 1)),
        )
    )


def test_fold_property_folds_composite_operators() -> None:
    inner = _seq(_and(A, ONE))
    prop = PropOr(
        (
            Not(inner),
            Nexttime(1, False, inner),
            Always(0, None, False, inner),
            Eventually(0, 2, False, inner),
            SequenceProperty(
                Within(
                    SeqOr(
                        Bool(_and(A, ONE)),
                        SeqAnd(Bool(B), SeqIntersect(Bool(A), Repeat(Bool(_or(B, ZERO)), 1, 2))),
                    ),
                    Bool(A),
                ),
                Strength.STRONG,
            ),
        )
    )
    plain = _seq(A)
    assert fold_property(prop) == PropOr(
        (
            Not(plain),
            Nexttime(1, False, plain),
            Always(0, None, False, plain),
            Eventually(0, 2, False, plain),
            SequenceProperty(
                Within(
                    SeqOr(Bool(A), SeqAnd(Bool(B), SeqIntersect(Bool(A), Repeat(Bool(B), 1, 2)))),
                    Bool(A),
                ),
                Strength.STRONG,
            ),
        )
    )


# --- never_matches -------------------------------------------------------------------------

_NEVER: list[Sequence] = [
    Bool(ZERO),
    SeqConcat(Bool(A), Delay(1, 1), Bool(ZERO)),
    SeqConcat(Bool(ZERO), Delay(0, 3), Bool(A)),
    SeqConcat(TRUE, Delay(1, 1), Bool(ZERO)),
    Repeat(Bool(ZERO), 1, 3),
    SeqOr(Bool(ZERO), Bool(ZERO)),
    SeqAnd(Bool(A), Bool(ZERO)),
    SeqIntersect(Bool(ZERO), Bool(A)),
    Throughout(ZERO, Bool(A)),
    Throughout(A, Bool(ZERO)),
    Within(Bool(A), Bool(ZERO)),
    Within(Bool(ZERO), Bool(A)),
    Goto(ZERO, 1, 2),
    NonConsecutive(ZERO, 2, None),
]

_MAYBE: list[Sequence] = [
    Bool(A),
    Bool(ONE),
    Repeat(Bool(ZERO), 0, 3),  # the empty repetition matches
    SeqOr(Bool(ZERO), Bool(A)),
    SeqAnd(Bool(A), Bool(B)),
    Goto(ZERO, 0, 2),
    Goto(A, 1, 2),
    Throughout(A, Bool(B)),
    Throughout(ZERO, Repeat(Bool(A), 0, 1)),  # the empty match needs no condition sample
    SeqConcat(Throughout(ZERO, Repeat(Bool(A), 0, 1)), Delay(1, 1), Bool(ONE)),
    SeqConcat(Bool(A), Delay(1, 2), Bool(B)),
]


@pytest.mark.parametrize("sequence", _NEVER)
def test_never_matches(sequence: Sequence) -> None:
    assert never_matches(sequence)


@pytest.mark.parametrize("sequence", _MAYBE)
def test_not_proved_never_to_match(sequence: Sequence) -> None:
    assert not never_matches(sequence)


# --- never_fails ---------------------------------------------------------------------------

_NEVER_FAILS: list[Property] = [
    _seq(ONE),
    SequenceProperty(Bool(ONE), Strength.WEAK),
    Implication(Bool(ZERO), _seq(A), True),
    Implication(Bool(A), _seq(ONE), True),
    Implication(Bool(A), _seq(ONE), False),
    Implication(Bool(A), SequenceProperty(SeqConcat(TRUE, Delay(1, 1), Bool(ONE))), True),
    Implication(Bool(A), SequenceProperty(SeqConcat(Bool(ONE), Delay(1, 3), Bool(ONE))), False),
    Implication(Bool(A), SequenceProperty(Repeat(Bool(ONE), 1, 3)), True),
    Implication(Bool(A), SequenceProperty(SeqOr(Bool(A), Bool(ONE))), True),
    Implication(
        Bool(A), SequenceProperty(SeqAnd(Bool(ONE), SeqConcat(TRUE, Delay(2, 2), Bool(ONE)))), True
    ),
    Implication(Bool(A), Implication(Bool(ZERO), _seq(B), True), True),
    Implication(SeqConcat(Bool(A), Delay(1, 1), Bool(ZERO)), _seq(B), True),
    PropAnd((_seq(ONE), Implication(Bool(ZERO), _seq(A), True))),
    PropOr((_seq(A), _seq(ONE))),
    Not(_seq(ZERO)),
    PropIf(ONE, _seq(ONE), _seq(A)),
    PropIf(ZERO, _seq(A), _seq(ONE)),
    PropIf(ZERO, _seq(A), None),
    PropIf(A, _seq(ONE), _seq(ONE)),
    PropIf(A, _seq(ONE), None),
    Nexttime(1, False, _seq(ONE)),
    Always(0, None, False, _seq(ONE)),
    Always(1, 3, False, Implication(Bool(ZERO), _seq(A), True)),
    Eventually(0, 2, False, _seq(ONE)),
    Until(ONE, A, False, False),
    Until(ONE, A, False, True),
    Until(A, ONE, False, False),
]

_FAILS_OR_UNPROVED: list[Property] = [
    _seq(ZERO),  # a sequence that never matches fails
    _seq(A),
    SequenceProperty(Bool(ONE), Strength.STRONG),
    Implication(Bool(A), _seq(ZERO), True),  # fails whenever a
    Implication(Bool(A), _seq(B), True),
    Implication(Bool(A), SequenceProperty(SeqConcat(TRUE, Delay(1, 1), Bool(A))), True),
    Implication(Bool(A), SequenceProperty(Repeat(Bool(ONE), 0, 2)), True),
    Implication(Bool(A), SequenceProperty(SeqIntersect(Bool(ONE), Bool(ONE))), True),
    PropAnd((_seq(ONE), _seq(A))),
    PropOr((_seq(A), _seq(B))),
    Not(_seq(ONE)),
    Not(_seq(A)),
    Not(SequenceProperty(SeqConcat(Bool(A), Delay(1, 1), Bool(ZERO)))),
    PropIf(A, _seq(ONE), _seq(A)),
    PropIf(A, _seq(A), None),
    Nexttime(1, True, _seq(ONE)),
    Always(0, 3, True, _seq(ONE)),
    Eventually(0, 2, True, _seq(ONE)),
    Eventually(0, None, True, _seq(ONE)),
    Until(ONE, A, True, False),  # strong: needs the right operand
    Until(A, ONE, False, True),  # inclusive: needs the left operand where the right holds
    Until(A, B, False, False),
    Implication(
        SeqConcat(Throughout(ZERO, Repeat(Bool(A), 0, 1)), Delay(1, 1), Bool(ONE)), _seq(B), True
    ),
]


@pytest.mark.parametrize("prop", _NEVER_FAILS)
def test_never_fails(prop: Property) -> None:
    assert never_fails(prop)


@pytest.mark.parametrize("prop", _FAILS_OR_UNPROVED)
def test_not_proved_never_to_fail(prop: Property) -> None:
    assert not never_fails(prop)


# --- never_succeeds ------------------------------------------------------------------------

_NEVER_SUCCEEDS: list[Property] = [
    _seq(ZERO),
    SequenceProperty(SeqConcat(Bool(A), Delay(1, 2), Bool(ZERO)), Strength.STRONG),
    Implication(Bool(ZERO), _seq(A), True),
    Implication(Bool(A), _seq(ZERO), True),
    Implication(Bool(A), _seq(ZERO), False),
    PropAnd((_seq(A), _seq(ZERO))),
    PropOr((_seq(ZERO), _seq(ZERO))),
    PropAnd((Implication(Bool(ZERO), _seq(A), True), Implication(Bool(B), _seq(ZERO), True))),
    PropOr((Implication(Bool(ZERO), _seq(A), True), Implication(Bool(ZERO), _seq(B), True))),
    PropIf(A, Implication(Bool(ZERO), _seq(B), True), None),
    Not(Implication(Bool(ZERO), _seq(A), True)),
    PropIf(ZERO, _seq(A), None),  # vacuous at every attempt
]

_MAY_SUCCEED_OR_UNPROVED: list[Property] = [
    _seq(A),
    _seq(ONE),
    Implication(Bool(A), _seq(ONE), True),  # v |-> !$isunknown(s): succeeds when v does
    Implication(Bool(A), _seq(B), True),
    PropAnd((_seq(A), _seq(B))),
    PropOr((_seq(A), _seq(ZERO))),
    # a vacuously true operand: `and`/`or` are nonvacuous through the other operand
    PropAnd((Implication(Bool(ZERO), _seq(A), True), _seq(B))),
    PropOr((_seq(ZERO), Implication(Bool(ZERO), _seq(A), True))),
    Implication(Bool(A), PropAnd((Implication(Bool(ZERO), _seq(A), True), _seq(B))), True),
    Not(_seq(ZERO)),
    Nexttime(1, False, _seq(ZERO)),
    Always(0, 3, False, _seq(ZERO)),
]


@pytest.mark.parametrize("prop", _NEVER_SUCCEEDS)
def test_never_succeeds(prop: Property) -> None:
    assert never_succeeds(prop)


@pytest.mark.parametrize("prop", _MAY_SUCCEED_OR_UNPROVED)
def test_not_proved_never_to_succeed(prop: Property) -> None:
    assert not never_succeeds(prop)


# --- soundness against the reference evaluator ---------------------------------------------


_ORACLE_A = Signal("m.a", BIT)
_ORACLE_B = Signal("m.b", BIT)
_LETTERS: st.SearchStrategy[Expr] = st.sampled_from([_ORACLE_A, _ORACLE_B, ZERO, ONE, ZERO, ONE])


def _oracle_sequences() -> st.SearchStrategy[Sequence]:
    """Non-nullable sequences (as properties and antecedents require); nullable parts
    (``[*0:n]``, ``throughout`` of one) only on the left of a concatenation."""
    single = _LETTERS.map(lambda letter: Bool(letter))
    delays = st.sampled_from([Delay(1, 1), Delay(0, 1)])
    counts = st.sampled_from([(0, 1), (0, 2), (1, 1), (1, 2)])
    repeated = st.builds(lambda c, s: Repeat(s, c[0], c[1]), counts, single)
    left = single | repeated | st.builds(Throughout, _LETTERS, repeated)
    both = st.builds(SeqConcat, left, delays, single)
    non_empty = st.builds(lambda s, n: Repeat(s, 1, n), single, st.integers(1, 2))
    return st.one_of(
        single,
        both,
        non_empty,
        st.builds(Throughout, _LETTERS, both | non_empty),
        st.builds(SeqOr, single, non_empty),
    )


def _oracle_trees(depth: int) -> st.SearchStrategy[Property]:
    """Property trees over two signals whose letters are constants half the time: every
    property form the criteria inspect, windows of at most one sample."""
    leaf = st.builds(
        SequenceProperty, _oracle_sequences(), st.sampled_from(list(Strength))
    ) | st.builds(Until, _LETTERS, _LETTERS, st.booleans(), st.booleans())
    if depth <= 0:
        return leaf
    inner = _oracle_trees(depth - 1)
    windows = st.sampled_from([(0, 0), (0, 1), (1, 1)])
    return st.one_of(
        leaf,
        inner.map(Not),
        st.builds(lambda p, q: PropAnd((p, q)), inner, inner),
        st.builds(lambda p, q: PropOr((p, q)), inner, inner),
        st.builds(PropIf, _LETTERS, inner, st.none() | inner),
        st.builds(Implication, _oracle_sequences(), inner, st.booleans()),
        st.builds(Nexttime, st.integers(0, 1), st.booleans(), inner),
        st.builds(lambda w, s, p: Always(w[0], w[1], s, p), windows, st.booleans(), inner),
        st.builds(lambda w, s, p: Eventually(w[0], w[1], s, p), windows, st.booleans(), inner),
    )


# every two-signal trace of one to three samples
_ORACLE_TRACES = tuple(
    Trace(tuple({"m.a": (bits >> (2 * i)) & 1, "m.b": (bits >> (2 * i + 1)) & 1} for i in range(n)))
    for n in (1, 2, 3)
    for bits in range(1 << (2 * n))
)


@pytest.mark.extended
@settings(max_examples=1000, deadline=None)
@given(_oracle_trees(2))
def test_criteria_agree_with_the_reference(prop: Property) -> None:
    """Whatever the criteria prove of the folded property, the reference evaluator (on the
    property as written, constants included) confirms on every short trace: no failure,
    online or at the end of the simulation, when it "never fails"; no cover success when
    it "never succeeds"."""
    folded = fold_property(prop)
    fails, succeeds = never_fails(folded), never_succeeds(folded)
    for trace in _ORACLE_TRACES if fails or succeeds else ():
        if fails:
            assert evaluate_attempts(prop, trace).failures == ()
            assert Outcome.FALSE not in finalize_attempts(prop, trace, None, cover=False)
        if succeeds:
            assert evaluate_attempts(prop, trace).successes == ()
