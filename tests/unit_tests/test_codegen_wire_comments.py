"""Condition wires name their source (generated_monitors contract point 26)."""

from __future__ import annotations

import re

from sva2yosys.codegen.systemverilog import monitor_module
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Binary, BinaryOp, FirstSample, Signal, Unary, UnaryOp
from sva2yosys.ir.property import Implication, SequenceProperty
from sva2yosys.ir.sequence import Bool, Delay, SeqConcat
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.compile import compile_directive

A, B = Signal("m.a", BIT), Signal("m.b", BIT)


def _text(antecedent: Bool, consequent: Bool) -> str:
    prop = Implication(antecedent, SequenceProperty(consequent), True)
    clock = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
    directive = Directive(
        DirectiveKind.ASSERT, "p", "m", clock, None, prop, SourceRange("t", 1, 1, 1, 2)
    )
    return monitor_module(compile_directive(directive), "mon")


def _wire_comments(text: str) -> dict[str, str]:
    return dict(re.findall(r"assign (e\d+) = [^;]*;  // (.*)", text))


def test_each_condition_wire_ends_with_its_expression() -> None:
    not_a = Unary(UnaryOp.LOGICAL_NOT, A, BIT)
    both = Binary(BinaryOp.LOGICAL_AND, B, not_a, BIT)
    text = _text(Bool(A), Bool(both))
    comments = _wire_comments(text)
    assert set(comments.values()) >= {"m.a", "m.b", "(!m.a)", "(m.b && (!m.a))"}
    assigned = re.findall(r"^    assign (e\d+) = ", text, re.M)
    assert sorted(comments) == sorted(assigned)


def test_the_first_sample_condition_is_named() -> None:
    antecedent = SeqConcat(Bool(FirstSample(BIT)), Delay(0, 0), Bool(A))
    text = _text(antecedent, Bool(B))  # type: ignore[arg-type]
    assert "assign e0 = !valid;  // first sample" in text


def test_a_long_expression_is_cut_to_eighty_characters() -> None:
    chain: Signal | Binary = A
    for index in range(40):
        chain = Binary(BinaryOp.LOGICAL_OR, chain, Signal(f"m.long_signal_{index}", BIT), BIT)
    comments = _wire_comments(_text(Bool(A), Bool(chain)))
    longest = max(comments.values(), key=len)
    assert len(longest) == 80
    assert longest.endswith("...")
    assert all(len(text) <= 80 and "\n" not in text for text in comments.values())
