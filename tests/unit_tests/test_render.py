"""Comment text of generated code (docstring contract of sva2yosys.codegen.render)."""

from __future__ import annotations

import pytest

from sva2yosys.codegen.render import block_comment, fold_whitespace, render_expr
from sva2yosys.ir.expr import (
    BIT,
    Binary,
    BinaryOp,
    Const,
    FirstSample,
    Sampled,
    SampledFunction,
    Signal,
    Unary,
    UnaryOp,
    ValueType,
)


def test_whitespace_runs_including_newlines_fold_to_one_space() -> None:
    assert fold_whitespace("  a\r\n\t b\n\n c  ") == "a b c"


def test_block_comment_cannot_end_early() -> None:
    comment = block_comment("a /* b */ c\n*/")
    assert comment == "/* a /* b * / c * / */"
    assert comment.count("*/") == 1 and comment.endswith("*/")
    assert "\n" not in comment


def test_expressions_render_with_signal_paths_as_names() -> None:
    a, b = Signal("a", BIT), Signal("top.b", ValueType(4, True))
    assert render_expr(a) == "a"
    assert render_expr(Unary(UnaryOp.LOGICAL_NOT, a, BIT)) == "(!a)"
    both = Binary(BinaryOp.LOGICAL_OR, a, Const(10, ValueType(4)), BIT)
    assert render_expr(both) == "(a || 4'ha)"
    assert render_expr(Const(255, ValueType(8, True))) == "8'shff"
    assert render_expr(Sampled(SampledFunction.PAST, b, BIT, 2)) == "$past(top.b, 2)"
    assert render_expr(Sampled(SampledFunction.ROSE, a, BIT)) == "$rose(a)"


def test_an_expression_without_source_text_is_refused() -> None:
    with pytest.raises(ValueError):
        render_expr(FirstSample(BIT))
