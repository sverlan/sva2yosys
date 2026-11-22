"""The obligation model of the reference evaluator: attempts of a property as obligations.

``evaluate`` maps a property to obligations (a consequent that must match, started by an
antecedent match) and reports failures and successes per sample; ``finalize`` decides the
obligations still pending at the end of a simulation. Conventions in
``support.reference.evaluate``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from support.reference.matching import _Matcher, match_ends, sequence_states
from support.reference.shapes import obligation_form as lower
from support.reference.trace import Trace
from support.reference.values import EvaluationError, holds
from sva2yosys.ir.expr import (
    Expr,
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
    effective_strength,
)
from sva2yosys.ir.sequence import (
    TRUE,
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


class Verdict(Enum):
    """Outcome of an obligation or attempt."""

    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"
    DISABLED = "disabled"
    VACUOUS = "vacuous"


@dataclass(frozen=True)
class Obligation:
    """A consequent that must match.

    Attributes:
        trigger: Sample at which the antecedent match ended (for a sequence property:
            the attempt's start).
        start: Sample at which the obligation is evaluated from: the trigger sample
            (for ``a |=> c``, the end of ``a ##1 1``; with an antecedent that admits an
            empty match, the end of ``a``, the consequent being ``##1 c``).
        verdict: ``PASS``, ``FAIL``, ``PENDING`` or ``DISABLED``.
        decided: Sample of the decision (pass, fail or disable), ``None`` if pending.
    """

    trigger: int
    start: int
    verdict: Verdict
    decided: int | None


@dataclass(frozen=True)
class Attempt:
    """One evaluation attempt, started at sample ``start``.

    ``verdict``: ``FAIL`` if an obligation fails (``decided`` = its earliest failure),
    ``DISABLED``, ``VACUOUS`` (antecedent cannot match any more and never matched),
    ``PASS`` (all obligations passed and the antecedent cannot match any more), else
    ``PENDING``.
    """

    start: int
    verdict: Verdict
    decided: int | None
    obligations: tuple[Obligation, ...]


@dataclass(frozen=True)
class Evaluation:
    """Per-attempt results and their per-sample summary.

    Attributes:
        attempts: One per sample, in order.
        failures: Sorted samples at which some obligation fails (what an ``assert``
            reports).
        successes: Sorted samples at which some obligation passes, i.e. non-vacuous
            successes (what a ``cover`` reports).
    """

    attempts: tuple[Attempt, ...]
    failures: tuple[int, ...]
    successes: tuple[int, ...]


@dataclass(frozen=True)
class Finalization:
    """What the end of a finite simulation adds to an :class:`Evaluation`.

    Attributes:
        end_failures: Sorted starts of the obligations that fail because the simulation
            ends while they are pending and their sequence is strong.
    """

    end_failures: tuple[int, ...]


def finalize(prop: Property, evaluation: Evaluation, cover: bool) -> Finalization:
    """Decide the obligations of ``evaluation`` still pending at the end of its trace
    (module docstring); ``cover`` gives the directive's default strength.

    Raises:
        EvaluationError: ``prop`` is a property ``and``: its evaluation merges the
            obligations of conjuncts that may have different strengths, so the end of a
            simulation is not decided for it (formal verification never reaches one).
    """
    sequence_property, negated = _obligation_sequence(lower(prop))
    strong = effective_strength(sequence_property.strength, cover) is Strength.STRONG
    if negated:  # not reverses the strength (§16.12.3)
        strong = not strong
    starts = {
        obligation.start
        for attempt in evaluation.attempts
        for obligation in attempt.obligations
        if obligation.verdict is Verdict.PENDING
    }
    return Finalization(tuple(sorted(starts)) if strong else ())


def _obligation_sequence(prop: Property) -> tuple[SequenceProperty, bool]:
    """The sequence property the obligations of ``prop`` are about, and whether they are
    negated."""
    match prop:
        case SequenceProperty():
            return prop, False
        case Not(operand=SequenceProperty() as inner):
            return inner, True
        case Implication(consequent=SequenceProperty() as consequent):
            return consequent, False
        case Implication(consequent=Not(operand=SequenceProperty() as inner)):
            return inner, True
        case Implication() | Not() | PropAnd() | PropOr() | PropIf():
            raise EvaluationError("an obligation must be about a sequence or not s")
        case Nexttime() | Always() | Eventually() | Until():
            raise EvaluationError("an obligation must be about a sequence or not s")


def evaluate(prop: Property, trace: Trace, disable: Expr | None = None) -> Evaluation:
    """Evaluate every attempt of ``prop`` on ``trace``.

    Raises:
        EvaluationError: The property has an unbounded range or cannot be evaluated.
        TraceError: A needed value is missing.
    """
    prop = lower(prop)
    if isinstance(prop, PropAnd):
        return _evaluate_and(prop, trace, disable)
    disabled_at = (
        tuple(sample for sample in range(len(trace)) if holds(disable, trace, sample))
        if disable is not None
        else ()
    )
    attempts = tuple(_attempt(prop, trace, start, disabled_at) for start in range(len(trace)))
    failures: set[int] = set()
    successes: set[int] = set()
    for attempt in attempts:
        for obligation in attempt.obligations:
            if obligation.verdict is Verdict.FAIL and obligation.decided is not None:
                failures.add(obligation.decided)
            elif obligation.verdict is Verdict.PASS and obligation.decided is not None:
                successes.add(obligation.decided)
    return Evaluation(attempts, tuple(sorted(failures)), tuple(sorted(successes)))


def _evaluate_and(prop: PropAnd, trace: Trace, disable: Expr | None) -> Evaluation:
    """``p and q`` (§16.12.5), attempt by attempt, independently of the monitors.

    An attempt fails at the earlier of its operands' failures; otherwise it is disabled
    if an operand is, pending if one is, and passes (nonvacuously if an operand does)
    at the later decision. ``failures`` is the union of the operands' failures, which is
    what the conjunct monitors report together: only its *first* sample is the failure of
    the conjunction (``scope: first-failure`` in the trace tables). ``successes`` is empty:
    the success of a conjunction is not defined here (a cover of it is rejected)."""
    parts = [evaluate(operand, trace, disable) for operand in prop.operands]
    attempts: list[Attempt] = []
    for start in range(len(trace)):
        found = [part.attempts[start] for part in parts]
        obligations = tuple(o for attempt in found for o in attempt.obligations)
        failed = [a.decided for a in found if a.verdict is Verdict.FAIL and a.decided is not None]
        if failed:
            verdict, decided = Verdict.FAIL, min(failed)
        elif any(a.verdict is Verdict.DISABLED for a in found):
            verdict = Verdict.DISABLED
            decided = min(
                a.decided for a in found if a.verdict is Verdict.DISABLED and a.decided is not None
            )
        elif any(a.verdict is Verdict.PENDING for a in found):
            verdict, decided = Verdict.PENDING, None
        else:
            verdict = (
                Verdict.PASS if any(a.verdict is Verdict.PASS for a in found) else Verdict.VACUOUS
            )
            decided = max(a.decided for a in found if a.decided is not None)
        attempts.append(Attempt(start, verdict, decided, obligations))
    failures = sorted({f for part in parts for f in part.failures})
    return Evaluation(tuple(attempts), tuple(failures), ())


# --- attempts -----------------------------------------------------------------------------


def _first_disable(disabled_at: tuple[int, ...], start: int, end: int) -> int | None:
    for sample in disabled_at:
        if start <= sample <= end:
            return sample
    return None


@dataclass(frozen=True)
class _Shape:
    """An attempt's obligations: ``(trigger, start)`` pairs, the consequent, and the sample
    after which the antecedent can produce no further trigger (``None``: still can)."""

    triggers: tuple[tuple[int, int], ...]
    consequent: Sequence
    antecedent_done: int | None
    negated: bool = False


def _shape(prop: Property, trace: Trace, start: int) -> _Shape:
    match prop:
        case SequenceProperty(sequence=sequence):
            return _Shape(((start, start),), sequence, start)
        case Not(operand=SequenceProperty(sequence=sequence)):
            return _Shape(((start, start),), sequence, start, negated=True)
        case Not():
            raise EvaluationError("'not' is evaluated only on a sequence")
        case PropAnd():
            raise EvaluationError("a property 'and' is evaluated by evaluate()")
        case PropOr() | PropIf() | Nexttime() | Always() | Eventually() | Until():
            raise EvaluationError("this property operator is not evaluated by the obligation model")
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            negated = isinstance(consequent, Not)
            inner = consequent.operand if isinstance(consequent, Not) else consequent
            if not isinstance(inner, SequenceProperty):
                raise EvaluationError("an implication consequent must be a sequence or not s")
            body = inner.sequence
            if not overlapping:
                if _nullable(antecedent):
                    raise EvaluationError("|=> with an antecedent admitting an empty match")
                antecedent = SeqConcat(antecedent, Delay(1, 1), TRUE)
            ends = sorted(match_ends(antecedent, trace, start))
            triggers = tuple((end, end) for end in ends)
            return _Shape(triggers, body, _finished(antecedent, trace, start), negated)


def _attempt(prop: Property, trace: Trace, start: int, disabled_at: tuple[int, ...]) -> Attempt:
    last = len(trace) - 1
    shape = _shape(prop, trace, start)
    triggers, consequent, antecedent_done = shape.triggers, shape.consequent, shape.antecedent_done
    raw = [_decide(consequent, trace, begin) for _, begin in triggers]
    if shape.negated:
        swap = {Verdict.PASS: Verdict.FAIL, Verdict.FAIL: Verdict.PASS}
        raw = [(swap.get(verdict, verdict), decided) for verdict, decided in raw]
    obligations: list[Obligation] = []
    for (trigger, begin), (verdict, decided) in zip(triggers, raw, strict=True):
        disabled = _first_disable(disabled_at, start, last if decided is None else decided)
        if disabled is not None:
            obligations.append(Obligation(trigger, begin, Verdict.DISABLED, disabled))
        else:
            obligations.append(Obligation(trigger, begin, verdict, decided))

    failed = [
        decided for verdict, decided in raw if verdict is Verdict.FAIL and decided is not None
    ]
    if failed:
        verdict, decided = Verdict.FAIL, min(failed)
    elif antecedent_done is None or any(v is Verdict.PENDING for v, _ in raw):
        verdict, decided = Verdict.PENDING, None
    elif not triggers:
        verdict, decided = Verdict.VACUOUS, antecedent_done
    else:
        verdict = Verdict.PASS
        decided = max([antecedent_done, *[d for _, d in raw if d is not None]])
    disabled = _first_disable(disabled_at, start, last if decided is None else decided)
    if disabled is not None:
        verdict, decided = Verdict.DISABLED, disabled
    return Attempt(start, verdict, decided, tuple(obligations))


def _decide(sequence: Sequence, trace: Trace, start: int) -> tuple[Verdict, int | None]:
    """Pass at the first sample where a non-empty match ends; fail at the first sample
    after which none is possible; otherwise pending."""
    slack = sequence_states(sequence)
    for known in range(start, len(trace)):
        ends = {e for e in _Matcher(trace, known, slack).ends(sequence, start) if e > start}
        if any(e <= known + 1 for e in ends):
            return Verdict.PASS, known
        if not ends:
            return Verdict.FAIL, known
    return Verdict.PENDING, None


def _nullable(sequence: Sequence) -> bool:
    """Whether ``sequence`` admits an empty match (structurally)."""
    match sequence:
        case Bool():
            return False
        case SeqConcat(left=left, delay=delay, right=right):
            # ##1 concatenates (both sides may be empty); ##0 fuses and ##n, n >= 2, adds
            # samples: neither is ever empty.
            one = delay.minimum <= 1 and (delay.maximum is None or delay.maximum >= 1)
            return one and _nullable(left) and _nullable(right)
        case Repeat(sequence=inner, minimum=minimum):
            return minimum == 0 or _nullable(inner)
        case Goto(minimum=minimum) | NonConsecutive(minimum=minimum):
            return minimum == 0
        case SeqOr(left=left, right=right):
            return _nullable(left) or _nullable(right)
        case SeqAnd(left=left, right=right) | SeqIntersect(left=left, right=right):
            return _nullable(left) and _nullable(right)
        case Within(inner=inner, outer=outer):
            return _nullable(inner) and _nullable(outer)
        case Throughout(sequence=inner):
            return _nullable(inner)


def _finished(sequence: Sequence, trace: Trace, start: int) -> int | None:
    """First sample after which no further non-empty match of ``sequence`` can end."""
    slack = sequence_states(sequence)
    for known in range(start, len(trace)):
        ends = _Matcher(trace, known, slack).ends(sequence, start)
        if all(e <= known + 1 for e in ends if e > start):
            return known
    return None
