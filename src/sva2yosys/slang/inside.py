"""Helpers of the ``inside`` lowering (IEEE 1800-2017 11.4.13), on the exporter's JSON.

Slang binds the left operand and every item to one common type (the widest width, unsigned
unless every operand is signed) and writes the conversions it inserts. 11.4.13 does not say
whether the comparisons are sized that way or each on its own, so the lowering accepts only
uniform lists, where both readings agree. This module answers that question and parses the
wildcard bits of constant items; it holds no logic of the converter (rejections are messages
returned to ``convert.py``).

Contract (``tests/functional_tests/slang_convert/CONTRACT.md``, point 16):

- :func:`natural_type` is the width and signedness an operand has when it is sized by itself:
  the type below the conversions that only change it to the context type (kinds
  ``Propagated`` and ``Implicit``); for ``+ - * / % & | ^ ~^`` and unary ``+ - ~`` the
  combination of the operands' natural types; for shifts the left operand's; for
  comparisons, logical operators, reductions and ``!`` one unsigned bit; for a conditional
  the combination of the two branches; any other node (names, literals, selects,
  concatenations, calls, explicit casts) its own type.
- :func:`dollar_problem` says whether a ``$`` bound may be dropped (11.4.13).
- :func:`type_mismatch` is ``None`` when ``other`` (an item or a range bound) has the natural
  type of ``left``: one width and one signedness for the whole list. 11.4.13 does not say
  whether each comparison is sized on its own or all share one context, and tools differ;
  for a uniform list every reading gives the same comparisons. Otherwise a message naming
  both types.
- :func:`wildcard_constant` reads a constant such as ``32'b1z0z`` and returns its bit pattern
  and wildcard mask (X, Z and ``?`` bits are wildcards), or ``None`` when it has none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sva2yosys.slang.export import JsonObject

_ARITHMETIC = frozenset(
    {
        "Add",
        "Subtract",
        "Multiply",
        "Divide",
        "Mod",
        "BinaryAnd",
        "BinaryOr",
        "BinaryXor",
        "BinaryXnor",
    }
)
_SHIFTS = frozenset(
    {
        "LogicalShiftLeft",
        "LogicalShiftRight",
        "ArithmeticShiftLeft",
        "ArithmeticShiftRight",
        "Power",
    }
)
_UNARY_ARITHMETIC = frozenset({"Plus", "Minus", "BitwiseNot"})
_KEPT = frozenset({"Propagated", "Implicit"})
"""Conversion kinds that only move an operand to its context's type."""

_SIZED = re.compile(r"(\d+)'(s)?([bohBOH])([0-9a-fA-F_xXzZ?]+)")
_BITS = {"b": 1, "o": 3, "h": 4}


@dataclass(frozen=True)
class WildcardConstant:
    """A constant with wildcard bits: ``pattern`` has 0 at every wildcard bit."""

    pattern: int
    wildcard: int
    width: int
    signed: bool


def _own(node: JsonObject) -> tuple[int, bool]:
    description = node.get("sva2yosys_integral")
    if not isinstance(description, dict):
        raise ValueError("operand without an integral type")
    width, signed = description.get("width"), description.get("signed")
    if not isinstance(width, int) or not isinstance(signed, bool):
        raise ValueError("malformed 'sva2yosys_integral'")
    return width, signed


def _child(node: JsonObject, key: str) -> JsonObject:
    child = node.get(key)
    if not isinstance(child, dict):
        raise ValueError(f"missing '{key}'")
    return child


def own_type(node: JsonObject) -> tuple[int, bool]:
    """The width and signedness of ``node`` as Slang typed it.

    Raises:
        ValueError: The node has no integral type.
    """
    return _own(node)


def natural_type(node: JsonObject) -> tuple[int, bool]:
    """The width and signedness of ``node`` sized by itself (module docstring).

    Raises:
        ValueError: A node on the way has no integral type.
    """
    kind = node.get("kind")
    if kind == "UnbasedUnsizedIntegerLiteral":
        return 1, False  # 5.7.1: one bit that fills its context
    if kind == "Conversion":
        if node.get("sva2yosys_conversionKind") in _KEPT:
            return natural_type(_child(node, "operand"))
        return _own(node)
    if kind == "UnaryOp":
        if node.get("op") in _UNARY_ARITHMETIC:
            return natural_type(_child(node, "operand"))
        return 1, False
    if kind == "BinaryOp":
        op = node.get("op")
        if op in _ARITHMETIC:
            (lw, ls), (rw, rs) = (natural_type(_child(node, k)) for k in ("left", "right"))
            return max(lw, rw), ls and rs
        if op in _SHIFTS:
            return natural_type(_child(node, "left"))
        return 1, False
    if kind == "ConditionalOp":
        (lw, ls), (rw, rs) = (natural_type(_child(node, k)) for k in ("left", "right"))
        return max(lw, rw), ls and rs
    return _own(node)


def _is_context_sized(node: JsonObject) -> bool:
    """Whether the value of ``node`` depends on the width of its context."""
    kind, op = node.get("kind"), node.get("op")
    return (
        kind == "UnbasedUnsizedIntegerLiteral"  # '0, '1, 'x, 'z fill the width of the context
        or (kind == "UnaryOp" and op in _UNARY_ARITHMETIC)
        or (kind == "BinaryOp" and (op in _ARITHMETIC or op in _SHIFTS))
        or kind == "ConditionalOp"
    )


def _describe(natural: tuple[int, bool]) -> str:
    width, signed = natural
    return f"{width}-bit {'signed' if signed else 'unsigned'}"


def type_mismatch(left: JsonObject, other: JsonObject) -> str | None:
    """Why ``other`` breaks the uniformity of the list, or ``None`` (module docstring).

    Raises:
        ValueError: An operand has no integral type.
    """
    natural_left, natural_other = natural_type(left), natural_type(other)
    if natural_other != natural_left:
        return (
            f"its operands are not all of one width and signedness ({_describe(natural_left)}"
            f" left operand, {_describe(natural_other)} item): IEEE 1800-2017 11.4.13 does not "
            "say whether each comparison is sized on its own or all share one context, and "
            "tools size such lists differently; use operands of one type (e.g. 4'd1 rather "
            "than 1 for a 4-bit operand)"
        )
    if _own(other) != _own(left):  # Slang's common type is then the natural one
        return "the operands of the comparison have different types after Slang's conversions"
    return None


def dollar_problem(left: JsonObject) -> str | None:
    """Why a ``$`` bound cannot be dropped from a comparison with ``left``, or ``None``.

    ``$`` is the lowest or highest value of the left operand's type (11.4.13), so the
    comparison with it holds for every value of ``left``, provided the value is not changed
    by the comparison's context: the operand is a leaf (its value lies in its type, which an
    arithmetic operand evaluated in a wider context would not) and Slang's common type has
    the operand's own signedness (else the lowest value of a signed type would be compared as
    a large unsigned number).
    """
    try:
        _, natural_signed = natural_type(left)
        _, common_signed = _own(left)
    except ValueError as error:
        return str(error)
    if _is_context_sized(left):
        return "the left operand is an arithmetic expression, whose type is set by its context"
    if natural_signed != common_signed:
        return "the other items of the set change the signedness of the comparison"
    return None


def wildcard_constant(text: str) -> WildcardConstant | None:
    """The bit pattern and wildcard mask of the sized constant ``text``, or ``None`` when it
    has no X, Z or ``?`` bit (or is not a sized binary, octal or hexadecimal constant)."""
    match = _SIZED.fullmatch(text)
    if match is None or not re.search(r"[xXzZ?]", match.group(4)):
        return None
    width, signed = int(match.group(1)), match.group(2) is not None
    base = match.group(3).lower()
    step = _BITS[base]
    digits = match.group(4).replace("_", "")
    pattern = wildcard = 0
    for digit in digits:
        pattern <<= step
        wildcard <<= step
        if digit in "xXzZ?":
            wildcard |= (1 << step) - 1
        else:
            pattern |= int(digit, 16)
    mask = (1 << width) - 1
    if digits[0] in "xXzZ?":  # IEEE 1800-2017 5.7.1: a leading x or z digit extends the value
        wildcard |= mask & ~((1 << (step * len(digits))) - 1)
    return WildcardConstant(pattern & mask & ~wildcard, wildcard & mask, width, signed)
