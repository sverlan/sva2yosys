"""Human-readable text for the comments of generated code (never for semantics).

- :func:`fold_whitespace`: the text of an assertion as written, on one line.
- :func:`block_comment`: a ``/* ... */`` comment that cannot end early.
- :func:`render_expr`: an IR expression as SystemVerilog-like text, fully parenthesised,
  for the ``// disable iff:`` line of a monitor header. It is total over the expression
  nodes; a node that cannot appear in a condition (``FirstSample``) raises ``ValueError``
  instead of printing something wrong.
"""

from __future__ import annotations

import re

from sva2yosys.ir.expr import (
    Binary,
    BitSelect,
    Concat,
    Conditional,
    Const,
    Convert,
    Expr,
    FirstSample,
    RangeSelect,
    Replicate,
    Sampled,
    SampledFunction,
    Signal,
    Unary,
)

_WHITESPACE = re.compile(r"\s+")


def fold_whitespace(text: str) -> str:
    """``text`` with every run of whitespace (newlines included) folded to one space, and
    no leading or trailing whitespace."""
    return _WHITESPACE.sub(" ", text).strip()


def block_comment(text: str) -> str:
    """``/* text */`` on one line: whitespace folded, and every ``*/`` inside ``text``
    written ``* /`` so that it cannot end the comment."""
    return f"/* {fold_whitespace(text).replace('*/', '* /')} */"


def render_expr(expr: Expr) -> str:
    """``expr`` as text; signals are named by their ``path``."""
    match expr:
        case Signal(path=path):
            return path
        case Const(value=value, type=value_type):
            signed = "s" if value_type.signed else ""
            return f"{value_type.width}'{signed}h{value:x}"
        case Unary(op=op, operand=operand):
            return f"({op.value}{render_expr(operand)})"
        case Binary(op=op, left=left, right=right):
            return f"({render_expr(left)} {op.value} {render_expr(right)})"
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            return f"({render_expr(condition)} ? {render_expr(then)} : {render_expr(otherwise)})"
        case Convert(operand=operand, type=value_type):
            return f"{value_type.width}'({render_expr(operand)})"
        case BitSelect(value=inner, index=index):
            return f"{render_expr(inner)}[{index}]"
        case RangeSelect(value=inner, msb=msb, lsb=lsb):
            return f"{render_expr(inner)}[{msb}:{lsb}]"
        case Concat(parts=parts):
            return "{" + ", ".join(render_expr(part) for part in parts) + "}"
        case Replicate(count=count, value=inner):
            return f"{{{count}{{{render_expr(inner)}}}}}"
        case Sampled(function=function, operand=operand, depth=depth):
            if function is SampledFunction.PAST and depth != 1:
                return f"{function.value}({render_expr(operand)}, {depth})"
            return f"{function.value}({render_expr(operand)})"
        case FirstSample():
            raise ValueError("the first-sample condition has no source text")
