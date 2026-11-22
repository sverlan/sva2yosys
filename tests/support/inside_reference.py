"""A direct evaluator of the set membership operator ``inside`` (IEEE 1800-2017 11.4.13).

Test oracle, written from the clause and independent of the converter's lowering (it shares
no code with ``sva2yosys.slang``): operands are Python integers with a width and a
signedness, the result is computed pattern by pattern.

The rules it implements (two-state model: a value operand has no X or Z bits; an item may
have wildcard bits, written as a mask):

- ``e inside { i1, i2, ... }`` is 1 when ``e`` matches some item, else 0. With two-state
  operands the "X when no item matches and some comparison is X" case cannot arise: the
  only X/Z/``?`` bits are the item's own, and those are wildcards (the right operand of the
  wildcard equality ``==?``, 11.4.6), not unknowns.
- A single (non-range) item matches when ``e ==? item``: the bits of ``e`` and of the item
  are compared at every position where the item bit is not a wildcard.
- A range ``[lo:hi]`` matches when ``lo <= e`` and ``e <= hi``: two comparisons, so a range
  with ``lo > hi`` is empty and matches nothing. A bound ``$`` is the lowest or highest value
  of the type of the left operand (11.4.13), compared like any other bound.
- Every comparison is sized and signed on its own, as an equality or relational operator
  (11.4.5, 11.4.6, 11.6, 11.8): the operands are extended to the wider of the two; the
  comparison is signed only when both operands are signed (otherwise both are unsigned),
  and an operand is sign-extended only when the comparison is signed. A wildcard msb of
  a signed item extends as a wildcard (a z msb extends with z). This is one of the readings
  of 11.4.13, which does not fix the sizing (docs/validation.md, I11); the conformance
  comparisons use it only on uniform lists (one width and signedness), where every reading
  gives the same comparisons. Its results on mixed lists pin this oracle, not the standard.

Contract: ``tests/unit_tests/test_inside_reference.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Operand:
    """A two-state operand: ``value`` is its bit pattern (``0 <= value < 2**width``)."""

    value: int
    width: int
    signed: bool

    def __post_init__(self) -> None:
        if not 0 <= self.value < 1 << self.width:
            raise ValueError("value does not fit the width")


@dataclass(frozen=True)
class ValueItem:
    """A non-range item. ``wildcard`` has a 1 at every bit that is X, Z or ``?``."""

    operand: Operand
    wildcard: int = 0


@dataclass(frozen=True)
class RangeItem:
    """A range item ``[low:high]``; a bound of ``None`` is ``$``, the lowest (as ``low``) or
    highest (as ``high``) value of the type of the left operand."""

    low: Operand | None
    high: Operand | None


@dataclass(frozen=True)
class FillItem:
    """An unbased unsized literal ``'0``, ``'1``, ``'x`` or ``'z`` (``digit`` is that character):
    unsigned, and every bit of the width of its comparison is set to the digit (5.7.1)."""

    digit: str


Item = ValueItem | RangeItem | FillItem


def _context(left: Operand, right: Operand) -> tuple[int, bool]:
    """Width and signedness of a comparison between two operands (11.8.1, 11.8.2)."""
    return max(left.width, right.width), left.signed and right.signed


def _pattern(operand: Operand, width: int, signed: bool) -> int:
    """The bit pattern of ``operand`` extended to ``width``: by sign only in a signed context."""
    if signed and operand.signed and operand.value >> (operand.width - 1) & 1:
        return operand.value | ((1 << width) - 1) & ~((1 << operand.width) - 1)
    return operand.value


def _number(operand: Operand, width: int, signed: bool) -> int:
    """The integer value of ``operand`` in a comparison context of ``width``/``signed``."""
    pattern = _pattern(operand, width, signed)
    if signed and pattern >> (width - 1) & 1:
        return pattern - (1 << width)
    return pattern


def _wildcard_equal(left: Operand, item: ValueItem) -> bool:
    """``left ==? item``: equal at every bit where the item is not a wildcard."""
    width, signed = _context(left, item.operand)
    wildcard = item.wildcard
    if signed and wildcard >> (item.operand.width - 1) & 1:
        wildcard |= ((1 << width) - 1) & ~((1 << item.operand.width) - 1)
    known = ((1 << width) - 1) & ~wildcard
    return _pattern(left, width, signed) & known == _pattern(item.operand, width, signed) & known


def _at_most(low: Operand, high: Operand) -> bool:
    """``low <= high`` as a relational operator."""
    width, signed = _context(low, high)
    return _number(low, width, signed) <= _number(high, width, signed)


def _extreme(left: Operand, *, highest: bool) -> Operand:
    """The lowest or highest value of the type of ``left`` (width and signedness)."""
    width = left.width
    if left.signed:
        return Operand((1 << (width - 1)) - 1 if highest else 1 << (width - 1), width, True)
    return Operand((1 << width) - 1 if highest else 0, width, False)


def _fill(left: Operand, item: FillItem) -> ValueItem:
    """The value item a fill literal is in a comparison with ``left``: one bit that fills the
    comparison's width, which is the width of ``left`` (the literal itself adds none)."""
    ones = (1 << left.width) - 1
    value = ones if item.digit == "1" else 0
    return ValueItem(Operand(value, left.width, False), ones if item.digit in "xz" else 0)


def inside(left: Operand, items: Sequence[Item]) -> bool:
    """``left inside { items }``: whether ``left`` matches some item (11.4.13)."""
    for item in items:
        if isinstance(item, FillItem):
            item = _fill(left, item)
        if isinstance(item, ValueItem):
            if _wildcard_equal(left, item):
                return True
        else:
            low = item.low if item.low is not None else _extreme(left, highest=False)
            high = item.high if item.high is not None else _extreme(left, highest=True)
            if _at_most(low, left) and _at_most(left, high):
                return True
    return False
