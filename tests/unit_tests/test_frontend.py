"""Contract of passes.frontend (the Yosys frontend option)."""

from __future__ import annotations

import pytest

from sva2yosys.passes.frontend import LIVENESS_PROBLEM, YosysFrontend, frontend_problem
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import Monitor, compile_monitors

NL = chr(10)


def _monitors(prop: str, kind: str = "assert") -> tuple[Monitor, ...]:
    text = NL.join(
        [
            "module m(input logic clk, a, b);",
            f"  p: {kind} property (@(posedge clk) {prop});",
            "endmodule",
            "",
        ]
    )
    document = export_texts([("m.sv", text)], SlangOptions(top=("m",)))
    (directive,) = convert(document).directives
    return compile_monitors(directive)


@pytest.mark.parametrize(
    "prop", ["a |-> ##1 b", "a |-> s_eventually b", "a |-> ##[1:$] b", "not (a until b)"]
)
def test_the_verilog_frontend_accepts_everything(prop: str) -> None:
    assert frontend_problem(_monitors(prop), YosysFrontend.VERILOG) is None


@pytest.mark.parametrize("prop", ["a |-> ##1 b", "a |-> ##[1:$] b", "a ##1 b"])
def test_the_slang_frontend_accepts_a_directive_without_a_liveness_part(prop: str) -> None:
    assert frontend_problem(_monitors(prop), YosysFrontend.SLANG) is None


@pytest.mark.parametrize(
    "prop", ["a |-> s_eventually b", "a |-> strong(##[1:$] b)", "not (a until b)"]
)
def test_the_slang_frontend_refuses_a_liveness_part(prop: str) -> None:
    assert frontend_problem(_monitors(prop), YosysFrontend.SLANG) == LIVENESS_PROBLEM


def test_the_message_names_the_option_the_frontend_and_the_way_out() -> None:
    assert "--yosys-frontend slang" in LIVENESS_PROBLEM
    assert "read_slang" in LIVENESS_PROBLEM
    assert "--yosys-frontend verilog" in LIVENESS_PROBLEM


def test_a_fairness_assumption_is_a_liveness_part() -> None:
    monitors = _monitors("a |-> s_eventually b", "assume")
    assert frontend_problem(monitors, YosysFrontend.SLANG) == LIVENESS_PROBLEM


def test_the_values_are_the_option_spellings() -> None:
    assert [f.value for f in YosysFrontend] == ["verilog", "slang"]
