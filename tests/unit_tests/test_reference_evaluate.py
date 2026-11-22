"""Unit contract of support.reference (expression values, traces, match ends, verdicts)."""

from __future__ import annotations

import pytest

from support.reference.evaluate import (
    EvaluationError,
    Verdict,
    evaluate,
    holds,
    match_ends,
    value,
)
from support.reference.trace import Trace, TraceError
from support.reference.values import truth
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
from sva2yosys.ir.property import Implication, SequenceProperty
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Goto, NonConsecutive, Repeat, SeqConcat

U4 = ValueType(4)
S4 = ValueType(4, True)
EMPTY = Trace(({},))


def _c(v: int, t: ValueType = U4) -> Const:
    return Const(v % (1 << t.width), t)


def _v(expr: Expr) -> int:
    return value(expr, EMPTY, 0)


# --- expressions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "left", "right", "vtype", "expected"),
    [
        (BinaryOp.ADD, 9, 9, U4, 2),
        (BinaryOp.SUBTRACT, 1, 2, U4, 15),
        (BinaryOp.MULTIPLY, 5, 5, U4, 9),
        (BinaryOp.DIVIDE, 15, 4, U4, 3),
        (BinaryOp.DIVIDE, -7, 2, S4, (-3) % 16),
        (BinaryOp.MOD, -7, 2, S4, (-1) % 16),
        (BinaryOp.BITWISE_AND, 12, 10, U4, 8),
        (BinaryOp.BITWISE_OR, 12, 10, U4, 14),
        (BinaryOp.BITWISE_XOR, 12, 10, U4, 6),
        (BinaryOp.BITWISE_XNOR, 12, 10, U4, 9),
        (BinaryOp.LOGICAL_SHIFT_LEFT, 3, 2, U4, 12),
        (BinaryOp.LOGICAL_SHIFT_LEFT, 3, 9, U4, 0),
        (BinaryOp.LOGICAL_SHIFT_RIGHT, 12, 2, U4, 3),
        (BinaryOp.ARITHMETIC_SHIFT_RIGHT, -8, 2, S4, (-2) % 16),
        (BinaryOp.ARITHMETIC_SHIFT_RIGHT, 8, 2, U4, 2),
    ],
)
def test_arithmetic(op: BinaryOp, left: int, right: int, vtype: ValueType, expected: int) -> None:
    assert _v(Binary(op, _c(left, vtype), _c(right, vtype), vtype)) == expected


@pytest.mark.parametrize(
    ("op", "left", "right", "vtype", "expected"),
    [
        (BinaryOp.LESS_THAN, -1, 1, S4, 1),
        (BinaryOp.LESS_THAN, 15, 1, U4, 0),
        (BinaryOp.GREATER_THAN_EQUAL, 3, 3, U4, 1),
        (BinaryOp.LESS_THAN_EQUAL, 4, 3, U4, 0),
        (BinaryOp.GREATER_THAN, -2, -3, S4, 1),
        (BinaryOp.EQUALITY, 5, 5, U4, 1),
        (BinaryOp.CASE_INEQUALITY, 5, 5, U4, 0),
        (BinaryOp.LOGICAL_AND, 2, 0, U4, 0),
        (BinaryOp.LOGICAL_OR, 2, 0, U4, 1),
        (BinaryOp.LOGICAL_IMPLICATION, 0, 0, U4, 1),
        (BinaryOp.LOGICAL_EQUIVALENCE, 2, 3, U4, 1),
    ],
)
def test_comparisons_and_logic(
    op: BinaryOp, left: int, right: int, vtype: ValueType, expected: int
) -> None:
    assert _v(Binary(op, _c(left, vtype), _c(right, vtype), BIT)) == expected


def test_division_by_zero_is_an_error() -> None:
    with pytest.raises(EvaluationError):
        _v(Binary(BinaryOp.DIVIDE, _c(1), _c(0), U4))


@pytest.mark.parametrize(
    ("op", "operand", "result_type", "expected"),
    [
        (UnaryOp.PLUS, 5, U4, 5),
        (UnaryOp.MINUS, 1, U4, 15),
        (UnaryOp.BITWISE_NOT, 5, U4, 10),
        (UnaryOp.LOGICAL_NOT, 0, BIT, 1),
        (UnaryOp.REDUCE_AND, 15, BIT, 1),
        (UnaryOp.REDUCE_NAND, 15, BIT, 0),
        (UnaryOp.REDUCE_OR, 0, BIT, 0),
        (UnaryOp.REDUCE_NOR, 0, BIT, 1),
        (UnaryOp.REDUCE_XOR, 7, BIT, 1),
        (UnaryOp.REDUCE_XNOR, 7, BIT, 0),
    ],
)
def test_unary(op: UnaryOp, operand: int, result_type: ValueType, expected: int) -> None:
    assert _v(Unary(op, _c(operand), result_type)) == expected


def test_conversion_extends_by_operand_sign_and_truncates() -> None:
    assert _v(Convert(_c(-2, S4), ValueType(8))) == 0xFE
    assert _v(Convert(_c(14, U4), ValueType(8))) == 0x0E
    assert _v(Convert(_c(0xAB, ValueType(8)), U4)) == 0xB


def test_selects_concat_replicate_conditional() -> None:
    byte = _c(0b1011_0110, ValueType(8))
    assert _v(BitSelect(byte, 2, BIT)) == 1
    assert _v(RangeSelect(byte, 7, 4, U4)) == 0b1011
    assert _v(Concat((_c(1, ValueType(2)), _c(3, ValueType(2))), U4)) == 0b0111
    assert _v(Replicate(3, _c(1, ValueType(2)), ValueType(6))) == 0b010101
    assert _v(Conditional(_c(0, BIT), _c(1), _c(2), U4)) == 2


def test_signals_are_masked_to_their_width() -> None:
    trace = Trace(({"t.v": -1},))
    assert value(Signal("t.v", U4), trace, 0) == 15


# --- sampled-value functions and traces ----------------------------------------------------


def test_sampled_functions_against_previous_sample() -> None:
    a = Signal("t.a", BIT)
    trace = Trace(({"t.a": 0}, {"t.a": 1}, {"t.a": 1}))
    rose = Sampled(SampledFunction.ROSE, a, BIT)
    stable = Sampled(SampledFunction.STABLE, a, BIT)
    past = Sampled(SampledFunction.PAST, a, BIT)
    assert [value(rose, trace, k) for k in range(3)] == [0, 1, 0]
    assert [value(stable, trace, k) for k in range(3)] == [0, 0, 1]
    assert [value(past, trace, k) for k in (1, 2)] == [0, 1]


def test_first_sample_is_one_at_sample_zero_only() -> None:
    trace = Trace(({}, {}, {}))
    first = FirstSample(BIT)
    assert [value(first, trace, k) for k in range(3)] == [1, 0, 0]
    assert [holds(first, trace, k) for k in range(3)] == [True, False, False]
    negated = Unary(UnaryOp.LOGICAL_NOT, first, BIT)
    assert [holds(negated, trace, k) for k in range(3)] == [False, True, True]  # never unknown


def test_past_has_no_value_at_first_sample() -> None:
    a = Signal("t.a", BIT)
    with pytest.raises(TraceError, match="sample 0"):
        value(Sampled(SampledFunction.PAST, a, BIT), Trace(({"t.a": 1},)), 0)


def test_condition_with_past_does_not_hold_at_first_sample() -> None:
    a = Signal("t.a", BIT)
    trace = Trace(({"t.a": 1}, {"t.a": 1}))
    not_past = Unary(UnaryOp.LOGICAL_NOT, Sampled(SampledFunction.PAST, a, BIT), BIT)
    assert [holds(not_past, trace, k) for k in range(2)] == [False, False]
    assert [holds(Sampled(SampledFunction.PAST, a, BIT), trace, k) for k in range(2)] == [
        False,
        True,
    ]
    rose = Sampled(SampledFunction.ROSE, a, BIT)
    assert holds(rose, trace, 0)


# At sample 0, X1 = $past(a) and X4 = $past(v) are X (16.9.3); K1 and K0 are known 1-bit
# operands, V9 a known 4-bit value 4'b1001 (v at sample 0). Expected logical values from
# IEEE 1800 clause 11.4 (None is X).
_A, _VS = Signal("t.a", BIT), Signal("t.v", U4)
X1 = Sampled(SampledFunction.PAST, _A, BIT)
X4 = Sampled(SampledFunction.PAST, _VS, U4)
K1, K0, V9 = _c(1, BIT), _c(0, BIT), _c(9)
S1 = ValueType(1, True)


def _b(op: BinaryOp, left: Expr, right: Expr, vtype: ValueType = BIT) -> Binary:
    return Binary(op, left, right, vtype)


def _u(op: UnaryOp, operand: Expr, vtype: ValueType = BIT) -> Unary:
    return Unary(op, operand, vtype)


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (X1, None),
        (_u(UnaryOp.LOGICAL_NOT, X1), None),
        (_b(BinaryOp.LOGICAL_AND, K0, X1), False),  # CE-04: 0 && X is 0
        (_u(UnaryOp.LOGICAL_NOT, _b(BinaryOp.LOGICAL_AND, K0, X1)), True),
        (_b(BinaryOp.LOGICAL_AND, K1, X1), None),
        (_b(BinaryOp.LOGICAL_OR, K1, X1), True),
        (_b(BinaryOp.LOGICAL_OR, K0, X1), None),
        (_b(BinaryOp.LOGICAL_IMPLICATION, X1, K1), True),
        (_b(BinaryOp.LOGICAL_IMPLICATION, K0, X1), True),
        (_b(BinaryOp.LOGICAL_IMPLICATION, K1, X1), None),
        (_b(BinaryOp.LOGICAL_EQUIVALENCE, K1, X1), None),
        (_b(BinaryOp.BITWISE_AND, K0, X1), False),
        (_b(BinaryOp.BITWISE_OR, K1, X1), True),
        (_b(BinaryOp.BITWISE_XOR, K1, X1), None),
        (_u(UnaryOp.BITWISE_NOT, X1), None),
        (_b(BinaryOp.EQUALITY, X1, X1), None),  # x == x is ambiguous
        (_b(BinaryOp.CASE_EQUALITY, X1, X1), True),  # === compares X as a value
        (_b(BinaryOp.CASE_EQUALITY, X1, K0), False),
        (_b(BinaryOp.CASE_INEQUALITY, X1, K1), True),
        (_b(BinaryOp.LESS_THAN, X4, V9), None),
        (_b(BinaryOp.ADD, X1, K1), None),
        (_b(BinaryOp.ADD, _b(BinaryOp.BITWISE_AND, X4, _c(0), U4), _c(1), U4), True),
        (_u(UnaryOp.MINUS, X1), None),
        # unary + is arithmetic too: 4'b1xxx gives all X (CE-04 review)
        (_u(UnaryOp.PLUS, _b(BinaryOp.BITWISE_OR, X4, _c(8), U4), U4), None),
        # vectors: known when the X bits cannot matter
        (_b(BinaryOp.BITWISE_OR, X4, _c(8), U4), True),  # 4'b1xxx is non-zero
        (_b(BinaryOp.BITWISE_AND, X4, _c(8), U4), None),  # 4'bx000
        (_b(BinaryOp.EQUALITY, _b(BinaryOp.BITWISE_OR, X4, _c(8), U4), _c(1)), False),
        (_b(BinaryOp.INEQUALITY, _b(BinaryOp.BITWISE_OR, X4, _c(8), U4), _c(1)), True),
        (_b(BinaryOp.EQUALITY, _b(BinaryOp.BITWISE_OR, X4, _c(8), U4), _c(8)), None),
        (_u(UnaryOp.REDUCE_AND, _b(BinaryOp.BITWISE_AND, X4, _c(7), U4)), False),
        (_u(UnaryOp.REDUCE_NAND, _b(BinaryOp.BITWISE_AND, X4, _c(7), U4)), True),
        (_u(UnaryOp.REDUCE_AND, _b(BinaryOp.BITWISE_OR, X4, _c(7), U4)), None),
        (_u(UnaryOp.REDUCE_OR, _b(BinaryOp.BITWISE_OR, X4, _c(1), U4)), True),
        (_u(UnaryOp.REDUCE_NOR, _b(BinaryOp.BITWISE_OR, X4, _c(1), U4)), False),
        (_u(UnaryOp.REDUCE_OR, X4), None),
        (_u(UnaryOp.REDUCE_XOR, _b(BinaryOp.BITWISE_OR, X4, _c(1), U4)), None),
        (BitSelect(_b(BinaryOp.BITWISE_OR, X4, _c(8), U4), 3, BIT), True),
        (BitSelect(_b(BinaryOp.BITWISE_OR, X4, _c(8), U4), 2, BIT), None),
        (RangeSelect(Concat((X1, _c(2, ValueType(2))), ValueType(3)), 1, 0, ValueType(2)), True),
        (Replicate(2, X1, ValueType(2)), None),
        # ?: with an X selector merges the operands bit by bit (11.4.11)
        (Conditional(X1, K1, K1, BIT), True),
        (Conditional(X1, K1, K0, BIT), None),
        (Conditional(X1, K0, K0, BIT), False),
        # shifts: known vacated bits, X amount gives X, >>> fills with an X sign
        (_b(BinaryOp.LOGICAL_SHIFT_LEFT, X4, _c(4), U4), False),
        (_b(BinaryOp.LOGICAL_SHIFT_RIGHT, _b(BinaryOp.BITWISE_OR, X4, _c(8), U4), _c(3), U4), True),
        (_b(BinaryOp.LOGICAL_SHIFT_RIGHT, X4, _c(3), U4), None),
        (_b(BinaryOp.LOGICAL_SHIFT_LEFT, _c(1), X4, U4), None),
        (
            BitSelect(
                _b(BinaryOp.ARITHMETIC_SHIFT_RIGHT, Convert(X4, ValueType(4, True)), _c(3), S4),
                0,
                BIT,
            ),
            None,
        ),
        # sign extension copies an X sign bit, zero extension adds known zeros
        (RangeSelect(Convert(Convert(X1, S1), ValueType(4, True)), 3, 1, ValueType(3)), None),
        (RangeSelect(Convert(X1, U4), 3, 1, ValueType(3)), False),
    ],
)
def test_conditions_with_x_follow_ieee_at_the_first_sample(
    condition: Expr, expected: bool | None
) -> None:
    trace = Trace(({"t.a": 1, "t.v": 9}, {"t.a": 0, "t.v": 9}))
    assert truth(condition, trace, 0) is expected
    assert holds(condition, trace, 0) is (expected is True)


def test_a_past_condition_known_at_a_later_sample_is_two_valued() -> None:
    trace = Trace(({"t.a": 1, "t.v": 9}, {"t.a": 0, "t.v": 9}))
    assert truth(_b(BinaryOp.LOGICAL_AND, K1, X1), trace, 1) is True
    assert truth(_b(BinaryOp.EQUALITY, X1, X1), trace, 1) is True


def test_a_depth_two_past_is_x_at_the_first_two_samples_only() -> None:
    deep = Sampled(SampledFunction.PAST, _A, BIT, 2)
    trace = Trace(({"t.a": 1}, {"t.a": 0}, {"t.a": 0}))
    either = _b(BinaryOp.LOGICAL_OR, deep, X1)
    assert [truth(either, trace, k) for k in range(3)] == [None, True, True]


def test_goto_skips_a_definitely_false_past_condition_at_the_first_sample() -> None:
    """CE-04: ``(a && $past(b))[->1]`` from sample 0, where ``a`` is 0: the condition is
    0 && X = 0, so its negation holds and the goto ends at sample 1."""
    a, b = Signal("t.a", BIT), Signal("t.b", BIT)
    condition = _b(BinaryOp.LOGICAL_AND, a, Sampled(SampledFunction.PAST, b, BIT))
    trace = Trace(({"t.a": 0, "t.b": 1}, {"t.a": 1, "t.b": 1}))
    assert match_ends(Goto(condition, 1, 1), trace, 0) == {1}


@pytest.mark.parametrize(
    ("trace", "path", "sample", "fragment"),
    [
        (Trace(({"t.a": 1},)), "t.a", 1, "outside"),
        (Trace(({"t.a": 1},)), "t.b", 0, "no value"),
        (Trace(({"t.a": 1},)), "t.a", -1, "outside"),
    ],
)
def test_trace_errors(trace: Trace, path: str, sample: int, fragment: str) -> None:
    with pytest.raises(TraceError, match=fragment):
        trace.value(path, sample)


# --- match ends -------------------------------------------------------------------------------

A = Bool(Signal("t.a", BIT))
B = Bool(Signal("t.b", BIT))
B_EXPR = Signal("t.b", BIT)


def _ab(a: str, b: str) -> Trace:
    return Trace(tuple({"t.a": int(x), "t.b": int(y)} for x, y in zip(a, b, strict=True)))


def test_match_ends_concatenation_and_fusion() -> None:
    trace = _ab("1100", "0110")
    assert match_ends(SeqConcat(A, Delay(1, 2), B), trace, 0) == {1, 2}
    assert match_ends(SeqConcat(A, Delay(0, 0), B), trace, 1) == {1}
    assert match_ends(SeqConcat(TRUE, Delay(2, 2), B), trace, 0) == {2}


def test_empty_left_operand_of_concatenation() -> None:
    trace = _ab("0000", "1100")
    empty_then_b = SeqConcat(Repeat(A, 0, 0), Delay(1, 1), B)
    assert match_ends(empty_then_b, trace, 0) == {0}
    assert match_ends(SeqConcat(Repeat(A, 0, 0), Delay(0, 0), B), trace, 0) == frozenset()


def test_matches_do_not_extend_beyond_the_trace() -> None:
    assert match_ends(SeqConcat(A, Delay(1, 3), B), _ab("10", "00"), 0) == frozenset()


def test_unbounded_delay_matches_up_to_the_end_of_the_trace() -> None:
    # a ##[1:$] b from 0: a at 0, b at samples 1 and 3
    assert match_ends(SeqConcat(A, Delay(1, None), B), _ab("1000", "0101"), 0) == {1, 3}
    assert match_ends(SeqConcat(A, Delay(2, None), B), _ab("1000", "0101"), 0) == {3}


def test_unbounded_repetition_matches_every_run_length() -> None:
    assert match_ends(Repeat(A, 1, None), _ab("1101", "0000"), 0) == {0, 1}
    assert match_ends(Repeat(A, 2, None), _ab("1101", "0000"), 0) == {1}
    assert match_ends(Repeat(A, 2, None), _ab("1011", "0000"), 0) == frozenset()


def test_goto_ends_at_the_counted_occurrence() -> None:
    trace = _ab("0000", "0101")
    assert match_ends(Goto(B_EXPR, 1, 1), trace, 0) == {1}
    assert match_ends(Goto(B_EXPR, 2, 2), trace, 0) == {3}
    assert match_ends(Goto(B_EXPR, 1, 2), trace, 0) == {1, 3}
    assert match_ends(Goto(B_EXPR, 1, None), trace, 0) == {1, 3}
    assert match_ends(Goto(B_EXPR, 3, None), trace, 0) == frozenset()


def test_nonconsecutive_may_continue_after_the_last_occurrence() -> None:
    trace = _ab("0000", "0101")
    assert match_ends(NonConsecutive(B_EXPR, 1, 1), trace, 0) == {1, 2}
    assert match_ends(NonConsecutive(B_EXPR, 2, 2), trace, 0) == {3}
    assert match_ends(NonConsecutive(B_EXPR, 1, None), trace, 0) == {1, 2, 3}
    assert match_ends(NonConsecutive(B_EXPR, 0, 0), trace, 0) == {0}


# --- attempt verdicts -------------------------------------------------------------------------


def test_vacuous_pending_and_failed_attempts() -> None:
    prop = Implication(SeqConcat(A, Delay(1, 1), B), SequenceProperty(A), overlapping=True)
    result = evaluate(prop, _ab("100", "011"))
    verdicts = [(a.start, a.verdict, a.decided) for a in result.attempts]
    assert verdicts == [
        (0, Verdict.FAIL, 1),
        (1, Verdict.VACUOUS, 1),
        (2, Verdict.VACUOUS, 2),
    ]
    pending = evaluate(prop, _ab("001", "000"))
    assert pending.attempts[2].verdict is Verdict.PENDING


def test_nested_implication_consequent_is_an_error() -> None:
    inner = Implication(A, SequenceProperty(B), overlapping=True)
    with pytest.raises(EvaluationError):
        evaluate(Implication(A, inner, overlapping=True), _ab("1", "1"))


# --- constants are evaluated at their own sample ---------------------------------

ZERO = Bool(Const(0, BIT))


def test_a_constant_false_condition_is_refuted_at_its_own_sample_only() -> None:
    """``a |-> ##1 1'b0``: pending while the sample of the constant is unknown, failed at it."""
    prop = Implication(A, SequenceProperty(SeqConcat(TRUE, Delay(1, 1), ZERO)), overlapping=True)
    one = evaluate(prop, _ab("1", "0"))
    assert one.failures == () and one.attempts[0].verdict is Verdict.PENDING
    two = evaluate(prop, _ab("10", "00"))
    assert two.failures == (1,) and two.attempts[0].verdict is Verdict.FAIL
    assert two.attempts[0].decided == 1


def test_a_constant_false_condition_at_the_trigger_sample_fails_there() -> None:
    prop = Implication(A, SequenceProperty(ZERO), overlapping=True)
    assert evaluate(prop, _ab("1", "0")).failures == (0,)


def test_unknown_samples_satisfy_a_constant_false_condition() -> None:
    """After the known samples every condition is satisfiable, constants included."""
    assert match_ends(ZERO, _ab("1", "0"), 0) == frozenset()
    prop = Implication(A, SequenceProperty(SeqConcat(TRUE, Delay(1, None), ZERO)), overlapping=True)
    result = evaluate(prop, _ab("1000", "0000"))
    assert result.failures == () and result.attempts[0].verdict is Verdict.PENDING
