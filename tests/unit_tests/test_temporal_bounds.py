"""Unit contract of sva2yosys.temporal.bounds (hand-derived span algebra)."""

from __future__ import annotations

import pytest

from sva2yosys.ir.expr import BIT, Const, Signal
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat, SeqIntersect, Sequence
from sva2yosys.temporal.bounds import SequenceBounds, obligation_slots, sequence_bounds

A = Bool(Signal("m.a", BIT))
B = Bool(Signal("m.b", BIT))
FALSE = Bool(Const(0, BIT))


def _cat(left: Sequence, low: int, high: int, right: Sequence) -> SeqConcat:
    return SeqConcat(left, Delay(low, high), right)


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        (A, SequenceBounds(False, 1, 1)),
        (FALSE, SequenceBounds(False, 1, 1)),
        (TRUE, SequenceBounds(False, 1, 1)),
        (_cat(A, 1, 1, B), SequenceBounds(False, 2, 2)),
        (_cat(A, 0, 0, B), SequenceBounds(False, 1, 1)),
        (_cat(A, 0, 2, B), SequenceBounds(False, 1, 3)),
        (_cat(A, 3, 3, B), SequenceBounds(False, 4, 4)),
        (Repeat(B, 3, 3), SequenceBounds(False, 3, 3)),
        (Repeat(B, 2, 5), SequenceBounds(False, 2, 5)),
        (Repeat(B, 0, 2), SequenceBounds(True, 1, 2)),
        (Repeat(B, 0, 0), SequenceBounds(True, None, None)),
        # empty left operand: b[*0] ##1 c is c
        (_cat(Repeat(A, 0, 0), 1, 1, B), SequenceBounds(False, 1, 1)),
        # empty right operand: b ##1 c[*0] is b
        (_cat(A, 1, 1, Repeat(B, 0, 0)), SequenceBounds(False, 1, 1)),
        # ##0 needs non-empty operands on both sides
        (_cat(Repeat(A, 0, 0), 0, 0, B), SequenceBounds(False, None, None)),
        # x in {0,1}, ##2, y in {0,1}: lengths 1..3, never empty
        (_cat(Repeat(A, 0, 1), 2, 2, Repeat(B, 0, 1)), SequenceBounds(False, 1, 3)),
        # both empty with ##1 gives the empty match
        (_cat(Repeat(A, 0, 1), 1, 1, Repeat(B, 0, 1)), SequenceBounds(True, 1, 2)),
        (Repeat(_cat(A, 1, 1, B), 2, 2), SequenceBounds(False, 4, 4)),
        (Repeat(Repeat(A, 0, 1), 2, 2), SequenceBounds(True, 1, 2)),
        # a constant false operand is a letter of length 1 (evaluated at its sample)
        (_cat(A, 1, 1, FALSE), SequenceBounds(False, 2, 2)),
        (_cat(A, 1, 1, Repeat(FALSE, 0, 1)), SequenceBounds(False, 1, 2)),
        # only structure removes matches: lengths that never meet
        (SeqIntersect(_cat(A, 1, 1, B), FALSE), SequenceBounds(False, None, None)),
        # two empty operands: ##1 gives the empty match, ##2 and ##3 lengths 1 and 2
        (_cat(Repeat(A, 0, 0), 1, 3, Repeat(B, 0, 0)), SequenceBounds(True, 1, 2)),
        (_cat(Repeat(A, 0, 0), 2, 3, Repeat(B, 0, 0)), SequenceBounds(False, 1, 2)),
    ],
)
def test_sequence_bounds(sequence: Sequence, expected: SequenceBounds) -> None:
    assert sequence_bounds(sequence) == expected


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        (_cat(A, 0, 10**9, B), SequenceBounds(False, 1, 10**9 + 1)),
        (_cat(Repeat(A, 0, 1), 1, 10**9, Repeat(B, 0, 1)), SequenceBounds(True, 1, 10**9 + 1)),
        (Repeat(B, 0, 10**9), SequenceBounds(True, 1, 10**9)),
        (Repeat(_cat(A, 1, 1, B), 10**9, 10**9), SequenceBounds(False, 2 * 10**9, 2 * 10**9)),
    ],
)
def test_huge_ranges_are_analysed_without_enumeration(
    sequence: Sequence, expected: SequenceBounds
) -> None:
    """The bounds come from the range ends: a range of 10**9 is as cheap as a short one."""
    assert sequence_bounds(sequence) == expected


@pytest.mark.parametrize(
    ("consequent", "slots"),
    [
        (B, 0),  # a |-> b: decided in the trigger sample
        (_cat(TRUE, 1, 1, B), 1),  # a |=> b, normalised
        (_cat(TRUE, 1, 5, B), 5),  # a |-> ##[1:5] b
        (_cat(B, 1, 3, A), 3),  # a |-> b ##[1:3] c
        (Repeat(B, 2, 5), 4),
        (FALSE, 0),
        (_cat(A, 1, 1, FALSE), 1),
    ],
)
def test_obligation_slots(consequent: Sequence, slots: int) -> None:
    assert obligation_slots(consequent) == slots
