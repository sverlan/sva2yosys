"""Letter automata for sequences.

A **letter** is one sample of a match: a conjunction of conditions (guards) that must
all hold at that sample. ``##0`` fusion is expressed by a single letter whose guards
combine the two fused letters; ``##1`` and longer delays move to the next sample. There
are no epsilon transitions: empty operands are resolved at construction time.

Normative semantics (``docs/semantics.md``):

- A *frontier* is the set of letters to test at the current sample. For a match starting
  at sample ``s`` the frontier at ``s`` is :attr:`Automaton.start`.
- At sample ``t`` the *held* letters are those of the frontier whose guards all hold.
  A non-empty match ends at ``t`` iff a held letter is accepting. The frontier at
  ``t + 1`` is the union of :attr:`Letter.next` over the held letters.
- The empty match is not represented (it never satisfies a property, and an antecedent's
  empty match never triggers); :attr:`Automaton.nullable` only records it.

Invariants of a built automaton (structural pruning, ``docs/semantics.md`` §3):

- every letter is reachable from :attr:`Automaton.start` and can reach an accepting letter.
  This is the only pruning: it depends on the shape and lengths of the sequence alone, never
  on the value of a condition;
- a constant false condition (``Const`` 0) is **not** pruned (IEEE 1800 Annex F:
  an unknown future sample satisfies every Boolean, ``1'b0`` included, so a path is refuted
  only by a known sample). It is an ordinary letter guarded by the condition table's entry for
  that constant, which no sample ever satisfies, so the letter is evaluated, and refutes, at
  its own sample. A constant true condition (``Const`` non-zero) is dropped from the guards of
  its letter: it holds at every sample, known or unknown, so this changes no decision sample;
- letters are numbered deterministically in construction order, and guards are
  condition identifiers from a shared :class:`ConditionTable`.

An unbounded delay (``##[m:$]``) or repetition (``[*m:$]``) builds a loop: a letter that
is its own successor (a filler sample, or the repeated operand's final letters leading back
to its start letters). ``goto`` and ``nonconsecutive`` repetition are built from their
definitions (``ir.sequence.goto_expansion``, §16.9.2). A cyclic automaton is only used as
an antecedent (``$`` is never replaced by a bound); the step's rules are unchanged.

Consequently a frontier is *alive* (some continuation can still match, every future
condition counting as satisfiable) exactly when it is non-empty.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field

from sva2yosys.ir.expr import Const, Expr
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
from sva2yosys.temporal.budgets import BudgetError, Work


@dataclass(frozen=True)
class Letter:
    """One letter of an automaton.

    Attributes:
        guards: Condition identifiers that must all hold (sorted, no duplicates; empty
            means the letter always holds).
        next: Letters to test at the next sample after this letter holds (sorted).
        accepting: A match ends at the sample where this letter holds.
    """

    guards: tuple[int, ...]
    next: tuple[int, ...]
    accepting: bool


@dataclass(frozen=True)
class Automaton:
    """A letter automaton for one sequence.

    ``incoming[target]`` is the immutable, cached tuple of source-letter indices
    whose successors contain target, in ascending source order. It includes cycles
    and accepting continuations. Build it once in O(V + E), independently of guards;
    it is derived metadata, excluded from repr, equality and hashing.
    """

    letters: tuple[Letter, ...]
    start: tuple[int, ...]
    nullable: bool
    incoming: tuple[tuple[int, ...], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        incoming: list[list[int]] = [[] for _ in self.letters]
        for source, letter in enumerate(self.letters):
            for target in set(letter.next):
                incoming[target].append(source)
        object.__setattr__(self, "incoming", tuple(tuple(sources) for sources in incoming))


@dataclass
class ConditionTable:
    """Assigns deterministic identifiers to conditions, in order of first use.

    Structurally equal conditions share an identifier.
    """

    conditions: list[Expr] = field(default_factory=list)

    def identify(self, condition: Expr) -> int:
        """The identifier of ``condition``, adding it if new."""
        for index, known in enumerate(self.conditions):
            if known == condition:
                return index
        self.conditions.append(condition)
        return len(self.conditions) - 1


def build_automaton(
    sequence: Sequence, table: ConditionTable, *, work: Work | None = None
) -> Automaton:
    """Build the pruned letter automaton of ``sequence``, registering its conditions.

    Raises:
        ValueError: An operand of ``and`` / ``intersect`` / ``within`` can match empty.
    """
    builder = _Builder(table, work)
    fragment = builder.sequence(sequence)
    return builder.finish(fragment)


def longest_path(automaton: Automaton) -> int:
    """Number of letters on the longest path from a start letter (0 if none).

    The automaton of a bounded sequence is acyclic, so this is the longest time (in
    samples) a frontier started at ``s`` can stay non-empty, counting sample ``s``.

    Raises:
        ValueError: The automaton has a cycle (an unbounded sequence has no longest path).
    """
    depth: dict[int, int] = {}
    visiting: set[int] = set()

    def visit(index: int) -> int:
        if index in visiting:
            raise ValueError("an unbounded sequence has no longest path")
        if index not in depth:
            visiting.add(index)
            successors = automaton.letters[index].next
            depth[index] = 1 + max((visit(n) for n in successors), default=0)
            visiting.discard(index)
        return depth[index]

    return max((visit(index) for index in automaton.start), default=0)


def has_cycle(automaton: Automaton) -> bool:
    """Whether some letter can be its own (indirect) successor: the automaton of an
    unbounded sequence (a ``$`` range, goto or nonconsecutive repetition) that still has a
    live loop after pruning."""
    # Kahn's algorithm (iterative: long ranges give long chains): a cycle is what is left
    # when no letter without remaining predecessors can be removed.
    incoming = [0] * len(automaton.letters)
    for letter in automaton.letters:
        for successor in letter.next:
            incoming[successor] += 1
    ready = [index for index, count in enumerate(incoming) if count == 0]
    removed = 0
    while ready:
        index = ready.pop()
        removed += 1
        for successor in automaton.letters[index].next:
            incoming[successor] -= 1
            if incoming[successor] == 0:
                ready.append(successor)
    return removed < len(automaton.letters)


_DONE = -1  # ``and``: the operand has ended
_BEFORE = -2  # ``within``: the inner sequence has not started yet
_AFTER = -3  # ``within``: the inner sequence has ended


@dataclass
class _Fragment:
    """A partially built automaton: its start letters, final letters and nullability."""

    start: set[int]
    finals: set[int]
    nullable: bool


class _Builder:
    """Mutable construction state; only :func:`build_automaton` uses it."""

    def __init__(self, table: ConditionTable, work: Work | None = None) -> None:
        self._table = table
        self._work = work
        self._guards: list[tuple[int, ...]] = []
        self._next: list[set[int]] = []

    def _letter(self, guards: tuple[int, ...]) -> int:
        if self._work is not None:
            self._work.letter(len(guards))
        self._guards.append(tuple(sorted(set(guards))))
        self._next.append(set())
        return len(self._guards) - 1

    def sequence(self, sequence: Sequence) -> _Fragment:
        self._charge()
        match sequence:
            case Goto():
                return self.sequence(goto_expansion(sequence))
            case NonConsecutive():
                return self.sequence(nonconsecutive_expansion(sequence))
            case Bool(expr=Const(value=value)) if value != 0:
                # holds at every sample, known or unknown: no guard changes no decision sample
                letter = self._letter(())
                return _Fragment({letter}, {letter}, False)
            case Bool(expr=expr):
                letter = self._letter((self._table.identify(expr),))
                return _Fragment({letter}, {letter}, False)
            case SeqConcat(left=left, delay=delay, right=right):
                return self._concat(
                    self.sequence(left), self.sequence(right), delay.minimum, delay.maximum
                )
            case Repeat(sequence=inner, minimum=minimum, maximum=maximum):
                return self._repeat(inner, minimum, maximum)
            case SeqIntersect(left=left, right=right):
                return self._product(self.sequence(left), self.sequence(right), "intersect")
            case SeqAnd(left=left, right=right):
                return self._product(self.sequence(left), self.sequence(right), "and")
            case Within(inner=inner, outer=outer):
                return self._product(self.sequence(inner), self.sequence(outer), "within")
            case Throughout(condition=condition, sequence=inner):
                # Every sample of the match must satisfy the condition: add it to every
                # letter of the operand's (freshly built) automaton.
                fragment = self.sequence(inner)
                guard = self._table.identify(condition)
                seen: set[int] = set()
                stack = list(fragment.start)
                while stack:
                    self._charge()
                    letter = stack.pop()
                    if letter not in seen:
                        seen.add(letter)
                        self._charge(len(self._next[letter]))
                        stack.extend(self._next[letter])
                for letter in seen:
                    self._charge(1 + len(self._guards[letter]))
                    self._guards[letter] = tuple(sorted({*self._guards[letter], guard}))
                return fragment
            case SeqOr(left=left, right=right):
                # The union of the two automata: a match of either operand.
                first, second = self.sequence(left), self.sequence(right)
                return _Fragment(
                    first.start | second.start,
                    first.finals | second.finals,
                    first.nullable or second.nullable,
                )

    def _product(self, first: _Fragment, second: _Fragment, mode: str) -> _Fragment:
        """The synchronous product of two non-nullable fragments (one letter per reachable
        pair of positions, guards conjoined).

        - ``intersect``: both advance together; a match ends where both end.
        - ``and``: an operand that has ended waits in ``_DONE`` (one more sample at a time)
          while the other continues; a match ends where the later one ends.
        - ``within``: ``first`` (the inner sequence) may wait in ``_BEFORE`` before it starts
          and in ``_AFTER`` after it ends, inside a match of ``second``.
        """
        if first.nullable or second.nullable:
            raise ValueError(f"an operand of {mode} that can match empty is not supported")

        def successors(fragment: _Fragment, position: int, idle: bool) -> set[int]:
            self._charge()
            if position == _DONE:
                return {_DONE}
            if position == _BEFORE:
                self._charge(len(first.start))
                return {_BEFORE} | first.start
            if position == _AFTER:
                return {_AFTER}
            self._charge(len(self._next[position]))
            following = set(self._next[position])
            if idle and position in fragment.finals:
                following.add(_AFTER if mode == "within" else _DONE)
            return following

        def ended(fragment: _Fragment, position: int) -> bool:
            return position in (_DONE, _AFTER) or position in fragment.finals

        def guards(position: int) -> tuple[int, ...]:
            return () if position < 0 else self._guards[position]

        start_count = (len(first.start) + (mode == "within")) * len(second.start)
        self._charge(start_count)
        if (
            self._work is not None
            and start_count > self._work.limits.expansion - self._work.letters
        ):
            raise BudgetError(
                f"the property needs more than {self._work.limits.expansion} expanded letters"
            )
        if mode == "within":
            starts = {(a, b) for a in {_BEFORE} | first.start for b in second.start}
        else:
            starts = {(a, b) for a in first.start for b in second.start}
        letters: dict[tuple[int, int], int] = {}
        pending = sorted(starts)
        while pending:
            self._charge()
            pair = pending.pop()
            if pair in letters:
                continue
            a, b = pair
            letters[pair] = self._letter(guards(a) + guards(b))
            for na in successors(first, a, mode in ("and", "within")):
                for nb in successors(second, b, mode == "and"):
                    self._charge()
                    if (na, nb) != (_DONE, _DONE):
                        pending.append((na, nb))
        for (a, b), letter in letters.items():
            for na in successors(first, a, mode in ("and", "within")):
                for nb in successors(second, b, mode == "and"):
                    self._charge()
                    if (na, nb) in letters:
                        self._connect(letter, (letters[(na, nb)],))
        if mode == "intersect":
            finals = {
                letter
                for (a, b), letter in letters.items()
                if a in first.finals and b in second.finals
            }
        elif mode == "and":
            finals = {
                letter
                for (a, b), letter in letters.items()
                if ended(first, a)
                and ended(second, b)
                and (a, b) != (_DONE, _DONE)
                and (a != _DONE or b in second.finals)
                and (b != _DONE or a in first.finals)
            }
        else:
            finals = {
                letter
                for (a, b), letter in letters.items()
                if ended(first, a) and b in second.finals
            }
        return _Fragment({letters[pair] for pair in starts}, finals, False)

    def _concat(self, left: _Fragment, right: _Fragment, low: int, high: int | None) -> _Fragment:
        """Compose fragments, using constant-time bounded-delay membership/endpoints.

        Positive delays form a contiguous integer interval; zero-delay fusion and
        nullable boundaries retain their separate rules. Do not enumerate an interval
        merely to test membership or find its last delay.
        """
        self._charge(len(left.start) + len(right.finals) + 1)
        start = set(left.start)
        finals = set(right.finals)
        nullable = False
        if high is None:
            return self._concat_unbounded(left, right, low)
        self._charge()
        delays = range(max(low, 1), high + 1)
        if delays:
            # fillers[j] is the (j + 1)-th arbitrary sample after the left operand.
            fillers = [self._letter(()) for _ in range(delays[-1] - 1)]
            after_left: set[int] = set()
            if 1 in delays:
                after_left |= right.start
            if fillers:
                after_left.add(fillers[0])
            for final in left.finals:
                self._connect(final, after_left)
            if left.nullable:
                start |= after_left
            for j, filler in enumerate(fillers):
                # One loop visit and one constant-time integer range probe.
                self._charge(2)
                if j + 2 in delays:
                    self._connect(filler, right.start)
                    if right.nullable:
                        finals.add(filler)
                if j + 1 < len(fillers):
                    self._connect(filler, (fillers[j + 1],))
            if right.nullable and 1 in delays:
                finals |= left.finals
            nullable = left.nullable and right.nullable and 1 in delays
        if low == 0:
            self._fuse(left, right, start, finals)
        return _Fragment(start, finals, nullable)

    def _concat_unbounded(self, left: _Fragment, right: _Fragment, low: int) -> _Fragment:
        """``left ##[low:$] right``: the delays ``max(low, 1) … $`` (and the fusion for
        ``low`` 0). After the left operand's end come ``max(low, 1) - 1`` mandatory
        filler samples, then any number of further filler samples (one looping letter),
        then the right operand."""
        start = set(left.start)
        finals = set(right.finals)
        first = max(low, 1)
        fillers = [self._letter(()) for _ in range(first - 1)]
        loop = self._letter(())
        entry = right.start | {loop}  # what may follow the last mandatory filler
        after_left = entry if not fillers else {fillers[0]}
        for final in left.finals:
            self._connect(final, after_left)
        if left.nullable:
            start |= after_left
        for index, filler in enumerate(fillers):
            self._connect(filler, entry if index + 1 == len(fillers) else {fillers[index + 1]})
        self._connect(loop, entry)
        if right.nullable:
            # the match may end on the last sample before the (empty) right operand
            finals.add(loop)
            finals |= left.finals if not fillers else {fillers[-1]}
        if low == 0:
            self._fuse(left, right, start, finals)
        return _Fragment(start, finals, left.nullable and right.nullable and first == 1)

    def _fuse(self, left: _Fragment, right: _Fragment, start: set[int], finals: set[int]) -> None:
        """Add one letter per (left final, right start) pair for ``left ##0 right``."""
        self._charge(len(left.finals) * len(self._next))
        predecessors = {
            final: [p for p, successors in enumerate(self._next) if final in successors]
            for final in left.finals
        }
        for final in sorted(left.finals):
            for first in sorted(right.start):
                fused = self._letter(self._guards[final] + self._guards[first])
                self._connect(fused, self._next[first])
                for predecessor in predecessors[final]:
                    self._connect(predecessor, (fused,))
                if final in left.start:
                    start.add(fused)
                if first in right.finals:
                    finals.add(fused)

    def _repeat_unbounded(self, inner: Sequence, low: int) -> _Fragment:
        """``inner[*low:$]``: ``max(low, 1) - 1`` copies of ``inner``, then a looping copy
        (its final letters lead back to its start letters), i.e. ``inner[+]``."""
        prefix: _Fragment | None = None
        for _ in range(max(low, 1) - 1):
            copy = self.sequence(inner)
            prefix = copy if prefix is None else self._concat(prefix, copy, 1, 1)
        loop = self.sequence(inner)
        for final in loop.finals:
            self._connect(final, loop.start)
        combined = loop if prefix is None else self._concat(prefix, loop, 1, 1)
        return _Fragment(combined.start, combined.finals, combined.nullable or low == 0)

    def _repeat(self, inner: Sequence, low: int, high: int | None) -> _Fragment:
        if high is None:
            return self._repeat_unbounded(inner, low)
        if high == 0:
            return _Fragment(set(), set(), True)
        start: set[int] = set()
        finals: set[int] = set()
        nullable = low == 0
        prefix = self.sequence(inner)
        for count in range(1, high + 1):
            self._charge()
            if count > 1:
                prefix = self._concat(prefix, self.sequence(inner), 1, 1)
            if count >= low:
                start |= prefix.start
                finals |= prefix.finals
                nullable = nullable or prefix.nullable
        return _Fragment(start, finals, nullable)

    def finish(self, fragment: _Fragment) -> Automaton:
        """Prune unreachable/dead letters, preserving creation-order numbering.

        Discover live positions by reverse reachability from reachable finals in
        O(V + E), ignoring guard values. Charge visits before expanding queues.
        """
        reachable: set[int] = set()
        stack = list(fragment.start)
        while stack:
            self._charge()
            letter = stack.pop()
            if letter not in reachable:
                reachable.add(letter)
                self._charge(len(self._next[letter]))
                stack.extend(self._next[letter])
        self._charge(len(self._next))
        incoming: list[list[int]] = [[] for _ in self._next]
        for source in reachable:
            self._charge(1 + len(self._next[source]))
            for target in self._next[source]:
                incoming[target].append(source)
        alive = set(fragment.finals & reachable)
        self._charge(len(alive))
        stack = list(alive)
        while stack:
            self._charge()
            letter = stack.pop()
            self._charge(len(incoming[letter]))
            for source in incoming[letter]:
                if source not in alive:
                    alive.add(source)
                    stack.append(source)
        kept = sorted(alive)
        self._charge(sum(1 + len(self._guards[i]) + len(self._next[i]) for i in kept))
        number = {old: new for new, old in enumerate(kept)}
        letters = tuple(
            Letter(
                guards=self._guards[old],
                next=tuple(sorted(number[n] for n in self._next[old] if n in number)),
                accepting=old in fragment.finals,
            )
            for old in kept
        )
        start = tuple(sorted(number[s] for s in fragment.start if s in number))
        self._charge(len(letters) + sum(len(letter.next) for letter in letters))
        return Automaton(letters, start, fragment.nullable)

    def _charge(self, amount: int = 1) -> None:
        if self._work is not None:
            self._work.charge(amount)

    def _connect(self, source: int, targets: Collection[int]) -> None:
        self._charge(1 + len(targets))
        self._next[source].update(targets)
