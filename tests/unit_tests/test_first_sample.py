"""Unit contract of sva2yosys.passes.first_sample (its module docstring is the contract)."""

from __future__ import annotations

import pytest

from sva2yosys.ir.directive import Directive
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
    RangeSelect,
    Replicate,
    Sampled,
    SampledDefault,
    SampledFunction,
    Signal,
    Unary,
    UnaryOp,
    ValueType,
)
from sva2yosys.passes.first_sample import (
    FirstSample,
    classify,
    default_class,
    first_sample_problems,
    gated_samples,
)
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions


def _directive(condition: str, disable: str | None = None, declarations: str = "") -> Directive:
    disable_text = f"disable iff ({disable}) " if disable is not None else ""
    text = (
        "module m(input logic clk, a, b, c, input logic [3:0] v,\n"
        "  input bit k, input bit [3:0] kv);\n"
        f"  {declarations}\n"
        f"  p: assert property (@(posedge clk) {disable_text}{condition});\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("m.sv", text)], SlangOptions(top=("m",))))
    assert conversion.rejections == ()
    return conversion.directives[0]


@pytest.mark.parametrize(
    "condition",
    [
        "$past(a) |-> b",
        "!$past(a) |-> b",
        "a |-> b == $past(b)",
        "$past(v) == v",
        "$past(v) == 3",
        "$past(v) + 1 > v",
        "$past(a) && b",
        "$past(v)[1]",
        "$past(v) ^ v",
        "|$past(v)",
        "~&$past(v)",
        "$rose(a) || b",
        "!$stable(v) |-> ##1 c",
        "$past(v) >> 1 == v",
        "a |-> $past(b)[*2]",
    ],
)
def test_accepted(condition: str) -> None:
    assert first_sample_problems(_directive(condition)) == ()


@pytest.mark.parametrize(
    "condition",
    [
        "$past(a) || b",
        "($past(v) & v) != 0",
        "($past(v) | v) != 0",
        "$past(a) ? b : c",
        "$past(v) != 3",
        "!($past(a) && b)",
        "{$past(a), b} == 2'b01",
        "$past(v) === v",
        "a |-> ($past(a) || b)",
        # counterexamples (design discussion): can be known true at the first sample in IEEE
        "($past(v) & 4'b0) == 0",
        "$past(a) ? b : b",
    ],
)
def test_rejected(condition: str) -> None:
    problems = first_sample_problems(_directive(condition))
    assert len(problems) == 1
    assert "first sample" in problems[0]


def test_past_in_disable_is_rejected() -> None:
    problems = first_sample_problems(_directive("a |-> b", disable="$past(c)"))
    assert problems == ("$past in a disable iff condition is not supported yet",)


def test_edge_functions_in_disable_are_accepted() -> None:
    assert first_sample_problems(_directive("a |-> b", disable="$rose(c)")) == ()


# --- transfer rules on IR built directly ---------------------------------------------------

U4 = ValueType(4)
S4 = ValueType(4, True)
U8 = ValueType(8)
PAST4 = Sampled(SampledFunction.PAST, Signal("m.v", U4), U4)
PAST4S = Sampled(SampledFunction.PAST, Signal("m.s", S4), S4)
CLEAN4 = Signal("m.w", U4)
SOME8 = Convert(PAST4, U8)  # zero-extended: SOME_X
ALL8 = Convert(PAST4S, ValueType(8, True))  # sign-extended: ALL_X
ZERO4 = Binary(BinaryOp.BITWISE_AND, PAST4, CLEAN4, U4)  # ZERO_OR_X
ZERO8 = Binary(BinaryOp.BITWISE_AND, SOME8, Signal("m.y", U8), U8)  # ZERO_OR_X
ONE1 = Binary(BinaryOp.LOGICAL_IMPLICATION, PAST4, CLEAN4, BIT)  # ONE_OR_X
ANY4 = Binary(BinaryOp.BITWISE_OR, PAST4, CLEAN4, U4)  # ANY
COND = Signal("m.c", BIT)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (PAST4, FirstSample.ALL_X),
        (Convert(PAST4, U8), FirstSample.SOME_X),
        (Convert(PAST4S, ValueType(8, True)), FirstSample.ALL_X),
        (Convert(Convert(PAST4, U8), U4), FirstSample.ZERO_OR_X),
        (Binary(BinaryOp.LOGICAL_SHIFT_LEFT, PAST4, Const(1, U4), U4), FirstSample.SOME_X),
        (Binary(BinaryOp.LOGICAL_SHIFT_LEFT, PAST4, Const(4, U4), U4), FirstSample.ZERO_OR_X),
        (Binary(BinaryOp.LOGICAL_SHIFT_RIGHT, PAST4, CLEAN4, U4), FirstSample.ZERO_OR_X),
        (Binary(BinaryOp.ARITHMETIC_SHIFT_RIGHT, PAST4S, Const(1, U4), S4), FirstSample.ALL_X),
        (Binary(BinaryOp.LOGICAL_SHIFT_LEFT, CLEAN4, PAST4, U4), FirstSample.ALL_X),
        (Binary(BinaryOp.BITWISE_AND, PAST4, PAST4, U4), FirstSample.ALL_X),
        (Binary(BinaryOp.BITWISE_OR, PAST4, PAST4, U4), FirstSample.ALL_X),
        (Conditional(Signal("m.c", BIT), PAST4, Convert(PAST4, U4), U4), FirstSample.ALL_X),
        (Conditional(Signal("m.c", BIT), PAST4, CLEAN4, U4), FirstSample.ANY),
        (Replicate(2, PAST4, U8), FirstSample.ALL_X),
        (
            Unary(UnaryOp.REDUCE_NAND, Binary(BinaryOp.BITWISE_AND, PAST4, CLEAN4, U4), BIT),
            FirstSample.ONE_OR_X,
        ),
        (Unary(UnaryOp.MINUS, Convert(PAST4, U8), U8), FirstSample.ALL_X),
        (Unary(UnaryOp.BITWISE_NOT, Convert(PAST4, U8), U8), FirstSample.ANY),
        (Binary(BinaryOp.LOGICAL_EQUIVALENCE, PAST4, CLEAN4, BIT), FirstSample.ALL_X),
        (Binary(BinaryOp.LOGICAL_IMPLICATION, PAST4, CLEAN4, BIT), FirstSample.ONE_OR_X),
        (Binary(BinaryOp.CASE_EQUALITY, PAST4, CLEAN4, BIT), FirstSample.ANY),
        # selects and concatenation of partly-X values
        (BitSelect(SOME8, 1, BIT), FirstSample.ZERO_OR_X),
        (BitSelect(ANY4, 1, BIT), FirstSample.ANY),
        (Concat((CLEAN4, CLEAN4), U8), FirstSample.CLEAN),
        (Concat((PAST4, PAST4), U8), FirstSample.ALL_X),
        (Concat((PAST4, ZERO4), U8), FirstSample.SOME_X),
        (Concat((ZERO4, ZERO4), U8), FirstSample.ZERO_OR_X),
        (Concat((PAST4, CLEAN4), U8), FirstSample.ANY),
        # reductions
        (Unary(UnaryOp.REDUCE_AND, SOME8, BIT), FirstSample.ZERO_OR_X),
        (Unary(UnaryOp.REDUCE_OR, SOME8, BIT), FirstSample.ALL_X),
        (Unary(UnaryOp.REDUCE_OR, ZERO4, BIT), FirstSample.ZERO_OR_X),
        (Unary(UnaryOp.REDUCE_XOR, ANY4, BIT), FirstSample.ANY),
        (Unary(UnaryOp.REDUCE_NOR, SOME8, BIT), FirstSample.ALL_X),
        (Unary(UnaryOp.REDUCE_NAND, SOME8, BIT), FirstSample.ONE_OR_X),
        (Unary(UnaryOp.REDUCE_XNOR, ANY4, BIT), FirstSample.ANY),
        # logical operators on uncertain truth values
        (Binary(BinaryOp.LOGICAL_AND, ONE1, COND, BIT), FirstSample.ANY),
        (Binary(BinaryOp.LOGICAL_OR, ANY4, COND, BIT), FirstSample.ANY),
        (Binary(BinaryOp.LOGICAL_EQUIVALENCE, ZERO4, CLEAN4, BIT), FirstSample.ANY),
        (Unary(UnaryOp.LOGICAL_NOT, ONE1, BIT), FirstSample.ZERO_OR_X),
        # shifts by a tainted amount that may be known
        (Binary(BinaryOp.LOGICAL_SHIFT_LEFT, CLEAN4, ZERO4, U4), FirstSample.ANY),
        # conversion and joins
        (Convert(SOME8, ValueType(16)), FirstSample.SOME_X),
        (Convert(ONE1, U4), FirstSample.ONE_OR_X),
        (Conditional(COND, ALL8, SOME8, U8), FirstSample.SOME_X),
        (Conditional(COND, SOME8, ZERO8, U8), FirstSample.ZERO_OR_X),
        (Conditional(ONE1, CLEAN4, CLEAN4, U4), FirstSample.ANY),
    ],
)
def test_transfer_rules(expr: Expr, expected: FirstSample) -> None:
    assert classify(expr) is expected


def test_a_past_of_depth_n_is_unknown_at_exactly_the_first_n_samples() -> None:
    # origin: mutant past-first-sample-class-off-by-one.
    # $past(v, 2) reads the value two ticks back: unknown at samples 0 and 1 (IEEE 1800
    # 16.9.3), known from sample 2 on, so the condition is gated for exactly 2 samples.
    past2 = Sampled(SampledFunction.PAST, Signal("m.v", U4), U4, 2)
    assert [classify(past2, k) for k in range(3)] == [
        FirstSample.ALL_X,
        FirstSample.ALL_X,
        FirstSample.CLEAN,
    ]
    assert gated_samples(past2) == 2


# --- default sampled values (IEEE 1800 section 16.5.1; docs/semantics.md section 8) -------------

_KNOWN = "logic i = 1'b0;"
_A = Signal("m.a", U4)  # a 4-state signal without initializer: default X
_K = Signal("m.k", U4, SampledDefault.KNOWN)


@pytest.mark.parametrize(
    ("expr", "is_all_x"),
    [
        (_A, True),
        (Binary(BinaryOp.ADD, _A, Const(1, U4), U4), True),
        (RangeSelect(_A, 3, 0, U4), True),
        (Concat((Const(0, ValueType(2)), _A), ValueType(6)), False),
        (Binary(BinaryOp.BITWISE_AND, _A, Const(1, U4), U4), False),
        (_K, False),
        (Const(3, U4), False),
        (Sampled(SampledFunction.PAST, _A, U4), False),
    ],
)
def test_default_class(expr: Expr, is_all_x: bool) -> None:
    assert (default_class(expr) is FirstSample.ALL_X) is is_all_x


@pytest.mark.parametrize(
    "condition",
    ["$stable(k)", "$past(k) == 1'b0", "$fell(k)", "$past({1'b1, a})", "$past(i) == 1'b0"],
)
def test_known_default_is_rejected(condition: str) -> None:
    problems = first_sample_problems(_directive(condition, declarations=_KNOWN))
    assert len(problems) == 1
    assert "before the first clock tick is not unknown" in problems[0]
    assert "§16.5.1" in problems[0]


def test_known_default_in_disable_is_rejected() -> None:
    problems = first_sample_problems(_directive("a |-> b", "$stable(kv & 4'b0011)", _KNOWN))
    assert len(problems) == 1
    assert "the operand contains a constant" not in problems[0]
    assert "'m.kv' is initialised or of a 2-state type" in problems[0]


@pytest.mark.parametrize("condition", ["$rose(a)", "$past(a + 1)", "$past(a) == 1'b0 || b"])
def test_unknown_default_is_accepted(condition: str) -> None:
    problems = first_sample_problems(_directive(condition, declarations=_KNOWN))
    assert not any("not unknown" in p for p in problems)


def test_the_constant_cause_is_named() -> None:
    problems = first_sample_problems(_directive("$past({1'b1, a})"))
    assert len(problems) == 1
    assert "the operand contains a constant" in problems[0]
