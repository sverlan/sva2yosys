"""Unit contract of sva2yosys.temporal.automaton (module docstring)."""

from __future__ import annotations

import pytest

from sva2yosys.ir.expr import BIT, Const, Signal
from sva2yosys.ir.sequence import (
    TRUE,
    Bool,
    Delay,
    Repeat,
    SeqConcat,
    SeqIntersect,
    SeqOr,
    Sequence,
)
from sva2yosys.temporal.automaton import (
    Automaton,
    ConditionTable,
    Letter,
    build_automaton,
    longest_path,
)
from sva2yosys.temporal.budgets import ResourceLimits, Work

A = Bool(Signal("m.a", BIT))
B = Bool(Signal("m.b", BIT))
C = Bool(Signal("m.c", BIT))
FALSE = Bool(Const(0, BIT))


def _cat(left: Sequence, low: int, high: int, right: Sequence) -> SeqConcat:
    return SeqConcat(left, Delay(low, high), right)


def _build(sequence: Sequence) -> tuple[Automaton, ConditionTable]:
    table = ConditionTable()
    return build_automaton(sequence, table), table


@pytest.mark.parametrize("edges", [(), ((),), ((0,),), ((1, 2), (1, 2), (0, 2)), ((1, 1), ())])
def test_incoming_edges_preserve_each_source_once_in_ascending_order(
    edges: tuple[tuple[int, ...], ...],
) -> None:
    # Includes a self-loop, accepting continuations, merging paths and duplicate edges.
    machine = Automaton(tuple(Letter((), targets, True) for targets in edges), (), False)
    expected = tuple(
        tuple(source for source, targets in enumerate(edges) if target in targets)
        for target in range(len(edges))
    )
    assert machine.incoming == expected
    assert machine.incoming is machine.incoming
    for bits in range(1 << len(edges)):
        held = {i for i in range(len(edges)) if bits & (1 << i)}
        forward = {target for source in held for target in edges[source]}
        reverse = {
            target for target, sources in enumerate(machine.incoming) if held.intersection(sources)
        }
        assert reverse == forward


def test_long_chain_pruning_fits_linear_work_and_preserves_numbering() -> None:
    # The old fixed-point scan exhausts this allowance before finishing the chain.
    work = Work(ResourceLimits(4096, 30000, 8192, 200000))
    machine = build_automaton(Repeat(A, 1000, 1000), ConditionTable(), work=work)
    assert len(machine.letters) == 1000
    assert machine.start == (0,)
    assert all(letter.guards == (0,) for letter in machine.letters)
    assert [letter.next for letter in machine.letters] == [(i + 1,) for i in range(999)] + [()]
    assert [i for i, letter in enumerate(machine.letters) if letter.accepting] == [999]


def test_dead_cycle_is_pruned_but_a_constant_false_live_path_is_retained() -> None:
    # Even lengths 2,4,... cannot intersect odd lengths 3,5,..., despite cycles.
    even = Repeat(_cat(A, 1, 1, A), 1, None)
    odd = _cat(B, 1, 1, Repeat(_cat(B, 1, 1, B), 1, None))
    dead = SeqIntersect(even, odd)
    empty, _ = _build(dead)
    assert empty.letters == () and empty.start == () and not empty.nullable
    machine, table = _build(SeqOr(dead, _cat(FALSE, 1, 1, C)))
    assert machine.start == (0,)
    assert len(machine.letters) == 2
    assert machine.letters[0].next == (1,)
    assert table.conditions[machine.letters[0].guards[0]] == Const(0, BIT)
    assert not machine.letters[0].accepting and machine.letters[1].accepting


def test_large_delay_window_fits_linear_probe_work_and_matches_every_endpoint() -> None:
    work = Work(ResourceLimits(4096, 40000, 8192, 200000))
    table = ConditionTable()
    machine = build_automaton(_cat(A, 1, 1000, B), table, work=work)
    assert len(machine.letters) == 1001
    assert machine.start == (0,)
    assert _ends(machine, table, [{"a", "b"}] * 1001) == set(range(1, 1001))


def _ends(automaton: Automaton, table: ConditionTable, rows: list[set[str]]) -> set[int]:
    """Samples at which a non-empty match starting at 0 ends, interpreting the contract.

    ``rows[t]`` is the set of signal names that hold at sample ``t``.
    """

    def holds(condition: int, t: int) -> bool:
        expr = table.conditions[condition]
        if isinstance(expr, Const):
            return expr.value != 0
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


def test_single_condition() -> None:
    automaton, table = _build(A)
    assert table.conditions == [Signal("m.a", BIT)]
    assert len(automaton.letters) == 1
    assert automaton.start == (0,)
    letter = automaton.letters[0]
    assert (letter.guards, letter.next, letter.accepting) == ((0,), (), True)
    assert not automaton.nullable


def test_constant_conditions() -> None:
    automaton, table = _build(TRUE)
    assert table.conditions == []
    assert automaton.letters[0].guards == ()
    # a constant false condition is an ordinary letter: guarded by the constant,
    # which no sample satisfies, so it refutes a partial match at its own sample
    false, false_table = _build(FALSE)
    assert false_table.conditions == [Const(0, BIT)]
    assert len(false.letters) == 1 and false.letters[0].guards == (0,)
    assert false.letters[0].accepting and false.start == (0,)
    assert _ends(false, false_table, [{"a"}, {"a"}]) == set()


def test_a_constant_false_letter_is_evaluated_at_its_own_sample() -> None:
    """``a ##1 1'b0``: alive after sample 0 whenever ``a`` holds, refuted at sample 1."""
    automaton, table = _build(_cat(A, 1, 1, FALSE))
    assert [letter.guards for letter in automaton.letters] == [(0,), (1,)]
    assert table.conditions == [Signal("m.a", BIT), Const(0, BIT)]
    assert automaton.letters[0].next == (1,) and automaton.letters[1].accepting
    # after sample 0 the frontier is {1'b0's letter}: not empty, so not yet failed
    assert automaton.start == (0,)
    assert _ends(automaton, table, [{"a"}, {"a"}, {"a"}]) == set()


def test_fusion_combines_guards() -> None:
    automaton, _ = _build(_cat(A, 0, 0, B))
    assert len(automaton.letters) == 1
    assert automaton.letters[0].guards == (0, 1)
    assert automaton.letters[0].accepting


def test_conditions_are_shared_and_numbered_in_order() -> None:
    _, table = _build(_cat(B, 1, 1, _cat(A, 1, 1, B)))
    assert table.conditions == [Signal("m.b", BIT), Signal("m.a", BIT)]
    assert table.identify(Signal("m.a", BIT)) == 1


@pytest.mark.parametrize(
    ("sequence", "rows", "ends"),
    [
        (_cat(A, 1, 1, B), [{"a"}, {"b"}, set()], {1}),
        (_cat(A, 1, 3, B), [{"a"}, set(), {"b"}, {"b"}, {"b"}], {2, 3}),
        (_cat(A, 0, 2, B), [{"a", "b"}, {"b"}, set()], {0, 1}),
        (_cat(TRUE, 2, 2, B), [set(), set(), {"b"}], {2}),
        (Repeat(B, 2, 3), [{"b"}, {"b"}, {"b"}, {"b"}], {1, 2}),
        (_cat(Repeat(A, 0, 1), 1, 1, B), [{"b"}, {"b"}], {0}),
        (_cat(Repeat(A, 0, 1), 1, 1, B), [{"a"}, {"b"}], {1}),
        (_cat(A, 1, 1, Repeat(B, 0, 1)), [{"a"}, {"b"}], {0, 1}),
        (_cat(Repeat(A, 0, 1), 2, 2, B), [set(), {"b"}, {"b"}], {1}),
        (Repeat(_cat(A, 1, 1, B), 2, 2), [{"a"}, {"b"}, {"a"}, {"b"}], {3}),
        (_cat(_cat(A, 0, 0, B), 1, 1, C), [{"a", "b"}, {"c"}], {1}),
        (_cat(_cat(A, 0, 0, B), 1, 1, C), [{"a"}, {"c"}], set()),
    ],
)
def test_matches_follow_the_contract(
    sequence: Sequence, rows: list[set[str]], ends: set[int]
) -> None:
    automaton, table = _build(sequence)
    assert _ends(automaton, table, rows) == ends


def test_dead_letters_are_pruned_structurally() -> None:
    """Only shape and lengths prune (``docs/semantics.md`` §3): an ``intersect`` of fixed
    lengths that never meet has no letter at all, whatever the conditions are."""
    automaton, _ = _build(SeqIntersect(_cat(A, 1, 1, B), C))
    assert (automaton.letters, automaton.start) == ((), ())
    # a constant false letter is not dead: it is evaluated at its sample
    branch, _ = _build(_cat(A, 1, 1, Repeat(FALSE, 0, 1)))
    assert len(branch.letters) == 2
    assert [letter.accepting for letter in branch.letters] == [True, True]


def test_every_letter_is_reachable_and_can_accept() -> None:
    automaton, _ = _build(_cat(Repeat(_cat(A, 0, 1, B), 1, 2), 0, 2, Repeat(C, 0, 2)))
    reachable = set(automaton.start)
    frontier = list(automaton.start)
    while frontier:
        for n in automaton.letters[frontier.pop()].next:
            if n not in reachable:
                reachable.add(n)
                frontier.append(n)
    assert reachable == set(range(len(automaton.letters)))
    for index, letter in enumerate(automaton.letters):
        seen, stack, accepts = {index}, [index], False
        while stack:
            current = automaton.letters[stack.pop()]
            accepts = accepts or current.accepting
            for n in current.next:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        assert accepts, f"letter {index} cannot reach an accepting letter"
        assert list(letter.next) == sorted(set(letter.next))
        assert list(letter.guards) == sorted(set(letter.guards))


@pytest.mark.parametrize(
    ("sequence", "length"),
    [(A, 1), (_cat(A, 1, 3, B), 4), (_cat(TRUE, 1, 5, B), 6), (FALSE, 1), (Repeat(B, 2, 5), 5)],
)
def test_longest_path(sequence: Sequence, length: int) -> None:
    automaton, _ = _build(sequence)
    assert longest_path(automaton) == length


def test_nullable_is_recorded() -> None:
    automaton, _ = _build(Repeat(A, 0, 2))
    assert automaton.nullable
