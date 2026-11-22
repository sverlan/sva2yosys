"""Functional contract of the parameter dependencies in the converter (CONTRACT.md, point 17).
The expected sets are hand-derived from the text of each case."""

from __future__ import annotations

import pytest

from sva2yosys.ir.directive import uncomparable_message
from sva2yosys.slang.convert import Conversion, convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

_CFG = "typedef struct { bit en; int n; } cfg_t;\n"
_PORTS = "(input logic clk, a, b, input logic [3:0] v)"


def _convert(text: str) -> Conversion:
    return convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))


def _module(body: str, parameters: str = "", ports: str = _PORTS, prefix: str = "") -> str:
    head = f"#({parameters}) " if parameters else ""
    return f"{prefix}module r {head}{ports};\n{body}endmodule\n"


def _message(names: str) -> str:
    return uncomparable_message(tuple(names.split(", ")))


_PLAIN = "  p0: assert property (@(posedge clk) a |-> b);\n"

# --- a non-comparable parameter outside the dependencies is accepted ----------------------

OUTSIDE = [
    pytest.param("  parameter upper = $;\n" + _PLAIN, "", "upper", id="dollar"),
    pytest.param('  parameter string S = "ab";\n' + _PLAIN, "", "S", id="string"),
    pytest.param("  parameter real R = 1.5;\n" + _PLAIN, "", "R", id="real"),
    pytest.param(
        "  logic unread;\n" + _PLAIN, "parameter type T = logic", "T", id="unread-type-parameter"
    ),
    pytest.param(
        "  typedef T td_t;\n  td_t unread;\n" + _PLAIN,
        "parameter type T = logic",
        "T",
        id="type-parameter-behind-an-unread-typedef",
    ),
]


@pytest.mark.parametrize(("body", "parameters", "name"), OUTSIDE)
def test_a_parameter_the_assertion_cannot_depend_on_is_accepted_and_unchanged(
    body: str, parameters: str, name: str
) -> None:
    conversion = _convert(_module(body, parameters))
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.name == "p0"
    assert directive.dependencies == frozenset()
    # The directive keeps the parameter among the visible ones: only the check ignores it.
    assert name in {n for n, _ in directive.parameters}


def test_a_struct_parameter_used_only_in_a_generate_condition_is_accepted() -> None:
    """The AXI checker case: cfg decides whether the assertion exists, nothing else."""
    text = _module(
        "  if (cfg.en) begin : g\n" + _PLAIN + "  end\n",
        "parameter cfg_t cfg = '{1'b1, 4}",
        prefix=_CFG,
    )
    conversion = _convert(text)
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.dependencies == frozenset()
    assert {n for n, _ in directive.parameters} == {"cfg"}


def test_integer_dependencies_are_recorded_beside_an_unused_uncomparable_parameter() -> None:
    text = _module(
        "  p0: assert property (@(posedge clk) a |-> ##N b);\n",
        "parameter int N = 3, parameter int M = 4, parameter real R = 1.5",
    )
    conversion = _convert(text)
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.dependencies == frozenset({"N"})


# --- the same parameter inside the dependencies is rejected --------------------------------

_DELAY = "  p0: assert property (@(posedge clk) a |-> ##(cfg.n) b);\n"

INSIDE = [
    pytest.param(
        _DELAY, "parameter cfg_t cfg = '{1'b1, 4}", _CFG, _PORTS, "cfg", id="struct-in-a-delay"
    ),
    pytest.param(
        "  p0: assert property (@(posedge clk) v[0] |-> b);\n",
        "parameter cfg_t cfg = '{1'b1, 4}",
        _CFG,
        "(input logic clk, a, b, input logic [cfg.n-1:0] v)",
        "cfg",
        id="struct-in-the-width-of-a-read-port",
    ),
    pytest.param(
        "  p0: assert property (@(posedge clk) s |-> b);\n",
        "parameter type T = logic",
        "",
        "(input logic clk, a, b, input T s)",
        "T",
        id="type-parameter-of-a-read-port",
    ),
    pytest.param(
        "  typedef T td_t;\n  td_t s;\n  p0: assert property (@(posedge clk) s |-> b);\n",
        "parameter type T = logic",
        "",
        _PORTS,
        "T",
        id="type-parameter-behind-a-typedef-of-a-read-variable",
    ),
    pytest.param(
        "  parameter upper = $;\n  p0: assert property (@(posedge clk) a |-> ##[0:upper] b);\n",
        "",
        "",
        _PORTS,
        "upper",
        id="dollar-in-a-range",
    ),
    pytest.param(
        "  T s;\n  p0: assert property (@(posedge clk) s |-> ##(cfg.n) b);\n",
        "parameter type T = logic, parameter cfg_t cfg = '{1'b1, 4}",
        _CFG,
        _PORTS,
        "T, cfg",
        id="two-names-sorted",
    ),
]


@pytest.mark.parametrize(("body", "parameters", "prefix", "ports", "names"), INSIDE)
def test_a_parameter_the_assertion_depends_on_is_rejected_with_the_exact_message(
    body: str, parameters: str, prefix: str, ports: str, names: str
) -> None:
    conversion = _convert(_module(body, parameters, ports, prefix))
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert (rejection.scope, rejection.name) == ("r", "p0")
    assert rejection.message == _message(names)
    assert rejection.source is not None
    assert rejection.source.file == "r.sv"


def test_only_the_assertion_that_depends_on_the_parameter_is_rejected() -> None:
    """One module, one struct parameter: the generate-guarded assertion is kept, the other not."""
    text = _module(
        "  if (cfg.en) begin : g\n"
        "    p0: assert property (@(posedge clk) a |-> b);\n"
        "    p1: assert property (@(posedge clk) a |-> ##(cfg.n) b);\n"
        "  end\n",
        "parameter cfg_t cfg = '{1'b1, 4}",
        prefix=_CFG,
    )
    conversion = _convert(text)
    (directive,) = conversion.directives
    assert directive.name == "p0"
    assert directive.dependencies == frozenset()
    (rejection,) = conversion.rejections
    assert rejection.name == "p1"
    assert rejection.message == _message("cfg")


# --- unknown dependencies count every visible parameter ----------------------------------

_UNIT = "parameter int UP = 2;\n"
_UNKNOWN = "  p0: assert property (@(posedge clk) a |-> ##UP b);\n"


def test_unknown_dependencies_reject_every_visible_uncomparable_parameter() -> None:
    """A compilation-unit parameter is outside the visible names (unknown)."""
    conversion = _convert(_module("  parameter real R = 1.5;\n" + _UNKNOWN, prefix=_UNIT))
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert rejection.message == _message("R")


def test_unknown_dependencies_are_kept_on_an_accepted_directive() -> None:
    conversion = _convert(_module("  parameter int N = 1;\n" + _UNKNOWN, prefix=_UNIT))
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.dependencies is None


# --- skipped X-check directives -----------------------------------------------------------

_XCHECK = "  p0: assert property (@(posedge clk) !$isunknown(v));\n"


def test_a_skipped_x_check_with_an_unrelated_uncomparable_parameter_is_skipped() -> None:
    conversion = _convert(_module("  parameter real R = 1.5;\n" + _XCHECK))
    assert conversion.rejections == ()
    assert conversion.directives == ()
    (skipped,) = conversion.skipped
    assert skipped.name == "p0"
    assert skipped.dependencies == frozenset()


def test_a_skipped_x_check_records_the_integer_width_parameter_it_reads() -> None:
    text = _module(
        "  parameter real R = 1.5;\n" + _XCHECK,
        "parameter int W = 4",
        "(input logic clk, a, b, input logic [W-1:0] v)",
    )
    conversion = _convert(text)
    assert conversion.rejections == ()
    (skipped,) = conversion.skipped
    assert skipped.dependencies == frozenset({"W"})


def test_a_skipped_x_check_reading_a_type_parameter_signal_is_rejected() -> None:
    text = _module(_XCHECK, "parameter type T = logic [3:0]", "(input logic clk, a, b, input T v)")
    conversion = _convert(text)
    assert conversion.skipped == ()
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert rejection.message == _message("T")


# --- an escaped parameter name is matched by name (review finding) -------------------


def test_an_escaped_parameter_used_as_a_delay_is_a_dependency_and_is_rejected() -> None:
    r"""`##(\P-x )`: the folded constant vanishes from the bound tree and a lookup by spelling
    cannot find an escaped name, so the name is matched against the visible parameters; the
    rewrite cannot write a condition on it, hence the rejection (hand-derived)."""
    conversion = _convert(
        "module r #(parameter int \\P-x = 3)(input logic clk, a, b);\n"
        "  p0: assert property (@(posedge clk) a |-> ##(\\P-x ) b);\n"
        "endmodule\n"
    )
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert rejection.message == _message("P-x")


def test_an_escaped_parameter_setting_a_width_is_a_dependency_and_is_rejected() -> None:
    conversion = _convert(
        "module r #(parameter int \\W-1 = 4)(input logic clk);\n"
        "  logic [\\W-1 :0] s;\n"
        "  p0: assert property (@(posedge clk) s != 0);\n"
        "endmodule\n"
    )
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert rejection.message == _message("W-1")


def test_an_unused_escaped_parameter_does_not_matter() -> None:
    conversion = _convert(
        "module r #(parameter int \\P-x = 3)(input logic clk, a, b);\n"
        "  p0: assert property (@(posedge clk) a |-> b);\n"
        "endmodule\n"
    )
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.dependencies == frozenset()


# --- a parameter the assertion depends on that its position hides (found in a review) -------


def _hidden_message(name: str) -> str:
    return (
        f"{name} is used by this assertion but hidden at its position by another declaration "
        "of the same name; the monitor of each elaboration is selected by comparing the "
        "parameters visible at the assertion, so this is not supported"
    )


_HIDING = (
    "  property q; a |-> ##D b; endproperty\n"
    "  if (1) begin : g\n"
    "    localparam int D = 1;\n"
    "    p0: assert property (@(posedge clk) q);\n"
    "  end\n"
)


def test_a_dependency_hidden_by_an_inner_declaration_is_rejected_with_its_own_message() -> None:
    """The property uses the module parameter D, overridable at instantiation; the block's own D
    hides it at the assertion, so no comparison of the visible parameters could key the monitor
    on it (an instance with D=3 would select the monitor of D=2)."""
    conversion = _convert(_module(_HIDING, "parameter int D = 2"))
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert rejection.name == "p0"
    assert rejection.message == _hidden_message("D")


def test_the_same_property_outside_the_hiding_block_is_accepted() -> None:
    body = "  property q; a |-> ##D b; endproperty\n  p0: assert property (@(posedge clk) q);\n"
    conversion = _convert(_module(body, "parameter int D = 2"))
    assert conversion.rejections == ()
    (directive,) = conversion.directives
    assert directive.dependencies == frozenset({"D"})
