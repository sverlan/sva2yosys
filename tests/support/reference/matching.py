"""Sequence matching of the reference evaluator (Annex F tight satisfaction, half-open ends).

The matcher, the structural state bound that sizes its horizon (see the conventions in
``support.reference.evaluate``), and the match ends of a sequence on a trace. Imports IR
types only.
"""

from __future__ import annotations

from support.reference.trace import Trace
from support.reference.values import holds
from sva2yosys.ir.expr import (
    BIT,
    Expr,
    Unary,
    UnaryOp,
)
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
)


def match_ends(sequence: Sequence, trace: Trace, start: int) -> frozenset[int]:
    """Samples at which non-empty matches of ``sequence`` starting at ``start`` end,
    using the trace only (no match may extend beyond its last sample)."""
    matcher = _Matcher(trace, len(trace) - 1, sequence_states(sequence))
    return frozenset(e - 1 for e in matcher.ends(sequence, start) if start < e <= len(trace))


def sequence_states(sequence: Sequence) -> int:
    """A sound upper bound ``N`` of the states of a nondeterministic automaton for
    ``sequence`` (a counting argument, not a guess).

    Pumping argument: after the known samples every sample is a "top" sample, on which every
    condition holds, constants included (``docs/semantics.md`` section 3), so whether a partial
    match can still complete depends on structure only: it is the emptiness of a unary
    automaton. A unary automaton with ``N`` states that accepts anything accepts a word of
    length at most ``N``. So if a match can complete after the known samples, one completes
    within ``N`` samples of them, and the matcher explores that far.

    The bound, by construction of the automaton: ``Bool`` 2; ``l ##[m:n] r``:
    ``N(l) + N(r) + n + 1`` (``m + 1`` for ``$``: a loop of that many); ``s[*m:n]``:
    ``(max(n, 1) + 1) * N(s)`` (``(m + 1) * N(s)`` for ``$``); goto and nonconsecutive with
    counts ``m … n``: ``2 * (n + 2)`` (``2 * (m + 2)`` for ``$``); ``or``: sum plus 1;
    ``and``, ``intersect``: the product ``(N(l) + 1) * (N(r) + 1)`` (``and`` waits in the
    finished state of the operand that ended); ``within``: ``(N(i) + 2) * (N(o) + 2)`` (the
    inner sequence idles before and after); ``throughout``: ``N(s) + 1`` (the condition
    only restricts the letters)."""
    match sequence:
        case Bool():
            return 2
        case (
            Goto(minimum=minimum, maximum=maximum)
            | NonConsecutive(minimum=minimum, maximum=maximum)
        ):
            return 2 * ((minimum if maximum is None else maximum) + 2)
        case SeqConcat(left=left, delay=delay, right=right):
            span = delay.minimum + 1 if delay.maximum is None else delay.maximum
            return sequence_states(left) + sequence_states(right) + span + 1
        case Repeat(sequence=inner, minimum=minimum, maximum=maximum):
            copies = minimum + 1 if maximum is None else max(maximum, 1) + 1
            return copies * sequence_states(inner)
        case SeqOr(left=left, right=right):
            return sequence_states(left) + sequence_states(right) + 1
        case SeqAnd(left=left, right=right) | SeqIntersect(left=left, right=right):
            return (sequence_states(left) + 1) * (sequence_states(right) + 1)
        case Within(inner=inner, outer=outer):
            return (sequence_states(inner) + 2) * (sequence_states(outer) + 2)
        case Throughout(sequence=inner):
            return sequence_states(inner) + 1


def _propertysequence_states(prop: Property) -> int:
    """The largest :func:`sequence_states` of a sequence of ``prop`` (0 without sequences)."""
    match prop:
        case SequenceProperty(sequence=sequence):
            return sequence_states(sequence)
        case Until():
            return 0
        case Implication(antecedent=antecedent, consequent=consequent):
            return max(sequence_states(antecedent) + 2, _propertysequence_states(consequent))
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _propertysequence_states(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return max((_propertysequence_states(o) for o in operands), default=0)
        case PropIf(then=then, otherwise=otherwise):
            return max(
                _propertysequence_states(then),
                _propertysequence_states(otherwise) if otherwise is not None else 0,
            )


class _Matcher:
    """Match ends of sequences, knowing samples ``0 … known``; later samples are unknown
    and satisfy every condition, constants included (``docs/semantics.md``, "alive")."""

    def __init__(self, trace: Trace, known: int, slack: int = 0) -> None:
        self._trace = trace
        self._known = min(known, len(trace) - 1)
        # half-open positions up to here are explored for unbounded ranges (``sequence_states``)
        self._horizon = self._known + 2 + slack
        self._memo: dict[tuple[int, int], frozenset[int]] = {}

    def holds(self, expr: Expr, position: int) -> bool:
        """Whether ``expr`` holds at ``position``: its value on a known sample; an unknown
        sample satisfies every condition, constants included (``docs/semantics.md`` section 3,
        IEEE 1800 Annex F). A constant is evaluated at its own sample like any condition."""
        if position <= self._known:
            return holds(expr, self._trace, position)
        return True

    def holds_not(self, expr: Expr, position: int) -> bool:
        """Whether ``!expr`` holds at ``position`` (an unknown sample satisfies it, whatever
        ``expr`` is)."""
        if position <= self._known:
            return holds(Unary(UnaryOp.LOGICAL_NOT, expr, BIT), self._trace, position)
        return True

    def ends(self, sequence: Sequence, start: int) -> frozenset[int]:
        """Half-open ends ``e`` such that samples ``start … e - 1`` tightly satisfy
        ``sequence`` (``e == start`` is the empty match)."""
        key = (id(sequence), start)
        cached = self._memo.get(key)
        if cached is None:
            cached = self._compute(sequence, start)
            self._memo[key] = cached
        return cached

    def _repeat_unbounded(self, inner: Sequence, low: int, start: int) -> frozenset[int]:
        """``inner[*low:$]``: the ends reachable after at least ``low`` matches of ``inner``
        (the count saturates at ``low``; an empty match of ``inner`` is allowed)."""
        result: set[int] = set()
        seen = {(start, 0)}
        stack = [(start, 0)]
        while stack:
            position, count = stack.pop()
            if count >= low:
                result.add(position)
            if position > self._horizon:
                continue
            for end in self.ends(inner, position):
                state = (end, min(count + 1, low))
                if state not in seen:
                    seen.add(state)
                    stack.append(state)
        return frozenset(result)

    def _counted(
        self, condition: Expr, low: int, high: int | None, start: int, *, trailing: bool
    ) -> frozenset[int]:
        """The ends of ``condition[->low:high]`` (``trailing`` false) or
        ``condition[=low:high]`` (true), from their definitions (§16.9.2): every sample of
        the match is one where ``condition`` holds (a hit) or one where ``!condition``
        holds, the hits are counted, and the count lies in ``low … high`` (for ``$`` it
        saturates at ``low``). A goto match ends at a hit (count 0: the empty match); a
        nonconsecutive match may continue with samples of ``!condition`` after its last hit
        (count 0: the empty match, or only such samples)."""

        def in_range(count: int) -> bool:
            return count >= low and (high is None or count <= high)

        ends: set[int] = {start} if low == 0 else set()
        counts: dict[int, set[int]] = {start: {0}}
        for position in range(start, self._horizon + 1):
            for count in counts.get(position, set()):
                if trailing and in_range(count):
                    ends.add(position)
                if self.holds(condition, position):
                    after = count + 1 if high is not None else min(count + 1, low)
                    if high is None or after <= high:
                        counts.setdefault(position + 1, set()).add(after)
                        if not trailing and in_range(after):
                            ends.add(position + 1)
                if self.holds_not(condition, position):
                    counts.setdefault(position + 1, set()).add(count)
        if trailing:
            ends.update(
                self._horizon + 1
                for count in counts.get(self._horizon + 1, set())
                if in_range(count)
            )
        return frozenset(ends)

    def _compute(self, sequence: Sequence, start: int) -> frozenset[int]:
        match sequence:
            case SeqOr(left=left, right=right):
                return self.ends(left, start) | self.ends(right, start)
            case SeqIntersect(left=left, right=right):
                return self.ends(left, start) & self.ends(right, start)
            case SeqAnd(left=left, right=right):
                return frozenset(
                    max(first, second)
                    for first in self.ends(left, start)
                    for second in self.ends(right, start)
                )
            case Within(inner=inner, outer=outer):
                return frozenset(
                    end
                    for end in self.ends(outer, start)
                    if any(
                        inner_end <= end
                        for begin in range(start, end)
                        for inner_end in self.ends(inner, begin)
                    )
                )
            case Throughout(condition=condition, sequence=inner):
                return frozenset(
                    end
                    for end in self.ends(inner, start)
                    if all(self.holds(condition, t) for t in range(start, end))
                )
            case Bool(expr=expr):
                return frozenset({start + 1}) if self.holds(expr, start) else frozenset()
            case Goto(condition=condition, minimum=low, maximum=high):
                return self._counted(condition, low, high, start, trailing=False)
            case NonConsecutive(condition=condition, minimum=low, maximum=high):
                return self._counted(condition, low, high, start, trailing=True)
            case SeqConcat(left=left, delay=Delay(minimum=low, maximum=high), right=right):
                result: set[int] = set()
                for left_end in self.ends(left, start):
                    # an unbounded delay is explored while the right operand can still
                    # start inside the horizon
                    top = max(low, self._horizon - left_end + 1) if high is None else high
                    for delay in range(low, top + 1):
                        if delay == 0:
                            if left_end > start:
                                result.update(
                                    e for e in self.ends(right, left_end - 1) if e > left_end - 1
                                )
                        else:
                            result.update(self.ends(right, left_end + delay - 1))
                return frozenset(result)
            case Repeat(sequence=inner, minimum=low, maximum=high):
                if high is None:
                    return self._repeat_unbounded(inner, low, start)
                result = {start} if low == 0 else set()
                current = {start}
                for count in range(1, high + 1):
                    current = {e for c in current for e in self.ends(inner, c)}
                    if count >= low:
                        result.update(current)
                return frozenset(result)
