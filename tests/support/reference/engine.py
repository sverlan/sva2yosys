"""The per-attempt engine of the reference evaluator (``docs/semantics.md`` section 9.2).

Truth and nonvacuity of every attempt of a property, by prefix; the finalization of the
attempts pending at the end of a trace. Conventions in ``support.reference.evaluate``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from support.reference.matching import _Matcher, _propertysequence_states
from support.reference.obligations import _first_disable, _nullable
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
    Delay,
    SeqConcat,
    Sequence,
)


class Outcome(Enum):
    """Result of one attempt of a property."""

    PENDING = "pending"
    TRUE = "true"
    FALSE = "false"
    DISABLED = "disabled"


@dataclass(frozen=True)
class AttemptResult:
    """The result of the attempt started at sample ``start``.

    Attributes:
        start: The attempt's first sample.
        outcome: ``TRUE`` / ``FALSE`` once decided, ``PENDING`` if not decided by the end of
            the trace, ``DISABLED`` if the ``disable iff`` condition held at a sample of
            ``[start, decision]`` (``[start, last]`` while pending).
        decided: The sample of the decision, or of the disable; ``None`` while pending.
        nonvacuous: Whether the attempt is nonvacuous (§16.14.8), as known at the end of
            the trace; ``None`` while still unknown there.
        nonvacuity_known: The first sample at which nonvacuity is known (it may be before
            or after the decision); ``None`` while unknown at the end of the trace.
            Reported for disabled attempts too, but then irrelevant.
    """

    start: int
    outcome: Outcome
    decided: int | None
    nonvacuous: bool | None
    nonvacuity_known: int | None


@dataclass(frozen=True)
class AttemptEvaluation:
    """Per-attempt results and their directive-level summary.

    Attributes:
        attempts: One per sample, in order.
        failures: Sorted decision samples of the ``FALSE`` attempts (what an ``assert``
            reports).
        successes: Sorted decision samples of the ``TRUE`` attempts that are nonvacuous
            with the nonvacuity known at or before the decision (what a ``cover`` reports).
    """

    attempts: tuple[AttemptResult, ...]
    failures: tuple[int, ...]
    successes: tuple[int, ...]


def attempt_results(
    prop: Property, trace: Trace, disable: Expr | None = None
) -> tuple[AttemptResult, ...]:
    """The result of every attempt of ``prop`` (one per start sample), evaluated by
    prefix: the truth of an attempt is decided at the first sample ``t`` at which the
    samples ``0 … t`` determine it, with later samples treated as in the module docstring.

    Truth:

    - sequence property: ``TRUE`` at the first sample a non-empty match ends, ``FALSE``
      when no non-empty match is possible any more;
    - ``not p``: the swapped truth of ``p``; ``p and q`` / ``p or q`` (n-ary): ``FALSE`` /
      ``TRUE`` at the first sample some operand is, else decided when all operands are;
    - ``if (e) p else q``: ``e`` at the attempt's first sample (first-sample rule) selects
      the branch; without ``else`` and ``e`` false: ``TRUE`` at the start;
    - ``a |-> p``: every distinct match of ``a`` ending at ``e`` starts an attempt of ``p``
      at ``e`` (``a |=> p`` is ``(a ##1 1) |-> p``); ``FALSE`` at the first sample some
      such attempt is; ``TRUE`` when ``a`` can match no more and all started attempts are
      ``TRUE`` (also when there are none);
    - ``nexttime[n] p`` (evaluated natively, strength ignored online): pending until
      sample ``s + n`` is known, then the attempt of ``p`` started there; ``always[m:n] p``:
      ``FALSE`` at the first sample some started offset ``s + i`` (``m <= i <= n``) is,
      ``TRUE`` once ``s + n`` is known and all offsets are; ``eventually[m:n] p``: ``TRUE``
      at the first sample some started offset is, ``FALSE`` once ``s + n`` is known and
      all offsets are. With ``n`` unbounded (``$``) the window never completes: ``always``
      is never ``TRUE`` and ``eventually`` never ``FALSE`` online;
    - ``p until q`` (Boolean ``p``, ``q``; ``holds`` at each sample ``t`` from the start):
      ``TRUE`` at the first ``t`` where ``q`` holds (``until_with``: where ``p`` and ``q``
      hold), ``FALSE`` at the first ``t`` where ``p`` does not hold and that condition does
      not give ``TRUE``; else pending (never decided if neither happens).

    Nonvacuity (:class:`AttemptResult`): a sequence property is nonvacuous; ``not p``
    as ``p``; ``and`` / ``or`` nonvacuous if an operand is, vacuous if all are, else
    unknown; ``if``: the selected branch's (vacuous without ``else`` when ``e`` is false);
    implication: nonvacuous if some started attempt is, vacuous if ``a`` can match no
    more and all started attempts are vacuous, else unknown; ``nexttime[n] p``: that of
    ``p`` at ``s + n`` (unknown before it is reached); ``always[m:n] p`` /
    ``eventually[m:n] p``: in three-valued logic, some offset ``i`` is nonvacuous while
    no earlier offset of the window has decided the property (all earlier offsets
    ``TRUE`` for ``always``, all ``FALSE`` for ``eventually``); offsets beyond the end of
    the trace stay unknown (so with ``$`` the nonvacuity is never known to be false);
    ``p until q`` (Boolean operands, which are sequences): nonvacuous from its start.

    Disable: as in the module docstring; the attempt is then ``DISABLED`` and ``decided``
    is the first such sample.

    Raises:
        EvaluationError: Unbounded range, ``|=>`` with an antecedent admitting an empty
            match, division by zero.
        TraceError: A needed value is missing.
    """
    engine = _AttemptEngine(trace, prop)
    last = len(trace) - 1
    disabled_at = (
        tuple(sample for sample in range(len(trace)) if holds(disable, trace, sample))
        if disable is not None
        else ()
    )
    results: list[AttemptResult] = []
    for start in range(len(trace)):
        decided: int | None = None
        outcome = Outcome.PENDING
        for known in range(start, last + 1):
            truth = engine.truth(prop, start, known)
            if truth is not Outcome.PENDING:
                decided, outcome = known, truth
                break
        nonvacuity_known = next(
            (
                known
                for known in range(start, last + 1)
                if engine.nonvacuity(prop, start, known) is not None
            ),
            None,
        )
        nonvacuous = engine.nonvacuity(prop, start, last) if start <= last else None
        disabled = _first_disable(disabled_at, start, last if decided is None else decided)
        if disabled is not None:
            outcome, decided = Outcome.DISABLED, disabled
        results.append(AttemptResult(start, outcome, decided, nonvacuous, nonvacuity_known))
    return tuple(results)


def evaluate_attempts(
    prop: Property, trace: Trace, disable: Expr | None = None
) -> AttemptEvaluation:
    """:func:`attempt_results` and the failures and successes they imply (see
    :class:`AttemptEvaluation`)."""
    attempts = attempt_results(prop, trace, disable)
    failures = sorted(
        {a.decided for a in attempts if a.outcome is Outcome.FALSE and a.decided is not None}
    )
    successes = sorted(
        {
            a.decided
            for a in attempts
            if a.outcome is Outcome.TRUE
            and a.decided is not None
            and a.nonvacuous is True
            and a.nonvacuity_known is not None
            and a.nonvacuity_known <= a.decided
        }
    )
    return AttemptEvaluation(attempts, tuple(failures), tuple(successes))


def finalize_attempts(
    prop: Property, trace: Trace, disable: Expr | None, cover: bool
) -> tuple[Outcome, ...]:
    """The outcome of every attempt when the simulation ends at the last sample.

    Decided and disabled attempts keep their outcome. A pending attempt is finalized
    bottom-up: a pending sequence property is ``FALSE`` if strong and ``TRUE`` if weak
    (explicit strength, else the directive default: weak for ``assert``/``assume``, strong
    for ``cover``); ``not`` swaps the finalized operand (so it reverses the strength);
    ``and`` / ``or`` / ``if`` combine the finalized operands; an implication has only the
    attempts of the antecedent matches that end inside the trace (matches ending after it
    do not exist), finalized and combined as ``and``, ``TRUE`` if there are none.
    ``nexttime[n] p`` whose event ``s + n`` is after the last sample is ``FALSE`` if
    strong, ``TRUE`` if weak (else the finalized ``p`` there); ``always[m:n] p`` is ``and``
    and ``eventually[m:n] p`` is ``or`` of the finalized ``p`` at the started offsets and,
    if ``s + n`` is after the last sample, of one more part for the missing offsets
    (``FALSE`` if the operator is strong, ``TRUE`` if weak); with an unbounded window those
    missing offsets always exist. ``p until q`` still pending is ``FALSE`` if strong, ``TRUE``
    if weak. The operator's strength is its keyword; a plain sequence operand keeps the
    directive default.
    """
    engine = _AttemptEngine(trace, prop)
    return tuple(
        engine.finalized(prop, result.start, cover)
        if result.outcome is Outcome.PENDING
        else result.outcome
        for result in attempt_results(prop, trace, disable)
    )


def _started_offsets(start: int, known: int, low: int, high: int | None) -> range:
    """The offsets ``low … high`` of a window whose attempt (at ``start + offset``) has
    started by sample ``known``; ``high`` ``None`` is unbounded."""
    last = known - start
    return range(low, (last if high is None else min(high, last)) + 1)


def _swap(outcome: Outcome) -> Outcome:
    if outcome is Outcome.TRUE:
        return Outcome.FALSE
    if outcome is Outcome.FALSE:
        return Outcome.TRUE
    return outcome


class _AttemptEngine:
    """Truth and nonvacuity of attempts given the samples ``0 … known``; memoised."""

    def __init__(self, trace: Trace, prop: Property) -> None:
        self._trace = trace
        self._slack = _propertysequence_states(prop)
        self._matchers: dict[int, _Matcher] = {}
        self._truth: dict[tuple[int, int, int], Outcome] = {}
        self._nonvacuity: dict[tuple[int, int, int], bool | None] = {}
        # normalised antecedents, kept alive so that their ids stay unique
        self._antecedents: dict[int, tuple[Implication, Sequence]] = {}

    def _matcher(self, known: int) -> _Matcher:
        if known not in self._matchers:
            self._matchers[known] = _Matcher(self._trace, known, self._slack)
        return self._matchers[known]

    def _antecedent(self, implication: Implication) -> Sequence:
        key = id(implication)
        if key not in self._antecedents:
            antecedent = implication.antecedent
            if not implication.overlapping:
                if _nullable(antecedent):
                    raise EvaluationError("|=> with an antecedent admitting an empty match")
                antecedent = SeqConcat(antecedent, Delay(1, 1), TRUE)
            self._antecedents[key] = (implication, antecedent)
        return self._antecedents[key][1]

    def _triggers(self, implication: Implication, start: int, known: int) -> tuple[list[int], bool]:
        """The samples at which non-empty antecedent matches end, up to ``known``, and
        whether further matches are still possible."""
        ends = self._matcher(known).ends(self._antecedent(implication), start)
        samples = sorted(end - 1 for end in ends if end > start)
        return [s for s in samples if s <= known], any(s > known for s in samples)

    def _branch(self, prop: PropIf, start: int) -> Property | None:
        if holds(prop.condition, self._trace, start):
            return prop.then
        return prop.otherwise

    def truth(self, prop: Property, start: int, known: int) -> Outcome:
        if start > known:
            return Outcome.PENDING
        key = (id(prop), start, known)
        if key not in self._truth:
            self._truth[key] = self._compute_truth(prop, start, known)
        return self._truth[key]

    def _compute_truth(self, prop: Property, start: int, known: int) -> Outcome:
        match prop:
            case SequenceProperty(sequence=sequence):
                ends = {e for e in self._matcher(known).ends(sequence, start) if e > start}
                if any(e <= known + 1 for e in ends):
                    return Outcome.TRUE
                return Outcome.PENDING if ends else Outcome.FALSE
            case Not(operand=operand):
                return _swap(self.truth(operand, start, known))
            case PropAnd(operands=operands):
                parts = [self.truth(o, start, known) for o in operands]
                if Outcome.FALSE in parts:
                    return Outcome.FALSE
                return Outcome.TRUE if all(p is Outcome.TRUE for p in parts) else Outcome.PENDING
            case PropOr(operands=operands):
                parts = [self.truth(o, start, known) for o in operands]
                if Outcome.TRUE in parts:
                    return Outcome.TRUE
                return Outcome.FALSE if all(p is Outcome.FALSE for p in parts) else Outcome.PENDING
            case PropIf():
                branch = self._branch(prop, start)
                return Outcome.TRUE if branch is None else self.truth(branch, start, known)
            case Implication(consequent=consequent):
                triggers, more = self._triggers(prop, start, known)
                parts = [self.truth(consequent, s, known) for s in triggers]
                if Outcome.FALSE in parts:
                    return Outcome.FALSE
                done = not more and all(p is Outcome.TRUE for p in parts)
                return Outcome.TRUE if done else Outcome.PENDING
            case Nexttime(count=count, operand=operand):
                if start + count > known:
                    return Outcome.PENDING
                return self.truth(operand, start + count, known)
            case Always(low=low, high=high, operand=operand):
                parts = [
                    self.truth(operand, start + i, known)
                    for i in _started_offsets(start, known, low, high)
                ]
                if Outcome.FALSE in parts:
                    return Outcome.FALSE
                complete = (
                    high is not None
                    and start + high <= known
                    and all(p is Outcome.TRUE for p in parts)
                )
                return Outcome.TRUE if complete else Outcome.PENDING
            case Eventually(low=low, high=high, operand=operand):
                parts = [
                    self.truth(operand, start + i, known)
                    for i in _started_offsets(start, known, low, high)
                ]
                if Outcome.TRUE in parts:
                    return Outcome.TRUE
                complete = (
                    high is not None
                    and start + high <= known
                    and all(p is Outcome.FALSE for p in parts)
                )
                return Outcome.FALSE if complete else Outcome.PENDING
            case Until(left=left, right=right, inclusive=inclusive):
                for sample in range(start, known + 1):
                    if holds(right, self._trace, sample) and (
                        not inclusive or holds(left, self._trace, sample)
                    ):
                        return Outcome.TRUE
                    if not holds(left, self._trace, sample):
                        return Outcome.FALSE
                return Outcome.PENDING

    def _window_nonvacuity(
        self,
        operand: Property,
        start: int,
        known: int,
        low: int,
        high: int | None,
        holding: Outcome,
    ) -> bool | None:
        """Nonvacuity of ``always`` / ``eventually`` over the offsets ``low … high``: some
        offset ``i`` is nonvacuous while no earlier offset of the window ``decided`` the
        property (``holding`` is the truth of an earlier offset that does so: ``FALSE`` for
        ``always``, ``TRUE`` for ``eventually``), in three-valued logic."""
        terms: list[bool | None] = []
        # an unbounded window has offsets after every known sample: nonvacuity unknown there
        offsets = range(low, known - start + 1) if high is None else range(low, high + 1)
        if high is None:
            # the offsets after the known samples: unknown, unless an earlier offset has
            # decided the property (the same gating as for a bounded window)
            tail: bool | None = None
            for j in offsets:
                if self.truth(operand, start + j, known) is holding:
                    tail = False
            terms.append(tail)
        for i in offsets:
            term = self.nonvacuity(operand, start + i, known)
            for j in range(low, i):
                truth = self.truth(operand, start + j, known)
                if truth is Outcome.PENDING:
                    earlier: bool | None = None
                else:
                    earlier = truth is not holding  # no earlier offset has decided
                if term is False or earlier is False:
                    term = False
                elif earlier is None:
                    term = None
            terms.append(term)
        if True in terms:
            return True
        return False if all(t is False for t in terms) else None

    def nonvacuity(self, prop: Property, start: int, known: int) -> bool | None:
        if start > known:
            return None
        key = (id(prop), start, known)
        if key not in self._nonvacuity:
            self._nonvacuity[key] = self._compute_nonvacuity(prop, start, known)
        return self._nonvacuity[key]

    def _compute_nonvacuity(self, prop: Property, start: int, known: int) -> bool | None:
        match prop:
            case SequenceProperty() | Until():
                return True
            case Not(operand=operand):
                return self.nonvacuity(operand, start, known)
            case PropAnd(operands=operands) | PropOr(operands=operands):
                parts = [self.nonvacuity(o, start, known) for o in operands]
                if True in parts:
                    return True
                return False if all(p is False for p in parts) else None
            case PropIf():
                branch = self._branch(prop, start)
                return False if branch is None else self.nonvacuity(branch, start, known)
            case Implication(consequent=consequent):
                triggers, more = self._triggers(prop, start, known)
                parts = [self.nonvacuity(consequent, s, known) for s in triggers]
                if True in parts:
                    return True
                return False if not more and all(p is False for p in parts) else None
            case Nexttime(count=count, operand=operand):
                return self.nonvacuity(operand, start + count, known)
            case Always(low=low, high=high, operand=operand):
                return self._window_nonvacuity(operand, start, known, low, high, Outcome.FALSE)
            case Eventually(low=low, high=high, operand=operand):
                return self._window_nonvacuity(operand, start, known, low, high, Outcome.TRUE)

    def _finalized_window(
        self, operand: Property, start: int, low: int, high: int | None, strong: bool, cover: bool
    ) -> list[Outcome]:
        """The finalized operand attempts of the started offsets, plus one part for the
        offsets after the end of the trace (``FALSE`` if ``strong``, else ``TRUE``)."""
        last = len(self._trace) - 1
        parts = [
            self.finalized(operand, start + i, cover)
            for i in range(low, (last - start if high is None else high) + 1)
            if start + i <= last
        ]
        if high is None or start + high > last:
            parts.append(Outcome.FALSE if strong else Outcome.TRUE)
        return parts

    def finalized(self, prop: Property, start: int, cover: bool) -> Outcome:
        """The outcome of the attempt when the simulation ends at the last sample."""
        known = len(self._trace) - 1
        settled = self.truth(prop, start, known)
        if settled is not Outcome.PENDING:
            return settled
        match prop:
            case SequenceProperty(strength=strength):
                strong = effective_strength(strength, cover) is Strength.STRONG
                return Outcome.FALSE if strong else Outcome.TRUE
            case Not(operand=operand):
                return _swap(self.finalized(operand, start, cover))
            case PropAnd(operands=operands):
                parts = [self.finalized(o, start, cover) for o in operands]
                return Outcome.FALSE if Outcome.FALSE in parts else Outcome.TRUE
            case PropOr(operands=operands):
                parts = [self.finalized(o, start, cover) for o in operands]
                return Outcome.TRUE if Outcome.TRUE in parts else Outcome.FALSE
            case PropIf():
                branch = self._branch(prop, start)
                return Outcome.TRUE if branch is None else self.finalized(branch, start, cover)
            case Implication(consequent=consequent):
                triggers, _ = self._triggers(prop, start, known)
                parts = [self.finalized(consequent, s, cover) for s in triggers]
                return Outcome.FALSE if Outcome.FALSE in parts else Outcome.TRUE
            case Nexttime(count=count, strong=strong, operand=operand):
                if start + count > known:
                    return Outcome.FALSE if strong else Outcome.TRUE
                return self.finalized(operand, start + count, cover)
            case Always(low=low, high=high, strong=strong, operand=operand):
                parts = self._finalized_window(operand, start, low, high, strong, cover)
                return Outcome.FALSE if Outcome.FALSE in parts else Outcome.TRUE
            case Eventually(low=low, high=high, strong=strong, operand=operand):
                parts = self._finalized_window(operand, start, low, high, strong, cover)
                return Outcome.TRUE if Outcome.TRUE in parts else Outcome.FALSE
            case Until(strong=strong):
                return Outcome.FALSE if strong else Outcome.TRUE
