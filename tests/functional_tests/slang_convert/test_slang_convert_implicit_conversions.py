"""Functional contract of the sizing of conversions (CONTRACT.md, point 15; IEEE 1800-2017
11.8.2, 11.8.3, 6.24.1).

An operand that Slang converts to the type of its context (kind ``Propagated``) is extended by
the sign of the context's type: a signed operand in an unsigned context is zero-extended. A
cast (kind ``Explicit``, also the conversion of an actual argument to a typed formal) keeps the
extension by the signedness of its source. Every expected value is derived by hand and the
expressions are evaluated with the reference evaluator's semantics.
Origin: reviews.
"""

from __future__ import annotations

import pytest

from support.reference.trace import Trace
from support.reference.values import value
from sva2yosys.ir.expr import Expr
from sva2yosys.ir.property import SequenceProperty
from sva2yosys.ir.sequence import Bool
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

_MODULE = """module tc(input logic clk, input logic signed [3:0] s4, input logic [7:0] u8,
  input logic [3:0] u4, input logic c);
  property q(logic [7:0] x); x == 8'hFF; endproperty
  p: assert property (@(posedge clk) {expression});
endmodule
"""


def _lowered(expression: str) -> Expr:
    text = _MODULE.replace("{expression}", expression)
    conversion = convert(export_texts([("t.sv", text)], SlangOptions(top=("tc",))))
    assert conversion.rejections == ()
    prop = conversion.directives[0].property
    assert isinstance(prop, SequenceProperty)
    assert isinstance(prop.sequence, Bool)
    return prop.sequence.expr


def _eval(expression: str, **signals: int) -> int:
    row = {"tc.s4": 0, "tc.u8": 0, "tc.u4": 0, "tc.c": 0}
    row.update({f"tc.{name}": v for name, v in signals.items()})
    return value(_lowered(expression), Trace((row,)), 0)


# (expression, signals, expected value, why). s4 = 15 is the signed 4-bit value -1.
CASES = [
    # s4 is zero-extended to 8 bits (0x0F) in the unsigned 8-bit sum: 15 + 1 = 16
    ("(s4 + u8) == 8'd16", {"s4": 15, "u8": 1}, 1, "propagated into an unsigned sum"),
    # an operand that is sign-extended would give 0xFF + 1 = 0 in 8 bits
    ("(s4 + u8) == 8'd0", {"s4": 15, "u8": 1}, 0, "not sign-extended in the sum"),
    # unsigned comparison: 15 < 20 (sign extension would make it 255 < 20)
    ("s4 < u8", {"s4": 15, "u8": 20}, 1, "propagated into an unsigned relation"),
    # the branches are converted to the unsigned 8-bit type of the conditional: 0x0F
    ("(c ? s4 : u8) == 8'd15", {"c": 1, "s4": 15, "u8": 0}, 1, "propagated into a branch"),
    ("(c ? s4 : u8) == 8'd255", {"c": 1, "s4": 15, "u8": 0}, 0, "not sign-extended in a branch"),
    # all-signed operands keep the sign extension: -1 + -1 in 8 signed bits is -2
    ("(s4 + s4) == -8'sd2", {"s4": 15}, 1, "signed context sign-extends"),
    # signed'(u4) reinterprets 1111 as -1 (no extension): -1 < 0
    ("signed'(u4) < 4'sd0", {"u4": 15}, 1, "explicit cast to signed"),
    # unsigned'(s4) reinterprets 1111 as 15
    ("unsigned'(s4) == 4'd15", {"s4": 15}, 1, "explicit cast to unsigned"),
    # a size cast is an assignment (6.24.1): the signed source is sign-extended to 0xFF
    ("8'(s4) == 8'd255", {"s4": 15}, 1, "size cast keeps the sign of its source"),
    ("8'(s4) == 8'd15", {"s4": 15}, 0, "size cast does not zero-extend a signed source"),
    # signed'(u4) is signed, so the 8-bit cast sign-extends it as well
    ("8'(signed'(u4)) == 8'd255", {"u4": 15}, 1, "size cast of a signed cast"),
    # the actual argument s4 is converted to the formal's type logic [7:0] as in an
    # assignment, by the sign of the source: -1 becomes 0xFF
    ("q(s4)", {"s4": 15}, 1, "actual argument keeps the sign of the source"),
    ("q(s4)", {"s4": 7}, 0, "a positive actual argument is 0x07"),
    # self-determined operands (11.6.1, 11.8.1) are not extended to the context. The shift
    # count u4 = 1 does not take part in the context; the comparison's 8-bit signed context
    # propagates into the shifted operand first (11.8.2): 11111111 >>> 1 = 11111111 (11.4.10),
    # i.e. -1, equal to -8'sd1.
    ("(s4 >>> u4) == -8'sd1", {"s4": 15, "u4": 1}, 1, "shift count is self-determined"),
    # {s4, u4} is 1111_0001 = 8'hF1: each operand keeps its own width, no extension
    ("{s4, u4} == 8'hF1", {"s4": 15, "u4": 1}, 1, "concatenation operands are self-determined"),
    ("{s4, u4} == 8'hFF", {"s4": 15, "u4": 1}, 0, "concatenation is not sign-extended"),
    # &1111 = 1 on the 4 bits of s4; &0111 = 0
    ("(&s4) == 1'b1", {"s4": 15}, 1, "reduction AND of all ones"),
    ("(&s4) == 1'b1", {"s4": 7}, 0, "reduction AND is over the 4 bits of s4 only"),
    # the reduction operand is self-determined: &1111 = 1, so 1 + 5 = 6 in the 8-bit sum;
    # zero-extending s4 to 8 bits first would give &8'h0F = 0 and a sum of 5
    ("((&s4) + u8) == 8'd6", {"s4": 15, "u8": 5}, 1, "reduction operand is self-determined"),
    # the selector is self-determined: 1000 is non-zero, so the true branch u8 = 7 is chosen
    ("(s4 ? u8 : 8'd0) == 8'd7", {"s4": 8, "u8": 7}, 1, "selector true"),
    ("(s4 ? u8 : 8'd0) == 8'd7", {"s4": 0, "u8": 7}, 0, "selector false"),
]


@pytest.mark.parametrize("case", CASES, ids=[c[3] + "|" + c[0] for c in CASES])
def test_conversion_extends_by_the_expected_signedness(
    case: tuple[str, dict[str, int], int, str],
) -> None:
    expression, signals, expected, _ = case
    assert _eval(expression, **signals) == expected


def test_missing_conversion_kind_is_a_located_export_error() -> None:
    """A ``Conversion`` without ``sva2yosys_conversionKind`` is rejected, never read as
    ``Explicit``. Origin: a review."""
    text = _MODULE.replace("{expression}", "(s4 + u8) == 8'd16")
    document = export_texts([("t.sv", text)], SlangOptions(top=("tc",)))

    def strip(node: object) -> None:
        if isinstance(node, dict):
            node.pop("sva2yosys_conversionKind", None)
            for child in node.values():
                strip(child)
        elif isinstance(node, list):
            for child in node:
                strip(child)

    strip(document)
    conversion = convert(document)
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert "sva2yosys_conversionKind" in rejection.message
    assert "internal export error" in rejection.message
