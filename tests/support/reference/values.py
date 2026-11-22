"""Values of IR expressions on a finite trace (part of the reference evaluator).

``support.reference.evaluate`` re-exports the public names. Imports IR types only.
"""

from __future__ import annotations

from support.reference.trace import Trace, TraceError
from sva2yosys.ir.expr import (
    Binary,
    BinaryOp,
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
    UnaryOp,
    ValueType,
)


class EvaluationError(ValueError):
    """The property cannot be evaluated (e.g. division by zero, unbounded range)."""


def value(expr: Expr, trace: Trace, sample: int) -> int:
    """The value (bit pattern of ``expr.type``) of ``expr`` at ``sample``.

    Raises:
        TraceError: A needed value is missing, including ``$past`` at sample 0 (no
            previous sample; conditions use :func:`holds`, which applies the
            first-sample rule).
        EvaluationError: Division or modulo by zero.
    """
    return _value(expr, trace, sample)


def holds(condition: Expr, trace: Trace, sample: int) -> bool:
    """Whether a condition holds at ``sample``: its value is known and non-zero (IEEE 1800
    12.4: a condition whose value is x is false).

    At samples ``0 … d-1``, ``d`` being the condition's largest ``$past`` depth, a
    ``$past(e, n)`` with ``n`` above the sample has the default sampled value X (16.9.3),
    and the condition is evaluated with the IEEE X rules of clause 11.4 (:func:`truth`). For
    a condition the pipeline's first-sample rule accepts, the value there is X or 0, so it
    does not hold (``docs/semantics.md`` section 8); a generated negation such as the
    ``!b`` of a goto ``b[->1]`` (16.9.2) can hold there, and does when IEEE says so.

    The trace is two-valued: X comes from ``$past`` alone. Bit counts and ``$isunknown`` are
    already lowered in the IR, so this evaluation does not cover them over X operands (the
    pipeline rejects bit counts over ``$past`` at conversion).
    """
    return truth(condition, trace, sample) is True


def truth(condition: Expr, trace: Trace, sample: int) -> bool | None:
    """The logical value of a condition at ``sample``: ``True`` or ``False`` when known,
    ``None`` when it is X (11.4.7: a vector is true when some bit is known 1, false when
    every bit is known 0, else X). X arises only at the samples described in :func:`holds`."""
    if sample >= _past_depth(condition):
        return _value(condition, trace, sample) != 0
    return _truth4(_value4(condition, trace, sample))


def _mask(width: int) -> int:
    return (1 << width) - 1


def _signed(pattern: int, value_type: ValueType) -> int:
    if value_type.signed and pattern >> (value_type.width - 1) & 1:
        return pattern - (1 << value_type.width)
    return pattern


def _past_depth(expr: Expr) -> int:
    """The largest ``$past`` depth in ``expr`` (0 without ``$past``)."""
    match expr:
        case Sampled(function=function, operand=operand, depth=depth):
            return max(depth if function is SampledFunction.PAST else 0, _past_depth(operand))
        case Signal() | Const() | FirstSample():
            return 0
        case Unary(operand=operand) | Convert(operand=operand):
            return _past_depth(operand)
        case Binary(left=left, right=right):
            return max(_past_depth(left), _past_depth(right))
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            return max(_past_depth(e) for e in (condition, then, otherwise))
        case BitSelect(value=inner) | RangeSelect(value=inner) | Replicate(value=inner):
            return _past_depth(inner)
        case Concat(parts=parts):
            return max((_past_depth(p) for p in parts), default=0)


def _value(expr: Expr, trace: Trace, sample: int) -> int:
    mask = _mask(expr.type.width)
    match expr:
        case Signal(path=path):
            return trace.value(path, sample) & mask
        case Const(value=constant):
            return constant & mask
        case FirstSample():
            return int(sample == 0)  # 1 at the first sample only (never unknown)
        case Unary(op=op, operand=operand):
            return _unary(op, _value(operand, trace, sample), operand.type) & mask
        case Binary(op=op, left=left, right=right):
            result = _binary(
                op, _value(left, trace, sample), _value(right, trace, sample), left.type
            )
            return result & mask
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            chosen = then if _value(condition, trace, sample) != 0 else otherwise
            return _value(chosen, trace, sample) & mask
        case Convert(operand=operand):
            return _signed(_value(operand, trace, sample), operand.type) & mask
        case BitSelect(value=inner, index=index):
            return _value(inner, trace, sample) >> index & 1
        case RangeSelect(value=inner, lsb=lsb):
            return _value(inner, trace, sample) >> lsb & mask
        case Concat(parts=parts):
            result = 0
            for part in parts:
                result = result << part.type.width | _value(part, trace, sample)
            return result & mask
        case Replicate(count=count, value=inner):
            piece = _value(inner, trace, sample)
            result = 0
            for _ in range(count):
                result = result << inner.type.width | piece
            return result & mask
        case Sampled(function=function, operand=operand, depth=depth):
            return _sampled(function, operand, depth, trace, sample) & mask


def _sampled(
    function: SampledFunction, operand: Expr, depth: int, trace: Trace, sample: int
) -> int:
    if function is SampledFunction.PAST:
        if sample < depth:
            raise TraceError(f"$past(.., {depth}) has no value at sample {sample}")
        return _value(operand, trace, sample - depth)
    current = _value(operand, trace, sample)
    previous = _value(operand, trace, sample - 1) if sample > 0 else None
    match function:
        case SampledFunction.ROSE:
            return int(current & 1 == 1 and (previous is None or previous & 1 == 0))
        case SampledFunction.FELL:
            return int(current & 1 == 0 and (previous is None or previous & 1 == 1))
        case SampledFunction.STABLE:
            return int(previous is not None and previous == current)
        case SampledFunction.CHANGED:
            return int(previous is None or previous != current)


def _unary(op: UnaryOp, operand: int, operand_type: ValueType) -> int:
    width = operand_type.width
    match op:
        case UnaryOp.PLUS:
            return operand
        case UnaryOp.MINUS:
            return -operand
        case UnaryOp.BITWISE_NOT:
            return ~operand
        case UnaryOp.LOGICAL_NOT:
            return int(operand == 0)
        case UnaryOp.REDUCE_AND:
            return int(operand == _mask(width))
        case UnaryOp.REDUCE_OR:
            return int(operand != 0)
        case UnaryOp.REDUCE_XOR:
            return operand.bit_count() & 1
        case UnaryOp.REDUCE_NAND:
            return int(operand != _mask(width))
        case UnaryOp.REDUCE_NOR:
            return int(operand == 0)
        case UnaryOp.REDUCE_XNOR:
            return 1 - (operand.bit_count() & 1)


def _binary(op: BinaryOp, left: int, right: int, operand_type: ValueType) -> int:
    signed_left, signed_right = _signed(left, operand_type), _signed(right, operand_type)
    match op:
        case BinaryOp.ADD:
            return left + right
        case BinaryOp.SUBTRACT:
            return left - right
        case BinaryOp.MULTIPLY:
            return left * right
        case BinaryOp.DIVIDE | BinaryOp.MOD:
            if right == 0:
                raise EvaluationError("division by zero")
            quotient = abs(signed_left) // abs(signed_right)
            if (signed_left < 0) != (signed_right < 0):
                quotient = -quotient
            if op is BinaryOp.DIVIDE:
                return quotient
            return signed_left - quotient * signed_right
        case BinaryOp.BITWISE_AND:
            return left & right
        case BinaryOp.BITWISE_OR:
            return left | right
        case BinaryOp.BITWISE_XOR:
            return left ^ right
        case BinaryOp.BITWISE_XNOR:
            return ~(left ^ right)
        case BinaryOp.EQUALITY | BinaryOp.CASE_EQUALITY:
            return int(left == right)
        case BinaryOp.INEQUALITY | BinaryOp.CASE_INEQUALITY:
            return int(left != right)
        case BinaryOp.LESS_THAN:
            return int(signed_left < signed_right)
        case BinaryOp.LESS_THAN_EQUAL:
            return int(signed_left <= signed_right)
        case BinaryOp.GREATER_THAN:
            return int(signed_left > signed_right)
        case BinaryOp.GREATER_THAN_EQUAL:
            return int(signed_left >= signed_right)
        case BinaryOp.LOGICAL_AND:
            return int(left != 0 and right != 0)
        case BinaryOp.LOGICAL_OR:
            return int(left != 0 or right != 0)
        case BinaryOp.LOGICAL_IMPLICATION:
            return int(left == 0 or right != 0)
        case BinaryOp.LOGICAL_EQUIVALENCE:
            return int((left != 0) == (right != 0))
        case BinaryOp.LOGICAL_SHIFT_LEFT | BinaryOp.ARITHMETIC_SHIFT_LEFT:
            return left << right if right < operand_type.width else 0
        case BinaryOp.LOGICAL_SHIFT_RIGHT:
            return left >> right
        case BinaryOp.ARITHMETIC_SHIFT_RIGHT:
            return signed_left >> right


# -- four-state evaluation (only where some $past has no history yet)
#
# A value is a pair (bits, unknown) of bit patterns of the expression's width: a bit of
# ``unknown`` is X, and its ``bits`` bit is 0. The rules are IEEE 1800 clause 11.4.

_Four = tuple[int, int]


def _truth4(four: _Four) -> bool | None:
    bits, unknown = four
    if bits:
        return True
    return False if unknown == 0 else None


def _from_truth(value: bool | None) -> _Four:
    return (0, 1) if value is None else (int(value), 0)


def _invert(value: bool | None, invert: bool) -> bool | None:
    return value if value is None or not invert else not value


def _value4(expr: Expr, trace: Trace, sample: int) -> _Four:
    width = expr.type.width
    mask = _mask(width)
    match expr:
        case Signal() | Const() | FirstSample():
            return _value(expr, trace, sample), 0
        case Sampled(function=SampledFunction.PAST, operand=operand, depth=depth):
            if sample < depth:
                return 0, mask
            return _value(operand, trace, sample - depth) & mask, 0
        case Sampled():
            # the edge functions are known at the first sample (16.9.3; operands not nested)
            return _value(expr, trace, sample), 0
        case Unary(op=op, operand=operand):
            bits, unknown = _unary4(op, _value4(operand, trace, sample), operand.type)
            return bits & mask, unknown & mask
        case Binary(op=op, left=left, right=right):
            left4, right4 = _value4(left, trace, sample), _value4(right, trace, sample)
            bits, unknown = _binary4(op, left4, right4, left.type)
            return bits & mask, unknown & mask
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            selector = _truth4(_value4(condition, trace, sample))
            if selector is not None:
                bits, unknown = _value4(then if selector else otherwise, trace, sample)
                return bits & mask, unknown & mask
            # 11.4.11: both operands, combined bit by bit (equal known bits stay, else X)
            then_bits, then_unknown = _value4(then, trace, sample)
            else_bits, else_unknown = _value4(otherwise, trace, sample)
            unknown = (then_unknown | else_unknown | (then_bits ^ else_bits)) & mask
            return then_bits & ~unknown & mask, unknown
        case Convert(operand=operand):
            bits, unknown = _value4(operand, trace, sample)
            source = operand.type
            if source.signed and width > source.width:
                extension = mask & ~_mask(source.width)
                sign = 1 << (source.width - 1)
                if unknown & sign:
                    unknown |= extension
                elif bits & sign:
                    bits |= extension
            return bits & mask, unknown & mask
        case BitSelect(value=inner, index=index):
            bits, unknown = _value4(inner, trace, sample)
            return bits >> index & 1, unknown >> index & 1
        case RangeSelect(value=inner, lsb=lsb):
            bits, unknown = _value4(inner, trace, sample)
            return bits >> lsb & mask, unknown >> lsb & mask
        case Concat(parts=parts):
            bits = unknown = 0
            for part in parts:
                part_bits, part_unknown = _value4(part, trace, sample)
                bits = bits << part.type.width | part_bits
                unknown = unknown << part.type.width | part_unknown
            return bits & mask, unknown & mask
        case Replicate(count=count, value=inner):
            piece_bits, piece_unknown = _value4(inner, trace, sample)
            bits = unknown = 0
            for _ in range(count):
                bits = bits << inner.type.width | piece_bits
                unknown = unknown << inner.type.width | piece_unknown
            return bits & mask, unknown & mask


def _unary4(op: UnaryOp, operand: _Four, operand_type: ValueType) -> _Four:
    """The result before masking to the result width (an all-X result is ``-1``)."""
    bits, unknown = operand
    if unknown == 0:
        return _unary(op, bits, operand_type), 0
    known_zero = ~bits & ~unknown & _mask(operand_type.width)
    match op:
        case UnaryOp.PLUS | UnaryOp.MINUS:
            # 11.4.3: both are arithmetic operators; an X operand bit makes the result all X
            return 0, -1
        case UnaryOp.BITWISE_NOT:
            return known_zero, unknown
        case UnaryOp.LOGICAL_NOT:
            return _from_truth(_invert(_truth4(operand), True))
        case UnaryOp.REDUCE_AND | UnaryOp.REDUCE_NAND:
            reduced = False if known_zero else None
            return _from_truth(_invert(reduced, op is UnaryOp.REDUCE_NAND))
        case UnaryOp.REDUCE_OR | UnaryOp.REDUCE_NOR:
            reduced = True if bits else None
            return _from_truth(_invert(reduced, op is UnaryOp.REDUCE_NOR))
        case UnaryOp.REDUCE_XOR | UnaryOp.REDUCE_XNOR:
            return 0, 1


def _binary4(op: BinaryOp, left: _Four, right: _Four, operand_type: ValueType) -> _Four:
    """The result before masking to the result width (an all-X result is ``-1``)."""
    left_bits, left_unknown = left
    right_bits, right_unknown = right
    if left_unknown == 0 and right_unknown == 0:
        return _binary(op, left_bits, right_bits, operand_type), 0
    full = _mask(operand_type.width)
    either_unknown = left_unknown | right_unknown
    match op:
        case BinaryOp.ADD | BinaryOp.SUBTRACT | BinaryOp.MULTIPLY | BinaryOp.DIVIDE | BinaryOp.MOD:
            return 0, -1  # 11.4.3: an X operand bit makes the whole result X
        case (
            BinaryOp.LESS_THAN
            | BinaryOp.LESS_THAN_EQUAL
            | BinaryOp.GREATER_THAN
            | BinaryOp.GREATER_THAN_EQUAL
        ):
            return 0, 1  # 11.4.4
        case BinaryOp.BITWISE_AND:
            zero = (~left_bits & ~left_unknown) | (~right_bits & ~right_unknown)
            return left_bits & right_bits, either_unknown & ~zero & full
        case BinaryOp.BITWISE_OR:
            one = left_bits | right_bits
            return one, either_unknown & ~one & full
        case BinaryOp.BITWISE_XOR:
            return (left_bits ^ right_bits) & ~either_unknown, either_unknown
        case BinaryOp.BITWISE_XNOR:
            return ~(left_bits ^ right_bits) & ~either_unknown & full, either_unknown
        case BinaryOp.EQUALITY | BinaryOp.INEQUALITY:
            # 11.4.5: known when some pair of known bits differs, else ambiguous (X)
            differ = (left_bits ^ right_bits) & ~either_unknown & full
            equal: bool | None = False if differ else None
            return _from_truth(_invert(equal, op is BinaryOp.INEQUALITY))
        case BinaryOp.CASE_EQUALITY | BinaryOp.CASE_INEQUALITY:
            # 11.4.5: X bits are compared as values; the result is always known
            same = left_bits == right_bits and left_unknown == right_unknown
            return int(same != (op is BinaryOp.CASE_INEQUALITY)), 0
        case (
            BinaryOp.LOGICAL_AND
            | BinaryOp.LOGICAL_OR
            | BinaryOp.LOGICAL_IMPLICATION
            | BinaryOp.LOGICAL_EQUIVALENCE
        ):
            return _from_truth(_logical(op, _truth4(left), _truth4(right)))
        case (
            BinaryOp.LOGICAL_SHIFT_LEFT
            | BinaryOp.ARITHMETIC_SHIFT_LEFT
            | BinaryOp.LOGICAL_SHIFT_RIGHT
            | BinaryOp.ARITHMETIC_SHIFT_RIGHT
        ):
            if right_unknown:
                return 0, -1  # 11.4.10: an X shift amount gives an X result
            return _shift4(op, left, right_bits, operand_type)


def _logical(op: BinaryOp, left: bool | None, right: bool | None) -> bool | None:
    """11.4.7: ``&&``, ``||``, ``->`` and ``<->`` over 0, 1 and X (``None``)."""
    match op:
        case BinaryOp.LOGICAL_AND:
            if left is False or right is False:
                return False
            return True if left is True and right is True else None
        case BinaryOp.LOGICAL_IMPLICATION:  # a -> b is !a || b
            return _logical(BinaryOp.LOGICAL_OR, _invert(left, True), right)
        case BinaryOp.LOGICAL_EQUIVALENCE:
            return None if left is None or right is None else left == right
        case _:  # ||
            if left is True or right is True:
                return True
            return False if left is False and right is False else None


def _shift4(op: BinaryOp, left: _Four, amount: int, operand_type: ValueType) -> _Four:
    bits, unknown = left
    width = operand_type.width
    full = _mask(width)
    match op:
        case BinaryOp.LOGICAL_SHIFT_LEFT | BinaryOp.ARITHMETIC_SHIFT_LEFT:
            if amount >= width:
                return 0, 0
            return bits << amount & full, unknown << amount & full
        case BinaryOp.LOGICAL_SHIFT_RIGHT:
            return bits >> amount, unknown >> amount
        case _:  # >>>: a signed operand fills with its sign bit, X included
            shifted_bits, shifted_unknown = bits >> amount, unknown >> amount
            if operand_type.signed:
                vacated = full & ~(full >> amount) if amount < width else full
                sign = 1 << (width - 1)
                if unknown & sign:
                    shifted_unknown |= vacated
                elif bits & sign:
                    shifted_bits |= vacated
            return shifted_bits, shifted_unknown
