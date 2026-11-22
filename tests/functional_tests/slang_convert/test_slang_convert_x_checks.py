"""Functional contract of ``$isunknown`` in the converter (CONTRACT.md, point 11)."""

from __future__ import annotations

import pytest

from sva2yosys.ir.directive import DirectiveKind
from sva2yosys.ir.expr import BIT, Const, Sampled, SampledFunction, Signal
from sva2yosys.ir.property import Implication, SequenceProperty
from sva2yosys.ir.sequence import Bool
from sva2yosys.slang.convert import Conversion, XChecks, convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangError, SlangOptions

_TEMPLATE = """module r(input logic clk, en, a, b, c, d, input logic [3:0] v);
  {snippet}
endmodule
"""


def _convert(snippet: str, x_checks: XChecks = XChecks.SKIP) -> Conversion:
    text = _TEMPLATE.format(snippet=snippet)
    return convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))), x_checks)


def _a() -> Signal:
    return Signal("r.a", BIT)


def _b() -> Signal:
    return Signal("r.b", BIT)


ASSERT_SKIPPED = [
    "!$isunknown(v)",
    "!$isunknown(v) && !$isunknown(a)",
    "a |-> !$isunknown(v)",
    "a |=> !$isunknown(v)",
    "a |-> ##1 !$isunknown(v)",
    "a |-> ##[1:3] !$isunknown(v)",
    "$isunknown(v) |-> b",
    "$isunknown(v) ##1 a |-> b",
    "a ##1 $isunknown(v) |=> b",
    "not $isunknown(v)",
    "(a |-> !$isunknown(v)) and (b |-> !$isunknown(v))",
    "(a |-> b) or (b |-> !$isunknown(v))",
    "a |-> (b |-> !$isunknown(v))",
    "if (a) !$isunknown(v) else !$isunknown(b)",
    "if (a) !$isunknown(v)",
    "nexttime !$isunknown(v)",
    "always (a |-> !$isunknown(v))",
    "always [1:3] !$isunknown(v)",
    "eventually [1:3] !$isunknown(v)",
    "a |-> (b || !$isunknown(v)) ",
    "!(a && $isunknown(v))",
    "a || b || !$isunknown(v) |-> ##1 !$isunknown(v)",
    "weak(!$isunknown(v))",
    "a until !$isunknown(v)",
    "!$isunknown(v) until a",
    "a |-> !$isunknown(v) until b",
]


@pytest.mark.parametrize("property_text", ASSERT_SKIPPED)
def test_an_assert_that_cannot_fail_is_skipped(property_text: str) -> None:
    conversion = _convert(f"x: assert property (@(posedge clk) {property_text});")
    assert conversion.directives == ()
    assert conversion.rejections == ()
    (skipped,) = conversion.skipped
    assert (skipped.scope, skipped.name, skipped.kind) == ("r", "x", DirectiveKind.ASSERT)
    assert skipped.reason.startswith("X-check:")
    assert "can never fail" in skipped.reason
    assert conversion.warnings == ()


@pytest.mark.parametrize(
    "property_text",
    ["!$isunknown(v)", "a |-> !$isunknown(v)", "$isunknown(v) |-> b", "not $isunknown(v)"],
)
def test_an_assume_that_cannot_fail_is_skipped(property_text: str) -> None:
    conversion = _convert(f"x: assume property (@(posedge clk) {property_text});")
    assert conversion.directives == ()
    (skipped,) = conversion.skipped
    assert skipped.kind is DirectiveKind.ASSUME
    assert "constrains nothing" in skipped.reason


@pytest.mark.parametrize(
    "property_text",
    [
        "$isunknown(v)",
        "a ##1 $isunknown(v)",
        "a ##[1:3] $isunknown(v)",
        "$isunknown(v) |-> b",
        "$isunknown(v) ##1 a |=> b",
        "a |-> $isunknown(v)",
        "a |=> $isunknown(v)",
        "$isunknown(v) and a",
        "(a |-> b) and $isunknown(v)",
        "($isunknown(v) |-> a) or ($isunknown(v) |-> b)",
        "$isunknown(v) or $isunknown(a)",
        "$isunknown(v)[*2]",
        "$isunknown(v) throughout a",
        "a within $isunknown(v)",
        "$isunknown(v)[->1]",
        "a ##1 b[->2] ##1 $isunknown(v)",
    ],
)
def test_a_cover_that_cannot_succeed_is_skipped(property_text: str) -> None:
    conversion = _convert(f"x: cover property (@(posedge clk) {property_text});")
    assert conversion.directives == (), property_text
    (skipped,) = conversion.skipped
    assert skipped.kind is DirectiveKind.COVER
    assert "can never succeed" in skipped.reason


def test_skipped_directives_keep_document_order_and_the_others_are_converted() -> None:
    conversion = _convert(
        "p1: assert property (@(posedge clk) !$isunknown(v));\n"
        "  p2: assert property (@(posedge clk) a |-> b);\n"
        "  p3: assume property (@(posedge clk) !$isunknown(a));\n"
        "  p4: cover property (@(posedge clk) $isunknown(a));\n"
        "  p5: assert property (@(posedge clk) a |=> b);"
    )
    assert [d.name for d in conversion.directives] == ["p2", "p5"]
    assert [s.name for s in conversion.skipped] == ["p1", "p3", "p4"]
    assert [s.kind for s in conversion.skipped] == [
        DirectiveKind.ASSERT,
        DirectiveKind.ASSUME,
        DirectiveKind.COVER,
    ]
    assert conversion.rejections == ()


def test_the_skipped_directive_is_located_at_the_directive() -> None:
    conversion = _convert("x: assert property (@(posedge clk) !$isunknown(v));")
    (skipped,) = conversion.skipped
    line = _TEMPLATE.format(snippet="x: assert property (@(posedge clk) !$isunknown(v));")
    assert skipped.source.file == "r.sv"
    assert skipped.source.line == 2
    assert skipped.source.column == line.splitlines()[1].index("x:") + 1


# --- mixed directives: accepted with a warning -----------------------------------------------


def test_the_x_part_of_a_mixed_assert_is_dropped_with_a_warning() -> None:
    snippet = "x: assert property (@(posedge clk) a |-> b && !$isunknown(v));"
    conversion = _convert(snippet)
    assert conversion.skipped == ()
    (directive,) = conversion.directives
    assert directive.property == Implication(Bool(_a()), SequenceProperty(Bool(_b())), True)
    (warning,) = conversion.warnings
    assert (warning.scope, warning.name) == ("r", "x")
    assert "dropped" in warning.message and "$isunknown" in warning.message
    assert "assert" in warning.message
    assert warning.directive == directive.source
    assert warning.source is not None
    assert (warning.source.file, warning.source.line) == ("r.sv", 2)
    assert (
        warning.source.column
        == _TEMPLATE.format(snippet=snippet).splitlines()[1].index("$isunknown") + 1
    )


@pytest.mark.parametrize(
    ("property_text", "expected"),
    [
        ("a |-> b && !$isunknown(v)", Implication(Bool(_a()), SequenceProperty(Bool(_b())), True)),
        ("a |-> (b || $isunknown(v))", Implication(Bool(_a()), SequenceProperty(Bool(_b())), True)),
        ("!$isunknown(v) && a |-> b", Implication(Bool(_a()), SequenceProperty(Bool(_b())), True)),
        (
            "($isunknown(v) ? a : b) |-> d",
            Implication(Bool(_b()), SequenceProperty(Bool(Signal("r.d", BIT))), True),
        ),
        (
            "a |-> $isunknown(v)",
            Implication(Bool(_a()), SequenceProperty(Bool(Const(0, BIT))), True),
        ),
        ("a && !$isunknown(v)", SequenceProperty(Bool(_a()))),
    ],
)
def test_folded_properties(property_text: str, expected: object) -> None:
    conversion = _convert(f"x: assert property (@(posedge clk) {property_text});")
    (directive,) = conversion.directives
    assert directive.property == expected
    assert len(conversion.warnings) == 1


@pytest.mark.parametrize(
    "property_text",
    [
        "a |-> $isunknown(v)",  # fails whenever a holds
        "a ##1 b |-> c && !$isunknown(v)",
        "strong(!$isunknown(v))",  # a strong sequence is not proved
        "!$isunknown(v) s_until a",
        "s_eventually !$isunknown(v)",
        "a |-> s_eventually !$isunknown(v)",
        "a until_with !$isunknown(v)",
        "(a |-> b) and !$isunknown(v)",
    ],
)
def test_a_directive_that_may_fail_is_never_skipped(property_text: str) -> None:
    conversion = _convert(f"x: assert property (@(posedge clk) {property_text});")
    assert conversion.skipped == ()
    assert len(conversion.directives) == 1
    assert len(conversion.warnings) == 1


@pytest.mark.parametrize(
    "property_text",
    [
        "a |-> !$isunknown(v)",
        # `and`/`or` are nonvacuous when either operand is (IEEE 1800 §16.14.8): a
        # vacuously true operand does not stop them from succeeding
        "(a |-> $isunknown(v)) and b",
        "($isunknown(v) |-> a) and b",
        "($isunknown(v) |-> a) or b",
        "$isunknown(v) or ($isunknown(v) |-> a)",
    ],
)
def test_a_cover_with_a_real_success_is_never_skipped(property_text: str) -> None:
    conversion = _convert(f"x: cover property (@(posedge clk) {property_text});")
    assert conversion.skipped == ()
    (directive,) = conversion.directives
    assert directive.kind is DirectiveKind.COVER
    (warning,) = conversion.warnings
    assert "cover" in warning.message


def test_a_nested_sampled_value_function_over_a_folded_condition_is_kept() -> None:
    conversion = _convert("x: assert property (@(posedge clk) a |-> $rose(b) && !$isunknown(v));")
    assert conversion.skipped == ()
    (directive,) = conversion.directives
    assert directive.property == Implication(
        Bool(_a()), SequenceProperty(Bool(Sampled(SampledFunction.ROSE, _b(), BIT))), True
    )


# --- disable iff and procedural enabling conditions ---------------------------------------


def test_a_disable_that_folds_to_true_skips_the_directive() -> None:
    conversion = _convert(
        "x: assert property (@(posedge clk) disable iff (!$isunknown(v)) a |-> b);"
    )
    assert conversion.directives == ()
    (skipped,) = conversion.skipped
    assert "disable iff" in skipped.reason and "always disabled" in skipped.reason


def test_a_disable_that_folds_to_false_never_disables_and_warns() -> None:
    conversion = _convert(
        "x: assert property (@(posedge clk) disable iff ($isunknown(v)) a |-> b);"
    )
    (directive,) = conversion.directives
    assert directive.disable is None
    (warning,) = conversion.warnings
    assert "disable iff" in warning.message and "never disables" in warning.message


def test_a_disable_with_a_real_part_keeps_it() -> None:
    conversion = _convert(
        "x: assert property (@(posedge clk) disable iff (en || $isunknown(v)) a |-> b);"
    )
    (directive,) = conversion.directives
    assert directive.disable == Signal("r.en", BIT)
    assert len(conversion.warnings) == 1


def test_a_default_disable_is_folded_too() -> None:
    conversion = _convert(
        "default disable iff ($isunknown(v));\n  x: assert property (@(posedge clk) a |-> b);"
    )
    (directive,) = conversion.directives
    assert directive.disable is None
    (warning,) = conversion.warnings
    assert "never disables" in warning.message


def test_an_enabling_condition_that_folds_to_false_skips_the_directive() -> None:
    conversion = _convert("always @(posedge clk) if ($isunknown(v)) x: assert property (a |-> b);")
    assert conversion.directives == ()
    (skipped,) = conversion.skipped
    assert "enabling condition" in skipped.reason and "never reached" in skipped.reason


def test_an_enabling_condition_that_folds_to_true_is_dropped_with_a_warning() -> None:
    conversion = _convert("always @(posedge clk) if (!$isunknown(v)) x: assert property (a |-> b);")
    (directive,) = conversion.directives
    assert directive.property == Implication(Bool(_a()), SequenceProperty(Bool(_b())), True)
    (warning,) = conversion.warnings
    assert "enabling condition" in warning.message


def test_an_enabling_condition_with_a_real_part_keeps_it() -> None:
    conversion = _convert(
        "always @(posedge clk) if (en && !$isunknown(v)) x: assert property (a |-> b);"
    )
    (directive,) = conversion.directives
    assert isinstance(directive.property, Implication)
    assert len(conversion.warnings) == 1


# --- constants, the option, rejections --------------------------------------------------------


def test_a_constant_x_operand_is_the_constant_one_not_an_x_check() -> None:
    conversion = _convert("x: assert property (@(posedge clk) !$isunknown(4'b1x00));")
    assert conversion.skipped == () and conversion.warnings == ()
    (directive,) = conversion.directives
    assert directive.property == SequenceProperty(Bool(Const(0, BIT)))  # !1: always fails


def test_a_known_constant_operand_is_the_constant_zero_not_an_x_check() -> None:
    conversion = _convert("x: assert property (@(posedge clk) !$isunknown(4'b0001));")
    assert conversion.skipped == () and conversion.warnings == ()
    (directive,) = conversion.directives
    assert directive.property == SequenceProperty(Bool(Const(1, BIT)))


@pytest.mark.parametrize(
    "property_text",
    [
        "!$isunknown(v)",
        "a |-> b && !$isunknown(v)",
        "a |-> !$isunknown(v)",
        "disable iff ($isunknown(v)) a |-> b",
    ],
)
def test_the_reject_option_rejects_every_use_with_a_location(property_text: str) -> None:
    snippet = f"x: assert property (@(posedge clk) {property_text});"
    conversion = _convert(snippet, XChecks.REJECT)
    assert conversion.directives == () and conversion.skipped == ()
    (rejection,) = conversion.rejections
    assert "$isunknown" in rejection.message and "reject" in rejection.message
    assert rejection.source is not None
    assert (rejection.source.file, rejection.source.line) == ("r.sv", 2)
    assert (
        rejection.source.column
        == _TEMPLATE.format(snippet=snippet).splitlines()[1].index("$isunknown") + 1
    )


def test_the_reject_option_rejects_a_procedural_enabling_condition() -> None:
    conversion = _convert(
        "always @(posedge clk) if (!$isunknown(v)) x: assert property (a |-> b);", XChecks.REJECT
    )
    assert len(conversion.rejections) == 1 and conversion.directives == ()


def test_the_reject_option_does_not_touch_other_assertions() -> None:
    conversion = _convert(
        "x: assert property (@(posedge clk) !$isunknown(v));\n"
        "  y: assert property (@(posedge clk) a |-> b);",
        XChecks.REJECT,
    )
    assert [r.name for r in conversion.rejections] == ["x"]
    assert [d.name for d in conversion.directives] == ["y"]


@pytest.mark.parametrize("x_checks", list(XChecks))
@pytest.mark.parametrize(
    "operand", ["$past(v)", "$past(v, 2)", "v ^ $past(v)", "$past(a) && b", "{v, $past(a)}"]
)
def test_the_first_sample_rule_rejects_an_operand_using_past(
    operand: str, x_checks: XChecks
) -> None:
    snippet = f"x: assert property (@(posedge clk) a |-> !$isunknown({operand}));"
    conversion = _convert(snippet, x_checks)
    assert conversion.directives == () and conversion.skipped == ()
    (rejection,) = conversion.rejections
    assert "$isunknown of an operand using $past" in rejection.message
    assert "first sample" in rejection.message
    assert rejection.source is not None
    assert (
        rejection.source.column
        == _TEMPLATE.format(snippet=snippet).splitlines()[1].index("$isunknown") + 1
    )


def test_isunknown_inside_past_is_rejected() -> None:
    """Hand-derived (IEEE 1800 16.5.1, 20.9): a |-> b && $past(!$isunknown(v)) with a = b = 1
    at sample 0 fails there in IEEE ($past is the default of !$isunknown(X) = 0), but the
    default is known (1 for an X operand), which the folded constant would hide: rejected.
    Origin: a review (the former reference-semantics trace case)."""
    conversion = _convert("x: assert property (@(posedge clk) a |-> b && $past(!$isunknown(v)));")
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert "it is 1 for an X operand" in rejection.message


def test_an_unsupported_operand_is_still_rejected() -> None:
    conversion = _convert("x: assert property (@(posedge clk) !$isunknown(v ** 2));")
    assert conversion.directives == () and conversion.skipped == ()
    (rejection,) = conversion.rejections
    assert "Power" in rejection.message


@pytest.mark.parametrize("x_checks", list(XChecks))
@pytest.mark.parametrize(
    "property_text",
    [
        "!$isunknown(v / {a, b})",
        "!$isunknown(v % {a, b})",
        "a |-> !$isunknown(v / {2'b0, c, d})",
        "!$isunknown(v / 4'd0)",
    ],
)
def test_an_operand_that_can_be_x_from_known_values_is_rejected(
    property_text: str, x_checks: XChecks
) -> None:
    """A divisor that may be zero gives X from known values, which `$isunknown` detects:
    folding the call to 0 would skip a directive that fails in IEEE."""
    conversion = _convert(f"x: assert property (@(posedge clk) {property_text});", x_checks)
    assert conversion.directives == () and conversion.skipped == (), property_text
    (rejection,) = conversion.rejections
    assert "divisor" in rejection.message
    assert rejection.source is not None and rejection.source.line == 2


def test_an_operand_with_a_non_zero_constant_divisor_is_folded() -> None:
    conversion = _convert("x: assert property (@(posedge clk) !$isunknown(v / 4'd2));")
    assert conversion.rejections == () and conversion.directives == ()
    (skipped,) = conversion.skipped
    assert skipped.reason.startswith("X-check:")


def test_a_hierarchical_operand_is_still_rejected() -> None:
    conversion = _convert(
        "sub u(.x(a));\n  x: assert property (@(posedge clk) !$isunknown(u.y));\n"
        "endmodule\nmodule sub(input logic x); logic y; assign y = x;"
    )
    assert conversion.directives == () and conversion.skipped == ()
    (rejection,) = conversion.rejections
    assert "hierarchical" in rejection.message


def test_isunknown_with_two_arguments_is_a_slang_error_not_a_conversion() -> None:
    with pytest.raises(SlangError):
        _convert("x: assert property (@(posedge clk) !$isunknown(v, a));")
