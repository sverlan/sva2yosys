"""Hypothesis strategies for valid basic directives and traces.

Generated directives use 1-bit signals ``m.a``, ``m.b``, ``m.c``, ``m.d`` (and ``m.clk``
for the clock). They are weighted towards the constructs that make temporal machines
hard: ``##0`` fusion, delay ranges containing 0, nullable and nested repetition,
repetition of concatenations, multi-endpoint antecedents, both implication kinds,
sampled-value functions, constants and ``disable iff``.

:func:`property_trees` and :func:`attempt_directives` generate nested property trees for
the per-attempt machine; their directives satisfy the structural rules only (``shapes=False``).

Every directive of :func:`directives` satisfies the validity rules that the converter and the
first-sample check enforce:

- the consequent (or sequence property) does not admit an empty match (Slang rejects
  that: ``SeqEmptyMatch``);
- there is no nested implication and no unbounded range;
- :func:`~sva2yosys.passes.validate.validation_problems` is empty (which includes the
  first-sample rule);
- the property is a shape the canonical machine decides exactly: validation also accepts a
  ``cover`` of an implication whose antecedent has several match lengths (the per-attempt
  machine decides it), so those are excluded here (``_canonical_cover``, computed with
  :func:`match_lengths`).

This module does not use ``sva2yosys.temporal``: nullability is computed here, so that
test expectations never depend on the code under test.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from hypothesis import strategies as st

from support.reference.trace import Trace
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import (
    BIT,
    Binary,
    BinaryOp,
    Const,
    Expr,
    Sampled,
    SampledFunction,
    Signal,
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
    Strength,
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
from sva2yosys.ir.source import SourceRange
from sva2yosys.passes.validate import validation_problems

SIGNALS = ("a", "b", "c", "d")
_SOURCE = SourceRange("generated.sv", 1, 1, 1, 2)
_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)

_DELAYS = [(0, 0), (1, 1), (0, 1), (0, 2), (1, 2), (1, 3), (2, 2)]
_REPEATS = [(0, 1), (1, 1), (1, 2), (0, 2), (2, 2), (2, 3)]


def _signal(name: str) -> Signal:
    return Signal(f"m.{name}", BIT)


def conditions() -> st.SearchStrategy[Expr]:
    """Boolean conditions over the signals, including sampled values and constants."""
    names = st.sampled_from(SIGNALS)
    plain = names.map(_signal)
    negated = plain.map(lambda s: Unary(UnaryOp.LOGICAL_NOT, s, BIT))
    sampled = st.builds(
        lambda f, s: Sampled(f, s, BIT),
        st.sampled_from(list(SampledFunction)),
        plain,
    )
    constants = st.sampled_from([Const(0, BIT), Const(1, BIT)])  # (weighted twice below)
    both = st.builds(lambda x, y: Binary(BinaryOp.LOGICAL_AND, x, y, BIT), plain, plain)
    past_equal = st.builds(
        lambda x, y: Binary(BinaryOp.EQUALITY, Sampled(SampledFunction.PAST, x, BIT), y, BIT),
        plain,
        plain,
    )
    deep_past = st.builds(
        lambda x, y: Binary(BinaryOp.EQUALITY, Sampled(SampledFunction.PAST, x, BIT, 2), y, BIT),
        plain,
        plain,
    )
    return st.one_of(
        plain, plain, plain, negated, sampled, both, past_equal, deep_past, constants, constants
    )


def sequences(max_depth: int = 3) -> st.SearchStrategy[Sequence]:
    """Bounded sequences of nesting depth at most ``max_depth``."""
    leaf: st.SearchStrategy[Sequence] = conditions().map(Bool)
    if max_depth <= 0:
        return leaf
    inner = sequences(max_depth - 1)
    concat = st.builds(
        lambda left, delay, right: SeqConcat(left, Delay(*delay), right),
        inner,
        st.sampled_from(_DELAYS),
        inner,
    )
    repeat = st.builds(
        lambda body, count: Repeat(body, *count),
        inner,
        st.sampled_from(_REPEATS),
    )
    either = st.builds(SeqOr, inner, inner)
    solid = inner.filter(lambda s: not nullable(s))
    both = st.builds(SeqAnd, solid, solid)
    meet = st.builds(SeqIntersect, solid, solid)
    inside = st.builds(Within, solid, solid)
    during = st.builds(Throughout, conditions(), inner)
    return st.one_of(leaf, concat, concat, repeat, either, both, meet, inside, during)


def nullable(sequence: Sequence) -> bool:
    """Whether ``sequence`` admits the empty match (independent of sva2yosys.temporal)."""
    match sequence:
        case Bool():
            return False
        case SeqConcat(left=left, delay=delay, right=right):
            one_allowed = delay.minimum <= 1 and (delay.maximum is None or delay.maximum >= 1)
            return one_allowed and nullable(left) and nullable(right)
        case Repeat(sequence=inner, minimum=minimum, maximum=maximum):
            return minimum == 0 or (maximum != 0 and nullable(inner))
        case SeqOr(left=left, right=right):
            return nullable(left) or nullable(right)
        case SeqAnd() | SeqIntersect() | Within():
            return False  # generated with non-nullable operands only
        case Throughout(sequence=inner):
            return nullable(inner)
        case Goto(minimum=minimum) | NonConsecutive(minimum=minimum):
            return minimum == 0  # count 0 is the empty match (§16.9.2)


def max_length(sequence: Sequence) -> int:
    """Longest possible match in samples, ignoring conditions (independent of
    sva2yosys.temporal; an upper bound is enough for sizing traces)."""
    match sequence:
        case Bool():
            return 1
        case SeqConcat(left=left, delay=delay, right=right):
            return max_length(left) + max(0, (delay.maximum or 0) - 1) + max_length(right)
        case Repeat(sequence=inner, maximum=maximum):
            return (maximum or 0) * max_length(inner)
        case SeqOr(left=left, right=right) | SeqAnd(left=left, right=right):
            return max(max_length(left), max_length(right))
        case SeqIntersect(left=left, right=right):
            return min(max_length(left), max_length(right))
        case Within(outer=outer):
            return max_length(outer)
        case Throughout(sequence=inner):
            return max_length(inner)
        case Goto() | NonConsecutive():
            raise AssertionError("not generated: goto and nonconsecutive repetition")


def match_lengths(sequence: Sequence) -> frozenset[int] | None:
    """A superset of the lengths (in samples) of the matches of ``sequence``, ignoring
    conditions (independent of sva2yosys.temporal), or ``None`` when no such set is computed
    (a part admits an empty match). ``min_length`` and ``max_length`` of a sequence are the
    extremes of this set. A singleton means every match has that length whatever the trace."""
    match sequence:
        case Bool():
            return frozenset({1})
        case SeqConcat(left=left, delay=delay, right=right):
            left_lengths, right_lengths = match_lengths(left), match_lengths(right)
            if left_lengths is None or right_lengths is None:
                return None
            high = delay.minimum if delay.maximum is None else delay.maximum
            return frozenset(
                x + y + d - 1
                for x in left_lengths
                for y in right_lengths
                for d in range(delay.minimum, high + 1)
            )
        case Repeat(sequence=inner, minimum=minimum, maximum=maximum):
            inner_lengths = match_lengths(inner)
            if inner_lengths is None or minimum == 0:
                return None
            sums = frozenset({0})
            totals: set[int] = set()
            for count in range(1, (minimum if maximum is None else maximum) + 1):
                sums = frozenset(s + x for s in sums for x in inner_lengths)
                if count >= minimum:
                    totals |= sums
            return frozenset(totals)
        case SeqOr(left=left, right=right):
            or_left, or_right = match_lengths(left), match_lengths(right)
            return None if or_left is None or or_right is None else or_left | or_right
        case SeqAnd(left=left, right=right):
            and_left, and_right = match_lengths(left), match_lengths(right)
            if and_left is None or and_right is None:
                return None
            return frozenset(max(x, y) for x in and_left for y in and_right)
        case SeqIntersect(left=left, right=right):
            meet_left, meet_right = match_lengths(left), match_lengths(right)
            return None if meet_left is None or meet_right is None else meet_left & meet_right
        case Within(outer=outer):
            return match_lengths(outer)
        case Throughout(sequence=inner):
            return match_lengths(inner)
        case Goto() | NonConsecutive():
            raise AssertionError("not generated: goto and nonconsecutive repetition")


def min_length(sequence: Sequence) -> int | None:
    """Shortest possible match (see :func:`match_lengths`); ``None`` if unknown or none."""
    lengths = match_lengths(sequence)
    return min(lengths) if lengths else None


def _canonical_cover(directive: Directive) -> bool:
    """Whether a generated directive stays within the shapes the canonical machine decides
    exactly: a ``cover`` of an implication needs an antecedent with a single match length
    (an attempt succeeds only if the consequents of all its antecedent matches do, which the
    per-attempt machine decides)."""
    if directive.kind is not DirectiveKind.COVER:
        return True
    match directive.property:
        case Implication(antecedent=antecedent):
            lengths = match_lengths(antecedent)
            return lengths is not None and len(lengths) <= 1
        case _:
            return True


def duration(directive: Directive) -> int:
    """Upper bound on the samples an attempt of ``directive`` spans, antecedent included."""
    match directive.property:
        case SequenceProperty(sequence=body) | Not(operand=SequenceProperty(sequence=body)):
            return max_length(body)
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            inner = consequent.operand if isinstance(consequent, Not) else consequent
            assert isinstance(inner, SequenceProperty)
            offset = 0 if overlapping else 1
            return max_length(antecedent) + offset + max_length(inner.sequence)
        case (
            Not() | PropAnd() | PropOr() | PropIf() | Nexttime() | Always() | Eventually() | Until()
        ):
            raise AssertionError("not generated: not of a non-sequence, 'and', 'or' or 'if'")


def has_unbounded_part(prop: Property) -> bool:
    """Whether ``prop`` has an unbounded part: a ``$`` range, goto or nonconsecutive
    repetition, ``always`` / ``eventually`` without an upper bound, or ``until`` (independent
    of sva2yosys.passes.validate)."""

    def unbounded(sequence: Sequence) -> bool:
        match sequence:
            case Bool():
                return False
            case SeqConcat(left=left, delay=delay, right=right):
                return delay.maximum is None or unbounded(left) or unbounded(right)
            case Repeat(sequence=inner, maximum=maximum):
                return maximum is None or unbounded(inner)
            case Goto() | NonConsecutive():
                return True
            case (
                SeqOr(left=left, right=right)
                | SeqAnd(left=left, right=right)
                | SeqIntersect(left=left, right=right)
                | Within(inner=left, outer=right)
            ):
                return unbounded(left) or unbounded(right)
            case Throughout(sequence=inner):
                return unbounded(inner)

    match prop:
        case SequenceProperty(sequence=body):
            return unbounded(body)
        case Until():
            return True
        case Always(high=high, operand=operand) | Eventually(high=high, operand=operand):
            return high is None or has_unbounded_part(operand)
        case Implication(antecedent=antecedent, consequent=consequent):
            return unbounded(antecedent) or has_unbounded_part(consequent)
        case Not(operand=operand) | Nexttime(operand=operand):
            return has_unbounded_part(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return any(has_unbounded_part(operand) for operand in operands)
        case PropIf(then=then, otherwise=otherwise):
            return has_unbounded_part(then) or (
                otherwise is not None and has_unbounded_part(otherwise)
            )


_OPEN_DELAYS = [(0, None), (1, None), (1, None), (2, None), (3, None)]
_OPEN_REPEATS = [(0, None), (1, None), (1, None), (2, None)]
_COUNTS = [(1, 1), (2, 2), (0, 2), (1, 2), (1, None), (2, None), (0, 1), (0, None), (3, 3)]


def unbounded_sequences(max_depth: int = 2) -> st.SearchStrategy[Sequence]:
    """Sequences of nesting depth at most ``max_depth`` that mix the bounded constructs with
    ``##[m:$]``, ``[*m:$]``, goto and nonconsecutive repetition: antecedent
    candidates. They may be nullable (``nullable`` tells); the operands of ``and`` /
    ``intersect`` / ``within`` are not."""
    leaf: st.SearchStrategy[Sequence] = conditions().map(Bool)
    counted = st.builds(
        lambda condition, count, consecutive: (
            Goto(condition, *count) if consecutive else NonConsecutive(condition, *count)
        ),
        st.one_of(
            st.sampled_from(SIGNALS).map(_signal),
            st.sampled_from(SIGNALS).map(_signal),
            st.sampled_from([Const(0, BIT), Const(1, BIT)]),
        ),
        st.sampled_from(_COUNTS),
        st.booleans(),
    )
    if max_depth <= 0:
        return st.one_of(leaf, counted)
    inner = unbounded_sequences(max_depth - 1)
    concat = st.builds(
        lambda left, delay, right: SeqConcat(left, Delay(*delay), right),
        inner,
        st.sampled_from(_OPEN_DELAYS + _DELAYS),
        inner,
    )
    repeat = st.builds(
        lambda body, count: Repeat(body, *count),
        inner,
        st.sampled_from(_OPEN_REPEATS + _REPEATS),
    )
    solid = inner.filter(lambda s: not nullable(s))
    return st.one_of(
        leaf,
        counted,
        concat,
        concat,
        concat,
        repeat,
        repeat,
        st.builds(SeqOr, inner, inner),
        st.builds(SeqAnd, solid, solid),
        st.builds(SeqIntersect, solid, solid),
        st.builds(Within, solid, solid),
        st.builds(Throughout, conditions(), inner),
    )


def _unbounded_properties() -> st.SearchStrategy[Property]:
    """Properties the canonical machine decides although they have unbounded parts: an
    unbounded antecedent into a bounded consequent, and unbounded ``always`` (alone, over a
    sequence, ``not`` of one or an implication, or as an implication's consequent)."""
    body = sequences(2).filter(lambda s: not nullable(s))
    decided = st.builds(
        lambda s, negated: Not(SequenceProperty(s)) if negated else SequenceProperty(s),
        body,
        st.booleans(),
    )
    implication = st.builds(Implication, unbounded_sequences(2), decided, st.booleans())
    bounded_implication = st.builds(Implication, sequences(2), decided, st.booleans())
    operand = st.one_of(decided, decided, bounded_implication, implication)
    lows = st.sampled_from([0, 0, 1, 2])
    always = st.builds(lambda low, p: Always(low, None, False, p), lows, operand)
    into_always = st.builds(
        Implication, st.one_of(sequences(2), unbounded_sequences(1)), always, st.booleans()
    )
    return st.one_of(implication, implication, implication, always, always, into_always)


def unbounded_directives() -> st.SearchStrategy[Directive]:
    """Valid ``assert`` / ``assume`` directives with unbounded antecedents or unbounded
    ``always`` (see :func:`_unbounded_properties`), with no ``disable iff`` or the ones of
    :func:`directives`. Validation reports nothing for them (the antecedent of a ``|=>``
    does not match empty), and the canonical machine decides them."""
    disables = st.one_of(
        st.none(),
        st.just(_signal("d")),
        st.just(Sampled(SampledFunction.ROSE, _signal("d"), BIT)),
    )
    kinds = st.sampled_from([DirectiveKind.ASSERT, DirectiveKind.ASSERT, DirectiveKind.ASSUME])
    return st.builds(
        lambda kind, disable, prop: Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE),
        kinds,
        disables,
        _unbounded_properties(),
    ).filter(lambda d: validation_problems(d) == ())


def has_false_constant(sequence: Sequence) -> bool:
    """Whether the constant 0 occurs as a condition of ``sequence`` (a ``Bool``, a goto or
    nonconsecutive condition, or a ``throughout`` condition). Test helper:
    such sequences are valid everywhere, and the generators produce them."""
    match sequence:
        case Bool(expr=expr):
            return isinstance(expr, Const) and expr.value == 0
        case Goto(condition=condition) | NonConsecutive(condition=condition):
            return isinstance(condition, Const) and condition.value == 0
        case SeqConcat(left=left, right=right):
            return has_false_constant(left) or has_false_constant(right)
        case Repeat(sequence=inner):
            return has_false_constant(inner)
        case Throughout(condition=condition, sequence=inner):
            return (isinstance(condition, Const) and condition.value == 0) or has_false_constant(
                inner
            )
        case (
            SeqOr(left=left, right=right)
            | SeqAnd(left=left, right=right)
            | SeqIntersect(left=left, right=right)
            | Within(inner=left, outer=right)
        ):
            return has_false_constant(left) or has_false_constant(right)


def weak_unbounded_consequents() -> st.SearchStrategy[SequenceProperty]:
    """Weak (default or explicit ``weak``) sequence properties with an unbounded part and no
    empty match (consequents of the configuration-set machine). Constant
    conditions, false ones included, are allowed (they are evaluated at their own
    sample)."""
    body = unbounded_sequences(2).filter(
        lambda s: not nullable(s) and has_unbounded_part(SequenceProperty(s))
    )
    strengths = st.sampled_from([Strength.DEFAULT, Strength.DEFAULT, Strength.WEAK])
    return st.builds(SequenceProperty, body, strengths)


def negated_unbounded_consequents() -> st.SearchStrategy[Not]:
    """``not`` of a sequence property with an unbounded part and no empty match, of every
    strength: ``not weak(s)`` (default or explicit, strong after the negation,
    accepted as an ``assume`` only in the one-configuration class) and ``not strong(s)``
    (weak after the negation)."""
    body = unbounded_sequences(2).filter(
        lambda s: not nullable(s) and has_unbounded_part(SequenceProperty(s))
    )
    strengths = st.sampled_from([Strength.DEFAULT, Strength.WEAK, Strength.STRONG])
    return st.builds(lambda s, strength: Not(SequenceProperty(s, strength)), body, strengths)


def until_properties() -> st.SearchStrategy[Until]:
    """Weak ``until`` and ``until_with`` over Boolean operands drawn from
    :func:`conditions` (sampled values, ``$past`` and constants included)."""
    return st.builds(
        lambda left, right, inclusive: Until(left, right, False, inclusive),
        conditions(),
        conditions(),
        st.booleans(),
    )


def _until_shapes() -> st.SearchStrategy[Property]:
    """An ``until`` in every position the machine accepts it: alone, as an implication
    consequent, under unbounded ``always`` (alone, over an implication, or as an
    implication's consequent), and in the forms ``nexttime``, bounded ``always`` and property
    ``if`` lower to."""
    until = until_properties()
    antecedents = st.one_of(sequences(2), sequences(2), unbounded_sequences(1))
    implication = st.builds(Implication, antecedents, until, st.booleans())
    lows = st.sampled_from([0, 0, 1, 2])
    always = st.builds(
        lambda low, p: Always(low, None, False, p), lows, st.one_of(until, implication)
    )
    into_always = st.builds(Implication, sequences(2), always, st.booleans())
    nexttime = st.builds(
        lambda count, p: Nexttime(count, False, p), st.sampled_from([0, 1, 2]), until
    )
    bounded_always = st.builds(
        lambda low, span, p: Always(low, low + span, False, p),
        st.sampled_from([0, 1]),
        st.sampled_from([0, 1, 2]),
        until,
    )
    enabled = st.builds(lambda e, p: PropIf(e, p, None), conditions(), until)
    branches = st.builds(
        lambda e, p, q: PropIf(e, p, SequenceProperty(Bool(q), Strength.WEAK)),
        st.sampled_from(SIGNALS).map(_signal),  # (an else branch rejects $past in the condition)
        until,
        conditions(),
    )
    return st.one_of(
        until,
        until,
        implication,
        implication,
        implication,
        always,
        into_always,
        nexttime,
        bounded_always,
        enabled,
        branches,
    )


def _config_properties() -> st.SearchStrategy[Property]:
    """Properties decided by the configuration-set machine: a weak unbounded sequence, an
    implication (bounded or unbounded antecedent) into one, and unbounded ``always`` over
    those (alone, over an implication, or as an implication's consequent);
    also a weak ``until`` / ``until_with`` in the same positions, and ``not`` of
    an unbounded sequence (:func:`negated_unbounded_consequents`; the directives that are
    rejected, an L2 ``assume`` of ``not weak(s)``, are dropped by the compilation filter)."""
    consequent = st.one_of(
        weak_unbounded_consequents(),
        weak_unbounded_consequents(),
        until_properties(),
        negated_unbounded_consequents(),
    )
    antecedents = st.one_of(sequences(2), sequences(2), unbounded_sequences(1))
    implication = st.builds(Implication, antecedents, consequent, st.booleans())
    operand = st.one_of(consequent, implication, implication)
    lows = st.sampled_from([0, 0, 1, 2])
    always = st.builds(lambda low, p: Always(low, None, False, p), lows, operand)
    into_always = st.builds(Implication, sequences(2), always, st.booleans())
    return st.one_of(consequent, implication, implication, implication, always, into_always)


def config_directives() -> st.SearchStrategy[Directive]:
    """Valid ``assert`` / ``assume`` directives with a weak unbounded consequent (see
    :func:`_config_properties`), with no ``disable iff`` or the ones of :func:`directives`.
    Validation reports nothing for them and the configuration-set machine decides them."""
    disables = st.one_of(
        st.none(),
        st.just(_signal("d")),
        st.just(Sampled(SampledFunction.ROSE, _signal("d"), BIT)),
    )
    kinds = st.sampled_from([DirectiveKind.ASSERT, DirectiveKind.ASSERT, DirectiveKind.ASSUME])
    return st.builds(
        lambda kind, disable, prop: Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE),
        kinds,
        disables,
        _config_properties(),
    ).filter(lambda d: validation_problems(d) == () and _within_budgets(d))


def until_directives() -> st.SearchStrategy[Directive]:
    """Valid ``assert`` / ``assume`` directives whose property is a weak ``until`` /
    ``until_with`` in one of the positions of :func:`_until_shapes`, with no ``disable iff``
    or the ones of :func:`directives`. Validation reports nothing for them and the
    configuration-set machine decides them (one machine per directive)."""
    disables = st.one_of(
        st.none(),
        st.just(_signal("d")),
        st.just(Sampled(SampledFunction.ROSE, _signal("d"), BIT)),
    )
    kinds = st.sampled_from([DirectiveKind.ASSERT, DirectiveKind.ASSERT, DirectiveKind.ASSUME])
    return st.builds(
        lambda kind, disable, prop: Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE),
        kinds,
        disables,
        _until_shapes(),
    ).filter(lambda d: validation_problems(d) == () and _within_budgets(d))


def strong_unbounded_properties() -> st.SearchStrategy[SequenceProperty]:
    """Strong (the cover default, or explicit ``strong``) sequence properties with an
    unbounded part and no empty match (what a ``cover`` of an unbounded sequence
    accepts). Constant conditions are allowed."""
    body = unbounded_sequences(2).filter(
        lambda s: not nullable(s) and has_unbounded_part(SequenceProperty(s))
    )
    strengths = st.sampled_from([Strength.DEFAULT, Strength.DEFAULT, Strength.STRONG])
    return st.builds(SequenceProperty, body, strengths)


def single_length_antecedents() -> st.SearchStrategy[Sequence]:
    """Bounded, non-empty sequences with exactly one match length (``match_lengths`` is a
    singleton): the antecedents of an accepted cover of an implication. Computed here, not by
    the code under test."""

    def single(sequence: Sequence) -> bool:
        lengths = match_lengths(sequence)
        return lengths is not None and len(lengths) == 1 and not nullable(sequence)

    return sequences(2).filter(single)


def _cover_properties() -> st.SearchStrategy[Property]:
    """What a cover of an unbounded sequence accepts: a strong unbounded sequence, or an
    implication (``|->`` or ``|=>``) with a single-length antecedent into one."""
    consequent = strong_unbounded_properties()
    implication = st.builds(Implication, single_length_antecedents(), consequent, st.booleans())
    return st.one_of(consequent, consequent, implication, implication, implication)


def cover_config_directives() -> st.SearchStrategy[Directive]:
    """Valid ``cover`` directives of strong unbounded sequences and of implications with a
    single-length antecedent into one (see :func:`_cover_properties`), with no ``disable iff``
    or the ones of :func:`directives`. Validation reports nothing for them and the
    configuration-set machine decides them."""
    disables = st.one_of(
        st.none(),
        st.just(_signal("d")),
        st.just(Sampled(SampledFunction.ROSE, _signal("d"), BIT)),
    )
    return st.builds(
        lambda disable, prop: Directive(
            DirectiveKind.COVER, "p", "m", _CLOCK, disable, prop, _SOURCE
        ),
        disables,
        _cover_properties(),
    ).filter(lambda d: validation_problems(d) == () and _within_budgets(d))


def _within_budgets(directive: Directive) -> bool:
    """Whether compiling ``directive`` stays within the configurations and letters budgets
    (a random nest of nondeterministic constructs can need more configurations than the
    budget allows; such a property is a rejection, tested apart). Also excludes strong
    assumptions outside the one-configuration class, whose compiler rejection is tested
    separately. Any other compile error is left to fail the test."""
    from sva2yosys.temporal.compile import ASSUME_LIVENESS, CompileError, compile_monitors

    try:
        compile_monitors(directive)
    except CompileError as error:
        return not (
            "configurations" in str(error)
            or "letters" in str(error)
            or str(error) == ASSUME_LIVENESS
        )
    return True


def _properties() -> st.SearchStrategy[Property]:
    body = sequences().filter(lambda s: not nullable(s))
    implication = st.builds(
        lambda a, c, overlapping, negated: Implication(
            a, Not(SequenceProperty(c)) if negated else SequenceProperty(c), overlapping
        ),
        sequences(2),
        body,
        st.booleans(),
        st.booleans(),
    )
    negation = body.map(lambda s: Not(SequenceProperty(s)))
    return st.one_of(body.map(SequenceProperty), negation, implication, implication)


def directives() -> st.SearchStrategy[Directive]:
    """Valid basic directives (see the module docstring)."""
    disables = st.one_of(
        st.none(),
        st.just(_signal("d")),
        st.just(Sampled(SampledFunction.ROSE, _signal("d"), BIT)),
    )
    kinds = st.sampled_from([DirectiveKind.ASSERT, DirectiveKind.ASSERT, DirectiveKind.COVER])
    return st.builds(
        lambda kind, disable, prop: Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE),
        kinds,
        disables,
        _properties(),
    ).filter(lambda d: validation_problems(d) == () and _canonical_cover(d))


def _leaf_sequences() -> st.SearchStrategy[Sequence]:
    """Small non-nullable sequences: ``x``, ``x ##1 y``, ``x ##[0:1] y``."""
    names = st.sampled_from(SIGNALS)
    plain = names.map(lambda n: Bool(_signal(n)))
    delayed = st.builds(
        lambda x, y, delay: SeqConcat(Bool(_signal(x)), Delay(*delay), Bool(_signal(y))),
        names,
        names,
        st.sampled_from([(1, 1), (0, 1)]),
    )
    return st.one_of(plain, delayed)


def property_trees(depth: int) -> st.SearchStrategy[Property]:
    """Property trees of nesting depth at most ``depth`` over the signals: leaf sequences,
    ``not``, binary ``and`` / ``or``, ``if`` with and without ``else``, and implications of
    both kinds whose consequent is any generated tree. No tree has a nullable property
    sequence. The trees also contain ``nexttime [n]`` (``0 <= n <= 2``),
    ``always [m:n]`` and ``eventually [m:n]`` (``0 <= m <= n <= 2``), weak and strong, over
    any generated tree. Not every tree is accepted by the production pipeline (that is the
    point: they exercise the per-attempt machine)."""
    leaf: st.SearchStrategy[Property] = _leaf_sequences().map(SequenceProperty)
    if depth <= 0:
        return leaf
    inner = property_trees(depth - 1)
    names = st.sampled_from(SIGNALS)
    windows = st.sampled_from([(0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2)])
    return st.one_of(
        leaf,
        st.builds(Nexttime, st.integers(0, 2), st.booleans(), inner),
        st.builds(lambda w, s, p: Always(w[0], w[1], s, p), windows, st.booleans(), inner),
        st.builds(lambda w, s, p: Eventually(w[0], w[1], s, p), windows, st.booleans(), inner),
        inner.map(Not),
        st.builds(lambda p, q: PropAnd((p, q)), inner, inner),
        st.builds(lambda p, q: PropOr((p, q)), inner, inner),
        st.builds(lambda n, p: PropIf(_signal(n), p, None), names, inner),
        st.builds(lambda n, p, q: PropIf(_signal(n), p, q), names, inner, inner),
        st.builds(Implication, _leaf_sequences(), inner, st.booleans()),
    )


MAX_ATTEMPT_SPAN = 10


def tree_span(prop: Property) -> int:
    """An upper bound on the samples an attempt of the tree ``prop`` can span (offsets of
    ``nexttime`` / ``always`` / ``eventually`` included; independent of sva2yosys.temporal),
    used to keep generated lives small."""
    match prop:
        case SequenceProperty(sequence=body):
            return max_length(body)
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            return max_length(antecedent) + (0 if overlapping else 1) + tree_span(consequent)
        case Not(operand=operand):
            return tree_span(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return max(tree_span(o) for o in operands)
        case PropIf(then=then, otherwise=otherwise):
            return max(tree_span(then), 0 if otherwise is None else tree_span(otherwise))
        case Nexttime(count=count, operand=operand):
            return count + tree_span(operand)
        case Always(high=high, operand=operand) | Eventually(high=high, operand=operand):
            assert high is not None, "not generated: unbounded"
            return high + tree_span(operand)
        case Until():
            raise AssertionError("not generated: until")


def attempt_directives(max_depth: int = 3) -> st.SearchStrategy[Directive]:
    """Directives (``assert`` twice as often as ``cover``) over :func:`property_trees`
    (``tree_span`` at most ``MAX_ATTEMPT_SPAN``, so that lives stay small), with
    no ``disable iff``, ``disable iff (d)`` or ``disable iff ($rose(d))``, whose
    structural validation (``validation_problems(d, shapes=False)``) is clean."""
    disables = st.one_of(
        st.none(),
        st.just(_signal("d")),
        st.just(Sampled(SampledFunction.ROSE, _signal("d"), BIT)),
    )
    kinds = st.sampled_from([DirectiveKind.ASSERT, DirectiveKind.ASSERT, DirectiveKind.COVER])
    return st.builds(
        lambda kind, disable, prop: Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE),
        kinds,
        disables,
        property_trees(max_depth),
    ).filter(
        lambda d: (
            validation_problems(d, shapes=False) == () and tree_span(d.property) <= MAX_ATTEMPT_SPAN
        )
    )


def traces(min_length: int = 1, max_length: int = 10) -> st.SearchStrategy[Trace]:
    """Traces of the signals with 0/1 values."""
    row = st.fixed_dictionaries({f"m.{name}": st.integers(0, 1) for name in SIGNALS})
    return st.lists(row, min_size=min_length, max_size=max_length).map(
        lambda rows: Trace(tuple(rows))
    )


def signals_of(directive: Directive) -> tuple[str, ...]:
    """Sorted paths of the signals a directive reads (excluding the clock)."""
    found: set[str] = set()

    def expr(node: Expr) -> None:
        match node:
            case Signal(path=path):
                found.add(path)
            case Const():
                pass
            case Unary(operand=operand) | Sampled(operand=operand):
                expr(operand)
            case Binary(left=left, right=right):
                expr(left)
                expr(right)
            case _:
                raise AssertionError(f"not generated: {node!r}")

    def sequence(node: Sequence) -> None:
        match node:
            case Bool(expr=condition):
                expr(condition)
            case SeqConcat(left=left, right=right):
                sequence(left)
                sequence(right)
            case Repeat(sequence=inner):
                sequence(inner)
            case (
                SeqOr(left=left, right=right)
                | SeqAnd(left=left, right=right)
                | SeqIntersect(left=left, right=right)
                | Within(inner=left, outer=right)
            ):
                sequence(left)
                sequence(right)
            case Throughout(condition=condition, sequence=inner):
                expr(condition)
                sequence(inner)
            case Goto() | NonConsecutive():
                raise AssertionError("not generated: goto and nonconsecutive repetition")

    if directive.disable is not None:
        expr(directive.disable)
    match directive.property:
        case SequenceProperty(sequence=body) | Not(operand=SequenceProperty(sequence=body)):
            sequence(body)
        case Implication(
            antecedent=antecedent,
            consequent=SequenceProperty(sequence=body)
            | Not(operand=SequenceProperty(sequence=body)),
        ):
            sequence(antecedent)
            sequence(body)
        case (
            Implication()
            | Not()
            | PropAnd()
            | PropOr()
            | PropIf()
            | Nexttime()
            | Always()
            | Eventually()
            | Until()
        ):
            raise AssertionError("not generated: nested implication, not of a property, 'and'")
    return tuple(sorted(found))


def all_signals(directive: Directive) -> tuple[str, ...]:
    """Sorted paths of every signal in ``directive``'s property and ``disable iff`` (the clock
    excluded), found by walking the IR generically: it covers every node, including goto,
    nonconsecutive repetition and ``always``."""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, Signal):
            found.add(node.path)
        elif isinstance(node, tuple):
            for child in node:
                walk(child)
        elif is_dataclass(node) and not isinstance(node, type):
            for item in fields(node):
                walk(getattr(node, item.name))

    walk(directive.property)
    walk(directive.disable)
    return tuple(sorted(found))
