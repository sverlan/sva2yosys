"""Functional contract of the parameter rule in the converter (CONTRACT.md, point 17)."""

from __future__ import annotations

import pytest

from sva2yosys.slang.convert import Conversion, convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

_TEMPLATE = """module r(input logic clk, a, b, input logic [3:0] v);
{body}
endmodule
"""

_TAIL = (
    " (a string, real or type parameter, an interface port, an escaped name); the monitor of "
    "each elaboration is selected by comparing integral parameters, so this is not supported"
)


def _message(names: str) -> str:
    return f"{names} is visible at this assertion but cannot be compared{_TAIL}"


def _convert(body: str) -> Conversion:
    text = _TEMPLATE.format(body=body)
    return convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))


_ASSERTION = "  p0: assert property (@(posedge clk) a |-> b);\n"


# The body starts on line 2 of the module; every case has the declaration on its first line
# and the assertion on the line after, column 3.
def _use(condition: str) -> str:
    return f"  p0: assert property (@(posedge clk) {condition} |-> b);\n"


# Every offending parameter is one the assertion depends on; the twin of each case with
# the parameter unused is accepted (test_slang_convert_dependencies.py).
REJECTED = [
    pytest.param(
        "  parameter upper = $;\n  p0: assert property (@(posedge clk) a |-> ##[0:upper] b);",
        "upper",
        id="dollar-valued-parameter",
    ),
    pytest.param(
        '  parameter string S = "ab";\n' + _use('(S == "ab")'), "S", id="string-parameter"
    ),
    pytest.param("  parameter real R = 1.5;\n" + _use("(R > 1.0)"), "R", id="real-parameter"),
    pytest.param("  parameter type T = logic;\n  T s;\n" + _use("s"), "T", id="type-parameter"),
    pytest.param(
        '  parameter string S = "ab";\n  parameter real R = 2.5;\n  parameter int N = 3;\n'
        + _use('((S == "ab") && (R > 1.0))'),
        "R, S",
        id="two-bad-parameters-sorted",
    ),
    pytest.param(
        "  parameter int N = 3;\n  parameter upper = $;\n"
        "  p0: assert property (@(posedge clk) a |-> ##[0:upper] b);\n",
        "upper",
        id="bad-parameter-beside-an-integer-one",
    ),
    pytest.param("  parameter int \\P-x = 3;\n" + _use("(v == \\P-x )"), "P-x", id="escaped-name"),
]


@pytest.mark.parametrize(("body", "names"), REJECTED)
def test_directive_with_an_uncomparable_parameter_is_rejected(body: str, names: str) -> None:
    conversion = _convert(body)
    assert conversion.directives == ()
    assert conversion.skipped == ()
    (rejection,) = conversion.rejections
    assert (rejection.scope, rejection.name) == ("r", "p0")
    assert rejection.message == _message(names)
    assert rejection.source is not None
    # The assertion is the last line of the body: line 1 is the module header.
    last_line = 1 + body.rstrip("\n").count("\n") + 1
    assert (rejection.source.file, rejection.source.line, rejection.source.column) == (
        "r.sv",
        last_line,
        3,
    )


def test_only_the_directive_that_sees_the_parameter_is_rejected() -> None:
    conversion = _convert(
        "  p1: assert property (@(posedge clk) a |-> b);\n"
        "  parameter real R = 1.5;\n"
        "  p2: assert property (@(posedge clk) (R > 1.0) |-> a);\n"
    )
    (rejection,) = conversion.rejections
    assert rejection.name == "p2"
    assert rejection.message == _message("R")
    (directive,) = conversion.directives
    assert directive.name == "p1"
    assert directive.parameters == ()


def test_a_parameter_declared_after_the_assertion_does_not_matter() -> None:
    conversion = _convert(_ASSERTION + "  parameter upper = $;\n")
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.name == "p0"
    assert directive.parameters == ()


def test_an_escaped_name_parameter_the_assertion_does_not_read_is_accepted() -> None:
    conversion = _convert("  parameter int \\P-x = 3;\n" + _ASSERTION)
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.dependencies == frozenset()


def test_a_directive_that_would_be_skipped_by_an_x_check_is_rejected() -> None:
    conversion = _convert(
        "  parameter upper = $;\n"
        "  p0: assert property (@(posedge clk) !$isunknown(v) |-> ##[0:upper] b);\n"
    )
    assert conversion.skipped == ()
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert rejection.name == "p0"
    assert rejection.message == _message("upper")


def test_an_x_check_skip_with_integer_parameters_is_still_skipped() -> None:
    conversion = _convert(
        "  parameter int N = 3;\n  p0: assert property (@(posedge clk) !$isunknown(v));\n"
    )
    assert conversion.rejections == ()
    (skipped,) = conversion.skipped
    assert skipped.name == "p0"


def test_integer_parameters_are_accepted_and_keep_their_values() -> None:
    conversion = _convert(
        "  parameter int N = 3;\n  parameter K = 2;\n"
        "  p0: assert property (@(posedge clk) a |-> ##[N:N+K] b);\n"
    )
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    values = dict(directive.parameters)
    assert set(values) == {"K", "N"}
    n, k = values["N"], values["K"]
    assert n is not None
    assert k is not None
    assert n.literal.endswith("3")
    assert k.literal.endswith("2")


def test_a_literal_dollar_in_a_range_is_not_a_parameter() -> None:
    conversion = _convert("  p0: assert property (@(posedge clk) a |-> ##[0:$] b);\n")
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.parameters == ()


def test_a_directive_without_parameters_is_unchanged() -> None:
    conversion = _convert(_ASSERTION)
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.parameters == ()


def test_an_unrelated_rejection_keeps_its_own_message() -> None:
    conversion = _convert(
        "  parameter upper = $;\n  always_comb p0: assert property (@(posedge clk) a);\n"
    )
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert rejection.name == "p0"
    assert "always_comb" in rejection.message
    assert "cannot be compared" not in rejection.message
