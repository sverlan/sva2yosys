"""The life of a property: the number of root ages of an attempt of it.

Normative: ``docs/semantics.md`` §9.2. An attempt of a property is followed for its *life*,
the number of samples after which it is certainly decided; the per-attempt machine
(:mod:`sva2yosys.temporal.attempts`) has one root age per sample of it. This is a structural
bound on the property as written or lowered; it does not depend on the machine.
"""

from __future__ import annotations

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
from sva2yosys.ir.sequence import TRUE, Delay, SeqConcat, Sequence
from sva2yosys.temporal.bounds import sequence_bounds


def life(prop: Property) -> int:
    """The number of root ages of an attempt of ``prop`` (>= 1): after that many samples
    it is certainly decided.

    - Sequence: ``sequence_bounds(s).max_length``, or 1 when it has no match.
    - ``not``: that of the operand.
    - ``and`` / ``or`` / ``if``: the maximum over the operands (``if`` without ``else``:
      that of ``then``).
    - Implication: 1 if the normalised antecedent (``a |=> p`` as ``(a ##1 1) |-> p``) has
      no non-empty match, else ``(max_length(antecedent) - 1) + life(consequent)``.
    - ``nexttime [n] p``: ``n + life(p)``; ``always`` / ``eventually [m:n] p``:
      ``n + life(p)`` (the written forms; the machine is built on the lowered property).

    Raises:
        ValueError: A sequence has an unbounded range.
    """
    match prop:
        case Nexttime(count=count, operand=operand):
            return count + life(operand)
        case Always(high=high, operand=operand) | Eventually(high=high, operand=operand):
            if high is None:
                raise ValueError("unbounded ranges ($) are not supported")
            return high + life(operand)
        case Until():
            raise ValueError("until is not supported yet")
        case SequenceProperty(sequence=sequence):
            return _bounded_length(sequence)
        case Not(operand=operand):
            return life(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return max((life(operand) for operand in operands), default=1)
        case PropIf(then=then, otherwise=otherwise):
            if otherwise is None:
                return life(then)
            return max(life(then), life(otherwise))
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            longest = _bounded_length(normalised_antecedent(antecedent, overlapping))
            return longest - 1 + life(consequent)


def _bounded_length(sequence: Sequence) -> int:
    """The longest match of ``sequence``, or 1 when it has none.

    Raises:
        ValueError: The sequence is unbounded (the per-attempt machine is bounded)."""
    bounds = sequence_bounds(sequence)
    if bounds.unbounded:
        raise ValueError("unbounded ranges ($) are not supported")
    return 1 if bounds.max_length is None else bounds.max_length


def normalised_antecedent(antecedent: Sequence, overlapping: bool) -> Sequence:
    """The antecedent of ``a |=> p`` as ``(a ##1 1)`` (§16.12.7); ``a |-> p`` unchanged."""
    return antecedent if overlapping else SeqConcat(antecedent, Delay(1, 1), TRUE)
