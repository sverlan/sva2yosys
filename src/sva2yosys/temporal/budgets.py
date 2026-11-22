"""Resource budgets of the temporal compilers.

The budget *definitions* (:class:`Budgets`, :class:`BudgetError`, :data:`DEFAULT_BUDGETS`)
and the up-front letter budget on the property as written (:func:`check_letter_budget`).
The budgets are *charged* where the resources are created: the letters, instances,
registers and gates of the per-attempt machine in :mod:`sva2yosys.temporal.attempts`, the
configurations of the configuration-set machine in :mod:`sva2yosys.temporal.configurations`.
Exceeding a budget raises :class:`BudgetError`; nothing is ever approximated
(``docs/semantics.md`` §9.4).
"""

from __future__ import annotations

from dataclasses import dataclass

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
    Until,
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
from sva2yosys.temporal.lower import window


class BudgetError(ValueError):
    """The property needs more of some resource than the :class:`Budgets` allow; the
    message names the budget and its limit."""


@dataclass(frozen=True)
class Budgets:
    """Resource limits of :func:`~sva2yosys.temporal.attempts.build_attempt_machine`,
    charged incrementally while building. Exceeding one raises :class:`BudgetError`;
    nothing is ever approximated.

    Attributes:
        letters: Letters of the sequence automata (also pre-checked by
            :func:`letter_estimate`).
        instances: Property-tree instances built.
        registers: Netlist registers created.
        gates: Netlist gates created.
        configurations: Reachable configurations of the configuration-set machine
            (``temporal.configurations``).
    """

    letters: int = 4096
    instances: int = 20000
    registers: int = 8192
    gates: int = 200000
    configurations: int = 256


DEFAULT_BUDGETS = Budgets()
"""The default limits (shared singleton, since :class:`Budgets` is immutable)."""


@dataclass(frozen=True)
class ResourceLimits:
    """Compiler resource policy, independent of verification-engine capacity.

    ``expansion`` bounds the saturating written-property letter estimate and letters
    created, including discarded intermediate letters. ``work`` bounds charged
    construction and emission-planning visits (including discarded candidates and
    folded operands). ``state_bits`` bounds only bounded slot-machine (``Machine``)
    temporal registers; attempt/configuration limits remain in Budgets. ``emission``
    bounds a conservative selected-layout reference allowance, rather than RTL tokens
    or Yosys gates. These are per-monitor, per-phase limits, not a
    whole-run RAM allowance or a promised solver runtime. Existing Budgets still apply.
    """

    expansion: int
    work: int
    state_bits: int
    emission: int

    def __post_init__(self) -> None:
        if min(self.expansion, self.work, self.state_bits, self.emission) < 0:
            raise ValueError("resource limits must be nonnegative")


DEFAULT_LIMITS = ResourceLimits(16384, 100000000, 8192, 200000)
"""Measured policy, with rationale and capacity controls in docs/usage.md.

These bound compiler construction, not Yosys capacity. Callers may supply a different
immutable policy. The existing per-attempt/configuration Budgets also remain enforced.
"""


@dataclass
class Work:
    """Incremental construction accounting, before allocating the charged resource.

    Counts include discarded intermediates. Charges saturate at limit + 1 and raise
    BudgetError naming the exhausted resource. No resource is approximated on exhaustion.
    """

    limits: ResourceLimits
    used: int = 0
    letters: int = 0
    emitted: int = 0

    def emit(self, amount: int) -> None:
        """Charge selected-layout expression references before constructing RTL."""
        if amount < 0:
            raise ValueError("an emission charge cannot be negative")
        self.emitted = min(self.limits.emission + 1, self.emitted + amount)
        if self.emitted > self.limits.emission:
            raise BudgetError(
                f"the monitor needs more than {self.limits.emission} emission references"
            )

    def charge(self, amount: int = 1) -> None:
        """Charge construction visits; negative charges are invalid."""
        if amount < 0:
            raise ValueError("a construction charge cannot be negative")
        self.used = min(self.limits.work + 1, self.used + amount)
        if self.used > self.limits.work:
            raise BudgetError(
                f"the property needs more than {self.limits.work} construction visits"
            )

    def letter(self, guards: int) -> None:
        """Charge one intermediate letter and its guard references before creation."""
        self.letters = min(self.limits.expansion + 1, self.letters + 1)
        if self.letters > self.limits.expansion:
            raise BudgetError(
                f"the property needs more than {self.limits.expansion} expanded letters"
            )
        self.charge(1 + guards)


def preflight(prop: Property, limits: ResourceLimits) -> Work:
    """Check written ranges with saturating arithmetic before lowering or dispatch.

    No range is expanded or enumerated. Unbounded ranges remain unbounded: only their
    mandatory prefix is counted. The estimate is a separate conservative admission
    policy; it is not added to the actual created-letter counter. Return the work
    counter for subsequent construction. Raise BudgetError on exhaustion.
    """
    work = Work(limits)
    pending: list[Property | Sequence] = [prop]
    while pending:
        work.charge()
        node = pending.pop()
        children: tuple[Property | Sequence, ...]
        expressions: tuple[Expr, ...] = ()
        match node:
            case SequenceProperty(sequence=sequence):
                children = (sequence,)
            case Implication(antecedent=antecedent, consequent=consequent):
                children = (antecedent, consequent)
            case (
                Not(operand=operand)
                | Nexttime(operand=operand)
                | Always(operand=operand)
                | Eventually(operand=operand)
            ):
                children = (operand,)
            case PropAnd(operands=operands) | PropOr(operands=operands):
                children = operands
            case PropIf(condition=condition, then=then, otherwise=otherwise):
                children = (then,) if otherwise is None else (then, otherwise)
                expressions = (condition,)
            case Until(left=left, right=right):
                children, expressions = (), (left, right)
            case Bool(expr=expr):
                children, expressions = (), (expr,)
            case (
                SeqConcat(left=left, right=right)
                | SeqAnd(left=left, right=right)
                | SeqOr(left=left, right=right)
                | SeqIntersect(left=left, right=right)
                | Within(inner=left, outer=right)
            ):
                children = (left, right)
            case Repeat(sequence=inner):
                children = (inner,)
            case Throughout(condition=condition, sequence=inner):
                children, expressions = (inner,), (condition,)
            case Goto(condition=condition) | NonConsecutive(condition=condition):
                children, expressions = (), (condition,)
        work.charge(len(children))
        pending.extend(children)
        check_expressions(expressions, limits, work=work)
    total = 0
    try:
        # Downstream typed-IR caches also require structural hashing. Check it here
        # after charging traversal, so deeply nested input has a resource diagnostic.
        hash(prop)
        for sequence in property_sequences(prop):
            total = min(limits.expansion + 1, total + letter_estimate(sequence, limits.expansion))
            if total > limits.expansion:
                raise BudgetError(
                    f"the property exceeds the {limits.expansion} expansion budget "
                    "(written letters)"
                )
    except RecursionError as error:
        raise BudgetError("written property nesting exceeds compiler capacity") from error
    return work


def check_expressions(
    expressions: tuple[Expr, ...], limits: ResourceLimits, *, work: Work | None = None
) -> None:
    """Bound expression traversal and sampled-history expansion before validation/emission.

    Widths and total distinct sampled depth-times-width must fit expansion.
    Node visits are charged against work without expanding any history or replication.
    This also checks conditions of manually supplied machines, independently of their
    source directive. The policy begins at typed IR, after Slang's elaboration.
    """
    account = Work(limits) if work is None else work
    sampled: list[Sampled] = []
    account.charge(len(expressions))
    pending = list(expressions)
    while pending:
        account.charge()
        expr = pending.pop()
        if expr.type.width > limits.expansion:
            raise BudgetError(f"an expression exceeds the {limits.expansion} expansion budget")
        children: tuple[Expr, ...]
        match expr:
            case Sampled(operand=operand, depth=depth):
                if depth > limits.expansion // max(1, operand.type.width):
                    raise BudgetError(
                        f"sampled history exceeds the {limits.expansion} expansion budget"
                    )
                sampled.append(expr)
                children = (operand,)
            case Unary(operand=operand) | Convert(operand=operand):
                children = (operand,)
            case Binary(left=left, right=right):
                children = (left, right)
            case Conditional(condition=condition, then=then, otherwise=otherwise):
                children = (condition, then, otherwise)
            case BitSelect(value=value) | RangeSelect(value=value) | Replicate(value=value):
                children = (value,)
            case Concat(parts=parts):
                children = parts
            case Const() | Signal() | FirstSample():
                # no operand; it shares the property's init bit (one bit, like the first
                # stage of a depth-1 history), so it adds no history to charge
                children = ()
        account.charge(len(children))
        pending.extend(children)
    # Hash only after every operand visit has been charged. Python's structural
    # dataclass hash can itself exceed recursion capacity on deeply nested typed IR.
    histories: set[Sampled] = set()
    history_bits = 0
    try:
        for root in expressions:
            hash(root)
        for expr in sampled:
            if expr not in histories:
                history_bits += expr.depth * expr.operand.type.width
                if history_bits > limits.expansion:
                    raise BudgetError(
                        f"sampled history exceeds the {limits.expansion} expansion budget"
                    )
                histories.add(expr)
                account.charge(expr.depth)
    except RecursionError as error:
        raise BudgetError("expression nesting exceeds compiler capacity") from error


def over(limit: int, name: str) -> BudgetError:
    return BudgetError(f"the property needs more than {limit} {name} for the per-attempt machine")


def letter_estimate(sequence: Sequence, limit: int) -> int:
    """A saturating structural upper bound of the letters of ``sequence``'s automaton,
    computed without iterating over any range: ``Bool`` 1; ``l ##[m:n] r``:
    ``e(l) + e(r) + n + e(l) * e(r)``; ``s[*m:n]``: ``1 + max(n, 1) * (e(s) + 1)`` (the
    automaton holds one copy of ``s`` per repetition, so ``[*0]`` still counts ``e(s)``);
    ``or``: sum; ``and`` / ``intersect`` / ``within``: ``(e(l) + 2) * (e(r) + 2)``;
    ``throughout``: ``1 + e(s)``. All arithmetic saturates at ``limit + 1``."""
    cap = limit + 1

    def estimate(node: Sequence) -> int:
        match node:
            case Bool():
                return 1
            case SeqConcat(left=left, delay=delay, right=right):
                first, second = estimate(left), estimate(right)
                high = min(cap, delay.minimum if delay.maximum is None else delay.maximum)
                return min(cap, first + second + high + first * second)
            case Repeat(sequence=inner, maximum=maximum, minimum=minimum):
                size = estimate(inner)
                high = min(cap, minimum if maximum is None else maximum)
                return min(cap, 1 + max(high, 1) * (size + 1))
            case SeqOr(left=left, right=right):
                return min(cap, estimate(left) + estimate(right))
            case (
                SeqAnd(left=left, right=right)
                | SeqIntersect(left=left, right=right)
                | Within(inner=left, outer=right)
            ):
                return min(cap, (estimate(left) + 2) * (estimate(right) + 2))
            case Throughout(sequence=inner):
                return min(cap, 1 + estimate(inner))
            case (
                Goto(minimum=minimum, maximum=maximum)
                | NonConsecutive(minimum=minimum, maximum=maximum)
            ):
                high = min(cap, minimum if maximum is None else maximum)
                return min(cap, 1 + max(high, 1) * 3)

    return estimate(sequence)


def property_sequences(prop: Property) -> list[Sequence]:
    """Every sequence of ``prop`` (property sequences and antecedents), plus the implicit
    window sequence of each temporal node: ``1 ##n 1`` for ``nexttime [n]`` (``n >= 1``),
    ``1 ##[m:n] 1`` for ``always`` / ``eventually [m:n]`` (not ``[0:0]``), so the letter
    budget sees a huge window before anything iterates over it."""
    match prop:
        case Nexttime(count=count, operand=operand):
            found = property_sequences(operand)
            return [window(count, count), *found] if count >= 1 else found
        case (
            Always(low=low, high=high, operand=operand)
            | Eventually(low=low, high=high, operand=operand)
        ):
            found = property_sequences(operand)
            if high is None:
                return [window(low, None), *found] if low >= 1 else found
            return [window(low, high), *found] if high >= 1 and 0 <= low <= high else found
        case SequenceProperty(sequence=sequence):
            return [sequence]
        case Until():
            return []
        case Implication(antecedent=antecedent, consequent=consequent):
            return [antecedent, *property_sequences(consequent)]
        case Not(operand=operand):
            return property_sequences(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return [s for operand in operands for s in property_sequences(operand)]
        case PropIf(then=then, otherwise=otherwise):
            found = property_sequences(then)
            if otherwise is not None:
                found += property_sequences(otherwise)
            return found


def check_letter_budget(prop: Property, budgets: Budgets = DEFAULT_BUDGETS) -> None:
    """Reject ``prop`` (as written) when the sum of :func:`letter_estimate` over its
    sequences exceeds ``budgets.letters``. This runs before anything that iterates over
    delay or repetition ranges (``sequence_bounds``, ``build_automaton``).

    Raises:
        BudgetError: The estimate exceeds the letters budget.
    """
    total = 0
    for sequence in property_sequences(prop):
        total += letter_estimate(sequence, budgets.letters)
        if total > budgets.letters:
            raise over(budgets.letters, "letters")
