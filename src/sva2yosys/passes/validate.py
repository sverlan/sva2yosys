"""Validity of IR directives, independent of how the IR was built.

The converter only produces valid IR, but the IR types can express more (nested
sampled-value functions, other clock edges, unbounded ranges, out-of-range selects).
Every consumer of IR that relies on the supported subset calls
:func:`validation_problems` first; the temporal compiler does.

Rules (each violation gives one message):

- the clock is ``posedge`` of a 1-bit signal, without ``iff``;
- unbounded parts (a delay or repetition range with ``$``, goto and nonconsecutive
  repetition, unbounded ``always`` / ``eventually``, the ``until`` family; the converter
  builds them for the reference semantics, ``docs/semantics.md`` §9.6) are supported only
  where the canonical machine decides them (:func:`_unbounded_supported`), in an
  ``assert`` or ``assume``: after lowering, every conjunct that has an unbounded part is an
  implication whose antecedent may be unbounded and whose consequent, a sequence or ``not``
  of one, is bounded. Unbounded ``always`` is lowered to such an implication
  (:func:`~sva2yosys.temporal.lower.lower`). Every other unbounded part is reported
  (``eventually``, strong ``until``, other operators), so none reaches the
  per-attempt machine. A ``cover`` is judged on the property as written
  (:func:`cover_unbounded`): a strong unbounded sequence, or an implication with
  a bounded single-length antecedent into one, is supported (the configuration-set machine
  decides it); an explicit ``weak(...)`` of an unbounded sequence, a weak ``until``, an
  unbounded or multi-length antecedent into an unbounded consequent are rejected with an
  explanation, and every other cover with an unbounded part with the generic messages;
- ``nexttime [n]`` has ``n >= 0`` and ``always`` / ``eventually [m:n]`` have
  ``0 <= m <= n`` (the converter only builds valid ones);
- ``a |=> p`` has an antecedent without an empty match;
- a property ``if`` with an ``else`` has a condition without ``$past`` (the converter
  rejects it too);
- (supported shapes, ``shapes=True``) every property ``and`` / ``or`` has at least two
  operands, and a ``cover`` satisfies the cover rule
  (:func:`~sva2yosys.temporal.nonvacuity.cover_problem`, ``docs/semantics.md`` section 9.5,
  checked on the property as written): its nonvacuity must be known when an attempt is
  decided true. Every other property shape is accepted; the canonical machine or the
  per-attempt machine decides it (:mod:`sva2yosys.temporal.compile`);
- sampled-value functions are not nested (their operand contains none); a ``$past``
  depth is at least 1, other functions have depth 1;
- bit selects index inside their operand, part selects have ``lsb <= msb`` inside their
  operand, replication counts are at least 1, concatenations are not empty;
- constants fit their type;
- the divisor of ``/`` and ``%`` is a non-zero ``Const`` (the exporter has Slang fold
  every constant expression, e.g. ``-1`` or ``N + 1``; anything left unfolded is
  rejected): division by zero gives X in
  SystemVerilog (so the IEEE assertion fails), while a generated monitor would compute a
  defined value and could prove the property;
- :func:`~sva2yosys.passes.first_sample.first_sample_problems` reports nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from sva2yosys.ir.clock import Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import (
    Binary,
    BinaryOp,
    BitSelect,
    Concat,
    Conditional,
    Const,
    Convert,
    Expr,
    FirstSample,
    RangeSelect,
    Replicate,
    Sampled,
    SampledFunction,
    Signal,
    Unary,
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
    conjuncts,
)
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
)
from sva2yosys.passes.first_sample import first_sample_problems, past_depth
from sva2yosys.temporal.bounds import sequence_bounds, unbounded_sequence
from sva2yosys.temporal.lower import lower
from sva2yosys.temporal.nonvacuity import cover_problem


@dataclass(frozen=True)
class CoverUnbounded:
    """Whether the unbounded parts of a ``cover`` are supported (``docs/semantics.md`` §6, §9.6),
    judged on the property **as written**.

    Attributes:
        supported: The cover has no unbounded part, or it is one the configuration-set machine
            decides exactly. A cover is supported with an unbounded part only when it is

            - a sequence property whose unbounded sequence is strong (the cover default, or
              ``strong(...)``); or
            - an implication (``|->``, ``|=>``) whose antecedent is bounded and has a single
              match length (``min_length == max_length`` over its non-empty matches: an empty
              match is ignored, as for bounded covers), and whose consequent is such a
              sequence property.
        explained: Specific rejection messages when the cover is not supported for a reason
            that is worth explaining (a weak unbounded sequence, a weak ``until``, an unbounded
            or multi-length antecedent). Empty when ``supported``, and also when the generic
            "not supported yet" messages of the unbounded parts say enough.
    """

    supported: bool
    explained: tuple[str, ...]


_WEAK_COVER = (
    "a cover of a weak unbounded sequence is not supported: weak(...) can hold on an infinite "
    "trace without a finite match, so a finite match does not decide what the cover counts "
    "(docs/semantics.md section 9.6)"
)
_ANTECEDENT_COVER = (
    "a cover of an implication with an unbounded antecedent is not supported: it needs one "
    "result per attempt, which the machines do not give for a loop in the antecedent "
    "(docs/semantics.md section 9.6)"
)
_LENGTHS_COVER = (
    "a cover of an implication with an unbounded consequent needs an antecedent with a single "
    "match length: an attempt with several antecedent matches succeeds only when the "
    "consequents of all of them do, which needs one result per attempt "
    "(docs/semantics.md section 9.6)"
)


def cover_unbounded(written: Property) -> CoverUnbounded:
    """Classify the unbounded parts of ``cover property (written)`` (see
    :class:`CoverUnbounded`). ``written`` is the property before lowering; a property without
    an unbounded part is ``supported`` with nothing to explain."""
    if not has_unbounded_part(written):
        return CoverUnbounded(True, ())
    untils = _weak_untils(written)
    if untils:
        return CoverUnbounded(
            False,
            tuple(
                f"a cover of '{name}' is not supported: it is weak, so a trace can satisfy it "
                "without any finite match, and what a cover counts is not decided by a finite "
                "match (docs/semantics.md section 9.6)"
                for name in untils
            ),
        )
    match written:
        case SequenceProperty(strength=strength):
            if strength is Strength.WEAK:
                return CoverUnbounded(False, (_WEAK_COVER,))
            return CoverUnbounded(True, ())
        case Implication(
            antecedent=antecedent,
            consequent=SequenceProperty(sequence=body, strength=strength),
        ):
            if unbounded_sequence(antecedent):
                return CoverUnbounded(False, (_ANTECEDENT_COVER,))
            bounds = sequence_bounds(antecedent)
            if bounds.min_length != bounds.max_length:
                return CoverUnbounded(False, (_LENGTHS_COVER,))
            if unbounded_sequence(body) and strength is Strength.WEAK:
                return CoverUnbounded(False, (_WEAK_COVER,))
            return CoverUnbounded(True, ())
        case _:
            return CoverUnbounded(False, ())


def _weak_untils(prop: Property) -> tuple[str, ...]:
    """The names (``until``, ``until_with``) of the weak ``until`` nodes anywhere in ``prop``,
    in order."""
    match prop:
        case Until(strong=strong, inclusive=inclusive):
            if strong:
                return ()
            return ("until_with" if inclusive else "until",)
        case SequenceProperty():
            return ()
        case (
            Implication(consequent=operand)
            | Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _weak_untils(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return tuple(name for operand in operands for name in _weak_untils(operand))
        case PropIf(then=then, otherwise=otherwise):
            found = _weak_untils(then)
            return found + (() if otherwise is None else _weak_untils(otherwise))


def validation_problems(directive: Directive, *, shapes: bool = True) -> tuple[str, ...]:
    """The validity rules ``directive`` violates, in rule order; empty if valid.

    With ``shapes=False`` only the supported-shapes rules are skipped (the operand count
    of ``and`` / ``or`` and the cover rule); the structural rules, the expression rules,
    the ``|=>`` antecedent rule, the ``$past`` if/else rule and the first-sample rule are
    still checked.

    The structural rules (sequences, expressions, ``|=>`` antecedents) are checked on the
    whole property tree as written; the operand-count rule on its lowered form
    (:func:`~sva2yosys.temporal.lower.lower`), the cover rule on the property as written."""
    problems: list[str] = []
    clock = directive.clock
    if clock.edge is not Edge.POSEDGE:
        problems.append(f"only posedge clocks are supported, not {clock.edge.value}")
    if clock.enable is not None:
        problems.append("clocks with an iff condition are not supported yet")
    if clock.signal.type.width != 1:
        problems.append("the clock must be a 1-bit signal")
    lowered = lower(directive.property)
    explained: tuple[str, ...] = ()
    if directive.kind is DirectiveKind.COVER:
        support = cover_unbounded(directive.property)
        allowed, explained = support.supported, support.explained
    else:
        # (a strong assume part is judged by the compiler, which knows its configurations)
        allowed = _unbounded_supported(lowered)
    problems.extend(_window_problems(directive.property))
    problems.extend(explained)
    if not allowed and not explained:
        problems.extend(_unsupported_property_problems(directive.property))
        problems.extend(_consequent_problems(lowered))
    sequences: list[Sequence] = []
    conditions: list[Expr] = []
    _collect(directive.property, sequences, conditions)
    for antecedent in _nonoverlapping_antecedents(directive.property):
        if sequence_bounds(antecedent).nullable:
            # a |=> p is (a ##1 1) |-> p; with an empty antecedent match that would
            # trigger, which IEEE excludes, and a |-> ##1 c would decide too early.
            problems.append("'|=>' with an antecedent that can match empty is not supported")
    if _has_past_if_else(directive.property):
        problems.append("a property if/else whose condition uses $past is not supported")
    if shapes:
        problems.extend(
            _shape_problems(lowered, directive.property, directive.kind is DirectiveKind.COVER)
        )
    for sequence in sequences:
        _check_sequence(sequence, problems, allowed or bool(explained))
    conditions.extend(c for s in sequences for c in _conditions(s))
    if directive.disable is not None:
        conditions.append(directive.disable)
    for condition in conditions:
        _check_expr(condition, problems, inside_sampled=False)
    if not problems:
        problems.extend(first_sample_problems(replace(directive, property=lowered)))
    return tuple(dict.fromkeys(problems))


def _collect(prop: Property, sequences: list[Sequence], conditions: list[Expr]) -> None:
    """Every sequence and every ``if`` condition anywhere in ``prop``."""
    match prop:
        case SequenceProperty(sequence=body):
            sequences.append(body)
        case Implication(antecedent=antecedent, consequent=consequent):
            sequences.append(antecedent)
            _collect(consequent, sequences, conditions)
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            _collect(operand, sequences, conditions)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            for operand in operands:
                _collect(operand, sequences, conditions)
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            conditions.append(condition)
            _collect(then, sequences, conditions)
            if otherwise is not None:
                _collect(otherwise, sequences, conditions)
        case Until(left=left, right=right):
            conditions.extend((left, right))


def _window_problems(prop: Property) -> list[str]:
    """The invalid ``nexttime`` counts and ``always`` / ``eventually`` ranges anywhere in
    ``prop``, one message each."""
    match prop:
        case SequenceProperty() | Until():
            return []
        case Implication(consequent=consequent):
            return _window_problems(consequent)
        case Not(operand=operand):
            return _window_problems(operand)
        case Nexttime(count=count, operand=operand):
            found = [f"nexttime count {count} must be at least 0"] if count < 0 else []
            return found + _window_problems(operand)
        case Always(low=low, high=high, operand=operand):
            invalid = low < 0 or (high is not None and high < low)
            found = [f"always range [{low}:{_bound(high)}] is invalid"] if invalid else []
            return found + _window_problems(operand)
        case Eventually(low=low, high=high, operand=operand):
            invalid = low < 0 or (high is not None and high < low)
            found = [f"eventually range [{low}:{_bound(high)}] is invalid"] if invalid else []
            return found + _window_problems(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return [p for operand in operands for p in _window_problems(operand)]
        case PropIf(then=then, otherwise=otherwise):
            found = _window_problems(then)
            if otherwise is not None:
                found += _window_problems(otherwise)
            return found


def _bound(high: int | None) -> str:
    return "$" if high is None else str(high)


def strong_unbounded_part(part: Property) -> bool:
    """Whether the conjunct ``part`` of the lowered property of an ``assert`` or ``assume`` is
    strong after negation (16.12.3: ``not`` reverses strength) with an unbounded sequence: a
    strong unbounded sequence property, ``not`` of a weak one (a ``DEFAULT`` strength is weak
    here, 16.12.2), or an implication into one of those. These are the shapes with a liveness
    part."""
    match part:
        case Implication(consequent=SequenceProperty() | Not() as consequent):
            return strong_unbounded_part(consequent)
        case SequenceProperty(sequence=body, strength=strength):
            return unbounded_sequence(body) and strength is Strength.STRONG
        case Not(operand=SequenceProperty(sequence=body, strength=strength)):
            return unbounded_sequence(body) and strength is not Strength.STRONG
        case _:
            return False


def _unbounded_supported(lowered: Property) -> bool:
    """Whether every unbounded part of the ``lowered`` property of an ``assert`` or ``assume``
    is one a machine decides exactly (``docs/semantics.md`` §9.6; a ``cover`` is judged on the
    property as written by :func:`cover_unbounded`): each conjunct that has an unbounded part
    is

    - an implication whose consequent is a sequence or ``not`` of one: the antecedent may be
      unbounded (the canonical machine), the consequent unbounded (the
      configuration-set machine, also under ``not``); or
    - a sequence property or ``not`` of one (the whole property).

    A part that is strong after negation (:func:`strong_unbounded_part`: liveness)
    is accepted here for ``assume`` too: the compiler accepts it only with one
    configuration (a fairness constraint) and rejects it otherwise.
    True when there is no unbounded part."""
    for part in conjuncts(lowered):
        if not has_unbounded_part(part):
            continue
        match part:
            case (
                Implication(consequent=SequenceProperty() | Not(operand=SequenceProperty()))
                | SequenceProperty()
                | Not(operand=SequenceProperty())
            ):
                continue
            case _:
                pass
        return False
    return True


def _consequent_problems(lowered: Property) -> list[str]:
    """For each conjunct with an unbounded antecedent (supported) whose consequent the
    canonical machine cannot decide, say that the consequent is the limit."""
    found: list[str] = []
    for part in conjuncts(lowered):
        if not isinstance(part, Implication) or not unbounded_sequence(part.antecedent):
            continue
        match part.consequent:
            case SequenceProperty(sequence=body) | Not(operand=SequenceProperty(sequence=body)):
                if not unbounded_sequence(body):
                    continue
                found.append("an unbounded consequent is not supported yet")
            case _:
                found.append(
                    "an unbounded antecedent needs a bounded sequence (or not of one) as "
                    "consequent; this consequent is not supported yet"
                )
    return found


def has_unbounded_part(prop: Property) -> bool:
    """Whether ``prop`` has an unbounded part: a sequence with a ``$`` range, goto or
    nonconsecutive repetition (structurally: whatever the counts), ``always`` or
    ``eventually`` without an upper bound, or an ``until``. The per-attempt machine, which is
    bounded, refuses any such property."""
    match prop:
        case SequenceProperty(sequence=body):
            return unbounded_sequence(body)
        case Until():
            return True
        case Always(high=high, operand=operand) | Eventually(high=high, operand=operand):
            return high is None or has_unbounded_part(operand)
        case Implication(antecedent=antecedent, consequent=consequent):
            return unbounded_sequence(antecedent) or has_unbounded_part(consequent)
        case Not(operand=operand) | Nexttime(operand=operand):
            return has_unbounded_part(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return any(has_unbounded_part(operand) for operand in operands)
        case PropIf(then=then, otherwise=otherwise):
            return has_unbounded_part(then) or (
                otherwise is not None and has_unbounded_part(otherwise)
            )


def _unsupported_property_problems(prop: Property) -> list[str]:
    """The property operators that the machines do not support yet, anywhere in
    ``prop`` (goto and nonconsecutive repetition are reported with the sequences)."""
    match prop:
        case SequenceProperty():
            return []
        case Until(strong=strong, inclusive=inclusive):
            name = ("s_" if strong else "") + ("until_with" if inclusive else "until")
            return [f"'{name}' is not supported yet"]
        case Always(high=high, operand=operand):
            found = (
                ["unbounded 'always' (no upper bound) is not supported yet"] if high is None else []
            )
            return found + _unsupported_property_problems(operand)
        case Eventually(high=high, operand=operand):
            found = (
                ["unbounded 's_eventually' (no upper bound) is not supported yet"]
                if high is None
                else []
            )
            return found + _unsupported_property_problems(operand)
        case Implication(consequent=consequent):
            return _unsupported_property_problems(consequent)
        case Not(operand=operand) | Nexttime(operand=operand):
            return _unsupported_property_problems(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return [p for operand in operands for p in _unsupported_property_problems(operand)]
        case PropIf(then=then, otherwise=otherwise):
            found = _unsupported_property_problems(then)
            if otherwise is not None:
                found += _unsupported_property_problems(otherwise)
            return found


def _nonoverlapping_antecedents(prop: Property) -> list[Sequence]:
    """The antecedents of every ``|=>`` anywhere in ``prop``."""
    match prop:
        case SequenceProperty() | Until():
            return []
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            found = _nonoverlapping_antecedents(consequent)
            return found if overlapping else [antecedent, *found]
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _nonoverlapping_antecedents(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return [a for operand in operands for a in _nonoverlapping_antecedents(operand)]
        case PropIf(then=then, otherwise=otherwise):
            found = _nonoverlapping_antecedents(then)
            if otherwise is not None:
                found += _nonoverlapping_antecedents(otherwise)
            return found


def _shape_problems(lowered: Property, written: Property, cover: bool) -> list[str]:
    """The supported-shapes rules: the operand count of every ``and`` / ``or`` in the
    ``lowered`` property, and for a ``cover`` the cover rule on the property as ``written``."""
    found: list[str] = []
    for text, operator in (
        ("a property 'and' needs at least two operands", PropAnd),
        ("a property 'or' needs at least two operands", PropOr),
    ):
        if _has_short_operator(lowered, operator):
            found.append(text)
    if cover:
        message = cover_problem(written)
        if message is not None:
            found.append(message)
    return found


def _has_short_operator(prop: Property, operator: type[PropAnd] | type[PropOr]) -> bool:
    """Whether an ``operator`` node anywhere in ``prop`` has fewer than two operands."""
    match prop:
        case SequenceProperty() | Until():
            return False
        case Implication(consequent=consequent):
            return _has_short_operator(consequent, operator)
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _has_short_operator(operand, operator)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return (isinstance(prop, operator) and len(operands) < 2) or any(
                _has_short_operator(o, operator) for o in operands
            )
        case PropIf(then=then, otherwise=otherwise):
            return _has_short_operator(then, operator) or (
                otherwise is not None and _has_short_operator(otherwise, operator)
            )


def _has_past_if_else(prop: Property) -> bool:
    """Whether a property ``if`` with an ``else`` anywhere in ``prop`` has a condition that
    uses ``$past``."""
    match prop:
        case SequenceProperty() | Until():
            return False
        case Implication(consequent=consequent):
            return _has_past_if_else(consequent)
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _has_past_if_else(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return any(_has_past_if_else(o) for o in operands)
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            if otherwise is not None and past_depth(condition) > 0:
                return True
            return _has_past_if_else(then) or (
                otherwise is not None and _has_past_if_else(otherwise)
            )


def _check_sequence(sequence: Sequence, problems: list[str], allowed: bool) -> None:
    """Append the problems of ``sequence``; ``allowed``: the directive's unbounded parts
    are supported (:func:`_unbounded_supported`), so they are not problems."""
    match sequence:
        case Bool():
            pass
        case SeqConcat(left=left, delay=delay, right=right):
            if delay.maximum is None and not allowed:
                problems.append("unbounded ranges ($) are not supported yet")
            _check_sequence(left, problems, allowed)
            _check_sequence(right, problems, allowed)
        case Repeat(sequence=inner, maximum=maximum):
            if maximum is None and not allowed:
                problems.append("unbounded ranges ($) are not supported yet")
            _check_sequence(inner, problems, allowed)
        case Goto(maximum=maximum):
            if not allowed:
                if maximum is None:
                    problems.append("unbounded ranges ($) are not supported yet")
                problems.append("goto repetition [->] is not supported yet")
        case NonConsecutive(maximum=maximum):
            if not allowed:
                if maximum is None:
                    problems.append("unbounded ranges ($) are not supported yet")
                problems.append("nonconsecutive repetition [=] is not supported yet")
        case SeqOr(left=left, right=right):
            _check_sequence(left, problems, allowed)
            _check_sequence(right, problems, allowed)
        case (
            SeqAnd(left=left, right=right)
            | SeqIntersect(left=left, right=right)
            | Within(inner=left, outer=right)
        ):
            _check_sequence(left, problems, allowed)
            _check_sequence(right, problems, allowed)
            if not problems and (_nullable(left) or _nullable(right)):
                problems.append(
                    "an operand of and/intersect/within that can match empty is not supported yet"
                )
        case Throughout(sequence=inner):
            _check_sequence(inner, problems, allowed)


def _conditions(sequence: Sequence) -> list[Expr]:
    match sequence:
        case Bool(expr=expr):
            return [expr]
        case SeqConcat(left=left, right=right):
            return _conditions(left) + _conditions(right)
        case Repeat(sequence=inner):
            return _conditions(inner)
        case Goto(condition=condition) | NonConsecutive(condition=condition):
            return [condition]
        case (
            SeqOr(left=left, right=right)
            | SeqAnd(left=left, right=right)
            | SeqIntersect(left=left, right=right)
            | Within(inner=left, outer=right)
        ):
            return _conditions(left) + _conditions(right)
        case Throughout(condition=condition, sequence=inner):
            return [condition, *_conditions(inner)]


def expression_problems(expr: Expr, *, inside_sampled: bool = False) -> list[str]:
    """The problems validation reports for the expression ``expr`` (e.g. a divisor that
    is not a non-zero constant), for a caller that checks an expression it then
    discards (``$isunknown``'s operand, docs/semantics.md §8a)."""
    problems: list[str] = []
    _check_expr(expr, problems, inside_sampled=inside_sampled)
    return problems


def _check_expr(expr: Expr, problems: list[str], *, inside_sampled: bool) -> None:
    match expr:
        case Signal():
            pass
        case FirstSample(type=value_type):
            if value_type.width != 1 or value_type.signed:
                problems.append("the first-sample value is a 1-bit unsigned value")
            if inside_sampled:
                problems.append("the first-sample value inside a sampled-value function")
        case Const(value=value, type=value_type):
            if not 0 <= value < 1 << value_type.width:
                problems.append(f"constant {value} does not fit {value_type.width} bit(s)")
        case Sampled(function=function, operand=operand, depth=depth):
            if inside_sampled:
                problems.append("nested sampled-value functions are not supported yet")
            if depth < 1 or (function is not SampledFunction.PAST and depth != 1):
                problems.append(f"{function.value} with depth {depth} is invalid")
            _check_expr(operand, problems, inside_sampled=True)
        case Unary(operand=operand) | Convert(operand=operand):
            _check_expr(operand, problems, inside_sampled=inside_sampled)
        case Binary(op=op, left=left, right=right):
            divisor_ok = isinstance(right, Const) and right.value != 0
            if op in (BinaryOp.DIVIDE, BinaryOp.MOD) and not divisor_ok:
                problems.append(f"the divisor of {op.value} must be a non-zero constant")
            _check_expr(left, problems, inside_sampled=inside_sampled)
            _check_expr(right, problems, inside_sampled=inside_sampled)
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            for part in (condition, then, otherwise):
                _check_expr(part, problems, inside_sampled=inside_sampled)
        case BitSelect(value=value, index=index):
            if not 0 <= index < value.type.width:
                problems.append(f"bit select index {index} is outside {value.type.width} bit(s)")
            _check_expr(value, problems, inside_sampled=inside_sampled)
        case RangeSelect(value=value, msb=msb, lsb=lsb):
            if not 0 <= lsb <= msb < value.type.width:
                problems.append(
                    f"part select [{msb}:{lsb}] is invalid for {value.type.width} bit(s)"
                )
            _check_expr(value, problems, inside_sampled=inside_sampled)
        case Replicate(count=count, value=value):
            if count < 1:
                problems.append(f"replication count {count} must be at least 1")
            _check_expr(value, problems, inside_sampled=inside_sampled)
        case Concat(parts=parts):
            if not parts:
                problems.append("a concatenation must not be empty")
            for part in parts:
                _check_expr(part, problems, inside_sampled=inside_sampled)


def _nullable(sequence: Sequence) -> bool:
    return sequence_bounds(sequence).nullable
