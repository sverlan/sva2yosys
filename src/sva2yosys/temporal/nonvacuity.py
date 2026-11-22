"""Nonvacuity analysis of the property as written, and the cover rule.

Normative: ``docs/semantics.md`` §9.5. :func:`launch_nv`, :func:`nv_at_true` and
:func:`nv_at_false` say when the nonvacuity of an attempt is already known (at launch, at its
true decision, at its false decision); :func:`cover_problem` applies them to reject a
``cover`` whose success could not be decided exactly. The per-attempt machine
(:mod:`sva2yosys.temporal.attempts`) tracks nonvacuity with the same rules; the written-form
helpers here also define ``nexttime`` / ``always`` / ``eventually``, which that machine only
sees lowered.
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


def launch_nv(prop: Property) -> bool:
    """Nonvacuity is unconditionally known at launch (``docs/semantics.md`` §9.5):

    - sequence property: yes;
    - ``not p``: ``launch_nv(p)``;
    - ``p and q`` / ``p or q``: ``launch_nv`` of some operand;
    - ``if``: no; implication: no; ``nexttime`` / ``always`` / ``eventually``: no (they
      lower to implications)."""
    match prop:
        case SequenceProperty() | Until():
            return True
        case Nexttime() | Always() | Eventually():
            return False
        case Not(operand=operand):
            return launch_nv(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return any(launch_nv(operand) for operand in operands)
        case PropIf() | Implication():
            return False


def nv_at_true(prop: Property) -> bool:
    """Whenever the attempt decides true, its nonvacuity is already final
    (``docs/semantics.md`` §9.5, "known at its true decision"):

    - sequence: yes;
    - ``not p``: ``nv_at_false(p)``;
    - ``p and q``: ``launch_nv`` of either operand, or ``nv_at_true`` of both;
    - ``p or q``: ``launch_nv`` of either operand;
    - ``if``: both branches (a missing ``else`` counts as yes);
    - implication: ``nv_at_true`` of the consequent;
    - ``nexttime p`` / ``always p``: ``nv_at_true(p)``; ``eventually p``: ``launch_nv(p)``."""
    match prop:
        case SequenceProperty() | Until():
            return True
        case Nexttime(operand=operand) | Always(operand=operand):
            return nv_at_true(operand)
        case Eventually(operand=operand):
            return launch_nv(operand)
        case Not(operand=operand):
            return nv_at_false(operand)
        case PropAnd(operands=operands):
            return any(launch_nv(o) for o in operands) or all(nv_at_true(o) for o in operands)
        case PropOr(operands=operands):
            return any(launch_nv(o) for o in operands)
        case PropIf(then=then, otherwise=otherwise):
            return nv_at_true(then) and (otherwise is None or nv_at_true(otherwise))
        case Implication(consequent=consequent):
            return nv_at_true(consequent)


def nv_at_false(prop: Property) -> bool:
    """The same for a false decision (``docs/semantics.md`` §9.5):

    - sequence: yes;
    - ``not p``: ``nv_at_true(p)``;
    - ``p and q``: ``launch_nv`` of either operand;
    - ``p or q``: ``launch_nv`` of either operand, or ``nv_at_false`` of both;
    - ``if``: both branches (a missing ``else`` counts as yes);
    - implication: ``launch_nv`` of the consequent;
    - ``nexttime p`` / ``eventually p``: ``nv_at_false(p)``; ``always p``: ``launch_nv(p)``."""
    match prop:
        case SequenceProperty() | Until():
            return True
        case Nexttime(operand=operand) | Eventually(operand=operand):
            return nv_at_false(operand)
        case Always(operand=operand):
            return launch_nv(operand)
        case Not(operand=operand):
            return nv_at_true(operand)
        case PropAnd(operands=operands):
            return any(launch_nv(o) for o in operands)
        case PropOr(operands=operands):
            return any(launch_nv(o) for o in operands) or all(nv_at_false(o) for o in operands)
        case PropIf(then=then, otherwise=otherwise):
            return nv_at_false(then) and (otherwise is None or nv_at_false(otherwise))
        case Implication(consequent=consequent):
            return launch_nv(consequent)


def cover_problem(prop: Property) -> str | None:
    """A message if ``cover property (prop)`` cannot be decided exactly (not
    ``nv_at_true(prop)``, ``docs/semantics.md`` §9.5), else ``None``. ``prop`` is the
    property as written (before lowering)."""
    if nv_at_true(prop):
        return None
    return (
        "a cover of this property is not supported: its nonvacuity is not known when an "
        "attempt succeeds, so the success could not be decided exactly "
        "(docs/semantics.md section 9.5)"
    )
