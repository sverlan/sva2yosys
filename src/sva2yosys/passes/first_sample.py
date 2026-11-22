"""First-sample check for ``$past`` (``docs/semantics.md`` §8).

At the first sample ``$past(e)`` is X in IEEE 1800 (``$past(e, n)``: at the first ``n``
samples), and a condition holds only if its value is known and non-zero. sva2yosys
evaluates no X: a condition simply does not hold while one of its ``$past`` is X
(:func:`gated_samples`). This is checked at each such sample ``k``, with the ``$past`` of
depth greater than ``k`` X and the others known. That equals IEEE exactly when the condition's
IEEE value there is X or 0, never a known non-zero value. This module decides that
conservatively by abstract interpretation, with every ``$past`` being all-X.

Abstract classes of a value at the first sample (:class:`FirstSample`):

- ``CLEAN``: does not depend on ``$past`` (known).
- ``ALL_X``: every bit is X.
- ``SOME_X``: every bit is 0 or X, and at least one bit is X (e.g. a zero-extended
  ``ALL_X``).
- ``ZERO_OR_X``: every bit is 0 or X (possibly all 0), so it is never known non-zero.
- ``ONE_OR_X``: its truth value is 1 or X.
- ``ANY``: may be known, partly known or X.

A condition (a sequence Boolean) is accepted when its class is ``CLEAN``, ``ALL_X``,
``SOME_X`` or ``ZERO_OR_X``; ``ONE_OR_X`` and ``ANY`` are rejected. A ``disable iff``
condition must be ``CLEAN`` (``$past`` in it is not supported).

Transfer rules (IEEE 1800 §11 X semantics). "X-carrying" means ``ALL_X`` or ``SOME_X``.
Whenever an operand is not ``CLEAN`` and no rule below applies, the result is ``ANY``.

- ``$past(e)``: ``ALL_X``; ``$rose``/``$fell``/``$stable``/``$changed``: ``CLEAN``.
- arithmetic ``+ - * / %``, relational ``< <= > >=``, unary ``+ -``: ``ALL_X`` if an
  operand is X-carrying (any X bit makes the whole result X).
- ``==``: ``ALL_X`` if an operand is ``ALL_X``; ``ZERO_OR_X`` if an operand is
  ``SOME_X`` (an X bit prevents a known 1). ``!=``: ``ALL_X`` if an operand is ``ALL_X``.
- ``^``, ``~^``: ``ALL_X`` if an operand is ``ALL_X``. ``~``: ``ALL_X`` stays ``ALL_X``.
- ``&``: ``ALL_X`` if both are ``ALL_X``; else ``ZERO_OR_X`` if an operand is
  ``ALL_X``, ``SOME_X`` or ``ZERO_OR_X``. ``|``: ``ALL_X`` if both are ``ALL_X``.
- logical ``&& || -> <-> !``: three-valued logic on truth values (X-carrying is X,
  ``ZERO_OR_X`` is 0 or X, ``ONE_OR_X`` is 1 or X, ``CLEAN`` is 0 or 1).
- reductions of ``ALL_X``: ``ALL_X``; of ``SOME_X``: ``| ^ ~| ~^`` give ``ALL_X``, ``&``
  gives ``ZERO_OR_X``, ``~&`` gives ``ONE_OR_X``; of ``ZERO_OR_X``: ``& | ^`` give
  ``ZERO_OR_X``, ``~& ~| ~^`` give ``ONE_OR_X``.
- shifts: an X-carrying amount gives ``ALL_X``; with a clean amount, ``>>>`` of a signed
  ``ALL_X`` is ``ALL_X``, a shift of ``ALL_X`` by a constant smaller than its width is
  ``SOME_X``, and any other shift of a value whose bits are 0 or X is ``ZERO_OR_X``.
- conversion: ``ALL_X`` truncated or sign-extended stays ``ALL_X``, zero-extended
  becomes ``SOME_X``; ``SOME_X`` extended stays ``SOME_X``, truncated becomes
  ``ZERO_OR_X``; ``ZERO_OR_X`` and ``ONE_OR_X`` keep their class.
- bit and part selects: ``ALL_X`` stays ``ALL_X``; ``SOME_X``/``ZERO_OR_X`` give
  ``ZERO_OR_X``. Replication keeps the class.
- concatenation of parts whose bits are all 0 or X: ``ALL_X`` if every part is ``ALL_X``,
  ``SOME_X`` if one is X-carrying, else ``ZERO_OR_X``.
- ``?:`` with a clean condition: the join of both branches; otherwise ``ANY``.

Default sampled values (IEEE 1800 §16.5.1). Before the first clock tick, ``$past``,
``$rose``, ``$fell``, ``$stable`` and ``$changed`` see the *default sampled value* of their
operand: a signal's declaration initializer, else its type default (0 for a 2-state type,
X for a 4-state one, also for a net); for an expression, the value computed from its
operands' defaults, constants included. sva2yosys models the whole operand as X there, which
is exact only when the operand's default is all X. :func:`default_class` decides that with the
same transfer rules, with the leaves ``Signal`` (``ALL_X`` if its default is X, else ``ANY``)
and ``Const`` (``CLEAN``); a nested sampled call is ``ANY``. :func:`first_sample_problems`
rejects a directive having a sampled-value call, at any depth, whose operand is not
``ALL_X``: ``a + 1`` and ``a[3:0]`` are accepted for a 4-state ``a``; ``{2'b0, a}``,
``a & MASK`` and any 2-state or initialised signal are rejected (nothing is guessed).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from sva2yosys.ir.directive import Directive
from sva2yosys.ir.expr import (
    BIT,
    Binary,
    BinaryOp,
    BitSelect,
    Concat,
    Conditional,
    Const,
    Convert,
    Expr,
    RangeSelect,
    Replicate,
    Sampled,
    SampledDefault,
    SampledFunction,
    Signal,
    Unary,
    UnaryOp,
)
from sva2yosys.ir.expr import FirstSample as FirstSampleExpr
from sva2yosys.ir.property import (
    Always,
    Eventually,
    Implication,
    Nexttime,
    Not,
    PropAnd,
    Property,
    PropIf,
    PropOr,
    SequenceProperty,
    Until,
)
from sva2yosys.ir.sequence import (
    Bool,
    Goto,
    NonConsecutive,
    Repeat,
    SeqAnd,
    SeqConcat,
    SeqIntersect,
    SeqOr,
    Sequence,
    Throughout,
    Within,
)


class FirstSample(Enum):
    """Abstract value of an expression at the first sample (see module docstring)."""

    CLEAN = "clean"
    ALL_X = "all-x"
    SOME_X = "some-x"
    ZERO_OR_X = "zero-or-x"
    ONE_OR_X = "one-or-x"
    ANY = "any"


_CLEAN = FirstSample.CLEAN
_ALL_X = FirstSample.ALL_X
_SOME_X = FirstSample.SOME_X
_ZERO_OR_X = FirstSample.ZERO_OR_X
_ONE_OR_X = FirstSample.ONE_OR_X
_ANY = FirstSample.ANY

_X_CARRYING = (_ALL_X, _SOME_X)
_ZERO_BITS = (_ALL_X, _SOME_X, _ZERO_OR_X)
_ACCEPTED = (_CLEAN, _ALL_X, _SOME_X, _ZERO_OR_X)

_ARITHMETIC = (
    BinaryOp.ADD,
    BinaryOp.SUBTRACT,
    BinaryOp.MULTIPLY,
    BinaryOp.DIVIDE,
    BinaryOp.MOD,
    BinaryOp.LESS_THAN,
    BinaryOp.LESS_THAN_EQUAL,
    BinaryOp.GREATER_THAN,
    BinaryOp.GREATER_THAN_EQUAL,
)
_LOGICAL = (
    BinaryOp.LOGICAL_AND,
    BinaryOp.LOGICAL_OR,
    BinaryOp.LOGICAL_IMPLICATION,
    BinaryOp.LOGICAL_EQUIVALENCE,
)
_SHIFTS = (
    BinaryOp.LOGICAL_SHIFT_LEFT,
    BinaryOp.LOGICAL_SHIFT_RIGHT,
    BinaryOp.ARITHMETIC_SHIFT_LEFT,
    BinaryOp.ARITHMETIC_SHIFT_RIGHT,
)

# Three-valued truth values: 0, 1 and "X" (unknown).
type _Truth = frozenset[int | str]
_X = "X"


def classify(expr: Expr, sample: int = 0) -> FirstSample:
    """The abstract value of ``expr`` at ``sample`` (0 is the first sample): a ``$past``
    of depth ``d`` is X while ``sample < d``, and known afterwards."""
    return _interpret(expr, sample, _first_sample_leaf)


def default_class(expr: Expr) -> FirstSample:
    """The abstract value of ``expr`` at its default sampled value (IEEE 1800 §16.5.1, the
    module docstring): ``ALL_X`` exactly when every bit is X there."""
    return _interpret(expr, 0, _default_leaf)


type _Leaf = Callable[[Signal | Const | Sampled, int], FirstSample]


def _first_sample_leaf(expr: Signal | Const | Sampled, sample: int) -> FirstSample:
    match expr:
        case Signal() | Const():
            return _CLEAN
        case Sampled(function=function, depth=depth):
            return _ALL_X if function is SampledFunction.PAST and sample < depth else _CLEAN


def _default_leaf(expr: Signal | Const | Sampled, sample: int) -> FirstSample:
    match expr:
        case Signal(default=default):
            return _ALL_X if default is SampledDefault.X else _ANY
        case Const():
            return _CLEAN
        case Sampled():
            return _ANY


def _interpret(expr: Expr, sample: int, leaf: _Leaf) -> FirstSample:
    """The abstract interpretation shared by :func:`classify` and :func:`default_class`;
    ``leaf`` gives the class of a signal, a constant and a sampled call."""

    def recurse(inner: Expr, sample: int) -> FirstSample:
        return _interpret(inner, sample, leaf)

    match expr:
        case FirstSampleExpr():
            return _CLEAN  # known at every sample (1 at the first, 0 after)
        case Signal() | Const() | Sampled():
            return leaf(expr, sample)
        case Unary(op=op, operand=operand):
            return _unary(op, recurse(operand, sample))
        case Binary(op=op, left=left, right=right):
            left_class = recurse(left, sample)
            if (
                op in _SHIFTS
                and left_class is _ALL_X
                and isinstance(right, Const)
                and right.value < left.type.width
                and not (op is BinaryOp.ARITHMETIC_SHIFT_RIGHT and expr.type.signed)
            ):
                return _SOME_X
            return _binary(op, left_class, recurse(right, sample), expr.type.signed)
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            if recurse(condition, sample) is not _CLEAN:
                return _ANY
            return _join(recurse(then, sample), recurse(otherwise, sample))
        case Convert(operand=operand):
            return _convert(
                recurse(operand, sample), operand.type.width, expr.type.width, operand.type.signed
            )
        case BitSelect(value=inner) | RangeSelect(value=inner):
            inner_class = recurse(inner, sample)
            if inner_class in (_CLEAN, _ALL_X):
                return inner_class
            return _ZERO_OR_X if inner_class in _ZERO_BITS else _ANY
        case Replicate(value=inner):
            return recurse(inner, sample)
        case Concat(parts=parts):
            classes = [recurse(part, sample) for part in parts]
            if all(c is _CLEAN for c in classes):
                return _CLEAN
            if all(c is _ALL_X for c in classes):
                return _ALL_X
            if all(c in _ZERO_BITS for c in classes):
                return _SOME_X if any(c in _X_CARRYING for c in classes) else _ZERO_OR_X
            return _ANY


def past_depth(expr: Expr) -> int:
    """The largest ``$past`` depth in ``expr`` (0 without ``$past``)."""
    match expr:
        case Signal() | Const() | FirstSampleExpr():
            return 0
        case Sampled(function=function, operand=operand, depth=depth):
            own = depth if function is SampledFunction.PAST else 0
            return max(own, past_depth(operand))
        case Unary(operand=operand) | Convert(operand=operand):
            return past_depth(operand)
        case Binary(left=left, right=right):
            return max(past_depth(left), past_depth(right))
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            return max(past_depth(condition), past_depth(then), past_depth(otherwise))
        case BitSelect(value=inner) | RangeSelect(value=inner) | Replicate(value=inner):
            return past_depth(inner)
        case Concat(parts=parts):
            return max((past_depth(part) for part in parts), default=0)


def gated_samples(condition: Expr) -> int:
    """The number of initial samples during which ``condition`` does not hold under the
    first-sample rule: the first sample from which its classification is ``CLEAN``."""
    return next(k for k in range(past_depth(condition) + 1) if classify(condition, k) is _CLEAN)


def first_sample_problems(directive: Directive) -> tuple[str, ...]:
    """Reasons why ``directive`` cannot use the first-sample rule; empty if it can.

    Every sequence Boolean of the property is checked, and the ``disable iff``
    condition must not contain ``$past``.
    """
    problems: list[str] = []
    if directive.disable is not None and classify(directive.disable) is not _CLEAN:
        problems.append("$past in a disable iff condition is not supported yet")
    conditions = _conditions(directive.property)
    problem = _default_problem(
        conditions if directive.disable is None else [directive.disable, *conditions]
    )
    if problem is not None:
        problems.append(problem)
    for condition in conditions:
        if any(classify(condition, k) not in _ACCEPTED for k in range(past_depth(condition))):
            problems.append(
                "this condition could be known true at the first sample although it uses "
                "$past (IEEE: $past is X there); restructure it, e.g. split '||' or compare "
                "with operands of the same width"
            )
            break
    return tuple(problems)


def _default_problem(conditions: list[Expr]) -> str | None:
    """The message for the first sampled-value call whose operand does not default to all X."""
    for condition in conditions:
        for call in _sampled_calls(condition):
            if default_class(call.operand) is _ALL_X:
                continue
            known = _known_signal(call.operand)
            cause = (
                f"'{known}' is initialised or of a 2-state type"
                if known is not None
                else "the operand contains a constant"
            )
            return (
                f"the value of '{call.function.value}' before the first clock tick is not "
                f"unknown (IEEE 1800 §16.5.1: {cause}); sva2yosys rejects properties whose "
                "first-tick result depends on it; this does not depend on a "
                "'disable iff': use the sampled-value function only on 4-state signals "
                "without an initializer and without constants in the operand"
            )
    return None


def _children(expr: Expr) -> tuple[Expr, ...]:
    match expr:
        case Signal() | Const() | FirstSampleExpr():
            return ()
        case Sampled(operand=operand) | Unary(operand=operand) | Convert(operand=operand):
            return (operand,)
        case Binary(left=left, right=right):
            return (left, right)
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            return (condition, then, otherwise)
        case BitSelect(value=inner) | RangeSelect(value=inner) | Replicate(value=inner):
            return (inner,)
        case Concat(parts=parts):
            return parts


def _sampled_calls(expr: Expr) -> list[Sampled]:
    """Every sampled-value call in ``expr``, at any depth, outermost first."""
    found = [expr] if isinstance(expr, Sampled) else []
    for child in _children(expr):
        found.extend(_sampled_calls(child))
    return found


def _known_signal(expr: Expr) -> str | None:
    """The path of the first signal of ``expr`` whose default sampled value is known."""
    if isinstance(expr, Signal):
        return expr.path if expr.default is SampledDefault.KNOWN else None
    for child in _children(expr):
        found = _known_signal(child)
        if found is not None:
            return found
    return None


def _conditions(prop: Property) -> list[Expr]:
    match prop:
        case SequenceProperty(sequence=sequence):
            return _sequence_conditions(sequence)
        case Implication(antecedent=antecedent, consequent=consequent):
            return _sequence_conditions(antecedent) + _conditions(consequent)
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _conditions(operand)
        case Until(left=left, right=right):
            return [left, right]
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return [c for operand in operands for c in _conditions(operand)]
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            found = [condition, *_conditions(then)]
            if otherwise is not None:
                found.extend(_conditions(otherwise))
            return found


def _sequence_conditions(sequence: Sequence) -> list[Expr]:
    match sequence:
        case Bool(expr=expr):
            return [expr]
        case SeqConcat(left=left, right=right):
            return _sequence_conditions(left) + _sequence_conditions(right)
        case Repeat(sequence=inner):
            return _sequence_conditions(inner)
        case Goto(condition=condition) | NonConsecutive(condition=condition):
            # the machine also sees the negation (ir.sequence.goto_expansion, §16.9.2)
            return [condition, Unary(UnaryOp.LOGICAL_NOT, condition, BIT)]
        case (
            SeqOr(left=left, right=right)
            | SeqAnd(left=left, right=right)
            | SeqIntersect(left=left, right=right)
            | Within(inner=left, outer=right)
        ):
            return _sequence_conditions(left) + _sequence_conditions(right)
        case Throughout(condition=condition, sequence=inner):
            return [condition, *_sequence_conditions(inner)]


# --- transfer functions ---------------------------------------------------------------


def _truth(value: FirstSample) -> _Truth:
    match value:
        case FirstSample.CLEAN:
            return frozenset({0, 1})
        case FirstSample.ALL_X | FirstSample.SOME_X:
            return frozenset({_X})
        case FirstSample.ZERO_OR_X:
            return frozenset({0, _X})
        case FirstSample.ONE_OR_X:
            return frozenset({1, _X})
        case FirstSample.ANY:
            return frozenset({0, 1, _X})


def _from_truth(truth: _Truth) -> FirstSample:
    if truth == {_X}:
        return _ALL_X
    if truth <= {0, _X}:
        return _ZERO_OR_X
    if truth <= {1, _X}:
        return _ONE_OR_X
    return _ANY


def _logic(op: BinaryOp, left: int | str, right: int | str) -> int | str:
    match op:
        case BinaryOp.LOGICAL_AND:
            if left == 0 or right == 0:
                return 0
            return 1 if left == 1 and right == 1 else _X
        case BinaryOp.LOGICAL_OR:
            if left == 1 or right == 1:
                return 1
            return 0 if left == 0 and right == 0 else _X
        case BinaryOp.LOGICAL_IMPLICATION:
            if left == 0 or right == 1:
                return 1
            return 0 if left == 1 and right == 0 else _X
        case _:
            if _X in (left, right):
                return _X
            return int(left == right)


def _unary(op: UnaryOp, operand: FirstSample) -> FirstSample:
    if operand is _CLEAN:
        return _CLEAN
    match op:
        case UnaryOp.LOGICAL_NOT:
            return _from_truth(frozenset(_X if t == _X else 1 - int(t) for t in _truth(operand)))
        case UnaryOp.PLUS | UnaryOp.MINUS:
            return _ALL_X if operand in _X_CARRYING else _ANY
        case UnaryOp.BITWISE_NOT:
            return _ALL_X if operand is _ALL_X else _ANY
        case UnaryOp.REDUCE_AND | UnaryOp.REDUCE_OR | UnaryOp.REDUCE_XOR:
            if operand is _ALL_X:
                return _ALL_X
            if operand is _SOME_X:
                return _ZERO_OR_X if op is UnaryOp.REDUCE_AND else _ALL_X
            return _ZERO_OR_X if operand is _ZERO_OR_X else _ANY
        case UnaryOp.REDUCE_NAND | UnaryOp.REDUCE_NOR | UnaryOp.REDUCE_XNOR:
            if operand is _ALL_X:
                return _ALL_X
            if operand is _SOME_X:
                return _ONE_OR_X if op is UnaryOp.REDUCE_NAND else _ALL_X
            return _ONE_OR_X if operand is _ZERO_OR_X else _ANY


def _binary(op: BinaryOp, left: FirstSample, right: FirstSample, signed: bool) -> FirstSample:
    if left is _CLEAN and right is _CLEAN:
        return _CLEAN
    both = (left, right)
    if op in _LOGICAL:
        truth = frozenset(_logic(op, a, b) for a in _truth(left) for b in _truth(right))
        return _from_truth(truth)
    if op in _ARITHMETIC:
        return _ALL_X if any(c in _X_CARRYING for c in both) else _ANY
    if op in _SHIFTS:
        if right in _X_CARRYING:
            return _ALL_X
        if right is not _CLEAN:
            return _ANY
        if op is BinaryOp.ARITHMETIC_SHIFT_RIGHT and signed and left is _ALL_X:
            return _ALL_X
        return _ZERO_OR_X if left in _ZERO_BITS else _ANY
    match op:
        case BinaryOp.EQUALITY:
            if _ALL_X in both:
                return _ALL_X
            return _ZERO_OR_X if _SOME_X in both else _ANY
        case BinaryOp.INEQUALITY:
            return _ALL_X if _ALL_X in both else _ANY
        case BinaryOp.BITWISE_XOR | BinaryOp.BITWISE_XNOR:
            return _ALL_X if _ALL_X in both else _ANY
        case BinaryOp.BITWISE_AND:
            if left is _ALL_X and right is _ALL_X:
                return _ALL_X
            return _ZERO_OR_X if any(c in _ZERO_BITS for c in both) else _ANY
        case BinaryOp.BITWISE_OR:
            return _ALL_X if left is _ALL_X and right is _ALL_X else _ANY
        case _:
            return _ANY


def _convert(operand: FirstSample, width: int, new_width: int, signed: bool) -> FirstSample:
    if operand in (_CLEAN, _ZERO_OR_X, _ONE_OR_X, _ANY):
        return operand
    if new_width <= width:
        return _ALL_X if operand is _ALL_X else _ZERO_OR_X
    if operand is _ALL_X:
        return _ALL_X if signed else _SOME_X
    return _SOME_X


def _join(first: FirstSample, second: FirstSample) -> FirstSample:
    if first is second:
        return first
    pair = {first, second}
    if pair <= set(_X_CARRYING):
        return _SOME_X
    if pair <= set(_ZERO_BITS):
        return _ZERO_OR_X
    return _ANY
