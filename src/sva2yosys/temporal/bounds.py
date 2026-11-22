"""Structural duration analysis of sequences (half-open match lengths in samples).

A match covering samples ``s … e - 1`` has length ``e - s``; length 0 is the empty match.
The algebra follows the matching rules of ``docs/semantics.md`` and Annex F:

- ``Bool(e)``: length 1, also for a constant false condition (``Const`` 0): the constant is
  evaluated at its sample like any condition (``docs/semantics.md`` §3), so
  it is a letter of length 1 that no known sample satisfies, not a sequence without matches.
- ``l ##k r`` with ``k >= 1``: ``|x| + (k - 1) + |y|`` for every match ``x`` of ``l`` and
  ``y`` of ``r``, either of which may be empty.
- ``l ##0 r``: ``|x| + |y| - 1`` for non-empty ``x`` and ``y`` only (they share a sample).
- ``s[*i:j]``: the concatenation (``##1`` without gap) of ``k`` matches of ``s`` for
  ``k`` in ``[i, j]``; ``k = 0`` is the empty match.

- ``s[*i:$]`` and ``l ##[i:$] r`` have no longest match: ``max_length`` is ``None`` while
  ``min_length`` is not (:attr:`SequenceBounds.unbounded`). ``$`` is never replaced by a
  finite limit.
- ``b[->i:j]`` and ``b[=i:j]`` are analysed through their definitions (§16.9.2:
  :func:`~sva2yosys.ir.sequence.goto_expansion`,
  :func:`~sva2yosys.ir.sequence.nonconsecutive_expansion`): they have no longest match,
  whatever the count range.

The analysis never iterates over a range: the bounds only depend on its ends (see
:func:`_concat` and :func:`_repeat`), so a range of ``10**9`` costs as little as ``[1:2]``.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    goto_expansion,
    nonconsecutive_expansion,
)


@dataclass(frozen=True)
class SequenceBounds:
    """Durations of a sequence's matches.

    Attributes:
        nullable: The empty match is possible.
        min_length: Shortest non-empty match, or ``None`` when no non-empty match exists
            (only structural emptiness is taken into account, e.g. an ``intersect`` of
            fixed lengths that never meet; the value of a condition never is).
        max_length: Longest non-empty match, or ``None`` when no non-empty match exists
            or when there is no longest one (then ``min_length`` is not ``None``: see
            :attr:`unbounded`).
    """

    nullable: bool
    min_length: int | None
    max_length: int | None

    @property
    def unbounded(self) -> bool:
        """Some non-empty match exists and there is no longest one (``$``)."""
        return self.min_length is not None and self.max_length is None


def sequence_bounds(sequence: Sequence) -> SequenceBounds:
    """The structural bounds of ``sequence``, in time linear in its size (independent of
    its delay and repetition ranges). An unbounded range gives ``max_length`` ``None``."""
    match sequence:
        case Bool():
            return SequenceBounds(False, 1, 1)
        case SeqConcat(left=left, delay=delay, right=right):
            return _concat(
                sequence_bounds(left), sequence_bounds(right), delay.minimum, delay.maximum
            )
        case Repeat(sequence=inner, minimum=minimum, maximum=maximum):
            return _repeat(sequence_bounds(inner), minimum, maximum)
        case SeqIntersect(left=left, right=right) | SeqAnd(left=left, right=right):
            first, second = sequence_bounds(left), sequence_bounds(right)
            if first.min_length is None or second.min_length is None:
                return SequenceBounds(False, None, None)
            low = max(first.min_length, second.min_length)
            if isinstance(sequence, SeqAnd):
                return SequenceBounds(False, low, _later(first.max_length, second.max_length))
            high = _earlier(first.max_length, second.max_length)
            return (
                SequenceBounds(False, low, high)
                if high is None or low <= high
                else SequenceBounds(False, None, None)
            )
        case Within(inner=inner, outer=outer):
            first, second = sequence_bounds(inner), sequence_bounds(outer)
            if first.min_length is None or second.min_length is None:
                return SequenceBounds(False, None, None)
            low = max(first.min_length, second.min_length)
            if second.max_length is not None and low > second.max_length:
                return SequenceBounds(False, None, None)
            return SequenceBounds(False, low, second.max_length)
        case Throughout(sequence=inner):
            return sequence_bounds(inner)
        case Goto():
            return sequence_bounds(goto_expansion(sequence))
        case NonConsecutive():
            return sequence_bounds(nonconsecutive_expansion(sequence))
        case SeqOr(left=left, right=right):
            first, second = sequence_bounds(left), sequence_bounds(right)
            shortest = [n for n in (first.min_length, second.min_length) if n is not None]
            if not shortest:
                return SequenceBounds(first.nullable or second.nullable, None, None)
            return SequenceBounds(
                first.nullable or second.nullable,
                min(shortest),
                _later(
                    first.max_length if first.min_length is not None else 0,
                    second.max_length if second.min_length is not None else 0,
                ),
            )


def unbounded_sequence(sequence: Sequence) -> bool:
    """Whether ``sequence`` has an unbounded part (structurally): a ``$`` range, or a goto
    or nonconsecutive repetition (which has no longest match whatever its count)."""
    match sequence:
        case Bool():
            return False
        case SeqConcat(left=left, delay=delay, right=right):
            return delay.maximum is None or unbounded_sequence(left) or unbounded_sequence(right)
        case Repeat(sequence=inner, maximum=maximum):
            return maximum is None or unbounded_sequence(inner)
        case Goto() | NonConsecutive():
            return True
        case (
            SeqOr(left=left, right=right)
            | SeqAnd(left=left, right=right)
            | SeqIntersect(left=left, right=right)
            | Within(inner=left, outer=right)
        ):
            return unbounded_sequence(left) or unbounded_sequence(right)
        case Throughout(sequence=inner):
            return unbounded_sequence(inner)


def obligation_slots(consequent: Sequence) -> int:
    """Registered ages an obligation for ``consequent`` may need.

    An obligation started at sample ``p`` is evaluated combinationally at ``p`` (age 0) and
    carried in registered slots for ages ``1 … max_length - 1`` at most: no non-empty match
    is longer, and every live path ends in a pass or a failure by then. ``consequent``
    is the normalised one (``temporal.compile.operands``).

    Returns:
        ``max(0, max_length - 1)``, and 0 when the sequence has no non-empty match.

    Raises:
        ValueError: The sequence is unbounded: no finite number of slots suffices (an
            unbounded consequent is outside the slot machine).
    """
    bounds = sequence_bounds(consequent)
    if bounds.unbounded:
        raise ValueError("an unbounded consequent needs unboundedly many obligation slots")
    return 0 if bounds.max_length is None else max(0, bounds.max_length - 1)


def _later(first: int | None, second: int | None) -> int | None:
    """The larger of two lengths, ``None`` (unbounded) if either is."""
    return None if first is None or second is None else max(first, second)


def _earlier(first: int | None, second: int | None) -> int | None:
    """The smaller of two lengths, where ``None`` (unbounded) is larger than any."""
    if first is None:
        return second
    return first if second is None else min(first, second)


def _plus(*parts: int | None) -> int | None:
    """The sum of lengths, ``None`` (unbounded) if any is."""
    total = 0
    for part in parts:
        if part is None:
            return None
        total += part
    return total


type _Span = tuple[int, int | None]
"""A range of possible lengths of one kind of match (``(0, 0)`` for the empty match; a
``None`` upper end is unbounded)."""


def _options(bounds: SequenceBounds) -> list[_Span]:
    """The length ranges a match of a sequence can take, the empty match included."""
    spans: list[_Span] = [(0, 0)] if bounds.nullable else []
    if bounds.min_length is not None:
        spans.append((bounds.min_length, bounds.max_length))
    return spans


def _combine(nullable: bool, spans: list[_Span]) -> SequenceBounds:
    non_empty = [span for span in spans if span[1] is None or span[1] > 0]
    if not non_empty:
        return SequenceBounds(nullable, None, None)
    longest: int | None = 0
    for _, high in non_empty:
        longest = _later(longest, high)
    return SequenceBounds(nullable, min(max(1, low) for low, _ in non_empty), longest)


def _concat(
    left: SequenceBounds, right: SequenceBounds, low: int, high: int | None
) -> SequenceBounds:
    """The bounds of ``left ##[low:high] right`` (``high`` ``None`` is ``$``).

    Each operand pairing's span is a translate by the delay, so the shortest non-empty
    span comes from the smallest delay giving a non-empty one (the smallest delay, or the
    next one when both operands are empty and the smallest is 1, the only delay giving the
    empty match) and the longest from ``high`` (none, for ``$``): those delays give the
    same bounds as the whole range."""
    spans: list[_Span] = []
    nullable = False
    if low == 0 and left.min_length is not None and right.min_length is not None:
        spans.append(
            (left.min_length + right.min_length - 1, _plus(left.max_length, right.max_length, -1))
        )
    first = max(low, 1)
    delays: list[int | None] = [
        *sorted(
            d
            for d in {first, first + 1, high}
            if d is not None and first <= d and (high is None or d <= high)
        )
    ]
    if high is None:
        delays.append(None)  # the unbounded tail of the range
    for delay in delays:
        for x_low, x_high in _options(left):
            for y_low, y_high in _options(right):
                # (the shortest of the unbounded tail is that of the smallest delay)
                minimum = x_low + (first if delay is None else delay) - 1 + y_low
                maximum = _plus(x_high, y_high, None if delay is None else delay - 1)
                if (minimum, maximum) == (0, 0):
                    nullable = True
                else:
                    spans.append((minimum, maximum))
    return _combine(nullable, spans)


def _repeat(inner: SequenceBounds, low: int, high: int | None) -> SequenceBounds:
    """The bounds of ``inner[*low:high]``: the span is monotone in the count, so the
    smallest and largest counts give the bounds of the whole range (no largest for
    ``$``)."""
    nullable = low == 0 or ((high is None or high >= 1) and inner.nullable)
    spans: list[_Span] = []
    if inner.min_length is not None:
        smallest = max(low, 1)
        for count in sorted(n for n in {smallest, high} if n is not None and smallest <= n):
            if high is not None and count > high:
                continue
            shortest = inner.min_length if inner.nullable else count * inner.min_length
            longest = None if inner.max_length is None else count * inner.max_length
            spans.append((shortest, longest))
        if high is None:
            shortest = inner.min_length if inner.nullable else smallest * inner.min_length
            spans.append((shortest, None))
    return _combine(nullable, spans)
