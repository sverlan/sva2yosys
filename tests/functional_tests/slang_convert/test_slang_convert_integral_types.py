"""Functional contract of packed integral operand types (CONTRACT.md, point 14).

Every packed integral type is accepted as a whole value, with the width and signedness the
exporter reports (``sva2yosys_integral``, IEEE 1800-2017 section 6.11: an integral value is
its bits). What is not a whole packed value stays rejected with a located message. Origin of
every case: corpus rejections of enums, typedefs and packed arrays.
"""

from __future__ import annotations

import pytest

from sva2yosys.ir.expr import (
    Binary,
    BinaryOp,
    BitSelect,
    Concat,
    Const,
    Convert,
    Expr,
    RangeSelect,
    Replicate,
    SampledDefault,
    Signal,
    ValueType,
)
from sva2yosys.ir.property import SequenceProperty
from sva2yosys.ir.sequence import Bool
from sva2yosys.passes.validate import validation_problems
from sva2yosys.slang.convert import Conversion, convert
from sva2yosys.slang.export import JsonObject, export_texts
from sva2yosys.slang.invoke import SlangOptions


def _document(body: str) -> JsonObject:
    text = f"module r(input logic clk, rst);\n  {body}\nendmodule\n"
    return export_texts([("r.sv", text)], SlangOptions(top=("r",)))


def _convert(body: str) -> Conversion:
    return convert(_document(body))


_ENUM = "typedef enum logic [1:0] {IDLE, BUSY, DONE} st_t; st_t state;"
_STRUCT = "typedef struct packed {logic [3:0] a; logic b;} s_t; s_t s, s_q;"

# Each case is accepted and validates; the comment gives the hand-derived reason.
MUST_ACCEPT = [
    # An enum member is a constant of the enum's width (2 bits here): `state == IDLE` compares
    # two 2-bit values (11.4.5). The member is a folded constant 2'd0.
    f"{_ENUM} assert property (@(posedge clk) state == IDLE);",
    # A logic-based enum is 4-state and uninitialised: its default sampled value is X (16.5.1),
    # so $past on it is accepted.
    f"{_ENUM} assert property (@(posedge clk) state == BUSY |=> $past(state) == BUSY);",
    # A typedef of a vector is that vector: 8-bit unsigned addition compared with 8 bits.
    "typedef logic [7:0] byte_t; byte_t a, b; assert property (@(posedge clk) (a + b) == 8'd5);",
    # `int unsigned` is a 32-bit unsigned 2-state value.
    "int unsigned n; assert property (@(posedge clk) n == 5);",
    # A whole packed struct is a flat 5-bit vector (4 + 1); equality compares all bits.
    f"{_STRUCT} assert property (@(posedge clk) s == s_q);",
    # A whole multi-dimensional packed array is a flat 32-bit vector.
    "logic [3:0][7:0] m, m_q; assert property (@(posedge clk) m == m_q);",
    # An enum with the default int base: 32-bit signed, compared with its member.
    "typedef enum {A, B, C} e_t; e_t e; assert property (@(posedge clk) e != C);",
    # A bit select of a typedef'd one-dimensional vector is a select of a vector.
    "typedef logic [7:0] byte_t; byte_t a; assert property (@(posedge clk) a[3] |-> a[7:4] == 0);",
]

# (source, message fragment)
MUST_REJECT = [
    # `m[1]` is an 8-bit element of a two-dimensional packed array, not a bit.
    (
        "logic [3:0][7:0] m; assert property (@(posedge clk) m[1] == 8'd0);",
        "selects into multi-dimensional packed arrays or packed structs are not supported yet",
    ),
    (
        "logic [3:0][7:0] m; assert property (@(posedge clk) m[1:0] == 16'd0);",
        "selects into multi-dimensional packed arrays or packed structs are not supported yet",
    ),
    # A select into a packed struct: bits of a struct, with member boundaries; deferred.
    (
        f"{_STRUCT} assert property (@(posedge clk) s[0]);",
        "selects into multi-dimensional packed arrays or packed structs are not supported yet",
    ),
    (
        f"{_STRUCT} assert property (@(posedge clk) s[4:2] == 3'd0);",
        "selects into multi-dimensional packed arrays or packed structs are not supported yet",
    ),
    # Member access stays unsupported (not supported yet).
    (f"{_STRUCT} assert property (@(posedge clk) s.b);", "unsupported construct"),
    # An unpacked array is not a packed integral value.
    (
        "logic [7:0] u [4]; assert property (@(posedge clk) u[0] == 8'd0);",
        "unsupported type 'logic[7:0]$[0:3]' (only packed integral types)",
    ),
    # `real` is not integral.
    (
        "real x; assert property (@(posedge clk) x > 0.5);",
        "(only packed integral types)",
    ),
]


@pytest.mark.parametrize("body", MUST_ACCEPT)
def test_a_packed_integral_value_is_accepted(body: str) -> None:
    conversion = _convert(body)
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert validation_problems(directive) == ()


@pytest.mark.parametrize(("body", "fragment"), MUST_REJECT)
def test_what_is_not_a_whole_packed_value_is_rejected(body: str, fragment: str) -> None:
    conversion = _convert(body)
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert fragment in rejection.message
    assert rejection.source is not None


def test_enum_and_struct_widths_and_signedness_follow_the_exporter() -> None:
    """A default-int enum compared with its member is a 32-bit signed comparison; a struct is
    5 unsigned bits; a ``logic [1:0]`` enum is 2 unsigned bits. By hand: IEEE 1800-2017
    6.19.3 (the base type of ``enum {A, B}`` is ``int``), 7.2.1 (packed struct bits)."""
    (directive,) = _convert(
        "typedef enum {A, B, C} e_t; e_t e; assert property (@(posedge clk) e == B);"
    ).directives
    assert isinstance(directive.property, SequenceProperty)
    assert isinstance(directive.property.sequence, Bool)
    expr = directive.property.sequence.expr
    assert isinstance(expr, Binary)
    assert expr.op is BinaryOp.EQUALITY
    assert isinstance(expr.left, Signal)
    assert expr.left.type == ValueType(32, True)
    assert expr.right == Const(1, ValueType(32, True))

    (directive,) = _convert(f"{_STRUCT} assert property (@(posedge clk) s == s_q);").directives
    assert isinstance(directive.property, SequenceProperty)
    assert isinstance(directive.property.sequence, Bool)
    expr = directive.property.sequence.expr
    assert isinstance(expr, Binary)
    assert isinstance(expr.left, Signal)
    assert expr.left.type == ValueType(5, False)

    (directive,) = _convert(f"{_ENUM} assert property (@(posedge clk) state == DONE);").directives
    assert isinstance(directive.property, SequenceProperty)
    assert isinstance(directive.property.sequence, Bool)
    expr = directive.property.sequence.expr
    assert isinstance(expr, Binary)
    assert isinstance(expr.left, Signal)
    assert expr.left.type == ValueType(2, False)
    assert expr.right == Const(2, ValueType(2, False))


def test_past_of_a_two_state_enum_is_rejected_by_the_default_sampled_value_rule() -> None:
    """`enum {A, B}` has the 2-state base type `int`: its default sampled value is 0, not X
    (16.5.1), so `$past(e)` is rejected like `$past` of an `int` (docs/semantics.md section 8)."""
    conversion = _convert(
        "typedef enum {A, B} e_t; e_t e; assert property (@(posedge clk) $past(e) == A);"
    )
    (directive,) = conversion.directives
    (problem,) = validation_problems(directive)
    assert "before the first clock tick" in problem


def test_a_missing_integral_description_is_an_error_not_a_guess() -> None:
    """A node without `sva2yosys_integral` whose type is integral is an internal export error
    (never parsed from the type string)."""
    document = _document("logic a; assert property (@(posedge clk) a);")

    def strip(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                strip(item)
        elif isinstance(node, dict):
            if node.get("kind") == "NamedValue":
                node.pop("sva2yosys_integral", None)
            for value in node.values():
                strip(value)

    strip(document)
    conversion = convert(document)
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert "sva2yosys_integral" in rejection.message


def test_a_non_integral_description_is_an_unsupported_type_not_an_export_error() -> None:
    """`false` (exporter: not integral) is the located "unsupported type" rejection."""
    conversion = _convert("real x; assert property (@(posedge clk) x > 0.5);")
    (rejection,) = conversion.rejections
    assert "unsupported type 'real' (only packed integral types)" in rejection.message


# --- review: width, signedness and select bounds per type family ----------------------


def _u(width: int) -> ValueType:
    return ValueType(width, False)


def _s(width: int) -> ValueType:
    return ValueType(width, True)


def _expr(body: str) -> Expr:
    (directive,) = _convert(body).directives
    assert isinstance(directive.property, SequenceProperty)
    assert isinstance(directive.property.sequence, Bool)
    return directive.property.sequence.expr


def _operand_types(body: str) -> tuple[ValueType, ValueType]:
    """Types of the two operands of the top comparison, which has no conversions when both
    sides have the same type."""
    expr = _expr(body)
    assert isinstance(expr, Binary)
    left, right = expr.left, expr.right
    assert isinstance(left, (Signal, Const, Concat, Replicate))
    assert isinstance(right, (Signal, Const, Concat, Replicate))
    return left.type, right.type


# (declarations, left, right, expected type of both). Each type is the canonical type's,
# IEEE 1800-2017 6.11.3 (signing), 6.19.3 (enum base type), 7.2.1 (packed struct: unsigned
# unless declared signed), 7.3.1 (packed union), 7.4.1 (packed array width = product).
FAMILIES = [
    # enum with a signed 2-bit base type: signed, 2 bits.
    ("typedef enum logic signed [1:0] {P, Q} t; t a, b;", "a", "b", _s(2)),
    # enum without a base type: int, signed 32 bits.
    ("typedef enum {P, Q} t; t a, b;", "a", "b", _s(32)),
    # int unsigned: unsigned 32 bits.
    ("int unsigned a, b;", "a", "b", _u(32)),
    # typedef of a signed vector: signed 8 bits.
    ("typedef logic signed [7:0] sb_t; sb_t a, b;", "a", "b", _s(8)),
    # packed struct: unsigned by default (4 + 1 bits), signed when declared so.
    ("typedef struct packed {logic [3:0] f; logic g;} t; t a, b;", "a", "b", _u(5)),
    ("typedef struct packed signed {logic [3:0] f; logic g;} t; t a, b;", "a", "b", _s(5)),
    # packed union: unsigned, the width of its members (4).
    ("typedef union packed {logic [3:0] f; bit [3:0] g;} t; t a, b;", "a", "b", _u(4)),
    # logic [3:0][7:0]: unsigned 4 * 8 = 32 bits.
    ("logic [3:0][7:0] a, b;", "a", "b", _u(32)),
    # concatenation of two 5-bit structs: 10 bits unsigned; replication {2{s}}: 10 bits.
    (
        "typedef struct packed {logic [3:0] f; logic g;} t; t a, b;",
        "{a, b}",
        "10'd0",
        _u(10),
    ),
    (
        "typedef struct packed {logic [3:0] f; logic g;} t; t a;",
        "{2{a}}",
        "10'd0",
        _u(10),
    ),
    # a cast to the enum type of an enum-typed constant keeps the enum's type.
    ("typedef enum logic [1:0] {P, Q} t; t a;", "a", "t'(2'd1)", _u(2)),
]


@pytest.mark.parametrize(("declarations", "left", "right", "expected"), FAMILIES)
def test_value_types_of_each_family(
    declarations: str, left: str, right: str, expected: ValueType
) -> None:
    """Origin: a review."""
    types = _operand_types(f"{declarations} assert property (@(posedge clk) {left} == {right});")
    assert types == (expected, expected)


def test_a_signed_enum_compares_signed_and_an_unsigned_enum_unsigned() -> None:
    """`e < 0`, 11.8.1: with only signed operands the comparison is signed (32 bits: the
    `int` enum and the literal); with an unsigned operand it is unsigned, and the operands
    are extended to the common width 32 as unsigned (a 2-bit unsigned enum is zero-extended).
    Origin: a review."""
    signed = _expr("typedef enum {P, Q} t; t e; assert property (@(posedge clk) e < 0);")
    assert isinstance(signed, Binary)
    assert signed.left == Convert(Signal("r.e", _s(32), SampledDefault.KNOWN), _s(32))
    assert signed.right == Const(0, _s(32))
    unsigned = _expr(
        "typedef enum logic [1:0] {P, Q} t; t e; assert property (@(posedge clk) e < 0);"
    )
    assert isinstance(unsigned, Binary)
    assert unsigned.left == Convert(Signal("r.e", _u(2), SampledDefault.X), _u(32))
    assert unsigned.right == Const(0, _u(32))


def test_casts_to_and_from_an_enum() -> None:
    """`int'(e)` of a 2-bit unsigned enum is its zero-extension as a signed 32-bit value
    (6.24.1); `t'(2'd1)` is the constant 1 of the enum's 2 unsigned bits. Origin: a review."""
    to_int = _expr(
        "typedef enum logic [1:0] {P, Q} t; t e; assert property (@(posedge clk) int'(e) == 1);"
    )
    assert isinstance(to_int, Binary)
    assert to_int.left == Convert(Signal("r.e", _u(2), SampledDefault.X), _s(32))
    assert to_int.right == Const(1, _s(32))
    to_enum = _expr(
        "typedef enum logic [1:0] {P, Q} t; t e; assert property (@(posedge clk) e == t'(2'd1));"
    )
    assert isinstance(to_enum, Binary)
    assert to_enum.right == Const(1, _u(2))


def test_select_bounds_of_a_typedef_and_of_a_reversed_range() -> None:
    """`byte_t a` is `[7:0]`: `a[3]` is bit 3, `a[7:4]` bits 7..4. `logic [0:3] rev` has its
    most significant bit at index 0: `rev[0]` is bit 3, `rev[1:2]` is bits 2..1 (the bounds
    are listed from the most significant side). Origin: a review."""
    expr = _expr(
        "typedef logic [7:0] byte_t; byte_t a;"
        " assert property (@(posedge clk) a[3] && a[7:4] == 4'd0);"
    )
    assert isinstance(expr, Binary)
    assert isinstance(expr.left, BitSelect)
    assert expr.left.index == 3
    assert isinstance(expr.right, Binary)
    assert isinstance(expr.right.left, RangeSelect)
    assert (expr.right.left.msb, expr.right.left.lsb) == (7, 4)
    expr = _expr("logic [0:3] rev; assert property (@(posedge clk) rev[0] && rev[1:2] == 2'd0);")
    assert isinstance(expr, Binary)
    assert isinstance(expr.left, BitSelect)
    assert expr.left.index == 3
    assert isinstance(expr.right, Binary)
    assert isinstance(expr.right.left, RangeSelect)
    assert (expr.right.left.msb, expr.right.left.lsb) == (2, 1)
