"""Functional contract of the set membership operator ``inside`` (CONTRACT.md, point 16).

The converter's lowering is evaluated with the reference evaluator's expression semantics on
every value of the operands (3 and 4 bits) and compared with the independent oracle
``support.inside_reference``, which implements IEEE 1800-2017 11.4.13 directly. Origin of every
case: a review.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from support.inside_reference import FillItem, Item, Operand, RangeItem, ValueItem, inside
from support.reference.trace import Trace
from support.reference.values import value
from sva2yosys.ir.expr import Expr
from sva2yosys.ir.property import SequenceProperty
from sva2yosys.ir.sequence import Bool
from sva2yosys.passes.validate import validation_problems
from sva2yosys.slang.convert import Conversion, convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

# name -> (width, signed): the inputs of the module under test
SIGNALS = {
    "v3": (3, False),
    "v4": (4, False),
    "s3": (3, True),
    "s4": (4, True),
    "w3": (3, False),
    "t3": (3, True),
}
_DECLARATIONS = ", ".join(
    f"input logic {'signed ' if signed else ''}[{width - 1}:0] {name}"
    for name, (width, signed) in SIGNALS.items()
)

Env = dict[str, int]
Spec = Callable[[Env], list[Item]]


def _convert(expression: str, extra: str = "") -> Conversion:
    text = (
        f"module tc(input logic clk, {_DECLARATIONS}{extra});\n"
        f"  p: assert property (@(posedge clk) {expression});\n"
        "endmodule\n"
    )
    return convert(export_texts([("t.sv", text)], SlangOptions(top=("tc",))))


def _lowered(expression: str) -> Expr:
    conversion = _convert(expression)
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert validation_problems(directive) == ()
    prop = directive.property
    assert isinstance(prop, SequenceProperty)
    assert isinstance(prop.sequence, Bool)
    return prop.sequence.expr


def var(name: str) -> Callable[[Env], Operand]:
    width, signed = SIGNALS[name]
    return lambda env: Operand(env[name], width, signed)


def lit(pattern: int, width: int, signed: bool = False) -> Callable[[Env], Operand]:
    return lambda env: Operand(pattern % (1 << width), width, signed)


def integer(number: int) -> Callable[[Env], Operand]:
    """An unsized decimal literal: a 32-bit signed ``int``."""
    return lit(number, 32, signed=True)


def values(*operands: Callable[[Env], Operand]) -> Spec:
    return lambda env: [ValueItem(f(env)) for f in operands]


@dataclass(frozen=True)
class Case:
    name: str
    left: str
    items: str
    spec: Spec
    left_value: Callable[[Env], Operand] | None = None
    """The operand the standard compares; the signal ``left`` by default."""


Bound = Callable[[Env], Operand] | None
"""A range bound; ``None`` is ``$``."""


def _range(low: Bound, high: Bound) -> Callable[[Env], Item]:
    return lambda env: RangeItem(
        None if low is None else low(env), None if high is None else high(env)
    )


def _mixed(*parts: Callable[[Env], Item]) -> Spec:
    return lambda env: [part(env) for part in parts]


def _wild(pattern: int, wildcard: int, width: int, signed: bool = False) -> Callable[[Env], Item]:
    return lambda env: ValueItem(Operand(pattern, width, signed), wildcard)


def _plain(f: Callable[[Env], Operand]) -> Callable[[Env], Item]:
    return lambda env: ValueItem(f(env))


# Only uniform lists are accepted (CONTRACT.md, point 16): the left operand and every item and
# bound have one width and signedness. The non-uniform spellings of these cases are rejected in
# NON_UNIFORM below.
CASES = [
    Case("values", "v4", "{4'd1, 4'd5, 4'd9}", values(lit(1, 4), lit(5, 4), lit(9, 4))),
    Case("range", "v4", "{[4'd3:4'd6]}", _mixed(_range(lit(3, 4), lit(6, 4)))),
    Case("wildcard_binary", "v4", "{4'b1?0?}", _mixed(_wild(0b1000, 0b0101, 4))),
    Case("wildcard_x_and_z_spelling", "v4", "{4'b1x0z}", _mixed(_wild(0b1000, 0b0101, 4))),
    Case(
        "wildcards_ranges_values_mixed",
        "v4",
        "{4'b1?0?, [4'd0:4'd1], 4'd15}",
        _mixed(_wild(0b1000, 0b0101, 4), _range(lit(0, 4), lit(1, 4)), _plain(lit(15, 4))),
    ),
    Case("all_wildcard_item", "v4", "{4'b????}", _mixed(_wild(0, 0b1111, 4))),
    # the 4-bit operand is cast to the 8 bits of the item, so the list is uniform
    Case(
        "wildcard_hex_as_wide_as_the_cast_operand",
        "8'(v4)",
        "{8'h?A}",
        _mixed(_wild(0x0A, 0xF0, 8)),
        left_value=lambda env: Operand(env["v4"], 8, False),
    ),
    Case("wildcard_as_wide_as_operand", "v4", "{4'b01?0}", _mixed(_wild(0b0100, 0b0010, 4))),
    Case(
        "reversed_range_matches_nothing",
        "v3",
        "{[3'd5:3'd2]}",
        _mixed(_range(lit(5, 3), lit(2, 3))),
    ),
    Case("dollar_low_unsigned", "v4", "{[$:4'd5]}", _mixed(_range(None, lit(5, 4)))),
    Case("dollar_high_unsigned", "v4", "{[4'd9:$]}", _mixed(_range(lit(9, 4), None))),
    Case("dollar_low_signed", "s4", "{[$:-4'sd3]}", _mixed(_range(None, lit(-3, 4, True)))),
    Case("dollar_high_signed", "s4", "{[4'sd2:$]}", _mixed(_range(lit(2, 4, True), None))),
    Case("dollar_with_signal_bound", "v3", "{[w3:$]}", _mixed(_range(var("w3"), None))),
    Case(
        "dollar_on_both_sides",
        "v3",
        "{[$:3'd2], [3'd6:$]}",
        _mixed(_range(None, lit(2, 3)), _range(lit(6, 3), None)),
    ),
    Case(
        "dollar_next_to_values",
        "s3",
        "{[$:-3'sd2], 3'sd2, [3'sd3:$]}",
        _mixed(
            _range(None, lit(-2, 3, True)), _plain(lit(2, 3, True)), _range(lit(3, 3, True), None)
        ),
    ),
    # an unbased unsized literal is one bit: it is uniform only next to a one-bit operand
    Case(
        "fill_against_a_one_bit_operand",
        "v4[0]",
        "{'1}",
        _mixed(lambda env: FillItem("1")),
        left_value=lambda env: Operand(env["v4"] & 1, 1, False),
    ),
    # '1 + 4'd1 is evaluated in the 4 bits of its comparison: 1111 + 0001 = 0000
    Case("fill_in_arithmetic", "v4", "{'1 + 4'd1}", values(lit(0, 4))),
    Case("point_range", "v3", "{[3'd4:3'd4]}", _mixed(_range(lit(4, 3), lit(4, 3)))),
    Case(
        "signed_negative_range",
        "s4",
        "{[-4'sd2:4'sd1]}",
        _mixed(_range(lit(-2, 4, True), lit(1, 4, True))),
    ),
    Case(
        "signed_values_and_range",
        "s4",
        "{-4'sd1, [4'sb1000:-4'sd6]}",
        _mixed(_plain(lit(-1, 4, True)), _range(lit(-8, 4, True), lit(-6, 4, True))),
    ),
    Case(
        "signed_sized_literals", "s3", "{3'sb111, 3'sd2}", values(lit(7, 3, True), lit(2, 3, True))
    ),
    Case("signed_wildcard", "s3", "{3'sb1?0}", _mixed(_wild(0b100, 0b010, 3, signed=True))),
    Case(
        "signed_wildcard_msb_is_wildcard",
        "s3",
        "{3'sb?01}",
        _mixed(_wild(0b001, 0b100, 3, signed=True)),
    ),
    Case("unsigned_signal_items", "v3", "{w3, 3'd2}", values(var("w3"), lit(2, 3))),
    Case(
        "unsigned_signal_bounds",
        "v3",
        "{[w3:v3]}",
        _mixed(_range(var("w3"), var("v3"))),
    ),
    Case(
        "signed_signal_items",
        "s3",
        "{t3, [t3:3'sd2]}",
        _mixed(_plain(var("t3")), _range(var("t3"), lit(2, 3, True))),
    ),
    Case("signed_signal_bounds", "s3", "{[s3:t3]}", _mixed(_range(var("s3"), var("t3")))),
    Case(
        "left_expression_widens_with_its_operands",
        "(v3 + 4'd1)",
        "{4'd2, 4'd3}",
        values(lit(2, 4), lit(3, 4)),
        left_value=lambda env: Operand(env["v3"] + 1, 4, False),
    ),
    Case(
        "left_expression_wraps_in_its_own_context",
        "(v3 + 3'd1)",
        "{3'd0, 3'd2}",
        values(lit(0, 3), lit(2, 3)),
        left_value=lambda env: Operand((env["v3"] + 1) % 8, 3, False),
    ),
]


def _used(case: Case) -> list[str]:
    text = f"{case.left} {case.items}"
    return [name for name in SIGNALS if name in text]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_lowering_equals_the_standard_on_every_value(case: Case) -> None:
    expr = _lowered(f"{case.left} inside {case.items}")
    names = _used(case)
    checked = 0
    for combination in itertools.product(*(range(1 << SIGNALS[n][0]) for n in names)):
        env = dict(zip(names, combination, strict=True))
        trace = Trace(({f"tc.{n}": env[n] for n in names},))
        left = case.left_value(env) if case.left_value is not None else var(case.left)(env)
        expected = inside(left, case.spec(env))
        assert value(expr, trace, 0) == int(expected), (case.name, env)
        checked += 1
    assert checked >= 8


def test_the_lowering_covers_all_values_of_three_and_four_bit_operands() -> None:
    assert {SIGNALS[c.left][0] for c in CASES if c.left in SIGNALS} == {3, 4}


def test_no_item_is_false_and_an_empty_range_never_matches() -> None:
    expr = _lowered("v4 inside {[4'd9:4'd3]}")
    assert all(value(expr, Trace(({"tc.v4": v},)), 0) == 0 for v in range(16))


def _rejection(expression: str, extra: str = "") -> str:
    conversion = _convert(expression, extra)
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert rejection.source is not None
    return rejection.message


_UNIFORM = "one width and signedness"
"""The stable fragment of the rejection of a list that is not uniform (point 16)."""

# (id, expression): each kind of non-uniformity is rejected, whatever Slang's common type is.
NON_UNIFORM = [
    ("int_literal_against_4_bits", "v4 inside {1}"),
    ("sized_items_of_two_widths", "v4 inside {4'd1, 8'd2}"),
    ("signed_left_unsigned_item", "s3 inside {w3}"),
    ("unsigned_left_signed_item", "v3 inside {t3}"),
    ("unsigned_left_signed_literals", "v4 inside {-1, 3'sb111}"),
    ("signed_items_with_an_unsigned_one", "s3 inside {t3, w3}"),
    ("unsigned_left_signed_bounds", "v3 inside {[s3:t3]}"),
    ("fill_against_a_wider_operand", "v4 inside {'1}"),
    ("fill_next_to_a_value", "v4 inside {'0, 4'd5}"),
    ("fill_next_to_a_wider_item", "v4 inside {'1, 8'h00}"),
    ("fill_in_arithmetic_next_to_a_wider_item", "v4 inside {'1 + 4'd0, 8'h00}"),
    ("fill_against_a_signed_operand", "s4 inside {'1}"),
    ("fill_next_to_a_signal", "v4 inside {'1, w3}"),
    ("range_bound_of_another_width", "v4 inside {[4'd1:8'd9]}"),
    ("range_bound_int", "v4 inside {[3:4'd6]}"),
    ("range_bounds_of_another_signedness", "s3 inside {[3'sd1:w3]}"),
    ("dollar_next_to_a_bound_of_another_width", "v3 inside {[$:4'd12]}"),
    ("dollar_next_to_an_item_of_another_signedness", "s3 inside {[$:3'sd1], w3}"),
    ("wildcard_narrower_than_the_operand", "v4 inside {3'b1?0}"),
    ("signed_wildcard_narrower_than_the_operand", "s4 inside {3'sb?01}"),
    ("signal_item_of_another_width", "v4 inside {w3}"),
    ("signed_item_wider_than_the_operand", "s3 inside {t3, 4'sd5}"),
    ("arithmetic_left_int_items", "(v3 + 3'd1) inside {2, 3}"),
    ("arithmetic_operand_in_a_wider_comparison", "(v3 + 3'd1) inside {3'd2, 8'd7}"),
]


@pytest.mark.parametrize(
    "expression", [e for _, e in NON_UNIFORM], ids=[name for name, _ in NON_UNIFORM]
)
def test_a_list_that_is_not_uniform_is_rejected(expression: str) -> None:
    assert _UNIFORM in _rejection(expression)


def test_dollar_bound_of_an_arithmetic_left_operand_is_rejected() -> None:
    assert "arithmetic" in _rejection("(v3 + 3'd1) inside {[$:3'd2]}")


def test_unpacked_array_item_is_rejected() -> None:
    message = _rejection("v4 inside {arr}", ", input logic [3:0] arr [2]")
    assert "array" in message


# Uniform lists that mirror real designs: an enum against members of its own type, an 8-bit
# signal against 8-bit literals, an `int` against plain integers.

_ENUM_MODULE = """
typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
module tc(input logic clk, input state_t st);
  p: assert property (@(posedge clk) st inside {IDLE, DONE});
endmodule
"""

_BYTE_MODULE = """
module tc(input logic clk, input logic [7:0] b);
  p: assert property (@(posedge clk) b inside {8'h10, 8'hA5, [8'h20:8'h2F]});
endmodule
"""

_INT_MODULE = """
module tc(input logic clk, input int n);
  p: assert property (@(posedge clk) n inside {1, 2, [4:6]});
endmodule
"""

_PARAMETER_MODULE = """
module tc #(parameter int P = 3) (input logic clk);
  p: assert property (@(posedge clk) P inside {1, 2, 3});
endmodule
"""


def _lowered_module(text: str) -> Expr:
    conversion = convert(export_texts([("t.sv", text)], SlangOptions(top=("tc",))))
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert validation_problems(directive) == ()
    prop = directive.property
    assert isinstance(prop, SequenceProperty)
    assert isinstance(prop.sequence, Bool)
    return prop.sequence.expr


def _holds(expr: Expr, signal: str, number: int) -> int:
    return value(expr, Trace(({signal: number},)), 0)


def test_enum_against_members_of_its_own_type_is_accepted() -> None:
    expr = _lowered_module(_ENUM_MODULE)
    assert [_holds(expr, "tc.st", n) for n in range(4)] == [1, 0, 1, 0]


def test_byte_against_sized_literals_is_accepted() -> None:
    expr = _lowered_module(_BYTE_MODULE)
    members = {0x10, 0xA5, *range(0x20, 0x30)}
    assert all(_holds(expr, "tc.b", n) == int(n in members) for n in range(256))


def test_int_against_plain_integers_is_accepted() -> None:
    expr = _lowered_module(_INT_MODULE)
    members = {1, 2, 4, 5, 6}
    assert all(_holds(expr, "tc.n", n) == int(n in members) for n in range(10))
    assert _holds(expr, "tc.n", 0xFFFFFFFF) == 0  # -1


def test_int_parameter_against_plain_integers_is_accepted() -> None:
    expr = _lowered_module(_PARAMETER_MODULE)
    assert value(expr, Trace(({},)), 0) == 1


# Origin of the next cases: a review. The two-state lowering must not change the value of
# an `inside` at the first samples, where a sampled value is X (16.5.1).


def test_inside_in_a_sampled_function_is_rejected() -> None:
    # $past(v inside {w}) at sample 0 is the X-or-1 value of the 4-state `inside` (an X item is
    # all wildcards, 11.4.13), not the X a two-state `v == w` would give
    for expression in (
        "$past(v3 inside {w3})",
        "$past(v4 inside {4'b1?0?})",
        "$past(v4 inside {[$:4'd3]})",
    ):
        assert "sampled-value function" in _rejection(expression), expression


def test_sampled_item_is_rejected() -> None:
    # v inside {$past(w)} holds at sample 0: the X item is all wildcards (11.4.6, 11.4.13),
    # which v == $past(w) does not give
    for expression in (
        "v3 inside {$past(w3)}",
        "v3 inside {[$past(w3):3'd5]}",
        "v3[0] inside {$rose(w3)}",
    ):
        assert "sampled-value function in an item" in _rejection(expression), expression


def test_wildcard_item_with_a_sampled_left_operand_is_rejected() -> None:
    # $past(v) is X at sample 0: an X bit under a wildcard of the item matches (11.4.13), an X
    # bit under a known item bit makes the result X; the first-sample rule does not model that
    assert "wildcard" in _rejection("$past(v4) inside {4'b1?0?}")


# v4 = 15 matches '1 in its own 4-bit comparison, but the common 8-bit type would compare
# 8'h0f with 8'hff (5.7.1: '1 fills the width of the context): the fill literals of
# `v4 inside {'1, 8'h00}` and the like are rejected in NON_UNIFORM above.


def test_x_fill_literal_is_rejected() -> None:
    _rejection("v4 inside {'x}")
    assert "constant" in _rejection("v4[0] inside {'x}")


# Origin: a re-review. Slang folds a constant `inside` in its common type, so the checks that
# compare that type with the standard's per-comparison sizing run on folded nodes too.


def test_folded_inside_with_a_fill_literal_in_a_wider_common_type_is_rejected() -> None:
    # In its own 4-bit comparison '1 is 4'b1111 and matches 4'd15 (5.7.1), so the assertion
    # `!(...)` fails; Slang's 8-bit common context compares 8'h0f with 8'hff and the folded
    # constant would make it pass
    assert _UNIFORM in _rejection("!(4'd15 inside {'1, 8'h00})")


def test_nested_folded_inside_is_rejected() -> None:
    assert _UNIFORM in _rejection("(v4 == 4'd1) || ((4'd15 inside {'1, 8'h00}) && v4[0])")


def test_folded_inside_with_equal_widths_is_accepted_and_folds() -> None:
    # 4'd15 matches 4'hF: the constant is 1, so !(...) is the constant 0
    expr = _lowered("!(4'd15 inside {4'hF, 4'h0})")
    assert value(expr, Trace(({},)), 0) == 0
    # '1 is one bit that fills its one-bit comparison
    assert value(_lowered("1'b1 inside {'1}"), Trace(({},)), 0) == 1
    assert value(_lowered("4'd3 inside {4'd3, 4'd9}"), Trace(({},)), 0) == 1
    assert value(_lowered("4'd4 inside {4'd3, [4'd5:4'd9]}"), Trace(({},)), 0) == 0


# Origin: a second re-review. Integers that Slang evaluates (selects, replication counts,
# $past depths, delay and repetition bounds, parameters) must not hide an `inside` whose
# per-comparison sizing differs from Slang's common type. `4'd15 inside {'1, 8'h00}` is 1 per
# comparison ('1 fills 4 bits and matches 15) but 0 in the 8-bit context.
_HIDDEN = "(4'd15 inside {'1, 8'h00})"


@pytest.mark.parametrize(
    "expression",
    [
        f"v4[{_HIDDEN}]",
        f"v4[{_HIDDEN}:0] == 1'b0",
        f"{{(1 + {_HIDDEN}){{v4[0]}}}} == 2'b11",
        f"$past(v4[0], 1 + {_HIDDEN})",
    ],
    ids=["bit_select", "part_select", "replication_count", "past_depth"],
)
def test_hidden_inside_in_a_constant_integer_is_rejected(expression: str) -> None:
    assert _UNIFORM in _rejection(expression)


@pytest.mark.parametrize(
    "expression",
    [
        f"v4[0] ##(1 + {_HIDDEN}) v4[1]",
        f"v4[0] ##[0:{_HIDDEN}] v4[1]",
        f"v4[0][*{_HIDDEN}] ##1 v4[1]",
        f"nexttime [{_HIDDEN}] v4[0]",
    ],
    ids=["delay", "delay_range", "repetition", "nexttime"],
)
def test_inside_in_a_bound_is_rejected(expression: str) -> None:
    assert _CONSTANT in _rejection(expression)


_CONSTANT = "evaluates it as a constant"
_CONSTANT_POSITIONS = {
    "sequence_argument": (
        "  sequence sq(int n); v3[0] ##n v3[1]; endsequence\n",
        f"sq(1 + {_HIDDEN})",
    ),
    # review: a default argument escaped the guard; per comparison
    # the delay is 2, in Slang's common context 1, so the verdicts differ
    "sequence_default_argument": (
        f"  sequence sq(int n = 1 + {_HIDDEN}); v3[0] ##n v3[1]; endsequence\n",
        "sq()",
    ),
    "localparam": (f"  localparam int N = 1 + {_HIDDEN};\n", "v3[0] ##N v3[1]"),
    "generate_condition": (
        f"  if ({_HIDDEN}) begin : g\n    q: assert property (@(posedge clk) v3[0]);\n  end\n",
        "v3[1]",
    ),
    "function": (
        f"  function automatic int f(); return 1 + {_HIDDEN}; endfunction\n"
        "  localparam int N = f();\n",
        "v3[0] ##N v3[1]",
    ),
    "declaration_initializer": (f"  wire w = {_HIDDEN};\n", "v3[0]"),
    # re-review: constants evaluated inside a safe context
    "size_cast_in_an_assertion": ("", f"(1 + {_HIDDEN})'(2'd2) == 2'd0"),
    "procedural_localparam": (
        "  always @(posedge clk) begin\n"
        f"    localparam int M = 1 + {_HIDDEN};\n"
        "    q: assert property (v3[0] ##M v3[1]);\n"
        "  end\n",
        "v3[0]",
    ),
    "declaration_dimension": (f"  logic [1 + {_HIDDEN}:0] r;\n", "v3[0]"),
    # third re-review: a type reference in an assertion operand
    "type_reference_in_bits": ("", f"$bits(type({{(1 + {_HIDDEN}){{1'b0}}}})) == 1"),
    # second re-review: an enum value in a procedural block
    "procedural_enum_value": (
        "  always @(posedge clk) begin\n"
        f"    typedef enum int {{N = 1 + {_HIDDEN}}} t;\n"
        "    q: assert property (N == 1);\n"
        "  end\n",
        "v3[0]",
    ),
}


_UNANALYSED = {
    "sequence_argument",
    "sequence_default_argument",
    "size_cast_in_an_assertion",
    "declaration_dimension",
    "procedural_enum_value",
    "type_reference_in_bits",
}
"""Constant positions that are not found among Slang's typed expressions: rejected as such;
the others are checked for uniformity like any ``inside``."""


@pytest.mark.parametrize("name", sorted(_CONSTANT_POSITIONS))
def test_non_uniform_inside_in_a_constant_position_rejects_the_design(name: str) -> None:
    declarations, prop = _CONSTANT_POSITIONS[name]
    text = (
        f"module tc(input logic clk, {_DECLARATIONS});\n{declarations}"
        f"  p: assert property (@(posedge clk) {prop});\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("t.sv", text)], SlangOptions(top=("tc",))))
    assert conversion.directives == ()
    expected = _CONSTANT if name in _UNANALYSED else _UNIFORM
    assert conversion.rejections
    assert all(expected in r.message for r in conversion.rejections)


@pytest.mark.parametrize(
    "declarations",
    [
        # the AXI4 checkers' pattern: an enum compared with members of its own type
        "  typedef enum logic [1:0] {S, M, D} agent_t;\n"
        "  localparam agent_t AGENT = M;\n"
        "  if (AGENT inside {S, M}) begin : g\n"
        "    q: assert property (@(posedge clk) v3[0]);\n  end\n",
        "  localparam int N = 1 + (4'd2 inside {4'd1, 4'd2});\n",
    ],
    ids=["generate_condition", "localparam"],
)
def test_uniform_inside_in_a_constant_position_is_accepted(declarations: str) -> None:
    text = (
        f"module tc(input logic clk, {_DECLARATIONS});\n{declarations}"
        "  p: assert property (@(posedge clk) v3[0] ##1 v3[1]);\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("t.sv", text)], SlangOptions(top=("tc",))))
    assert conversion.rejections == ()


def test_inside_in_a_parameter_override_rejects_the_design() -> None:
    """review: a parameter override escaped the guard."""
    text = (
        f"module leaf #(parameter int N = 1)(input logic clk, {_DECLARATIONS});\n"
        "  p: assert property (@(posedge clk) v3[0] ##N v3[1]);\n"
        "endmodule\n"
        f"module tc(input logic clk, {_DECLARATIONS});\n"
        f"  leaf #(.N(1 + {_HIDDEN})) u(.*);\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("t.sv", text)], SlangOptions(top=("tc",))))
    assert conversion.directives == ()
    assert conversion.rejections
    assert all(_UNIFORM in r.message for r in conversion.rejections)


def _design_code(statements: str) -> str:
    return (
        f"module leaf(input logic clk, input logic x, {_DECLARATIONS});\n"
        "  p: assert property (@(posedge clk) v3 inside {3'd1, 3'd2});\n"
        "endmodule\n"
        f"module tc(input logic clk, {_DECLARATIONS});\n{statements}"
        "  leaf u(.clk(clk), .x(1'b0), .*);\n"
        "endmodule\n"
    )


def test_inside_in_run_time_design_code_is_not_checked() -> None:
    """Contract point 16: an ``inside`` in a continuous assignment, a procedural block or an
    immediate assertion in an ``initial`` block (OpenTitan prim_intr_hw) is read by Yosys and
    folds into no assertion, even when its list is not uniform."""
    text = _design_code(
        f"  wire w; assign w = {_HIDDEN};\n"
        "  logic r;\n"
        f"  always_ff @(posedge clk) r <= {_HIDDEN};\n"
        f"  initial assert ({_HIDDEN});\n"
    )
    conversion = convert(export_texts([("t.sv", text)], SlangOptions(top=("tc",))))
    assert conversion.rejections == ()
    assert len(conversion.directives) == 1


def test_inside_in_a_port_connection_is_checked() -> None:
    """Contract point 16: Slang parses a port connection as a property expression, so an
    ``inside`` there is checked like a constant one."""
    uniform = _design_code("").replace(".x(1'b0)", ".x(v4 inside {4'd1, 4'd2})")
    mixed = _design_code("").replace(".x(1'b0)", f".x({_HIDDEN})")
    ok = convert(export_texts([("t.sv", uniform)], SlangOptions(top=("tc",))))
    bad = convert(export_texts([("t.sv", mixed)], SlangOptions(top=("tc",))))
    assert ok.rejections == ()
    assert bad.directives == ()
    assert all(_UNIFORM in r.message for r in bad.rejections)


def test_procedural_declarations_are_constant_positions() -> None:
    """Contract point 16: inside a procedural block a declaration (here a variable's width,
    which a procedural assertion reads) is a constant position, not run-time code."""
    text = _design_code(f"  always @(posedge clk) begin\n    logic [1 + {_HIDDEN}:0] t;\n  end\n")
    conversion = convert(export_texts([("t.sv", text)], SlangOptions(top=("tc",))))
    assert conversion.directives == ()


def test_inside_as_a_boolean_operand_next_to_a_bound_is_still_accepted() -> None:
    # the delay is a literal and the inside is a plain Boolean operand
    expr = _convert("v4 inside {4'd1, 4'd2} ##1 v4[0]")
    assert expr.rejections == ()


def test_deeply_nested_constant_inside_is_checked_in_linear_time() -> None:
    inner = "4'd1"
    for _ in range(20):
        inner = f"(({inner}) inside {{4'd1, 4'd2}} ? 4'd1 : 4'd0)"
    started = time.perf_counter()
    expr = _lowered(f"{inner} == 4'd1")
    assert time.perf_counter() - started < 5
    assert value(expr, Trace(({},)), 0) == 1
