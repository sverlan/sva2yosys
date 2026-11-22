"""Unit contract of the unbounded ranges of sva2yosys.temporal.bounds and .automaton:
``$`` is never replaced by a finite limit.

Bounds: an unbounded range gives ``max_length`` ``None`` while ``min_length`` is set
(:attr:`~sva2yosys.temporal.bounds.SequenceBounds.unbounded`). Automata: an unbounded delay
or repetition builds a loop, and goto / nonconsecutive repetition are built from their
definitions (§16.9.2). All expectations are derived by hand.
"""

from __future__ import annotations

import pytest

from sva2yosys.ir.expr import BIT, Const, Signal, Unary, UnaryOp
from sva2yosys.ir.sequence import (
    Bool,
    Delay,
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
from sva2yosys.temporal.automaton import (
    Automaton,
    ConditionTable,
    build_automaton,
    has_cycle,
    longest_path,
)
from sva2yosys.temporal.bounds import SequenceBounds, obligation_slots, sequence_bounds

SIGNAL_A = Signal("m.a", BIT)
SIGNAL_B = Signal("m.b", BIT)
A = Bool(SIGNAL_A)
B = Bool(SIGNAL_B)
C = Bool(Signal("m.c", BIT))
FALSE = Bool(Const(0, BIT))


def _cat(left: Sequence, low: int, high: int | None, right: Sequence) -> SeqConcat:
    return SeqConcat(left, Delay(low, high), right)


# --- bounds --------------------------------------------------------------------------------

_BOUNDS = [
    (_cat(A, 1, None, B), SequenceBounds(False, 2, None)),
    (_cat(A, 0, None, B), SequenceBounds(False, 1, None)),
    (_cat(A, 3, None, B), SequenceBounds(False, 4, None)),
    (Repeat(A, 1, None), SequenceBounds(False, 1, None)),
    (Repeat(A, 3, None), SequenceBounds(False, 3, None)),
    (Repeat(A, 0, None), SequenceBounds(True, 1, None)),
    (Repeat(_cat(A, 1, 1, B), 2, None), SequenceBounds(False, 4, None)),
    # empty operands: a[*0:$] ##1 b is b or a..b; ##0 needs non-empty operands
    (_cat(Repeat(A, 0, None), 1, 1, B), SequenceBounds(False, 1, None)),
    (_cat(Repeat(A, 0, None), 0, 0, B), SequenceBounds(False, 1, None)),
    (_cat(Repeat(A, 0, None), 1, 1, Repeat(B, 0, None)), SequenceBounds(True, 1, None)),
    (Repeat(Repeat(A, 0, 1), 1, None), SequenceBounds(True, 1, None)),
    # goto and nonconsecutive repetition have no longest match, whatever the counts
    (Goto(SIGNAL_A, 2, 2), SequenceBounds(False, 2, None)),
    (Goto(SIGNAL_A, 1, None), SequenceBounds(False, 1, None)),
    (Goto(SIGNAL_A, 0, 2), SequenceBounds(True, 1, None)),
    (NonConsecutive(SIGNAL_A, 2, 2), SequenceBounds(False, 2, None)),
    (NonConsecutive(SIGNAL_A, 0, 1), SequenceBounds(True, 1, None)),
    # composition
    (SeqOr(A, _cat(A, 1, None, B)), SequenceBounds(False, 1, None)),
    (SeqAnd(A, Repeat(B, 1, None)), SequenceBounds(False, 1, None)),
    (SeqIntersect(_cat(A, 2, 3, B), Repeat(B, 1, None)), SequenceBounds(False, 3, 4)),
    (SeqIntersect(_cat(A, 3, 3, B), Repeat(B, 1, 2)), SequenceBounds(False, None, None)),
    (Within(A, Repeat(B, 2, None)), SequenceBounds(False, 2, None)),
    (Throughout(SIGNAL_A, Repeat(B, 1, None)), SequenceBounds(False, 1, None)),
]


@pytest.mark.parametrize(("sequence", "expected"), _BOUNDS)
def test_unbounded_sequences_have_no_longest_match(
    sequence: Sequence, expected: SequenceBounds
) -> None:
    assert sequence_bounds(sequence) == expected


def test_only_a_sequence_with_matches_and_no_longest_one_is_unbounded() -> None:
    assert not SequenceBounds(False, None, None).unbounded
    assert not SequenceBounds(True, None, None).unbounded
    assert not SequenceBounds(False, 1, 3).unbounded
    assert SequenceBounds(False, 1, None).unbounded
    assert sequence_bounds(_cat(A, 1, None, B)).unbounded
    assert not sequence_bounds(_cat(A, 1, 10**9, B)).unbounded


def test_obligation_slots_refuses_an_unbounded_consequent() -> None:
    with pytest.raises(ValueError, match="unbounded"):
        obligation_slots(_cat(A, 1, None, B))
    with pytest.raises(ValueError, match="unbounded"):
        obligation_slots(Goto(SIGNAL_A, 1, 1))


# --- automata ------------------------------------------------------------------------------


def _build(sequence: Sequence) -> tuple[Automaton, ConditionTable]:
    table = ConditionTable()
    return build_automaton(sequence, table), table


def _ends(automaton: Automaton, table: ConditionTable, rows: list[set[str]]) -> set[int]:
    """Samples at which a non-empty match starting at 0 ends (the contract of the module
    docstring of ``temporal.automaton``); ``rows[t]`` is the set of signals true at ``t``."""

    def holds(condition: int, t: int) -> bool:
        expr = table.conditions[condition]
        if isinstance(expr, Const):
            return expr.value != 0
        if isinstance(expr, Unary):  # the only operator the definitions of §16.9.2 add: !b
            assert expr.op is UnaryOp.LOGICAL_NOT and isinstance(expr.operand, Signal)
            return expr.operand.path.removeprefix("m.") not in rows[t]
        assert isinstance(expr, Signal)
        return expr.path.removeprefix("m.") in rows[t]

    ends: set[int] = set()
    frontier = set(automaton.start)
    for t in range(len(rows)):
        held = {i for i in frontier if all(holds(g, t) for g in automaton.letters[i].guards)}
        if any(automaton.letters[i].accepting for i in held):
            ends.add(t)
        frontier = {n for i in held for n in automaton.letters[i].next}
    return ends


_N: set[str] = set()
_ENDS: list[tuple[Sequence, list[set[str]], set[int]]] = [
    # a ##[1:$] b: any later b ends a match
    (_cat(A, 1, None, B), [{"a"}, _N, {"b"}, _N, {"b"}], {2, 4}),
    (_cat(A, 1, None, B), [{"a", "b"}, _N], set()),
    (_cat(A, 1, None, B), [{"a"}, {"b"}, {"b"}], {1, 2}),
    # a ##[2:$] b: not the next sample
    (_cat(A, 2, None, B), [{"a"}, {"b"}, {"b"}, {"b"}, _N], {2, 3}),
    (_cat(A, 3, None, B), [{"a"}, {"b"}, {"b"}, {"b"}, {"b"}], {3, 4}),
    # a ##[0:$] b fuses or waits
    (_cat(A, 0, None, B), [{"a", "b"}, {"b"}, _N, {"b"}], {0, 1, 3}),
    (_cat(A, 0, None, B), [{"b"}, {"a"}, {"b"}], set()),
    # a[*1:$]: the run ends at every sample while a holds
    (Repeat(A, 1, None), [{"a"}, {"a"}, _N, {"a"}], {0, 1}),
    (Repeat(A, 2, None), [{"a"}, {"a"}, {"a"}, _N], {1, 2}),
    (Repeat(A, 3, None), [{"a"}, {"a"}, {"a"}, {"a"}], {2, 3}),
    # a[*0:$] ##1 b: the empty run lets b match at the first sample
    (_cat(Repeat(A, 0, None), 1, 1, B), [{"b"}, {"a"}], {0}),
    (_cat(Repeat(A, 0, None), 1, 1, B), [{"a"}, {"a"}, {"b"}, {"b"}], {2}),
    (_cat(Repeat(A, 0, None), 1, 1, B), [{"a"}, _N, {"b"}], set()),
    # a ##1 b[*0:$]: the match may end at a, or after any run of b
    (_cat(A, 1, 1, Repeat(B, 0, None)), [{"a"}, {"b"}, {"b"}, _N], {0, 1, 2}),
    # a ##[1:$] b[*0:$]: the right operand may be empty: ends at every later sample
    (_cat(A, 1, None, Repeat(B, 0, None)), [{"a"}, _N, _N, {"b"}], {0, 1, 2, 3}),
    (_cat(A, 2, None, Repeat(B, 0, None)), [{"a"}, _N, _N, {"b"}], {1, 2, 3}),
    # (a ##1 b)[*1:$]
    (Repeat(_cat(A, 1, 1, B), 1, None), [{"a"}, {"b"}, {"a"}, {"b"}, {"a"}], {1, 3}),
    (Repeat(_cat(A, 1, 1, B), 2, None), [{"a"}, {"b"}, {"a"}, {"b"}, {"a"}, {"b"}], {3, 5}),
    # goto: b[->2] ends exactly at the second b
    (Goto(SIGNAL_B, 2, 2), [_N, {"b"}, _N, {"b"}, {"b"}], {3}),
    (Goto(SIGNAL_B, 1, 2), [_N, {"b"}, _N, {"b"}, {"b"}], {1, 3}),
    (Goto(SIGNAL_B, 1, None), [_N, {"b"}, _N, {"b"}, {"b"}], {1, 3, 4}),
    (Goto(SIGNAL_B, 2, None), [{"b"}, {"b"}, _N, {"b"}], {1, 3}),
    # nonconsecutive: b[=2] ends at the second b or any later sample before the third
    (NonConsecutive(SIGNAL_B, 2, 2), [{"b"}, _N, {"b"}, _N, {"b"}], {2, 3}),
    (NonConsecutive(SIGNAL_B, 2, 2), [{"b"}, _N, {"b"}, _N, _N], {2, 3, 4}),
    (NonConsecutive(SIGNAL_B, 1, 2), [{"b"}, _N, {"b"}, _N], {0, 1, 2, 3}),
    (NonConsecutive(SIGNAL_B, 0, 1), [{"b"}, _N, _N], {0, 1, 2}),
    (NonConsecutive(SIGNAL_B, 0, 0), [_N, _N, {"b"}], {0, 1}),
    # goto then c right after the occurrence, nonconsecutive then c anywhere after
    (_cat(Goto(SIGNAL_B, 2, 2), 1, 1, C), [{"b"}, {"b"}, {"c"}, {"b"}], {2}),
    (_cat(NonConsecutive(SIGNAL_B, 2, 2), 1, 1, C), [{"b"}, {"b"}, {"c"}, {"c"}], {2, 3}),
    # a throughout of a looped sequence, and an intersect of two looped sequences
    (Throughout(SIGNAL_A, Repeat(B, 1, None)), [{"a", "b"}, {"a", "b"}, {"b"}], {0, 1}),
    (SeqIntersect(Repeat(A, 1, None), Repeat(B, 2, None)), [{"a", "b"}] * 3, {1, 2}),
]


@pytest.mark.parametrize(
    ("sequence", "rows", "ends"), _ENDS, ids=[str(index) for index in range(len(_ENDS))]
)
def test_unbounded_sequences_match_as_defined(
    sequence: Sequence, rows: list[set[str]], ends: set[int]
) -> None:
    automaton, table = _build(sequence)
    assert _ends(automaton, table, rows) == ends


def test_an_unbounded_range_builds_a_loop() -> None:
    for sequence in (_cat(A, 1, None, B), Repeat(A, 1, None), Goto(SIGNAL_B, 2, 2)):
        automaton, _ = _build(sequence)
        assert has_cycle(automaton)
        with pytest.raises(ValueError, match="unbounded"):
            longest_path(automaton)
    for sequence in (_cat(A, 1, 3, B), Repeat(A, 2, 5), _cat(A, 0, 0, B)):
        automaton, _ = _build(sequence)
        assert not has_cycle(automaton)


def test_dead_loops_are_pruned_structurally() -> None:
    """A loop that cannot reach a match disappears (here the lengths never meet: the product
    of ``a ##[1:$] b`` with a single letter has no accepting letter)."""
    automaton, _ = _build(SeqIntersect(_cat(A, 1, None, B), C))
    assert (automaton.letters, automaton.start) == ((), ())
    assert not has_cycle(automaton)


def test_a_loop_of_a_constant_false_condition_stays_alive() -> None:
    """``1'b0`` is evaluated at its sample, so a loop around it is a live loop."""
    automaton, table = _build(_cat(A, 1, None, FALSE))
    assert has_cycle(automaton) and len(automaton.letters) == 3  # a, the filler loop, 1'b0
    assert table.conditions == [Signal("m.a", BIT), Const(0, BIT)]
    automaton, _ = _build(Repeat(FALSE, 1, None))
    assert has_cycle(automaton) and len(automaton.letters) == 1


def test_nullability_of_unbounded_sequences() -> None:
    assert _build(Repeat(A, 0, None))[0].nullable
    assert not _build(Repeat(A, 1, None))[0].nullable
    assert _build(_cat(Repeat(A, 0, None), 1, None, Repeat(B, 0, None)))[0].nullable
    assert not _build(_cat(Repeat(A, 0, None), 2, None, Repeat(B, 0, None)))[0].nullable
    assert _build(NonConsecutive(SIGNAL_B, 0, 0))[0].nullable
    assert _build(Goto(SIGNAL_B, 0, 1))[0].nullable
    assert not _build(Goto(SIGNAL_B, 1, 1))[0].nullable


def test_goto_and_nonconsecutive_are_built_from_their_definitions() -> None:
    """The machines use the definition of §16.9.2 (``ir.sequence.goto_expansion``)."""
    assert _build(Goto(SIGNAL_B, 2, 3))[0] == _build(goto_expansion(Goto(SIGNAL_B, 2, 3)))[0]
    assert (
        _build(NonConsecutive(SIGNAL_B, 1, None))[0]
        == _build(nonconsecutive_expansion(NonConsecutive(SIGNAL_B, 1, None)))[0]
    )


def test_a_constant_condition_in_a_goto_is_evaluated_at_its_sample() -> None:
    # 1'b1[->2] is (!1'b1[*0:$] ##1 1'b1)[*2]: the negation is the constant-false letter, kept
    automaton, table = _build(Goto(Const(1, BIT), 2, 2))
    assert table.conditions == [Const(0, BIT)]
    assert _ends(automaton, table, [_N, _N, _N]) == {1}  # two consecutive hits
    # 1'b0[->1] is (1'b1)[*0:$] ##1 1'b0: alive while the loop holds, never matched
    automaton, table = _build(Goto(Const(0, BIT), 1, 1))
    assert has_cycle(automaton) and table.conditions == [Const(0, BIT)]
    assert _ends(automaton, table, [_N, _N, _N]) == set()


def test_the_definitions_of_goto_and_nonconsecutive_repetition() -> None:
    step = goto_expansion(Goto(SIGNAL_B, 2, 3))
    assert isinstance(step, Repeat)
    assert (step.minimum, step.maximum) == (2, 3)
    assert isinstance(step.sequence, SeqConcat)
    assert step.sequence.delay == Delay(1, 1)
    assert step.sequence.right == B
    skip = step.sequence.left
    assert isinstance(skip, Repeat)
    assert (skip.minimum, skip.maximum) == (0, None)
    expanded = nonconsecutive_expansion(NonConsecutive(SIGNAL_B, 2, 3))
    assert isinstance(expanded, SeqConcat)
    assert expanded.left == step
    assert expanded.delay == Delay(1, 1)
    assert expanded.right == skip
