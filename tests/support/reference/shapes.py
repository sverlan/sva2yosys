"""The reference's own mapping from a property to the obligation shapes its evaluator
understands (a sequence, ``not`` of one, or an implication into one, under property ``and``).

Written from ``docs/semantics.md`` (sections 9.3, 9.5, 9.6) and IEEE 1800 sections 16.12.x,
not taken from the product's ``temporal.lower``: the oracle must not share the lowering it
checks. It imports IR types only. Each rule is a law of the property operators:

- weak ``p until q`` is the weak sequence ``p[*0:$] ##1 q``; ``p until_with q`` is
  ``p[*0:$] ##1 (p && q)`` (strong forms: strong sequences);
- ``nexttime [n] p`` is ``(1 ##n 1) |-> p``; ``always [m:n] p`` is ``(1 ##[m:n] 1) |-> p``;
  ``eventually [m:n] p`` is ``not ((1 ##[m:n] 1) |-> not p)``; the ``[0]`` / ``[0:0]`` forms
  are ``p``; unbounded ``always [m:$] p`` uses the window ``1 ##[m:$] 1``;
- ``w |-> (a |-> c)`` is ``(w ##0 a) |-> c`` and ``w |-> (a |=> c)`` is ``(w ##0 a ##1 1) |-> c``
  (for an unbounded ``always`` and a sequence or ``not`` sequence ``c``);
- ``if (e) p`` is ``e |-> p``, fused into ``p``'s antecedent for an implication;
  ``if (e) s1 else s2`` with sequences of one strength is ``(e ##0 s1) or (!e ##0 s2)``;
- an implication distributes over a consequent ``and``.
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
)
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat, SeqOr, Sequence


def obligation_form(prop: Property) -> Property:
    """``prop`` rewritten into obligation shapes (module docstring)."""
    match prop:
        case SequenceProperty():
            return prop
        case Until(left=left, right=right, strong=strong, inclusive=inclusive):
            tail: Expr = Binary(BinaryOp.LOGICAL_AND, left, right, BIT) if inclusive else right
            body = SeqConcat(Repeat(Bool(left), 0, None), Delay(1, 1), Bool(tail))
            return SequenceProperty(body, Strength.STRONG if strong else Strength.WEAK)
        case Not(operand=operand):
            return Not(obligation_form(operand))
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            if isinstance(consequent, Always) and consequent.high is None:
                return _chain(antecedent, overlapping, obligation_form(consequent))
            return _imp(antecedent, obligation_form(consequent), overlapping)
        case Nexttime(count=count, operand=operand):
            inner = obligation_form(operand)
            if count < 0:  # invalid: kept, the evaluator rejects it
                return Nexttime(count, prop.strong, inner)
            if count == 0:
                return inner
            return _imp(_window(count, count), inner, True)
        case Always(low=low, high=high, operand=operand):
            inner = obligation_form(operand)
            if (high is None and prop.strong) or low < 0 or (high is not None and high < low):
                return Always(low, high, prop.strong, inner)  # invalid: kept
            if high is None:
                return _chain(_window(low, None), True, inner)
            if high == 0:
                return inner
            return _imp(_window(low, high), inner, True)
        case Eventually(low=low, high=high, operand=operand):
            inner = obligation_form(operand)
            if high is None or low < 0 or high < low:  # unbounded (liveness) or invalid: kept
                return Eventually(low, high, prop.strong, inner)
            if high == 0:
                return inner
            negated = inner.operand if isinstance(inner, Not) else Not(inner)
            return Not(_imp(_window(low, high), negated, True))
        case PropAnd(operands=operands):
            flat: list[Property] = []
            for operand in operands:
                part = obligation_form(operand)
                flat.extend(part.operands if isinstance(part, PropAnd) else (part,))
            return PropAnd(tuple(flat))
        case PropOr(operands=operands):
            return PropOr(tuple(obligation_form(o) for o in operands))
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            return _if(
                condition, obligation_form(then), obligation_form(otherwise) if otherwise else None
            )


def _window(low: int, high: int | None) -> Sequence:
    return SeqConcat(TRUE, Delay(low, high), TRUE)


def _imp(antecedent: Sequence, consequent: Property, overlapping: bool) -> Property:
    if isinstance(consequent, PropAnd):
        return PropAnd(
            tuple(_imp(antecedent, operand, overlapping) for operand in consequent.operands)
        )
    return Implication(antecedent, consequent, overlapping)


def _chain(outer: Sequence, overlapping: bool, inner: Property) -> Property:
    match inner:
        case Implication(antecedent=a, consequent=c, overlapping=inner_overlap) if isinstance(
            c, SequenceProperty
        ) or (isinstance(c, Not) and isinstance(c.operand, SequenceProperty)):
            head = outer if overlapping else SeqConcat(outer, Delay(1, 1), TRUE)
            tail = a if inner_overlap else SeqConcat(a, Delay(1, 1), TRUE)
            return Implication(SeqConcat(head, Delay(0, 0), tail), c, True)
        case PropAnd(operands=operands):
            return PropAnd(tuple(_chain(outer, overlapping, o) for o in operands))
        case _:
            return Implication(outer, inner, overlapping)


def _if(condition: Expr, then: Property, otherwise: Property | None) -> Property:
    if otherwise is None:
        match then:
            case Implication(antecedent=a, consequent=c, overlapping=o):
                return Implication(SeqConcat(Bool(condition), Delay(0, 0), a), c, o)
            case PropAnd(operands=operands):
                return PropAnd(tuple(_if(condition, p, None) for p in operands))
            case _:
                return Implication(Bool(condition), then, True)
    negation = Unary(UnaryOp.LOGICAL_NOT, condition, BIT)

    def branches(left: SequenceProperty, right: SequenceProperty) -> SequenceProperty:
        return SequenceProperty(
            SeqOr(
                SeqConcat(Bool(condition), Delay(0, 0), left.sequence),
                SeqConcat(Bool(negation), Delay(0, 0), right.sequence),
            ),
            left.strength,
        )

    match then, otherwise:
        case SequenceProperty() as l, SequenceProperty() as r if l.strength is r.strength:
            return branches(l, r)
        case Not(operand=SequenceProperty() as l), Not(operand=SequenceProperty() as r) if (
            l.strength is r.strength
        ):
            return Not(branches(l, r))
        case _:
            return PropIf(condition, then, otherwise)
