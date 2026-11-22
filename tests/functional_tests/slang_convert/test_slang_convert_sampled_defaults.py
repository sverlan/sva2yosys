"""Functional contract of default sampled values (CONTRACT.md, point 13).

Every expected result is derived by hand from IEEE 1800-2017 section 16.5.1: before the first
clock tick a sampled-value function sees the operand's default sampled value (a variable's
initializer, else 0 for a 2-state type and X for a 4-state type or net, computed through
expressions), and sva2yosys models the operand as X there, so it rejects the other cases.
"""

from __future__ import annotations

import pytest

from sva2yosys.ir.expr import BIT, Binary, BinaryOp, SampledDefault, Signal
from sva2yosys.ir.property import SequenceProperty
from sva2yosys.ir.sequence import Bool
from sva2yosys.passes.validate import validation_problems
from sva2yosys.slang.convert import Conversion, convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

_MESSAGE = "before the first clock tick is not unknown"


def _convert(body: str, ports: str = "") -> Conversion:
    text = f"module r(input logic clk, rst{ports});\n  {body}\nendmodule\n"
    return convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))


def _problems(body: str, ports: str = "") -> tuple[str, ...]:
    conversion = _convert(body, ports)
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    return validation_problems(directive)


# (source, cause fragment, origin)
MUST_REJECT = [
    # 2-state variable: default 0, not X. Rejected (16.5.1). Origin: reviews (D1).
    ("bit a; assert property (@(posedge clk) $stable(a));", "'r.a' is initialised or of a 2-state"),
    ("bit a; cover property (@(posedge clk) $fell(a));", "'r.a' is initialised or of a 2-state"),
    # initialised 4-state variable: default is the initializer. Origin: reviews (D1).
    (
        "logic b = 1'b0; assert property (@(posedge clk) $past(b) == 1'b0);",
        "'r.b' is initialised or of a 2-state",
    ),
    # constant part of an expression: the default of {1'b1, a} is {1, x}. Origin: reviews (D1).
    (
        "logic a; assert property (@(posedge clk) $past({1'b1, a}));",
        "the operand contains a constant",
    ),
    # int is 2-state. Origin: D1 corpus probe.
    (
        "int n; assert property (@(posedge clk) $past(n) != 5);",
        "'r.n' is initialised or of a 2-state",
    ),
    # in the disable condition; the AND with a constant keeps known bits. Origin: D1 corpus probe.
    (
        "logic [3:0] d; assert property (@(posedge clk) disable iff (rst) $stable(d & 4'b0011));",
        "the operand contains a constant",
    ),
]

MUST_ACCEPT = [
    # 4-state variable without initializer: default X (16.5.1).
    "logic a; assert property (@(posedge clk) $rose(a));",
    "logic a; assert property (@(posedge clk) $past(a) == 1'b0);",
    "logic a; assert property (@(posedge clk) $past(a + 1));",
    # a net: default X.
    "wire w; assert property (@(posedge clk) $stable(w));",
]


@pytest.mark.parametrize(("body", "cause"), [(b, c) for b, c in MUST_REJECT])
def test_a_known_default_is_rejected(body: str, cause: str) -> None:
    problems = _problems(body)
    assert len(problems) == 1
    assert _MESSAGE in problems[0]
    assert "IEEE 1800 §16.5.1" in problems[0]
    assert cause in problems[0]


@pytest.mark.parametrize("body", MUST_ACCEPT)
def test_an_unknown_default_is_accepted(body: str) -> None:
    assert _problems(body) == ()


def test_input_ports_follow_their_type() -> None:
    assert _problems("assert property (@(posedge clk) $rose(a));", ports=", input logic a") == ()
    (problem,) = _problems("assert property (@(posedge clk) $stable(a));", ports=", input bit a")
    assert _MESSAGE in problem


def test_the_axi_reset_pattern_is_accepted() -> None:
    """Input ports of 4-state type: ``$rose(ARESETn) |-> !ARVALID``."""
    body = "assert property (@(posedge clk) $rose(ARESETn) |-> !ARVALID);"
    assert _problems(body, ports=", input logic ARESETn, ARVALID") == ()


def test_signals_carry_the_default_class() -> None:
    conversion = _convert("bit b; logic a; assert property (@(posedge clk) a && b);")
    (directive,) = conversion.directives
    assert isinstance(directive.property, SequenceProperty)
    assert isinstance(directive.property.sequence, Bool)
    expr = directive.property.sequence.expr
    assert expr == Binary(
        BinaryOp.LOGICAL_AND,
        Signal("r.a", BIT, SampledDefault.X),
        Signal("r.b", BIT, SampledDefault.KNOWN),
        BIT,
    )


def test_a_missing_sampled_default_is_an_error_not_x() -> None:
    document = export_texts(
        [
            (
                "r.sv",
                "module r(input logic clk, a);\n  assert property (@(posedge clk) a);\nendmodule\n",
            )
        ],
        SlangOptions(top=("r",)),
    )

    def strip(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                strip(item)
        elif isinstance(node, dict):
            node.pop("sva2yosys_sampledDefault", None)
            for value in node.values():
                strip(value)

    strip(document)
    conversion = convert(document)
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert "sva2yosys_sampledDefault" in rejection.message


_HIDDEN = "does not support it inside a sampled-value function"

# (source, ports, fragment). Expected: rejected by the converter, IEEE 1800-2017 16.5.1: the IR
# would show an all-X default although the real default is known. Hand-derived.
# Origin: a review.
HIDDEN_KNOWN_DEFAULTS = [
    # 6.11.2: the cast turns X into 0, so $fell(bit'(a)) sees default 0 and fails at tick 0.
    ("assert property (@(posedge clk) $fell(bit'(a)));", ", input logic a", "a cast to a 2-state"),
    # 20.9: $countones counts X bits as 0, so its default is 0, known.
    (
        "assert property (@(posedge clk) $stable($countones(v)));",
        ", input logic [1:0] v",
        "$countones",
    ),
    (
        "assert property (@(posedge clk) $past($onehot(v)));",
        ", input logic [1:0] v",
        "$onehot",
    ),
    (
        "assert property (@(posedge clk) $past($onehot0(v)));",
        ", input logic [1:0] v",
        "$onehot0",
    ),
    # 20.9: $isunknown(X) is 1, so the default of a | $isunknown(a) is X | 1 = 1.
    (
        "assert property (@(posedge clk) $past(a | $isunknown(a)));",
        ", input logic a",
        "$isunknown",
    ),
    (
        "assert property (@(posedge clk) $past(!$isunknown(a)));",
        ", input logic a",
        "$isunknown",
    ),
]


@pytest.mark.parametrize(("body", "ports", "fragment"), HIDDEN_KNOWN_DEFAULTS)
def test_a_construct_hiding_a_known_default_is_rejected(
    body: str, ports: str, fragment: str
) -> None:
    conversion = _convert(body, ports)
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert _MESSAGE in rejection.message
    assert "IEEE 1800 §16.5.1" in rejection.message
    assert _HIDDEN in rejection.message
    assert fragment in rejection.message


@pytest.mark.parametrize(
    "body",
    [
        "assert property (@(posedge clk) $fell(logic'(a)));",
        "assert property (@(posedge clk) $past(signed'(v)) == 0);",
        # outside a sampled operand the same constructs are unaffected
        "assert property (@(posedge clk) bit'(a) |-> $countones(v) == 1);",
    ],
)
def test_four_state_casts_and_unsampled_uses_are_accepted(body: str) -> None:
    conversion = _convert(body, ", input logic a, input logic [1:0] v")
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert validation_problems(directive) == ()


def test_a_missing_four_state_flag_is_an_error_not_four_state() -> None:
    document = export_texts(
        [
            (
                "r.sv",
                "module r(input logic clk, a);\n"
                "  assert property (@(posedge clk) $fell(logic'(a)));\nendmodule\n",
            )
        ],
        SlangOptions(top=("r",)),
    )

    def strip(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                strip(item)
        elif isinstance(node, dict):
            node.pop("sva2yosys_fourState", None)
            for value in node.values():
                strip(value)

    strip(document)
    conversion = convert(document)
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert "sva2yosys_fourState" in rejection.message
