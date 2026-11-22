"""Functional contract of the shared parameter rule in the rewrite (CONTRACT.md, point 21)."""

from __future__ import annotations

import pytest

from sva2yosys.rewrite import RewriteError, Severity, rewrite, syntax_items
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

_TAIL = (
    " (a string, real or type parameter, an interface port, an escaped name); the monitor of "
    "each elaboration is selected by comparing integral parameters, so this is not supported"
)

_HEADER = "module p(input logic clk, a, b);\n"
_ASSERTION = "  p0: assert property (@(posedge clk) a |-> b);\n"


def _use(condition: str) -> str:
    return f"  p0: assert property (@(posedge clk) {condition} |-> b);\n"


# (body, names): the assertion is the last line of the body, column 3. Every offending
# parameter is one the assertion depends on; GOOD holds the unused twins.
BAD = [
    pytest.param(
        "  parameter upper = $;\n  p0: assert property (@(posedge clk) a |-> ##[0:upper] b);\n",
        "upper",
        id="dollar-valued-parameter",
    ),
    pytest.param(
        '  parameter string S = "ab";\n' + _use('(S == "ab")'), "S", id="string-parameter"
    ),
    pytest.param("  parameter real R = 1.5;\n" + _use("(R > 1.0)"), "R", id="real-parameter"),
    pytest.param("  parameter type T = logic;\n  T s;\n" + _use("s"), "T", id="type-parameter"),
    pytest.param(
        '  parameter string S = "ab";\n  parameter real R = 2.5;\n'
        + _use('((S == "ab") && (R > 1.0))'),
        "R, S",
        id="two-bad-parameters-sorted",
    ),
    pytest.param("  parameter int \\P-x = 3;\n" + _use("(a == \\P-x )"), "P-x", id="escaped-name"),
]

GOOD = [
    pytest.param(
        "  parameter int N = 3;\n  parameter K = 2;\n"
        "  p0: assert property (@(posedge clk) a |-> ##[N:N+K] b);\n",
        id="integer-parameters",
    ),
    pytest.param("  p0: assert property (@(posedge clk) a |-> ##[0:$] b);\n", id="literal-dollar"),
    pytest.param(_ASSERTION + "  parameter upper = $;\n", id="bad-parameter-after-the-assertion"),
    pytest.param('  parameter string S = "ab";\n' + _ASSERTION, id="unused-string-parameter"),
    pytest.param("  parameter real R = 1.5;\n" + _ASSERTION, id="unused-real-parameter"),
    pytest.param("  parameter type T = logic;\n" + _ASSERTION, id="unused-type-parameter"),
    pytest.param("  parameter upper = $;\n" + _ASSERTION, id="unused-dollar-parameter"),
    pytest.param("  parameter int \\P-x = 3;\n" + _ASSERTION, id="unused-escaped-name"),
]


def _sources(body: str) -> list[tuple[str, str]]:
    return [("v.sv", _HEADER + body + "endmodule\n")]


def _check_mode(body: str) -> list[str]:
    """The messages with which check mode (the conversion alone) rejects the directives."""
    document = export_texts(_sources(body), SlangOptions(top=("p",)))
    conversion = convert(document)
    return [r.message for r in conversion.rejections]


def _generation_errors(body: str) -> list[str]:
    """The error diagnostics of generation; empty when it succeeds."""
    sources = _sources(body)
    document = export_texts(sources, SlangOptions(top=("p",)))
    try:
        rewrite(
            sources,
            frozenset({"v.sv"}),
            syntax_items(document),
            convert(document),
            Severity.ERROR,
        )
    except RewriteError as error:
        return [str(d) for d in error.diagnostics if d.severity is Severity.ERROR]
    return []


@pytest.mark.parametrize(("body", "names"), BAD)
def test_generation_refuses_the_site_with_the_located_message(body: str, names: str) -> None:
    errors = _generation_errors(body)
    line = 2 + body.count("\n") - 1
    expected = f"{names} is visible at this assertion but cannot be compared{_TAIL}"
    assert any(e.startswith(f"v.sv:{line}:3") and expected in e for e in errors), errors


@pytest.mark.parametrize(("body", "names"), BAD)
def test_check_mode_and_generation_refuse_the_same_directive_with_the_same_text(
    body: str, names: str
) -> None:
    expected = f"{names} is visible at this assertion but cannot be compared{_TAIL}"
    assert _check_mode(body) == [expected]
    assert any(expected in e for e in _generation_errors(body))


@pytest.mark.parametrize("body", GOOD)
def test_check_mode_and_generation_both_accept_the_directive(body: str) -> None:
    assert _check_mode(body) == []
    assert _generation_errors(body) == []


def test_integer_parameters_generate_a_monitor() -> None:
    body = (
        "  parameter int N = 3;\n  parameter K = 2;\n"
        "  p0: assert property (@(posedge clk) a |-> ##[N:N+K] b);\n"
    )
    sources = _sources(body)
    document = export_texts(sources, SlangOptions(top=("p",)))
    result = rewrite(
        sources, frozenset({"v.sv"}), syntax_items(document), convert(document), Severity.ERROR
    )
    (edited,) = [f for f in result.files if f.source == "v.sv"]
    assert "assert property" not in edited.text.replace("/*", "").split("*/")[-1]
    assert "p0" in edited.text
