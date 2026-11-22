"""Property expressions (IEEE 1800 §16.12): sequence properties and the property operators that
are supported, the bounded temporal operators (``nexttime``, ``always [m:n]``,
``eventually [m:n]``), and the unbounded forms and ``until`` family (defined by the reference
semantics first).

Clocking and ``disable iff`` are attributes of the :class:`~sva2yosys.ir.directive.Directive`
(single leading clock, top-level disable); nested clocks and abort operators will become
property nodes when they are supported.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sva2yosys.ir.expr import Expr
from sva2yosys.ir.sequence import Sequence


class Strength(Enum):
    """The strength of a sequence used as a property (§16.12.2)."""

    DEFAULT = "default"  # as written without weak()/strong(): see effective_strength
    WEAK = "weak"
    STRONG = "strong"


@dataclass(frozen=True)
class SequenceProperty:
    """A sequence used as a property, ``strong(s)``/``weak(s)`` or neither (§16.12.2).

    For a bounded sequence the strength never changes an online verdict: it only decides
    an attempt still pending when a finite simulation ends (``docs/semantics.md`` §9.1).
    """

    sequence: Sequence
    strength: Strength = Strength.DEFAULT


@dataclass(frozen=True)
class Implication:
    """``antecedent |-> consequent`` (``overlapping``) or ``antecedent |=> consequent``."""

    antecedent: Sequence
    consequent: Property
    overlapping: bool


@dataclass(frozen=True)
class Not:
    """``not operand`` (§16.12.3): true when ``operand`` is false and vice versa; a
    disabled or pending attempt stays so. The strength of a sequence operand is
    reversed (``not weak(s)`` is strong). Any operand; which machine decides it is
    :func:`~sva2yosys.temporal.compile.machine_exact`'s choice (``docs/semantics.md``
    §9.3, §9.4)."""

    operand: Property


@dataclass(frozen=True)
class PropAnd:
    """``p and q`` of properties that are not both sequences (§16.12.5): an attempt fails
    at the earlier of its operands' failures and succeeds when both have. At the top of
    an ``assert`` / ``assume`` it gives one monitor per conjunct (:func:`conjuncts`): the
    property fails exactly when a conjunct does, which is exact up to and including the
    first failure (``docs/semantics.md`` §6, §9.3). The attempt-level success (both
    succeed) is not the OR of the successes, so a ``cover`` is never split: it gets one
    per-attempt machine, under the cover rule, as does an ``and`` that is still nested
    after :func:`~sva2yosys.temporal.lower.lower`. Two sequences joined by ``and`` are a
    ``SeqAnd`` sequence, not this node."""

    operands: tuple[Property, ...]


@dataclass(frozen=True)
class PropOr:
    """``p or q`` of properties that are not both default-strength sequences (§16.12.6):
    an attempt succeeds when an operand does. Two default-strength sequences joined by
    ``or`` are a ``SeqOr`` sequence, not this node. Decided by the per-attempt machine
    (a ``cover`` under the cover rule, ``docs/semantics.md`` §9.5)."""

    operands: tuple[Property, ...]


@dataclass(frozen=True)
class PropIf:
    """``if (condition) then [else otherwise]`` (§16.12.6). The condition is sampled at
    the attempt's first sample. :func:`~sva2yosys.temporal.lower.lower` rewrites it
    without ``else``, or with two sequence branches of the same strength (or two
    ``not`` of such branches); the other shapes get the per-attempt machine (a condition
    using ``$past`` together with ``else`` is rejected)."""

    condition: Expr
    then: Property
    otherwise: Property | None


@dataclass(frozen=True)
class Nexttime:
    """``nexttime [count] operand`` (weak) or ``s_nexttime [count] operand`` (``strong``)
    (§16.12.10); ``nexttime p`` has ``count`` 1. The attempt is the operand's attempt
    started ``count`` clock events later (``count`` 0: the operand itself). Weak and strong
    differ only when the simulation ends before that event (``docs/semantics.md`` §9.5:
    true if weak, false if strong). Valid: ``count >= 0``."""

    count: int
    strong: bool
    operand: Property


@dataclass(frozen=True)
class Always:
    """``always [low:high] operand`` (weak) or ``s_always [low:high] operand`` (``strong``)
    (§16.12.11): the operand's attempts started at offsets ``low … high`` must all hold;
    ``high`` ``None`` is ``$`` (``always p`` is ``Always(0, None, False, p)``). IEEE allows
    an unbounded range only for the weak form, so a strong node with ``high`` ``None`` is
    never built by the converter. Valid: ``0 <= low`` and ``low <= high`` when bounded."""

    low: int
    high: int | None
    strong: bool
    operand: Property


@dataclass(frozen=True)
class Eventually:
    """``eventually [low:high] operand`` (weak) or ``s_eventually [low:high] operand``
    (``strong``) (§16.12.13): the operand's attempt at some offset ``low … high`` holds;
    ``high`` ``None`` is ``$`` (``s_eventually p`` is ``Eventually(0, None, True, p)``).
    IEEE allows an unbounded range only for the strong form, so a weak node with ``high``
    ``None`` is never built by the converter. Valid: ``0 <= low`` and ``low <= high`` when
    bounded."""

    low: int
    high: int | None
    strong: bool
    operand: Property


@dataclass(frozen=True)
class Until:
    """``left until right`` (weak, exclusive), ``left s_until right`` (``strong``),
    ``left until_with right`` (``inclusive``) and ``left s_until_with right`` (both)
    (§16.12.12), with Boolean operands (property operands are not supported).

    ``left until right`` holds when ``left`` holds at every sample from the attempt's start
    up to, but not including, the first sample at which ``right`` holds; it fails at the
    first sample where neither holds. With ``inclusive``, ``left`` must also hold at the
    sample where ``right`` holds. The weak forms are also true if ``right`` never holds
    and ``left`` always does; the strong forms need ``right`` to hold. On Boolean operands
    ``left until right`` is ``weak(left[*0:$] ##1 right)`` and ``left until_with right`` is
    ``weak(left[*0:$] ##1 (left && right))``; the strong forms are the same sequences,
    strong."""

    left: Expr
    right: Expr
    strong: bool
    inclusive: bool


type Property = (
    SequenceProperty
    | Implication
    | Not
    | PropAnd
    | PropOr
    | PropIf
    | Nexttime
    | Always
    | Eventually
    | Until
)


def conjuncts(prop: Property) -> tuple[Property, ...]:
    """The conjuncts of ``prop``, flattening nested :class:`PropAnd`; ``(prop,)`` for
    any other property."""
    if isinstance(prop, PropAnd):
        return tuple(c for operand in prop.operands for c in conjuncts(operand))
    return (prop,)


def effective_strength(strength: Strength, cover: bool) -> Strength:
    """``strength`` with ``DEFAULT`` resolved (§16.12.2): weak in ``assert`` and
    ``assume``, strong in ``cover``."""
    if strength is not Strength.DEFAULT:
        return strength
    return Strength.STRONG if cover else Strength.WEAK
