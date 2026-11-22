"""The canonical monitor machine and its normative per-sample step.

A :class:`Machine` is plain data. Its state (:class:`MachineState`) is the **temporal
state** only: the merged antecedent frontier and the per-age obligation slots. The
sampled-value history (``$past`` and edge-function registers, and the count of samples
seen) is not part of it: it is sampled continuously on the property's clock and is
never cleared by ``disable iff`` (``docs/semantics.md`` §7, §8). The step receives the
history's effect through the condition values and ``elapsed``.

Step at sample ``t`` (state before ``t`` -> events of ``t``, state before ``t + 1``):

1. **Conditions.** A condition with ``past_depth[i] = d > 0`` does not hold at the
   property clock's first ``d`` samples (``elapsed < d``); its raw value is then not
   consulted. Otherwise a condition holds when its raw value is non-zero.
2. **Disable first.** If the disable condition holds, every event is false and the next
   state is empty. Nothing else is evaluated.
3. **Antecedent (existential).** Test ``state.antecedent`` united with the antecedent's
   start letters (a new attempt begins every sample). The obligation is *triggered* if a
   held letter is accepting. The next antecedent frontier is the union of the held
   letters' ``next``; accepting letters with continuations keep them (later endpoints of
   the same attempt still trigger). Without an antecedent (sequence property), every
   sample triggers.
4. **Obligations (universal, per age).** If triggered, a new obligation of age 0 tests
   the consequent's start letters; obligations of age ``a = 1 … slots`` test
   ``state.slots[a - 1]``. For each obligation independently: if a held letter is
   accepting, it **passes** and is discharged entirely (its other paths are dropped);
   otherwise, if the union of the held letters' ``next`` is empty, it **fails**;
   otherwise that union is its frontier at age ``a + 1``. Obligations of different ages
   are never merged; obligations triggered at the same sample are one obligation.
5. **Events** are Boolean per sample: ``success`` if some obligation passed,
   ``failure`` if some obligation failed.
6. An obligation still alive at age ``slots`` would need a slot beyond the machine's
   bound: :class:`MachineError` (the bound is computed so that this cannot happen).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sva2yosys.ir.directive import Directive
from sva2yosys.ir.expr import Expr
from sva2yosys.temporal.automaton import Automaton
from sva2yosys.temporal.budgets import DEFAULT_LIMITS, BudgetError, ResourceLimits, Work


class MachineError(RuntimeError):
    """The machine reached a state its construction rules out (an internal error)."""


@dataclass(frozen=True)
class Machine:
    """A compiled directive.

    Attributes:
        directive: The source directive (for names, kind, clock and diagnostics).
        conditions: The condition table; guards and ``disable`` refer to its indices.
        past_depth: Per condition: the number of initial samples at which it is
            forced false (0: never), :func:`~sva2yosys.passes.first_sample.gated_samples`:
            1 for a condition using ``$past(e)``, ``n`` for ``$past(e, n)``
            (``docs/semantics.md`` §8). The code generator must use this value rather than
            search the expression for ``$past`` itself.
        disable: Index of the ``disable iff`` condition, or ``None``.
        antecedent: The antecedent automaton, or ``None`` for a sequence property; for
            ``a |=> c`` that of ``a ##1 1`` (``temporal.compile.operands``).
        consequent: The consequent automaton.
        slots: Number of registered obligation ages (``1 … slots``).
        negated: The consequent is ``not s``: an obligation fails where ``s`` matches
            and succeeds where ``s`` can no longer match (its outputs are swapped).
        limits: Compiler resource policy inherited by emission; excluded from semantic
            equality. An explicit emitter override may replace it.
    """

    directive: Directive
    conditions: tuple[Expr, ...]
    past_depth: tuple[int, ...]
    disable: int | None
    antecedent: Automaton | None
    consequent: Automaton
    slots: int
    negated: bool = False
    limits: ResourceLimits = field(default=DEFAULT_LIMITS, compare=False)


@dataclass(frozen=True)
class MachineState:
    """Temporal state before a sample.

    Attributes:
        antecedent: Antecedent letters to test (merged over all running attempts).
        slots: ``slots[a - 1]`` is the frontier of the obligation of age ``a``; an empty
            set means no obligation of that age. Its length is ``Machine.slots``.
    """

    antecedent: frozenset[int]
    slots: tuple[frozenset[int], ...]


@dataclass(frozen=True)
class StepEvents:
    """Events of one sample."""

    disabled: bool
    triggered: bool
    success: bool
    failure: bool


def check_resources(machine: Machine, work: Work | None = None) -> None:
    """Check graph traversal and actual reachable temporal bits before size expansion.

    Each predecessor/edge visit is charged before following it. Only reachable slot
    positions count, not the dense width-times-age product. No huge reach table is built.
    """
    account = Work(machine.limits) if work is None else work
    limits = account.limits
    if machine.slots > limits.expansion:
        raise BudgetError(f"the machine exceeds the {limits.expansion} expansion budget")
    automata = (machine.antecedent, machine.consequent)
    letters = sum(len(a.letters) for a in automata if a is not None)
    if letters > limits.expansion:
        raise BudgetError(f"the machine needs more than {limits.expansion} expanded letters")
    for automaton in automata:
        if automaton is not None:
            account.charge(len(automaton.letters))
            if len(automaton.letters) > limits.expansion:
                raise BudgetError(
                    f"the machine needs more than {limits.expansion} expanded letters"
                )
            account.charge(
                sum(len(letter.guards) + len(letter.next) for letter in automaton.letters)
            )
    bits = 0

    def add_bits(count: int) -> None:
        nonlocal bits
        bits += count
        if bits > limits.state_bits:
            raise BudgetError(
                f"the machine needs more than {limits.state_bits} temporal state bits"
            )

    if machine.antecedent is not None:
        consequent = machine.antecedent
        seen: set[int] = set()
        account.charge(sum(len(consequent.letters[i].next) for i in consequent.start))
        pending = [
            target for index in consequent.start for target in consequent.letters[index].next
        ]
        while pending:
            account.charge()
            index = pending.pop()
            if index in seen:
                continue
            seen.add(index)
            add_bits(1)
            successors = consequent.letters[index].next
            account.charge(len(successors))
            pending.extend(successors)
    frontier = set(machine.consequent.start)
    for _ in range(machine.slots):
        account.charge(1 + len(frontier))
        following: set[int] = set()
        for index in frontier:
            successors = machine.consequent.letters[index].next
            account.charge(len(successors))
            for target in successors:
                if target not in following:
                    following.add(target)
                    if bits + len(following) > limits.state_bits:
                        raise BudgetError(
                            f"the machine needs more than {limits.state_bits} temporal state bits"
                        )
        add_bits(len(following))
        frontier = following


def initial_state(machine: Machine) -> MachineState:
    """The empty temporal state before the first sample."""
    return MachineState(frozenset(), tuple(frozenset() for _ in range(machine.slots)))


def step(
    machine: Machine,
    state: MachineState,
    elapsed: int,
    raw: Callable[[int], bool],
) -> tuple[MachineState, StepEvents]:
    """Perform one sample (see the module docstring).

    Args:
        machine: The machine.
        state: The temporal state before the sample.
        elapsed: The number of earlier samples of the property's clock (0 at the
            first sample).
        raw: ``raw(i)``: whether condition ``i``'s value is non-zero at this sample. It is
            not called for a condition while ``elapsed < past_depth[i]``.

    Returns:
        The state before the next sample and the events of this sample.

    Raises:
        MachineError: An obligation would outlive the slot bound.
    """
    known: dict[int, bool] = {}

    def holds(condition: int) -> bool:
        if elapsed < machine.past_depth[condition]:
            return False
        if condition not in known:
            known[condition] = raw(condition)
        return known[condition]

    if machine.disable is not None and holds(machine.disable):
        return initial_state(machine), StepEvents(
            disabled=True, triggered=False, success=False, failure=False
        )

    antecedent = machine.antecedent
    if antecedent is None:
        triggered = True
        next_antecedent: frozenset[int] = frozenset()
    else:
        held = held_letters(antecedent, state.antecedent | set(antecedent.start), holds)
        triggered = any(antecedent.letters[letter].accepting for letter in held)
        next_antecedent = _successors(antecedent, held)

    consequent = machine.consequent
    obligations: list[tuple[int, frozenset[int]]] = []
    if triggered:
        obligations.append((0, frozenset(consequent.start)))
    obligations.extend((age + 1, frontier) for age, frontier in enumerate(state.slots) if frontier)

    success = failure = False
    slots = [frozenset[int]() for _ in range(machine.slots)]
    for age, frontier in obligations:
        held = held_letters(consequent, frontier, holds)
        if any(consequent.letters[letter].accepting for letter in held):
            success = True
            continue
        following = _successors(consequent, held)
        if not following:
            failure = True
            continue
        if age >= machine.slots:
            raise MachineError(
                f"an obligation of age {age} outlives the machine's {machine.slots} slot(s)"
            )
        slots[age] = following
    if machine.negated:
        success, failure = failure, success
    events = StepEvents(disabled=False, triggered=triggered, success=success, failure=failure)
    return MachineState(next_antecedent, tuple(slots)), events


@dataclass(frozen=True)
class MachineSize:
    """Size metrics of a machine (reported per iteration).

    Attributes:
        conditions: Distinct conditions.
        antecedent_letters: Letters of the antecedent (its frontier width in bits).
        consequent_letters: Letters of the consequent.
        slots: Registered obligation ages.
        temporal_bits: Registered temporal state bits of the generated monitor: the
            antecedent letters of :func:`antecedent_reach` plus, per age, the letters of
            :func:`slot_reach`.
        canonical_bits: The same without pruning: antecedent letters plus
            ``slots * consequent_letters``.
    """

    conditions: int
    antecedent_letters: int
    consequent_letters: int
    slots: int
    temporal_bits: int
    canonical_bits: int


def machine_size(machine: Machine) -> MachineSize:
    """Size metrics of ``machine``."""
    antecedent = len(machine.antecedent.letters) if machine.antecedent is not None else 0
    consequent = len(machine.consequent.letters)
    return MachineSize(
        conditions=len(machine.conditions),
        antecedent_letters=antecedent,
        consequent_letters=consequent,
        slots=machine.slots,
        temporal_bits=len(antecedent_reach(machine))
        + sum(len(reach) for reach in slot_reach(machine)),
        canonical_bits=antecedent + machine.slots * consequent,
    )


def slot_reach(machine: Machine) -> tuple[frozenset[int], ...]:
    """Per registered age ``a`` (``1 … slots``), the consequent letters that slot ``a``
    can ever hold: those reachable from the start letters in exactly ``a`` steps, guards
    ignored.

    In every state reachable by :func:`step`, ``state.slots[a - 1]`` is a subset of
    ``slot_reach(machine)[a - 1]``: the obligation of age 0 starts at the start letters,
    and the frontier of age ``a + 1`` consists of successors of letters of age ``a``
    (``step``). Disable clears every slot. The other bits are therefore constant 0, and a
    monitor need not store them.
    """
    consequent = machine.consequent
    reach: list[frozenset[int]] = []
    frontier = frozenset(consequent.start)
    for _ in range(machine.slots):
        frontier = frozenset(n for letter in frontier for n in consequent.letters[letter].next)
        reach.append(frontier)
    return tuple(reach)


def antecedent_reach(machine: Machine) -> frozenset[int]:
    """The antecedent letters that the registered antecedent frontier can ever hold:
    those reachable from the start letters in one or more steps, guards ignored (the
    registered frontier only holds successors, ``step``). Empty for a sequence property."""
    return successor_reach(machine.antecedent)


def successor_reach(antecedent: Automaton | None) -> frozenset[int]:
    """The letters of ``antecedent`` reachable from its start letters in one or more steps
    (:func:`antecedent_reach` for an automaton; empty for ``None``, a sequence property)."""
    if antecedent is None:
        return frozenset()
    seen: set[int] = set()
    todo = [n for letter in antecedent.start for n in antecedent.letters[letter].next]
    while todo:
        letter = todo.pop()
        if letter not in seen:
            seen.add(letter)
            todo.extend(antecedent.letters[letter].next)
    return frozenset(seen)


def held_letters(
    automaton: Automaton, frontier: Iterable[int], holds: Callable[[int], bool]
) -> frozenset[int]:
    """The letters of ``frontier`` whose guards all hold (guards evaluated in order); the
    held-letters step shared by the machines and the liveness witness."""
    return frozenset(
        letter
        for letter in sorted(frontier)
        if all(holds(guard) for guard in automaton.letters[letter].guards)
    )


def _successors(automaton: Automaton, held: frozenset[int]) -> frozenset[int]:
    return frozenset(n for letter in held for n in automaton.letters[letter].next)
