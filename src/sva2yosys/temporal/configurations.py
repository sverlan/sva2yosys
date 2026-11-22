"""The configuration-set machine: unbounded consequents (``docs/semantics.md`` §9.6, R2).

Normative: ``docs/semantics.md`` §9.6, ``docs/strategies.md`` (*Unbounded consequents*).

A **configuration** is the complete frontier (set of letters) of one consequent
obligation: a state of the subset construction of the consequent automaton. The slot
machine keeps one obligation per age, which is unbounded here; this machine keeps one bit
per *reachable configuration*: "some live obligation is in exactly this configuration".
Obligations of different attempts are merged **only when their complete configurations are
identical**. Identical configurations fail, match and die at the same samples, so the
merge is exact for every output: ``assert`` / ``assume`` consume failures, and the outputs
are per-sample bits. A union of different frontiers would be wrong (it hides a dead
obligation behind a live one, ``docs/strategies.md`` Example 3).

Step at sample ``t`` (state before ``t`` -> events of ``t``, state before ``t + 1``):

1. Conditions and ``disable iff`` as in :mod:`sva2yosys.temporal.machine`.
2. The antecedent (if any) is stepped as there; ``triggered`` starts an obligation in
   configuration 0 (the start frontier of the consequent).
3. Every live configuration (and the new one) is one obligation: if a held letter is
   accepting it **passes** (``success``) and is discharged; otherwise, if no held letter
   has a successor it **fails**; otherwise it moves to the configuration equal to the union
   of the held letters' successors. Several configurations moving to one are merged.
4. The next state is the set of configurations reached.

Configurations are found statically (:func:`configuration_sets`), treating every
guard group as independently satisfiable (letters with equal guards are held together; a
letter without guards is always held): an over-approximation, so an unreachable
configuration costs a register and never a verdict. A budget
(:attr:`~sva2yosys.temporal.budgets.Budgets.configurations`) bounds their number; nothing
is approximated beyond it (:class:`~sva2yosys.temporal.budgets.BudgetError`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sva2yosys.ir.directive import Directive
from sva2yosys.ir.expr import Expr
from sva2yosys.temporal.automaton import Automaton
from sva2yosys.temporal.budgets import DEFAULT_LIMITS, BudgetError, ResourceLimits, Work
from sva2yosys.temporal.liveness import Liveness
from sva2yosys.temporal.machine import (
    MachineError,
    StepEvents,
    held_letters,
    successor_reach,
)


@dataclass(frozen=True)
class ConfigMachine:
    """A compiled directive whose consequent is an unbounded sequence (weak, or strong: then
    the machine also has a liveness part, ``liveness``).

    Attributes:
        directive: The source directive (lowered property).
        conditions: The condition table; guards and ``disable`` refer to its indices.
        past_depth: As :attr:`~sva2yosys.temporal.machine.Machine.past_depth`.
        disable: Index of the ``disable iff`` condition, or ``None``.
        antecedent: The antecedent automaton, or ``None`` for a sequence property.
        consequent: The consequent automaton (cyclic).
        configurations: The reachable configurations; entry 0 is the consequent's start
            frontier. Empty when the consequent automaton has no letter (an obligation
            then fails at its trigger).
        liveness: How the liveness part is checked (:mod:`sva2yosys.temporal.liveness`): ``None``
            for a ``cover`` and for a weak consequent; for an ``assert`` whose consequent is a
            strong sequence, ``PENDING`` with at most one configuration, else ``WITNESS``;
            also for a negated weak one (``not weak(s)`` is strong).
            For a strong ``assume``, always ``PENDING`` (at most one configuration): its
            liveness part is a fairness constraint; other strong assumptions are rejected
            by the compiler.
        negated: The consequent is ``not s``: the configurations and the step
            are those of ``s``, but an obligation **fails** where ``s`` matches and
            **succeeds** where its frontier dies (the events of :func:`configuration_step`
            are swapped, as in the slot machine).
        limits: Resource policy inherited by emission, excluded from semantic equality.
    """

    directive: Directive
    conditions: tuple[Expr, ...]
    past_depth: tuple[int, ...]
    disable: int | None
    antecedent: Automaton | None
    consequent: Automaton
    configurations: tuple[frozenset[int], ...]
    liveness: Liveness | None = None
    negated: bool = False
    limits: ResourceLimits = field(default=DEFAULT_LIMITS, compare=False)


@dataclass(frozen=True)
class ConfigState:
    """Temporal state before a sample: the antecedent frontier and the indices of the
    configurations some live obligation is in."""

    antecedent: frozenset[int]
    live: frozenset[int]


@dataclass(frozen=True)
class ConfigSize:
    """Size metrics: ``configurations`` registers, ``antecedent_bits`` registered
    antecedent letters, ``temporal_bits`` their sum."""

    conditions: int
    configurations: int
    antecedent_bits: int
    temporal_bits: int


def configuration_sets(
    consequent: Automaton, limit: int, *, work: Work | None = None
) -> tuple[frozenset[int], ...]:
    """The configurations reachable from ``consequent``'s start frontier (entry 0), in
    discovery order, guard groups independent.

    From a configuration the frontier after a sample is the union of the successors of the
    held letters. Letters with equal guards are held together and a letter without guards
    always is, so the held set ranges over the unions of whole guard groups (the always-held
    group in every one); a held set with an accepting letter discharges the obligation and
    leaves no frontier. The candidate successors are therefore all unions of the groups'
    successor sets (added one group at a time, so the work is bounded by ``limit``).

    Raises:
        BudgetError: More than ``limit`` configurations (also while a single
            configuration's successors are being enumerated).
    """
    if not consequent.letters:
        return ()
    start = frozenset(consequent.start)
    found: list[frozenset[int]] = [start]
    seen = {start}
    todo = [start]
    while todo:
        for image in _images(consequent, todo.pop(0), limit, work):
            if image and image not in seen:
                if len(found) >= limit:
                    raise _over(limit)
                seen.add(image)
                found.append(image)
                todo.append(image)
    return tuple(found)


def _over(limit: int) -> BudgetError:
    return BudgetError(
        f"the property needs more than {limit} configurations for the configuration-set machine"
    )


def _images(
    consequent: Automaton, configuration: frozenset[int], limit: int, work: Work | None = None
) -> set[frozenset[int]]:
    """Every frontier (possibly empty: a failure) an obligation in ``configuration`` can have
    after a sample that discharges it by no match, for any choice of held guard groups."""
    groups: dict[tuple[int, ...], set[int]] = {}
    discharged: set[tuple[int, ...]] = set()
    for letter in sorted(configuration):
        guards = consequent.letters[letter].guards
        if work is not None:
            work.charge(1 + len(guards) + len(consequent.letters[letter].next))
        if consequent.letters[letter].accepting:
            discharged.add(guards)
        groups.setdefault(guards, set()).update(consequent.letters[letter].next)
    if () in discharged:
        return set()  # an always-held accepting letter: the obligation matches at once
    images: set[frozenset[int]] = {frozenset(groups.get((), ()))}
    for guards, successors in sorted(groups.items()):
        if not guards or guards in discharged:
            continue  # holding a group with an accepting letter is a match, not a successor
        if work is not None:
            work.charge(sum(len(image) + len(successors) + 1 for image in images))
        images |= {image | successors for image in images}
        if len(images) > limit:
            raise _over(limit)
    return images


def never_fails(machine: ConfigMachine) -> bool:
    """Whether ``machine`` can provably never report a failure (``docs/semantics.md`` §9.6, R2:
    the CLI warns for such an ``assert`` / ``assume``; the monitor is unchanged).

    A discharge by matching is not a failure: ``a |-> b[*1:$]`` can fail at once (``b``
    false) and ``p until q`` can fail; ``a |-> ##[1:$] b`` cannot, and neither can
    ``always ##[1:$] b`` or ``##[1:$] b`` as a property.

    Criterion. The step (module docstring) is monotone in the set of held letters: holding
    more letters can only add accepting letters and successors, never remove them. An
    obligation in a configuration therefore fails on some sample iff it fails when only the
    letters without guards are held (the smallest held set any sample can give: every guard
    false): no held letter is accepting and none has a successor. A machine without
    configurations (an empty consequent) fails at its trigger. The result is ``True`` iff
    no configuration of ``machine.configurations`` can fail in that sense.

    The answer is conservative: ``True`` is a proof, ``False`` only means "not proved".
    The configurations over-approximate the reachable ones (every guard group is treated as
    independently satisfiable), so an unreachable configuration that can fail only makes
    the answer ``False``; and "all guards false" is taken as possible even when guards are
    correlated (``b`` and ``!b``), which again can only make it ``False``. Constant-false
    letters are guarded letters that never hold (``docs/semantics.md`` §3) and constant-true
    guards are dropped by the automaton, so they need no special case. Antecedent, trigger
    and ``disable iff`` are irrelevant: they only decide whether and when an obligation
    exists, and an obligation can only be removed by them, never made to fail.

    A negated machine (``not s``) fails where ``s`` matches, which this
    criterion does not analyse: the answer is ``False`` (not proved).
    """
    if machine.negated or not machine.configurations:
        return False
    letters = machine.consequent.letters
    return all(
        any(
            not letters[index].guards and (letters[index].accepting or bool(letters[index].next))
            for index in configuration
        )
        for configuration in machine.configurations
    )


def initial_config_state() -> ConfigState:
    """The empty state before the first sample."""
    return ConfigState(frozenset(), frozenset())


def configuration_step(
    machine: ConfigMachine,
    state: ConfigState,
    elapsed: int,
    raw: Callable[[int], bool],
) -> tuple[ConfigState, StepEvents]:
    """Perform one sample (see the module docstring).

    Raises:
        MachineError: A successor frontier is not one of ``machine.configurations`` (an
            internal inconsistency of the static construction).
    """
    known: dict[int, bool] = {}

    def holds(condition: int) -> bool:
        if elapsed < machine.past_depth[condition]:
            return False
        if condition not in known:
            known[condition] = raw(condition)
        return known[condition]

    if machine.disable is not None and holds(machine.disable):
        return initial_config_state(), StepEvents(
            disabled=True, triggered=False, success=False, failure=False
        )

    antecedent = machine.antecedent
    if antecedent is None:
        triggered = True
        next_antecedent: frozenset[int] = frozenset()
    else:
        held = held_letters(antecedent, state.antecedent | set(antecedent.start), holds)
        triggered = any(antecedent.letters[letter].accepting for letter in held)
        next_antecedent = frozenset(n for letter in held for n in antecedent.letters[letter].next)

    consequent = machine.consequent
    success = failure = False
    live: set[int] = set()
    obligations = set(state.live)
    if triggered:
        if machine.configurations:
            obligations.add(0)
        else:
            failure = True  # the consequent can never match: it fails at its trigger
    for index in sorted(obligations):
        held = held_letters(consequent, machine.configurations[index], holds)
        if any(consequent.letters[letter].accepting for letter in held):
            success = True
            continue
        following = frozenset(n for letter in held for n in consequent.letters[letter].next)
        if not following:
            failure = True
            continue
        try:
            live.add(machine.configurations.index(following))
        except ValueError:
            raise MachineError(
                f"the frontier {sorted(following)} is not a configuration of the machine"
            ) from None
    if machine.negated:  # not s: a match fails, a dead obligation succeeds
        success, failure = failure, success
    events = StepEvents(disabled=False, triggered=triggered, success=success, failure=failure)
    return ConfigState(next_antecedent, frozenset(live)), events


def config_size(machine: ConfigMachine) -> ConfigSize:
    """Size metrics of ``machine``."""
    antecedent_bits = len(successor_reach(machine.antecedent))
    return ConfigSize(
        conditions=len(machine.conditions),
        configurations=len(machine.configurations),
        antecedent_bits=antecedent_bits,
        temporal_bits=antecedent_bits + len(machine.configurations),
    )
