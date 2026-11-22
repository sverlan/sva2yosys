"""Run a machine on a finite trace (Layer 2 of the validation).

The simulator interprets the machine data through :func:`~sva2yosys.temporal.machine.step`
and adds no temporal semantics of its own. Raw condition values are expression values at
each sample; the property clock's first sample is sample 0.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from support.reference.evaluate import value
from support.reference.lasso import Lasso
from support.reference.lasso import _depth as _condition_depth
from support.reference.trace import Trace
from sva2yosys.temporal.attempts import AttemptEvents, AttemptMachine, initial_attempt_state
from sva2yosys.temporal.attempts import step as attempt_step
from sva2yosys.temporal.configurations import (
    ConfigMachine,
    ConfigState,
    configuration_step,
    initial_config_state,
)
from sva2yosys.temporal.liveness import Liveness, witness_step
from sva2yosys.temporal.machine import Machine, MachineState, StepEvents, initial_state, step


@dataclass(frozen=True)
class Snapshot:
    """One simulated sample: the state before it, its events and the state after it."""

    sample: int
    before: MachineState
    events: StepEvents
    after: MachineState


@dataclass(frozen=True)
class Simulation:
    """Result of a simulation.

    Attributes:
        snapshots: One per sample.
        failures: Samples with a failure event.
        successes: Samples with a success event.
        pending: Obligations still open after the last sample (non-empty slots).
    """

    snapshots: tuple[Snapshot, ...]
    failures: tuple[int, ...]
    successes: tuple[int, ...]
    pending: int


def simulate(machine: Machine, trace: Trace) -> Simulation:
    """Step ``machine`` through every sample of ``trace``.

    Raises:
        MachineError: The machine reached an impossible state.
        TraceError: A needed value is missing.
    """
    state = initial_state(machine)
    snapshots: list[Snapshot] = []
    failures: list[int] = []
    successes: list[int] = []
    for sample in range(len(trace)):

        def raw(condition: int, sample: int = sample) -> bool:
            return value(machine.conditions[condition], trace, sample) != 0

        after, events = step(machine, state, sample, raw)
        snapshots.append(Snapshot(sample, state, events, after))
        if events.failure:
            failures.append(sample)
        if events.success:
            successes.append(sample)
        state = after
    pending = sum(1 for frontier in state.slots if frontier)
    return Simulation(tuple(snapshots), tuple(failures), tuple(successes), pending)


class AttemptOutcome(Enum):
    """The state of one root attempt after a simulation."""

    TRUE = "true"
    FALSE = "false"
    PENDING = "pending"
    DISABLED = "disabled"


@dataclass(frozen=True)
class AttemptRecord:
    """One root attempt.

    Attributes:
        start: The sample at which it started.
        outcome: Its outcome at the end of the simulation.
        decided: The sample of its decision (or of the disable that ended it), ``None``
            while pending.
        nonvacuous: The machine's nonvacuity at the decision; ``None`` unless the
            outcome is TRUE or FALSE.
    """

    start: int
    outcome: AttemptOutcome
    decided: int | None
    nonvacuous: bool | None


@dataclass(frozen=True)
class AttemptSimulation:
    """Result of :func:`simulate_attempts`.

    Attributes:
        events: Per sample.
        failures: Samples with a failure event.
        successes: Samples with a success event.
        attempts: One record per sample (the attempt started there), in start order.
        pending: Attempts neither decided nor disabled at the end.
    """

    events: tuple[AttemptEvents, ...]
    failures: tuple[int, ...]
    successes: tuple[int, ...]
    attempts: tuple[AttemptRecord, ...]
    pending: int


def simulate_attempts(machine: AttemptMachine, trace: Trace) -> AttemptSimulation:
    """Step the attempt machine through every sample of ``trace``.

    The attempt started at sample ``s`` is root age ``t - s`` at sample ``t``. A disable
    at ``t`` before its decision makes it DISABLED at ``t``; an attempt started at the
    disabling sample is DISABLED there too. ``pending`` counts the attempts neither
    decided nor disabled at the end.

    Raises:
        MachineError: The machine's overflow self-check fired.
        TraceError: A needed value is missing.
    """
    state = initial_attempt_state(machine)
    events: list[AttemptEvents] = []
    failures: list[int] = []
    successes: list[int] = []
    outcomes: list[AttemptOutcome] = []
    decided: list[int | None] = []
    nonvacuous: list[bool | None] = []
    for sample in range(len(trace)):

        def raw(condition: int, sample: int = sample) -> bool:
            return value(machine.conditions[condition], trace, sample) != 0

        state, event = attempt_step(machine, state, sample, raw)
        events.append(event)
        if event.failure:
            failures.append(sample)
        if event.success:
            successes.append(sample)
        outcomes.append(AttemptOutcome.PENDING)
        decided.append(None)
        nonvacuous.append(None)
        for start in range(sample + 1):
            if outcomes[start] is not AttemptOutcome.PENDING:
                continue
            age = event.ages[sample - start] if sample - start < machine.life else None
            if event.disabled:
                outcomes[start] = AttemptOutcome.DISABLED
                decided[start] = sample
            elif age is not None and (age.true or age.false):
                outcomes[start] = AttemptOutcome.TRUE if age.true else AttemptOutcome.FALSE
                decided[start] = sample
                nonvacuous[start] = age.nonvacuous
    records = tuple(
        AttemptRecord(start, outcomes[start], decided[start], nonvacuous[start])
        for start in range(len(trace))
    )
    pending = sum(1 for record in records if record.outcome is AttemptOutcome.PENDING)
    return AttemptSimulation(tuple(events), tuple(failures), tuple(successes), records, pending)


@dataclass(frozen=True)
class ConfigSimulation:
    """Result of :func:`simulate_configurations`.

    Attributes:
        failures: Samples with a failure event.
        successes: Samples with a success event.
        states: The state after every sample.
        live: Whether some obligation is live after the last sample.
    """

    failures: tuple[int, ...]
    successes: tuple[int, ...]
    states: tuple[ConfigState, ...]
    live: bool


def simulate_configurations(machine: ConfigMachine, trace: Trace) -> ConfigSimulation:
    """Step the configuration-set machine through every sample of ``trace`` (only drives
    the product's ``configuration_step``).

    Raises:
        MachineError: A successor frontier is not a known configuration.
        TraceError: A needed value is missing.
    """
    state = initial_config_state()
    failures: list[int] = []
    successes: list[int] = []
    states: list[ConfigState] = []
    for sample in range(len(trace)):

        def raw(condition: int, sample: int = sample) -> bool:
            return value(machine.conditions[condition], trace, sample) != 0

        state, events = configuration_step(machine, state, sample, raw)
        states.append(state)
        if events.failure:
            failures.append(sample)
        if events.success:
            successes.append(sample)
    return ConfigSimulation(tuple(failures), tuple(successes), tuple(states), bool(state.live))


def liveness_fails(machine: ConfigMachine, lasso: Lasso) -> bool:
    """Whether the liveness check of ``machine`` fails on the infinite trace ``lasso``.

    Contract (the check is "``busy`` is not 1 at every sample from some point
    on"). Exact, by cycle detection: nothing is a guessed bound and no temporal semantics is
    added (only ``configuration_step`` and ``witness_step`` are driven, with condition values
    from the reference ``value``).

    The sampled trace is periodic from ``W = loop + d``, ``d`` the largest number of earlier
    samples a condition reads (at least the machine's past depths): a condition at a position
    ``p >= W`` reads only loop samples, so it equals the one at the canonical position
    ``W + (p - W) % period`` (the position itself below ``W``), and no condition is gated
    there. A run is a deterministic function of (canonical position, state), so it is
    ultimately periodic and the first repeated pair closes the cycle.

    - ``Liveness.PENDING``: run ``configuration_step`` along the trace; the check fails iff
      "some obligation is live after the sample" (``bool(state.live)``) holds at every sample
      of the repeated cycle.
    - ``Liveness.WITNESS``: for every launch sample ``p`` (``events.triggered``) up to the
      first repetition of (canonical position, ``ConfigState``) of the configuration machine
      (a launch later is equivalent to one inside the cycle), run ``witness_step`` with
      ``pick`` true at ``p`` only, until the witness is idle (that run passes) or a pair
      (canonical position, frontier) repeats (the witness is busy at every sample of the
      cycle: the check fails). The check fails iff some run does.

    Raises:
        ValueError: ``machine.liveness`` is ``None`` (no liveness part).
        MachineError: A successor frontier is not a known configuration.
    """
    if machine.liveness is None:
        raise ValueError("the machine has no liveness part")
    depth = max(
        (*machine.past_depth, *(_condition_depth(c) for c in machine.conditions)), default=0
    )
    run = _Lasso(machine, lasso, loop_start=lasso.loop + depth)
    match machine.liveness:
        case Liveness.PENDING:
            return run.pending_fails()
        case Liveness.WITNESS:
            return run.witness_fails()


def lasso_first_failure(machine: ConfigMachine, lasso: Lasso) -> int | None:
    """The first sample at which ``machine`` reports a failure on the infinite trace
    ``lasso``, or ``None`` when it never does.

    Exact, by the cycle detection of :func:`liveness_fails` (the events of the whole run are
    those of the samples up to the first repeated pair of canonical position and
    ``ConfigState``, which repeat from then on), driving only ``configuration_step``.

    Raises:
        MachineError: A successor frontier is not a known configuration.
    """
    depth = max(
        (*machine.past_depth, *(_condition_depth(c) for c in machine.conditions)), default=0
    )
    history, _ = _Lasso(machine, lasso, loop_start=lasso.loop + depth).configuration_run()
    for sample, (_, events) in enumerate(history):
        if events.failure:
            return sample
    return None


@dataclass(frozen=True)
class _Lasso:
    """The configuration machine run along a lasso, with the position classes of
    :func:`liveness_fails` (``loop_start`` is ``W``)."""

    machine: ConfigMachine
    lasso: Lasso
    loop_start: int

    def canonical(self, position: int) -> int:
        if position < self.loop_start:
            return position
        return self.loop_start + (position - self.loop_start) % self.lasso.period

    def raw(self, position: int) -> Callable[[int], bool]:
        trace = self.lasso.unroll(self.loop_start + self.lasso.period)
        canonical = self.canonical(position)

        def raw(condition: int) -> bool:
            return value(self.machine.conditions[condition], trace, canonical) != 0

        return raw

    def configuration_run(self) -> tuple[list[tuple[ConfigState, StepEvents]], int]:
        """The (state after, events) of every sample up to the first repeated pair (canonical
        position, state before the sample), and the index where the cycle starts."""
        state = initial_config_state()
        seen: dict[tuple[int, ConfigState], int] = {}
        history: list[tuple[ConfigState, StepEvents]] = []
        position = 0
        while True:
            key = (self.canonical(position), state)
            if key in seen:
                return history, seen[key]
            seen[key] = position
            state, events = configuration_step(
                self.machine, state, self.canonical(position), self.raw(position)
            )
            history.append((state, events))
            position += 1

    def pending_fails(self) -> bool:
        history, start = self.configuration_run()
        return all(bool(state.live) for state, _ in history[start:])

    def witness_fails(self) -> bool:
        history, start = self.configuration_run()
        length = len(history)

        def events_at(position: int) -> StepEvents:
            index = position if position < length else start + (position - start) % (length - start)
            return history[index][1]

        for launch in range(length):
            if not history[launch][1].triggered:
                continue
            frontier: frozenset[int] | None = None
            seen: set[tuple[int, frozenset[int] | None]] = set()
            position = launch
            while True:
                frontier = witness_step(
                    self.machine,
                    frontier,
                    events_at(position),
                    position == launch,
                    self.canonical(position),
                    self.raw(position),
                )
                if frontier is None:
                    break
                position += 1
                key = (self.canonical(position), frontier)
                if key in seen:
                    return True
                seen.add(key)
        return False
