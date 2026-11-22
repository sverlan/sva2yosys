"""Exact results of property attempts on infinite, ultimately periodic traces ("lassos").

A lasso is a finite list of written samples and a loop start ``k``: the infinite trace is
the samples ``0 … k-1`` followed by the samples ``k … n-1`` repeated forever. It is the form
of a counterexample to a liveness property, so this module is the exact oracle for strong
(liveness) properties: on an infinite trace every attempt is TRUE, FALSE or disabled; a
weak attempt that never gets decided online is TRUE and a strong one FALSE
(``docs/semantics.md`` §9.2, "Semantic frame").

Method and soundness argument (details at :func:`lasso_attempt_results`):

- The sampled trace is periodic from the position ``W`` (``k`` plus the largest sampled-value
  depth ``d`` of the property, rounded up to a loop boundary). Any attempt started at
  ``p >= W`` behaves as the one started at ``W + (p - W) mod L`` (``L = n - k``), shifted.
- Online decisions are computed on an unrolled copy of the trace, exactly, using the
  finite-trace sequence matcher. "Never decided" is concluded only through a structural
  pumping argument (a repeated configuration of the sequence automaton and the loop
  position), never from a guessed number of unrollings.
- Results of compound properties are computed by recursion on the property, per
  position class, from the online verdict and decision sample of the operands and their
  limit outcome (what the operand is on the infinite trace).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from support.reference.evaluate import (
    EvaluationError,
    Outcome,
    _Matcher,
    _nullable,
    _propertysequence_states,
    holds,
)
from support.reference.trace import Trace
from sva2yosys.ir.expr import (
    Binary,
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


@dataclass(frozen=True)
class Lasso:
    """The infinite trace ``samples[0 : loop]`` followed by ``samples[loop:]`` forever.

    Attributes:
        samples: The written samples, one mapping per sample from signal path to value.
        loop: Index of the first sample of the repeated part; ``0 <= loop < len(samples)``.

    Raises:
        EvaluationError: ``loop`` is outside the samples.
    """

    samples: tuple[Mapping[str, int], ...]
    loop: int

    def __post_init__(self) -> None:
        if not 0 <= self.loop < len(self.samples):
            raise EvaluationError(f"loop {self.loop} is outside the {len(self.samples)} samples")

    @property
    def period(self) -> int:
        """The length of the repeated part."""
        return len(self.samples) - self.loop

    def index(self, position: int) -> int:
        """The written sample that the infinite trace has at ``position``."""
        if position < len(self.samples):
            return position
        return self.loop + (position - self.loop) % self.period

    def unroll(self, length: int) -> Trace:
        """The first ``length`` samples of the infinite trace."""
        return Trace(tuple(self.samples[self.index(i)] for i in range(length)))


@dataclass(frozen=True)
class LassoAttemptResult:
    """The exact result of the attempt started at written sample ``start``.

    Attributes:
        start: The attempt's first sample.
        outcome: ``TRUE`` or ``FALSE`` (never ``PENDING``: an attempt never decided online is
            decided by its strength), or ``DISABLED`` if the ``disable iff`` condition holds
            at some sample from ``start`` to the decision (to infinity when the attempt is
            never decided online).
        decided: The sample at which the attempt is decided online, the first disable sample
            for ``DISABLED``, or ``None`` when no finite prefix decides the attempt (its
            outcome holds only in the limit).
    """

    start: int
    outcome: Outcome
    decided: int | None


def lasso_attempt_results(
    prop: Property, lasso: Lasso, disable: Expr | None = None, cover: bool = False
) -> tuple[LassoAttemptResult, ...]:
    """The exact result of every attempt of ``prop`` started at a written sample, on the
    infinite trace ``lasso``; ``cover`` gives the directive's default strength (strong in a
    cover).

    Semantics (the rules of ``attempt_results`` on a trace that never ends):

    - *Online verdict*: an attempt is decided TRUE or FALSE at the first sample ``t`` such
      that the samples ``0 … t`` determine it, later samples being unknown
      (``docs/semantics.md`` §3); this is what the finite-trace evaluator reports, and a
      decision made on a finite prefix holds on every continuation, so it is the infinite
      trace's decision too.
    - *Limit outcome*: an attempt that no prefix decides is decided by the whole trace.
      A sequence that is alive forever without ever matching is TRUE if weak and FALSE if
      strong (§16.12.2); ``not`` reverses it; ``and`` / ``or`` combine the limit outcomes
      of their operands; an implication is the ``and`` of the limit outcomes of all its
      triggered consequents (TRUE if none); ``nexttime``, ``always [m:n]`` and
      ``eventually [m:n]`` combine the operand attempts at *all* their offsets, which all
      exist on an infinite trace (the strength flag of a bounded form therefore never
      matters, and the offsets "after the end" of the finite evaluator do not exist);
      ``until`` never decided is TRUE if weak, FALSE if strong.
    - ``disable iff``: as for finite traces, the attempt is disabled if the condition holds
      at some sample of ``[start, decision]``, of ``[start, ∞)`` while undecided.

    Why the result is exact (no guessed number of unrollings). Let ``d`` be the largest
    depth of any sampled-value function in the property or the disable condition (``$past(e,
    n)`` has depth ``n``; ``$rose``, ``$fell``, ``$stable`` and ``$changed`` depth 1), ``k``
    the loop start, ``L`` its length, and ``W = k + ceil(d / L) * L``. A sampled value at a
    position ``q >= W`` reads positions ``q - d … q`` that are all in the loop, so every
    value, hence every condition, at ``q >= W`` equals the one at ``q + L``; the first-sample
    rule only concerns positions below ``d <= W``. So the suffix from ``q >= W`` and the
    suffix from ``q + L`` are the same word and the attempts started there have the same
    results (decision samples shifted by ``L``): there are only ``Q = W + L`` classes of
    start positions, and everything is computed for the first representative of each.

    A *sequence* with an automaton of at most ``S`` states (:func:`sequence_states`) run on
    this word from a start ``p < Q`` has configurations (automaton state, position class), at
    most ``S * Q`` of them, where the class of a position below ``W`` is the position and
    otherwise its residue modulo ``L``. A run longer than ``S * Q`` repeats a configuration,
    and the part between two equal configurations can be cut or repeated: so (a) if a match
    exists at all, one ends within ``S * Q`` samples of ``p``, and for each position class
    the earliest end is within that distance too; (b) if some run over the prefix ``0 … t``
    is alive with ``t - p > S * Q``, it contains a repeated configuration and can be pumped
    to a run alive after any later prefix, so alive at such a ``t`` means alive forever (an
    attempt pending forever), and "can still produce a match end" is likewise permanent. The
    trace is therefore unrolled to ``M = Q + K + S + 8`` samples with ``K = (S + 1) * Q``,
    where ``S`` bounds the states of every sequence of the property: a match found within
    the unrolled trace is exact, none found there does not exist, and aliveness at ``M - 1``
    is aliveness forever. The decision samples are found by bisection on the monotone
    predicates "alive at ``t``" and "can still match after ``t``".

    Windows (``always`` / ``eventually`` with ``$``) range over offsets ``q >= p + low``;
    every position class occurs among the first ``L`` positions from ``max(p + low, W)``,
    and the earliest occurrence of a class decides first, so only those offsets are
    examined. Triggers of an implication likewise: the earliest trigger of each class is
    within ``S * Q`` of the start (a) and later ones of the class have the same consequent
    result. ``p until q`` is scanned over one loop period past ``max(p, W)``.

    Not provided: nonvacuity (the infinite-trace results here are TRUE / FALSE only).

    Raises:
        EvaluationError: Division by zero, ``|=>`` with an antecedent admitting an empty
            match, or a property shape the finite evaluator rejects.
    """
    return _LassoEngine(prop, lasso, disable, cover).results()


# --- sampled-value depth ------------------------------------------------------------------


def _depth(expr: Expr) -> int:
    """The largest number of earlier samples the value of ``expr`` reads."""
    match expr:
        case Sampled(function=function, operand=operand, depth=depth):
            own = depth if function is SampledFunction.PAST else 1
            return own + _depth(operand)
        case FirstSample():
            # 1 at sample 0 and 0 after it: like a depth-1 sampled function, the trace is
            # periodic only from sample 1 on
            return 1
        case Signal() | Const():
            return 0
        case Unary(operand=operand) | Convert(operand=operand):
            return _depth(operand)
        case Binary(left=left, right=right):
            return max(_depth(left), _depth(right))
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            return max(_depth(e) for e in (condition, then, otherwise))
        case BitSelect(value=inner) | RangeSelect(value=inner) | Replicate(value=inner):
            return _depth(inner)
        case Concat(parts=parts):
            return max((_depth(p) for p in parts), default=0)


def _sequence_depth(sequence: Sequence) -> int:
    match sequence:
        case Bool(expr=expr):
            return _depth(expr)
        case Goto(condition=condition) | NonConsecutive(condition=condition):
            return _depth(condition)
        case SeqConcat(left=left, right=right):
            return max(_sequence_depth(left), _sequence_depth(right))
        case Repeat(sequence=inner):
            return _sequence_depth(inner)
        case (
            SeqOr(left=left, right=right)
            | SeqAnd(left=left, right=right)
            | SeqIntersect(left=left, right=right)
        ):
            return max(_sequence_depth(left), _sequence_depth(right))
        case Within(inner=inner, outer=outer):
            return max(_sequence_depth(inner), _sequence_depth(outer))
        case Throughout(condition=condition, sequence=inner):
            return max(_depth(condition), _sequence_depth(inner))


def _property_depth(prop: Property) -> int:
    match prop:
        case SequenceProperty(sequence=sequence):
            return _sequence_depth(sequence)
        case Implication(antecedent=antecedent, consequent=consequent):
            return max(_sequence_depth(antecedent), _property_depth(consequent))
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _property_depth(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return max((_property_depth(o) for o in operands), default=0)
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            return max(
                _depth(condition),
                _property_depth(then),
                _property_depth(otherwise) if otherwise is not None else 0,
            )
        case Until(left=left, right=right):
            return max(_depth(left), _depth(right))


# --- the engine ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Res:
    """An attempt on the infinite trace: its online verdict (``TRUE`` / ``FALSE`` at
    ``decided``, or ``PENDING`` forever) and its limit outcome (``limit``: TRUE or FALSE
    on the whole infinite trace; equal to the verdict when decided)."""

    online: Outcome
    decided: int | None
    limit: bool

    def shifted(self, by: int) -> _Res:
        return self if self.decided is None else replace(self, decided=self.decided + by)


def _lowest(parts: list[_Res], online: Outcome) -> int:
    return min(p.decided for p in parts if p.online is online and p.decided is not None)


def _highest(parts: list[_Res]) -> int:
    return max(p.decided for p in parts if p.decided is not None)


class _LassoEngine:
    def __init__(self, prop: Property, lasso: Lasso, disable: Expr | None, cover: bool) -> None:
        self._prop = prop
        self._lasso = lasso
        self._disable = disable
        self._cover = cover
        depth = _property_depth(prop)
        if disable is not None:
            depth = max(depth, _depth(disable))
        period = lasso.period
        # first position from which the sampled trace is periodic and loop-aligned
        self._warm = lasso.loop + -(-depth // period) * period
        self._classes = self._warm + period  # Q
        # S: a bound of the states of every sequence (``|=>`` adds ``##1 1`` to an antecedent)
        self._states = max(_propertysequence_states(prop), 2) + 4
        reach = (self._states + 1) * self._classes  # K
        self._length = self._classes + reach + self._states + 8  # M
        self._trace = lasso.unroll(self._length)
        self._matchers: dict[int, _Matcher] = {}
        self._memo: dict[tuple[int, int], _Res] = {}
        self._antecedents: dict[int, tuple[Implication, Sequence]] = {}

    # -- positions

    def _canon(self, position: int) -> int:
        if position < self._classes:
            return position
        return self._warm + (position - self._warm) % self._lasso.period

    def _holds(self, expr: Expr, position: int) -> bool:
        """``expr`` as a condition at any position of the infinite trace."""
        return holds(expr, self._trace, self._canon(position))

    def _matcher(self, known: int) -> _Matcher:
        if known not in self._matchers:
            self._matchers[known] = _Matcher(self._trace, known, self._states)
        return self._matchers[known]

    # -- top level

    def results(self) -> tuple[LassoAttemptResult, ...]:
        out: list[LassoAttemptResult] = []
        for start in range(len(self._lasso.samples)):
            res = self.res(self._prop, start)
            decided = res.decided if res.online is not Outcome.PENDING else None
            disabled = self._first_disable(start, decided)
            if disabled is not None:
                out.append(LassoAttemptResult(start, Outcome.DISABLED, disabled))
            else:
                outcome = Outcome.TRUE if res.limit else Outcome.FALSE
                out.append(LassoAttemptResult(start, outcome, decided))
        return tuple(out)

    def _first_disable(self, start: int, decided: int | None) -> int | None:
        if self._disable is None:
            return None
        end = (
            max(start, self._warm) + self._lasso.period - 1  # one period of every class
            if decided is None
            else decided
        )
        for sample in range(start, end + 1):
            if self._holds(self._disable, sample):
                return sample
        return None

    # -- recursion

    def res(self, prop: Property, position: int) -> _Res:
        """The attempt of ``prop`` started at ``position`` (any position of the trace)."""
        canonical = self._canon(position)
        key = (id(prop), canonical)
        if key not in self._memo:
            self._memo[key] = self._compute(prop, canonical)
        return self._memo[key].shifted(position - canonical)

    def _compute(self, prop: Property, p: int) -> _Res:
        match prop:
            case SequenceProperty(sequence=sequence, strength=strength):
                strong = effective_strength(strength, self._cover) is Strength.STRONG
                return self._sequence(sequence, strong, p)
            case Not(operand=operand):
                inner = self.res(operand, p)
                swapped = {Outcome.TRUE: Outcome.FALSE, Outcome.FALSE: Outcome.TRUE}
                return _Res(swapped.get(inner.online, inner.online), inner.decided, not inner.limit)
            case PropAnd(operands=operands):
                parts = [self.res(o, p) for o in operands]
                limit = all(part.limit for part in parts)
                if any(part.online is Outcome.FALSE for part in parts):
                    return _Res(Outcome.FALSE, _lowest(parts, Outcome.FALSE), False)
                if all(part.online is Outcome.TRUE for part in parts):
                    return _Res(Outcome.TRUE, _highest(parts), True)
                return _Res(Outcome.PENDING, None, limit)
            case PropOr(operands=operands):
                parts = [self.res(o, p) for o in operands]
                limit = any(part.limit for part in parts)
                if any(part.online is Outcome.TRUE for part in parts):
                    return _Res(Outcome.TRUE, _lowest(parts, Outcome.TRUE), True)
                if all(part.online is Outcome.FALSE for part in parts):
                    return _Res(Outcome.FALSE, _highest(parts), False)
                return _Res(Outcome.PENDING, None, limit)
            case PropIf(condition=condition, then=then, otherwise=otherwise):
                branch = then if self._holds(condition, p) else otherwise
                return _Res(Outcome.TRUE, p, True) if branch is None else self.res(branch, p)
            case Implication():
                return self._implication(prop, p)
            case Nexttime(count=count, operand=operand):
                return self.res(operand, p + count)
            case Always(low=low, high=high, operand=operand):
                parts = [self.res(operand, p + i) for i in self._offsets(p, low, high)]
                limit = all(part.limit for part in parts)
                if any(part.online is Outcome.FALSE for part in parts):
                    return _Res(Outcome.FALSE, _lowest(parts, Outcome.FALSE), False)
                if high is not None and all(part.online is Outcome.TRUE for part in parts):
                    return _Res(Outcome.TRUE, max(p + high, _highest(parts)), True)
                return _Res(Outcome.PENDING, None, limit)
            case Eventually(low=low, high=high, operand=operand):
                parts = [self.res(operand, p + i) for i in self._offsets(p, low, high)]
                limit = any(part.limit for part in parts)
                if any(part.online is Outcome.TRUE for part in parts):
                    return _Res(Outcome.TRUE, _lowest(parts, Outcome.TRUE), True)
                if high is not None and all(part.online is Outcome.FALSE for part in parts):
                    return _Res(Outcome.FALSE, max(p + high, _highest(parts)), False)
                return _Res(Outcome.PENDING, None, limit)
            case Until(left=left, right=right, inclusive=inclusive, strong=strong):
                # beyond one period from max(p, W) every sample repeats an earlier one
                for sample in range(p, max(p, self._warm) + self._lasso.period):
                    if self._holds(right, sample) and (not inclusive or self._holds(left, sample)):
                        return _Res(Outcome.TRUE, sample, True)
                    if not self._holds(left, sample):
                        return _Res(Outcome.FALSE, sample, False)
                return _Res(Outcome.PENDING, None, not strong)

    def _offsets(self, p: int, low: int, high: int | None) -> range:
        """The window offsets to examine: all of them if bounded, else the offsets of the
        positions from ``p + low`` through one period past ``max(p + low, W)``."""
        if high is not None:
            return range(low, high + 1)
        return range(low, max(p + low, self._warm) + self._lasso.period - p)

    # -- sequences

    def _sequence(self, sequence: Sequence, strong: bool, p: int) -> _Res:
        last = self._length - 1
        ends = [e for e in self._matcher(last).ends(sequence, p) if p < e <= self._length]
        if ends:
            return _Res(Outcome.TRUE, min(ends) - 1, True)
        if self._alive(sequence, p, last):
            return _Res(Outcome.PENDING, None, not strong)  # alive forever (pumping)
        low, high = p, last  # dead at high; find the first dead sample
        while low < high:
            middle = (low + high) // 2
            if self._alive(sequence, p, middle):
                low = middle + 1
            else:
                high = middle
        return _Res(Outcome.FALSE, low, False)

    def _alive(self, sequence: Sequence, p: int, known: int) -> bool:
        """Whether some non-empty match from ``p`` is still possible after ``0 … known``."""
        return any(e > p for e in self._matcher(known).ends(sequence, p))

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

    def _implication(self, prop: Implication, p: int) -> _Res:
        antecedent = self._antecedent(prop)
        last = self._length - 1
        triggers = sorted(
            e - 1 for e in self._matcher(last).ends(antecedent, p) if p < e <= self._length
        )
        parts = [self.res(prop.consequent, s) for s in triggers]
        limit = all(part.limit for part in parts)
        if any(part.online is Outcome.FALSE for part in parts):
            return _Res(Outcome.FALSE, _lowest(parts, Outcome.FALSE), False)
        done = self._done(antecedent, p)
        if done is not None and all(part.online is Outcome.TRUE for part in parts):
            return _Res(Outcome.TRUE, max([done, *(part.decided or 0 for part in parts)]), True)
        return _Res(Outcome.PENDING, None, limit)

    def _done(self, antecedent: Sequence, p: int) -> int | None:
        """The first sample after which no further non-empty match of ``antecedent`` can end,
        or ``None`` if matches can end arbitrarily late (permanent by pumping)."""

        def finished(known: int) -> bool:
            return all(e <= known + 1 for e in self._matcher(known).ends(antecedent, p) if e > p)

        last = self._length - 1
        if not finished(last):
            return None
        low, high = p, last
        while low < high:
            middle = (low + high) // 2
            if finished(middle):
                high = middle
            else:
                low = middle + 1
        return low
