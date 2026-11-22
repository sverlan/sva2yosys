"""Value expressions over sampled design signals (two-state bit vectors).

Every node carries its result :class:`ValueType`, as determined by Slang's
elaboration (operands are already converted to the operation's type by explicit
:class:`Convert` nodes), so evaluation never re-derives SystemVerilog sizing rules.
Values are unsigned bit patterns of ``type.width`` bits; ``type.signed`` only changes
how comparisons, division, arithmetic shifts and widening interpret them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class ValueType:
    """A packed two-state integral type."""

    width: int
    signed: bool = False


BIT = ValueType(1)
"""The type of Boolean results (1 bit, unsigned)."""


class UnaryOp(Enum):
    """Unary operators (IEEE 1800 §11.4)."""

    PLUS = "+"
    MINUS = "-"
    BITWISE_NOT = "~"
    LOGICAL_NOT = "!"
    REDUCE_AND = "&"
    REDUCE_OR = "|"
    REDUCE_XOR = "^"
    REDUCE_NAND = "~&"
    REDUCE_NOR = "~|"
    REDUCE_XNOR = "~^"


class BinaryOp(Enum):
    """Binary operators (IEEE 1800 §11.4)."""

    ADD = "+"
    SUBTRACT = "-"
    MULTIPLY = "*"
    DIVIDE = "/"
    MOD = "%"
    BITWISE_AND = "&"
    BITWISE_OR = "|"
    BITWISE_XOR = "^"
    BITWISE_XNOR = "~^"
    EQUALITY = "=="
    INEQUALITY = "!="
    CASE_EQUALITY = "==="
    CASE_INEQUALITY = "!=="
    LESS_THAN = "<"
    LESS_THAN_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_THAN_EQUAL = ">="
    LOGICAL_AND = "&&"
    LOGICAL_OR = "||"
    LOGICAL_IMPLICATION = "->"
    LOGICAL_EQUIVALENCE = "<->"
    LOGICAL_SHIFT_LEFT = "<<"
    LOGICAL_SHIFT_RIGHT = ">>"
    ARITHMETIC_SHIFT_LEFT = "<<<"
    ARITHMETIC_SHIFT_RIGHT = ">>>"


class SampledFunction(Enum):
    """Sampled-value functions (IEEE 1800 §16.9.3), relative to the property's clock."""

    PAST = "$past"
    ROSE = "$rose"
    FELL = "$fell"
    STABLE = "$stable"
    CHANGED = "$changed"


class SampledDefault(Enum):
    """The default sampled value of a signal before the first clock tick (IEEE 1800-2017
    §16.5.1): the value ``$past``, ``$rose``, ``$fell``, ``$stable`` and ``$changed`` see
    there.

    ``X``: all bits X (a 4-state net, or a 4-state variable without a declaration
    initializer). ``KNOWN``: a known value (the initializer, or the 0 default of a 2-state
    type).
    """

    X = "x"
    KNOWN = "known"


@dataclass(frozen=True)
class Signal:
    """A design signal, identified by its elaborated hierarchical path.

    ``default`` is the signal's default sampled value class (§16.5.1); the converter sets
    it from the exporter's ``sva2yosys_sampledDefault``.
    """

    path: str
    type: ValueType
    default: SampledDefault = SampledDefault.X


@dataclass(frozen=True)
class Const:
    """A constant; ``value`` is the bit pattern, ``0 <= value < 2**type.width``."""

    value: int
    type: ValueType


@dataclass(frozen=True)
class Unary:
    op: UnaryOp
    operand: Expr
    type: ValueType


@dataclass(frozen=True)
class Binary:
    op: BinaryOp
    left: Expr
    right: Expr
    type: ValueType


@dataclass(frozen=True)
class Conditional:
    """``condition ? then : otherwise`` (condition is true when non-zero)."""

    condition: Expr
    then: Expr
    otherwise: Expr
    type: ValueType


@dataclass(frozen=True)
class Convert:
    """Resize ``operand`` to ``type``: truncate, or extend by sign when the operand is signed."""

    operand: Expr
    type: ValueType


@dataclass(frozen=True)
class BitSelect:
    """``value[index]`` with a constant ``index`` counted from bit 0 of ``value``.

    Variable indices are not represented: an out-of-range read is X in SystemVerilog
    and unconstrained in Yosys, so they are rejected until that is modelled.
    """

    value: Expr
    index: int
    type: ValueType


@dataclass(frozen=True)
class RangeSelect:
    """``value[msb:lsb]`` with constant bounds counted from bit 0 of ``value``."""

    value: Expr
    msb: int
    lsb: int
    type: ValueType


@dataclass(frozen=True)
class Concat:
    """``{parts[0], parts[1], ...}``: ``parts[0]`` is the most significant."""

    parts: tuple[Expr, ...]
    type: ValueType


@dataclass(frozen=True)
class Replicate:
    """``{count{value}}``."""

    count: int
    value: Expr
    type: ValueType


@dataclass(frozen=True)
class Sampled:
    """A sampled-value function applied to ``operand`` (whose own clock is the property's).

    ``depth`` is the number of samples ``$past`` looks back (``$past(e, depth)``); it is
    1 for the other functions.
    """

    function: SampledFunction
    operand: Expr
    type: ValueType
    depth: int = 1


@dataclass(frozen=True)
class FirstSample:
    """1 at the first tick of the property's clock, 0 at every later tick (one bit,
    unsigned).

    The tick is the one at which ``$past`` and the edge functions have no history yet
    (docs/semantics.md §8); ``disable iff`` does not change it. It is not written by users:
    the converter puts it in the enabling condition of a concurrent assertion in an
    ``initial`` procedure, whose only evaluation attempt starts at that tick (IEEE
    1800-2017 §16.14.6; docs/semantics.md §6). No SystemVerilog syntax produces it
    elsewhere; it is well defined in any Boolean (never unknown).
    """

    type: ValueType


type Expr = (
    Signal
    | Const
    | Unary
    | Binary
    | Conditional
    | Convert
    | BitSelect
    | RangeSelect
    | Concat
    | Replicate
    | Sampled
    | FirstSample
)
