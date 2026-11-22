"""Unit contract of ``sva2yosys.optimize.forms`` (its module docstring)."""

from __future__ import annotations

import pytest

from sva2yosys.optimize.forms import (
    Form,
    Scope,
    Strategy,
    Window,
    choose,
    scope,
    state_bits,
    window,
)
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import compile_directive
from sva2yosys.temporal.machine import Machine, machine_size


def _machine(prop: str, kind: str = "assert") -> Machine:
    text = (
        "module m(input logic clk, a, b, c);\n"
        f"  p: {kind} property (@(posedge clk) {prop});\nendmodule\n"
    )
    (directive,) = convert(export_texts([("m.sv", text)], SlangOptions(top=("m",)))).directives
    return compile_directive(directive)


def _b(machine: Machine) -> int:
    return [str(c) for c in machine.conditions].index(
        next(str(c) for c in machine.conditions if "m.b" in str(c))
    )


@pytest.mark.parametrize(
    ("prop", "bounds"),
    [
        ("a |-> ##[1:8] b", (1, 8)),
        ("a |=> ##[2:5] b", (2, 5)),  # the trigger is a ##1 1: one tick later
        ("##[1:4] b", (1, 4)),
        ("a ##1 c |-> ##3 b", (3, 3)),
    ],
)
def test_window_pattern(prop: str, bounds: tuple[int, int]) -> None:
    machine = _machine(prop)
    assert window(machine) == Window(*bounds, _b(machine))


@pytest.mark.parametrize(
    ("prop", "kind"),
    [
        ("a |-> ##[0:3] b", "assert"),  # the window starts at the trigger
        ("a |-> b", "assert"),
        ("a |=> b", "assert"),  # (a ##1 1) |-> b: no window after the trigger
        ("a |-> b ##1 c", "assert"),
        ("a |-> ##[1:3] b ##1 c", "assert"),
        ("a |-> ##[1:3] 1'b1", "assert"),  # a constant condition
        ("a ##[1:3] b", "cover"),  # covers consume every success
    ],
)
def test_no_window(prop: str, kind: str) -> None:
    machine = _machine(prop, kind)
    assert window(machine) is None
    assert state_bits(machine, Form.WINDOW) is None


def test_state_bits() -> None:
    machine = _machine("a |-> ##[1:32] b")
    assert state_bits(machine, Form.DIRECT) == machine_size(machine).temporal_bits == 63
    assert state_bits(machine, Form.WINDOW) == 1 + 6  # last trigger + a counter up to 32
    assert state_bits(machine, Form.HISTORY) == 32 * 2  # triggers + history of b
    chained = _machine("a |-> ##[1:8] b ##[1:8] c")
    assert state_bits(chained, Form.HISTORY) == 16 * 3
    assert state_bits(_machine("a |-> b"), Form.HISTORY) is None  # no slots


def test_scopes() -> None:
    assert scope(Form.DIRECT) is Scope.EVERY_SAMPLE
    assert scope(Form.HISTORY) is Scope.EVERY_SAMPLE
    assert scope(Form.WINDOW) is Scope.UNTIL_FIRST_FAILURE


def test_choice() -> None:
    wide = _machine("a |-> ##[1:32] b")
    chained = _machine("a |-> ##[1:8] b ##[1:8] c ##[1:8] a")
    short = _machine("a |-> b[*1:3] ##1 c")
    assert choose(wide, Strategy.DIRECT) is Form.DIRECT  # the default: no optimisation
    assert choose(wide, Strategy.AUTO) is Form.WINDOW
    # history has fewer registers here, but more logic: auto never picks it
    assert state_bits(chained, Form.HISTORY) < state_bits(chained, Form.DIRECT)  # type: ignore[operator]
    assert choose(chained, Strategy.AUTO) is Form.DIRECT
    assert choose(chained, Strategy.HISTORY) is Form.HISTORY
    assert choose(short, Strategy.AUTO) is Form.DIRECT  # nothing smaller
    small = _machine("a |-> ##[1:8] b")  # 15 bits direct, 5 window: not worth it
    assert choose(small, Strategy.AUTO) is Form.DIRECT
    assert choose(small, Strategy.WINDOW) is Form.WINDOW
    assert choose(_machine("a |-> ##[1:10] b"), Strategy.AUTO) is Form.WINDOW  # 19 >= 19
    assert choose(wide, Strategy.HISTORY) is Form.HISTORY  # forced, although larger
    assert choose(short, Strategy.WINDOW) is Form.DIRECT  # forced but not applicable


# --- unbounded antecedents: the direct form only ------------------------------------


@pytest.mark.parametrize(
    "prop",
    [
        "a ##[1:$] b |-> ##[1:8] c",
        "a[*1:$] ##1 b |=> ##[2:5] c",
        "b[->2] |-> ##3 c",
        "b[=2] ##1 a |-> ##[1:4] c",
    ],
)
def test_an_unbounded_antecedent_has_the_direct_form_only(prop: str) -> None:
    machine = _machine(prop)
    assert state_bits(machine, Form.WINDOW) is None
    assert state_bits(machine, Form.HISTORY) is None
    assert state_bits(machine, Form.DIRECT) is not None
    for strategy in Strategy:
        assert choose(machine, strategy) is Form.DIRECT
    # the same consequent behind a bounded antecedent keeps its window
    bounded = _machine(prop.replace("##[1:$]", "##[1:3]").split("|")[0] + "|-> ##[1:8] c")
    assert window(bounded) is not None


def test_forcing_an_optimised_form_on_an_unbounded_antecedent_says_why() -> None:
    from sva2yosys.codegen.systemverilog import monitor_module

    machine = _machine("a ##[1:$] b |-> ##[1:8] c")
    for form in (Form.WINDOW, Form.HISTORY):
        with pytest.raises(ValueError, match="unbounded antecedent"):
            monitor_module(machine, "m", form)
