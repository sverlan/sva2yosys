"""Sequence expressions (IEEE 1800 §16.7, §16.9), the supported subset.

Match semantics follow Annex F (tight satisfaction over finite words). A sequence
that starts at ``##`` is represented with an explicit leading ``Bool(true)``:
``##[m:n] s`` is ``Concat(TRUE, Delay(m, n), s)``, which matches exactly like the
IEEE definition (``##0 s`` is ``s``).
"""

from __future__ import annotations

from dataclasses import dataclass

from sva2yosys.ir.expr import BIT, Const, Expr, Unary, UnaryOp


@dataclass(frozen=True)
class Delay:
    """A cycle delay ``##[minimum:maximum]``; ``maximum`` ``None`` means ``$``.

    ``0 <= minimum`` and, when bounded, ``minimum <= maximum``. A delay of 0 fuses the
    operands (the right one starts at the sample where the left one ends).
    """

    minimum: int
    maximum: int | None

    def __post_init__(self) -> None:
        _check_range("delay", self.minimum, self.maximum)


@dataclass(frozen=True)
class Bool:
    """A Boolean expression: matches one sample at which ``expr`` is non-zero."""

    expr: Expr


@dataclass(frozen=True)
class SeqConcat:
    """``left ##[m:n] right``."""

    left: Sequence
    delay: Delay
    right: Sequence


@dataclass(frozen=True)
class Repeat:
    """Consecutive repetition ``sequence[*minimum:maximum]``; ``maximum`` ``None`` is ``$``.

    ``minimum`` may be 0, in which case the empty match is included.
    """

    sequence: Sequence
    minimum: int
    maximum: int | None

    def __post_init__(self) -> None:
        _check_range("repetition", self.minimum, self.maximum)


@dataclass(frozen=True)
class SeqOr:
    """``left or right`` (§16.9.7): the matches of either operand."""

    left: Sequence
    right: Sequence


@dataclass(frozen=True)
class SeqAnd:
    """``left and right`` (§16.9.5): both match from the same start; a match ends at the
    later of the two ends."""

    left: Sequence
    right: Sequence


@dataclass(frozen=True)
class SeqIntersect:
    """``left intersect right`` (§16.9.6): both match from the same start to the same end."""

    left: Sequence
    right: Sequence


@dataclass(frozen=True)
class Throughout:
    """``condition throughout sequence`` (§16.9.9): a match of ``sequence`` during which
    ``condition`` holds at every sample."""

    condition: Expr
    sequence: Sequence


@dataclass(frozen=True)
class Within:
    """``inner within outer`` (§16.9.10): a match of ``outer`` that contains a match of
    ``inner`` (starting at or after its start, ending at or before its end)."""

    inner: Sequence
    outer: Sequence


@dataclass(frozen=True)
class Goto:
    """Goto repetition ``condition[->minimum:maximum]`` (§16.9.2); ``maximum`` ``None`` is
    ``$``; ``condition[->n]`` has ``minimum == maximum == n``.

    ``condition[->n]`` is ``(!condition[*0:$] ##1 condition)[*n]``: a match ends at the
    sample where ``condition`` holds for the ``n``-th time, counting from the start, with
    no other requirement on the samples in between. A range is the union of the counts
    ``minimum … maximum``. Count 0 is the empty match. The operand is a Boolean
    expression, not a sequence.
    """

    condition: Expr
    minimum: int
    maximum: int | None

    def __post_init__(self) -> None:
        _check_range("goto repetition", self.minimum, self.maximum)


@dataclass(frozen=True)
class NonConsecutive:
    """Nonconsecutive repetition ``condition[=minimum:maximum]`` (§16.9.2); ``maximum``
    ``None`` is ``$``.

    ``condition[=n]`` is ``condition[->n] ##1 !condition[*0:$]``: ``condition`` holds at
    exactly ``n`` samples of the match, and the match may continue after the last of them
    for any number of samples where it does not hold. A range is the union of the counts
    ``minimum … maximum``. Count 0 is ``!condition[*0:$]``. The operand is a Boolean
    expression, not a sequence.
    """

    condition: Expr
    minimum: int
    maximum: int | None

    def __post_init__(self) -> None:
        _check_range("nonconsecutive repetition", self.minimum, self.maximum)


type Sequence = (
    Bool
    | SeqConcat
    | Repeat
    | Goto
    | NonConsecutive
    | SeqOr
    | SeqAnd
    | SeqIntersect
    | Throughout
    | Within
)


def _check_range(what: str, minimum: int, maximum: int | None) -> None:
    """Reject ``minimum < 0`` and ``maximum < minimum`` (``None`` is unbounded)."""
    if minimum < 0 or (maximum is not None and maximum < minimum):
        raise ValueError(f"invalid {what} range [{minimum}:{'$' if maximum is None else maximum}]")


TRUE = Bool(Const(1, BIT))
"""The sequence that matches any single sample (the implicit start of ``##n s``)."""


def _negated(condition: Expr) -> Expr:
    """``!condition`` (a constant folds to a constant)."""
    if isinstance(condition, Const):
        return Const(0 if condition.value else 1, BIT)
    return Unary(UnaryOp.LOGICAL_NOT, condition, BIT)


def goto_expansion(node: Goto) -> Sequence:
    """The definition of ``condition[->m:n]`` (§16.9.2) as a sequence of the other nodes:
    ``(!condition[*0:$] ##1 condition)[*m:n]``. The machines use it; the reference
    evaluator does not (it has its own native evaluation)."""
    step = SeqConcat(
        Repeat(Bool(_negated(node.condition)), 0, None), Delay(1, 1), Bool(node.condition)
    )
    return Repeat(step, node.minimum, node.maximum)


def nonconsecutive_expansion(node: NonConsecutive) -> Sequence:
    """The definition of ``condition[=m:n]`` (§16.9.2) as a sequence of the other nodes:
    ``condition[->m:n] ##1 !condition[*0:$]``."""
    return SeqConcat(
        goto_expansion(Goto(node.condition, node.minimum, node.maximum)),
        Delay(1, 1),
        Repeat(Bool(_negated(node.condition)), 0, None),
    )
