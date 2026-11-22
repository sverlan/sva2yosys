"""Lower property operators to the shapes the temporal machine handles.

The converter preserves the property structure Slang gives (``if``, ``or``, nested
implications). :func:`lower` rewrites the operators that the machine supports only in an
equivalent, simpler form; everything else is left as written, for the per-attempt machine
(or for validation to reject).

Rules (children are lowered first):

- ``if (e) p`` is ``e |-> p`` (§16.12.6), fused into ``p``'s antecedent when ``p`` is an
  implication: ``(e ##0 a) |-> c``; over a property ``and`` it maps over the operands.
  Any other ``p`` gives ``Implication(Bool(e), p, True)``.
- ``if (e) s1 else s2`` with two sequence properties of the same strength is the sequence
  ``(e ##0 s1) or (!e ##0 s2)``: the unselected branch dies at the attempt's first sample
  (``not`` distributes over ``if``, so two ``not s`` branches give ``not`` of it). Other
  ``if``/``else`` shapes stay :class:`~sva2yosys.ir.property.PropIf`.
- ``a |-> (p and q)`` is ``(a |-> p) and (a |-> q)``: every antecedent match starts an
  attempt of each, and the first failure of either is the failure (§16.12.5, §16.12.7).
- ``nexttime [n] p`` (``n >= 1``) is ``(1 ##n 1) |-> p``; ``always [m:n] p`` is
  ``(1 ##[m:n] 1) |-> p``; ``eventually [m:n] p`` is ``not ((1 ##[m:n] 1) |-> not p)``
  (``not not q`` is ``q``). ``nexttime [0]``, ``always [0:0]`` and ``eventually [0:0]``
  are ``p`` itself. The strong forms lower identically: strength only matters when the
  simulation ends inside the window (``docs/semantics.md`` §9.5), which the online
  verdict does not see. A node with an invalid count or range is kept (its operand
  lowered) for validation to report.
- Unbounded ``always [m:$] p`` (weak; ``always p`` is ``m`` 0) is ``(1 ##[m:$] 1) |-> p``,
  flattened when ``p`` is an implication into a sequence or ``not`` of one
  (:func:`_chain`): ``w |-> (a |-> c)`` is ``(w ##0 a) |-> c`` and ``w |-> (a |=> c)`` is
  ``(w ##0 a ##1 1) |-> c`` (§16.12.7: ``a |=> c`` is ``(a ##1 1) |-> c``). The failures of
  ``x |-> (y |-> c)`` and ``(x ##0 y) |-> c`` coincide (every attempt of ``y`` starts at an
  end of ``x``); their nonvacuity does not, which is why validation rejects every ``cover``
  with an unbounded part. ``a |-> always p`` is flattened the same way. Only an unbounded
  ``always`` flattens: nested implications written by the user stay as they are (bounded
  ones go to the per-attempt machine, byte-identical monitors).
- Weak ``p until q`` is the weak sequence ``p[*0:$] ##1 q`` and ``p until_with q`` is
  ``p[*0:$] ##1 (p && q)`` (:func:`until_sequence`, ``docs/semantics.md`` §9.3, §9.6),
  a :class:`~sva2yosys.ir.property.SequenceProperty` with explicit weak strength, so
  every position a weak unbounded sequence is accepted in accepts an ``until``. The strong
  forms are kept as written.
- Unbounded ``eventually`` (``high`` ``None``) and a strong or invalid unbounded ``always``
  are kept as written (operand lowered): validation rejects them (``docs/semantics.md``
  §9.6).
"""

from __future__ import annotations

from sva2yosys.ir.expr import BIT, Binary, BinaryOp, Expr, Unary, UnaryOp
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
    Strength,
    Until,
    conjuncts,
)
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat, SeqOr, Sequence
from sva2yosys.temporal.bounds import unbounded_sequence


def lower(prop: Property) -> Property:
    """``prop`` with ``if`` and implication-over-``and`` rewritten as described in the
    module docstring; the identity on every other shape."""
    match prop:
        case SequenceProperty():
            return prop
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            lowered = lower(consequent)
            if isinstance(consequent, Always) and consequent.high is None:
                return _chain(antecedent, overlapping, lowered)
            return _implication(antecedent, lowered, overlapping)
        case Not(operand=operand):
            return Not(lower(operand))
        case Nexttime(count=count, operand=operand):
            inner = lower(operand)
            if count == 0:
                return inner
            if count < 0:  # invalid: left for validation to report
                return Nexttime(count, prop.strong, inner)
            return _implication(window(count, count), inner, True)
        case Always(low=low, high=high, operand=operand):
            inner = lower(operand)
            if high is None:
                if prop.strong or low < 0:  # invalid (strong has no unbounded form): kept
                    return Always(low, high, prop.strong, inner)
                return _chain(window(low, None), True, inner)
            if not 0 <= low <= high:  # invalid: kept
                return Always(low, high, prop.strong, inner)
            if high == 0:
                return inner
            return _implication(window(low, high), inner, True)
        case Eventually(low=low, high=high, operand=operand):
            inner = lower(operand)
            if high is None:
                if (
                    prop.strong
                    and low >= 0
                    and isinstance(inner, SequenceProperty)
                    and not unbounded_sequence(inner.sequence)
                ):
                    # s_eventually [m:$] p is strong(1[*m:$] ##1 p) (§16.12.13): p starts at
                    # an offset k >= m (for k = 0, (empty ##1 p) is p at the first sample,
                    # §16.9.2.1). Same matches as 1 ##[m:$] p, but for m = 0 the start
                    # frontier is a fixed point: one configuration, the pending-bit class.
                    return SequenceProperty(
                        SeqConcat(Repeat(TRUE, low, None), Delay(1, 1), inner.sequence),
                        Strength.STRONG,
                    )
                return Eventually(low, high, prop.strong, inner)  # kept: validation rejects it
            if not 0 <= low <= high:  # invalid: kept
                return Eventually(low, high, prop.strong, inner)
            if high == 0:
                return inner
            negated = inner.operand if isinstance(inner, Not) else Not(inner)
            return Not(_implication(window(low, high), negated, True))
        case Until():
            strength = Strength.STRONG if prop.strong else Strength.WEAK
            return SequenceProperty(until_sequence(prop), strength)
        case PropAnd(operands=operands):
            return PropAnd(tuple(c for operand in operands for c in conjuncts(lower(operand))))
        case PropOr(operands=operands):
            return PropOr(tuple(lower(operand) for operand in operands))
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            if otherwise is None:
                return _enabled(condition, lower(then))
            lowered_then, lowered_else = lower(then), lower(otherwise)
            match lowered_then, lowered_else:
                case SequenceProperty() as left, SequenceProperty() as right:
                    if left.strength is right.strength:
                        return _branches(condition, left, right)
                case (
                    Not(operand=SequenceProperty() as left),
                    Not(operand=SequenceProperty() as right),
                ):
                    if left.strength is right.strength:
                        return Not(_branches(condition, left, right))
                case _:
                    pass
            return PropIf(condition, lowered_then, lowered_else)


def until_sequence(prop: Until) -> Sequence:
    """The sequence of a Boolean-operand ``until``: ``left[*0:$] ##1 right`` and, with
    ``inclusive`` (``until_with``), ``left[*0:$] ##1 (left && right)`` (§16.12.12). The
    sequence is the same for the weak and the strong forms; the strength is the node's.
    ``$`` stays unbounded. The condition ``left && right`` of ``until_with`` is an ordinary
    condition of the lowered property, so the first-sample rule applies to it as written."""
    condition = prop.right
    if prop.inclusive:
        condition = Binary(BinaryOp.LOGICAL_AND, prop.left, prop.right, BIT)
    return SeqConcat(Repeat(Bool(prop.left), 0, None), Delay(1, 1), Bool(condition))


def window(low: int, high: int | None) -> Sequence:
    """The sequence ``1 ##[low:high] 1``: it matches at the offsets ``low … high``
    (``high`` ``None``: ``low …`` and every later one)."""
    return SeqConcat(TRUE, Delay(low, high), TRUE)


def _chain(outer: Sequence, overlapping: bool, inner: Property) -> Property:
    """``outer |-> inner`` (or ``|=>``) for an ``inner`` that is an implication into a
    sequence or ``not`` of one: the single implication ``(outer' ##0 a') |-> c`` with
    ``outer'`` the antecedent ``outer`` (``outer ##1 1`` for ``|=>``) and ``a'`` that of
    ``inner`` likewise. Over a property ``and`` it maps over the operands; any other
    ``inner`` gives ``Implication(outer, inner, overlapping)``."""
    match inner:
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=inner_overlap):
            decided = isinstance(consequent, SequenceProperty) or (
                isinstance(consequent, Not) and isinstance(consequent.operand, SequenceProperty)
            )
            if decided:
                head = outer if overlapping else SeqConcat(outer, Delay(1, 1), TRUE)
                tail = antecedent if inner_overlap else SeqConcat(antecedent, Delay(1, 1), TRUE)
                return Implication(SeqConcat(head, Delay(0, 0), tail), consequent, True)
        case PropAnd(operands=operands):
            return PropAnd(tuple(_chain(outer, overlapping, operand) for operand in operands))
        case _:
            pass
    return Implication(outer, inner, overlapping)


def _implication(antecedent: Sequence, consequent: Property, overlapping: bool) -> Property:
    """``antecedent |-> consequent`` (or ``|=>``) with an already lowered consequent;
    over a property ``and`` it maps over the operands."""
    if isinstance(consequent, PropAnd):
        return PropAnd(
            tuple(
                Implication(antecedent, operand, overlapping) for operand in conjuncts(consequent)
            )
        )
    return Implication(antecedent, consequent, overlapping)


def _branches(condition: Expr, left: SequenceProperty, right: SequenceProperty) -> SequenceProperty:
    negation = Unary(UnaryOp.LOGICAL_NOT, condition, BIT)
    return SequenceProperty(
        SeqOr(
            SeqConcat(Bool(condition), Delay(0, 0), left.sequence),
            SeqConcat(Bool(negation), Delay(0, 0), right.sequence),
        ),
        left.strength,
    )


def _enabled(enable: Expr, prop: Property) -> Property:
    """``enable |-> prop``, fused into an implication's antecedent."""
    match prop:
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            return Implication(
                SeqConcat(Bool(enable), Delay(0, 0), antecedent), consequent, overlapping
            )
        case PropAnd(operands=operands):
            return PropAnd(tuple(_enabled(enable, operand) for operand in operands))
        case (
            SequenceProperty()
            | Not()
            | PropOr()
            | PropIf()
            | Nexttime()
            | Always()
            | Eventually()
            | Until()
        ):
            return Implication(Bool(enable), prop, True)
