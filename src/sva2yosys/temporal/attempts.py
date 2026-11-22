"""The per-attempt (netlist) machine: one instance of the root property per root age.

Normative: ``docs/semantics.md`` §9.2, §9.4, §9.5. Every attempt of the property (one
starts at every sample) is followed separately for its whole life; attempts are never
merged. The machine is a :class:`~sva2yosys.temporal.netlist.Netlist` whose registers are
the per-age state of the instances; it is exact at every sample.

Construction (:func:`build_attempt_machine`): the lowered property tree is instantiated
once per root age ``alpha = 0 … life - 1`` (root age 0 is combinational). A sub-instance
launched at static offset ``o`` is at local age ``alpha - o``; its state is read from
registers keyed by (root age, tree path, part, index), and a missing key reads constant 0
(unreachable state does not exist). Each instance yields (true, false, nonvacuous,
pending, live) gates and next-state gates, with the invariant that pending holds iff some
next-state bit of the instance is 1 (decided or not-live instances have all next bits 0).

Node rules (``held_i = frontier_i & guards_i``):

- Sequence automaton: frontier is the start letters at age 0 (and ``launch``), else the
  ``letter`` registers; true = some accepting held letter; false = live, not true, and no
  held letter has a successor; pending = not true and some successor; nonvacuous = 1.
- ``not``: swaps true and false; nonvacuity, pending, live and next of the operand.
- ``and`` (``or``): latches per operand ``done_i`` (operand true / false earlier) and
  ``nv_i``; false (true) when some operand is; true (false) when every operand is true
  (false) by now; children's next state is cleared on the decision.
- ``if`` with ``else``: the condition is sampled at age 0 and selects the instance of one
  branch.
- Implication: the antecedent is a sequence ("ant" registers); each match end launches a
  consequent sub-instance at its own offset; sub-instances are never merged; false when
  some sub-instance is false; true when live, not false, the antecedent has no
  continuation and no sub-instance is pending; an accumulator remembers that some
  sub-attempt was known nonvacuous.
- ``nexttime`` / ``always`` / ``eventually``: lowered to implications before construction
  (:func:`~sva2yosys.temporal.lower.lower`); the written-form helpers of
  :mod:`~sva2yosys.temporal.life`, :mod:`~sva2yosys.temporal.nonvacuity` and
  :mod:`~sva2yosys.temporal.budgets` also define them, for the cover rule and the letter
  budget on the written property.
- Life cut: at local age ``life(p) - 1`` an instance returns no next state and its
  pending is ORed into ``overflow`` (a structural self-check; :func:`step` raises).

Nonvacuity (nv) is tracked as "known true by this sample" while the instance is live; the
machine's value is an under-approximation of the reference's, and equals it when
:func:`~sva2yosys.temporal.nonvacuity.cover_problem` is ``None`` (``docs/semantics.md`` §9.5).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

from sva2yosys.ir.directive import Directive
from sva2yosys.ir.expr import Const, Expr
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
from sva2yosys.ir.sequence import Sequence
from sva2yosys.passes.first_sample import gated_samples
from sva2yosys.temporal.automaton import Automaton, ConditionTable, build_automaton
from sva2yosys.temporal.bounds import sequence_bounds
from sva2yosys.temporal.budgets import (
    DEFAULT_BUDGETS,
    DEFAULT_LIMITS,
    Budgets,
    ResourceLimits,
    Work,
    check_expressions,
    check_letter_budget,
    over,
    preflight,
)
from sva2yosys.temporal.life import life, normalised_antecedent
from sva2yosys.temporal.lower import lower
from sva2yosys.temporal.machine import MachineError
from sva2yosys.temporal.netlist import (
    AgeOutputs,
    NAnd,
    Netlist,
    NetlistBuilder,
    NNot,
    NOr,
    evaluate,
    next_state,
)
from sva2yosys.temporal.nonvacuity import launch_nv


@dataclass(frozen=True)
class AttemptMachine:
    """A compiled directive as a per-attempt netlist.

    Attributes:
        directive: The source directive with its property lowered.
        conditions: The condition table; ``NInput`` and ``disable`` refer to its indices.
        past_depth: Per condition: initial samples at which it is forced false
            (as :class:`~sva2yosys.temporal.machine.Machine`).
        disable: Index of the ``disable iff`` condition, or ``None``.
        life: ``life(root) >= 1``: the number of root ages.
        netlist: The netlist.
        limits: Resource policy inherited by emission, excluded from semantic equality.
    """

    directive: Directive
    conditions: tuple[Expr, ...]
    past_depth: tuple[int, ...]
    disable: int | None
    life: int
    netlist: Netlist
    limits: ResourceLimits = field(default=DEFAULT_LIMITS, compare=False)


@dataclass(frozen=True)
class AgeEvent:
    """Outcome of the root attempt of one root age at one sample."""

    true: bool
    false: bool
    nonvacuous: bool
    pending: bool


@dataclass(frozen=True)
class AttemptEvents:
    """Events of one sample.

    Attributes:
        disabled: ``disable iff`` holds (state cleared, no other event).
        failure: Some root attempt fails at this sample.
        success: Some root attempt is true and nonvacuous at this sample.
        ages: Per root age ``0 … life - 1``.
    """

    disabled: bool
    failure: bool
    success: bool
    ages: tuple[AgeEvent, ...]


@dataclass(frozen=True)
class AttemptSize:
    """Size statistics: condition count, ``life``, register count, and gate count
    (``NNot`` / ``NAnd`` / ``NOr`` gates only)."""

    conditions: int
    life: int
    registers: int
    gates: int


type _Key = tuple[tuple[int, ...], str, int]
"""A state bit of an instance: (tree path, part, index)."""


@dataclass(frozen=True)
class _Inst:
    """One instance of a property node at one root age."""

    true: int
    false: int
    nonvacuous: int
    pending: int
    live: int
    next: tuple[tuple[_Key, int], ...]


@dataclass(frozen=True)
class _FrontierPlan:
    """Structural positions at one local age and ascending target/source edge groups.

    Guards are ignored. Include accepting continuations; distinct attempts and root
    ages are never merged. Cache lazily, charging visits before allocating entries.
    """

    letters: tuple[int, ...]
    following: tuple[tuple[int, tuple[int, ...]], ...]


@dataclass(frozen=True)
class _Run:
    """One sample of a sequence automaton: its frontier, whether a held letter is
    accepting, and target/gate pairs for possible successors."""

    frontier: tuple[int, ...]
    accepting: int
    successors: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Antecedent:
    """The normalised antecedent of an implication."""

    automaton: Automaton
    last_end: int  # the longest end offset of a match; -1 when there is no match


class _Construction:
    """Mutable state of :func:`build_attempt_machine`."""

    def __init__(self, root: Property, table: ConditionTable, budgets: Budgets, work: Work) -> None:
        self._root = root
        self._table = table
        self._budgets = budgets
        self._work = work
        self._letters = 0
        self._instances = 0
        self._registers = 0
        self._gates = 0
        self.netlist = NetlistBuilder(self._charge_gate, self._charge_register, work.charge)
        self._values: dict[tuple[int, tuple[int, ...], str, int], int] = {}
        self._automata: dict[Sequence, Automaton] = {}
        self._frontiers: dict[int, list[_FrontierPlan]] = {}
        self._warmed: set[int] = set()
        self._antecedents: dict[int, _Antecedent] = {}
        self._lives: dict[int, int] = {}
        self._overflow: list[int] = []
        self._alpha = 0

    # --- budgets ---------------------------------------------------------------------------

    def _charge_gate(self) -> None:
        self._gates += 1
        if self._gates > self._budgets.gates:
            raise over(self._budgets.gates, "gates")

    def _charge_register(self) -> None:
        self._registers += 1
        if self._registers > self._budgets.registers:
            raise over(self._budgets.registers, "registers")

    def _charge_instance(self) -> None:
        self._work.charge()
        self._instances += 1
        if self._instances > self._budgets.instances:
            raise over(self._budgets.instances, "instances")

    # --- small helpers ---------------------------------------------------------------------

    def _frontier_plan(self, automaton: Automaton, age: int) -> _FrontierPlan:
        """Lazy guard-independent per-age structure, empty beyond the last frontier.

        Planning and current-run lower bounds are charged before expanding caches or
        creating gates. This is not a prediction of exact gates after folding.
        """
        key = id(automaton)
        if key not in self._frontiers:
            self._work.charge()
            self._frontiers[key] = []
        plans = self._frontiers[key]
        while len(plans) <= age:
            if plans and not plans[-1].following:
                return _FrontierPlan((), ())
            self._work.charge()
            if plans:
                self._work.charge(len(plans[-1].following))
                letters = tuple(target for target, _ in plans[-1].following)
            else:
                self._work.charge(len(automaton.start))
                letters = automaton.start
            incoming: dict[int, list[int]] = {}
            for source in letters:
                self._work.charge(1 + len(automaton.letters[source].next))
                for target in automaton.letters[source].next:
                    incoming.setdefault(target, []).append(source)
            self._work.charge(len(incoming) + sum(map(len, incoming.values())))
            plans.append(
                _FrontierPlan(
                    letters,
                    tuple(
                        (target, tuple(sorted(set(incoming[target]))))
                        for target in sorted(incoming)
                    ),
                )
            )
        return plans[age]

    def _life(self, prop: Property) -> int:
        key = id(prop)
        if key not in self._lives:
            self._lives[key] = life(prop)
        return self._lives[key]

    def _automaton(self, sequence: Sequence) -> Automaton:
        if sequence not in self._automata:
            automaton = build_automaton(sequence, self._table, work=self._work)
            self._letters += len(automaton.letters)
            if self._letters > self._budgets.letters:
                raise over(self._budgets.letters, "letters")
            self._automata[sequence] = automaton
        return self._automata[sequence]

    def _antecedent(self, prop: Implication) -> _Antecedent:
        key = id(prop)
        if key not in self._antecedents:
            normalised = normalised_antecedent(prop.antecedent, prop.overlapping)
            bounds = sequence_bounds(normalised)
            if bounds.unbounded:
                raise ValueError("unbounded ranges ($) are not supported")
            longest = bounds.max_length
            self._antecedents[key] = _Antecedent(
                self._automaton(normalised), -1 if longest is None else longest - 1
            )
        return self._antecedents[key]

    def _condition(self, condition: Expr) -> int:
        if isinstance(condition, Const):
            return self.netlist.const(condition.value != 0)
        return self.netlist.input(self._table.identify(condition))

    def _read(self, age: int, path: tuple[int, ...], part: str, index: int) -> int:
        """The register value gate of a state bit for root age ``age`` (constant 0 when no
        such register exists). Every state read goes through here."""
        gate = self._values.get((age, path, part, index))
        return self.netlist.const(False) if gate is None else gate

    def _state(self, path: tuple[int, ...], part: str, index: int, local_age: int) -> int:
        """A state bit of the instance at ``path``; constant 0 at local age 0."""
        if local_age == 0:
            return self.netlist.const(False)
        return self._read(self._alpha, path, part, index)

    def _implication_false(self, falses: list[int]) -> int:
        """An implication is false when some consequent sub-attempt is."""
        return self.netlist.or_(falses)

    def _absent(self) -> _Inst:
        zero = self.netlist.const(False)
        return _Inst(zero, zero, zero, zero, zero, ())

    def _killed(
        self, entries: Iterable[tuple[_Key, int]], keep: int
    ) -> tuple[tuple[_Key, int], ...]:
        """``entries`` with every next-state gate ANDed with ``keep``."""
        return tuple((key, self.netlist.and_((gate, keep))) for key, gate in entries)

    # --- construction ----------------------------------------------------------------------

    def construct(self, root_life: int) -> Netlist:
        """Instantiate the root once per root age and finish the netlist."""
        nl = self.netlist
        ages: list[AgeOutputs] = []
        failures: list[int] = []
        successes: list[int] = []
        for alpha in range(root_life):
            self._alpha = alpha
            inst = self._build(self._root, (), alpha, nl.const(alpha == 0))
            ages.append(AgeOutputs(inst.true, inst.false, inst.nonvacuous, inst.pending))
            failures.append(inst.false)
            successes.append(nl.and_((inst.true, inst.nonvacuous)))
            if alpha + 1 < root_life:
                for (path, part, index), gate in inst.next:
                    if gate == nl.const(False):
                        continue
                    register = nl.add_register(gate, alpha + 1, path, part, index)
                    self._values[(alpha + 1, path, part, index)] = nl.register_value(register)
        return nl.finish(tuple(ages), nl.or_(failures), nl.or_(successes), nl.or_(self._overflow))

    def _build(self, prop: Property, path: tuple[int, ...], age: int, launch: int) -> _Inst:
        """The instance of ``prop`` at tree ``path`` and local age ``age``; ``launch`` is the
        gate that starts it now (used at age 0 only)."""
        span = self._life(prop)
        if age >= span:
            return self._absent()
        self._charge_instance()
        inst = self._node(prop, path, age, launch)
        if age == span - 1:
            self._overflow.append(inst.pending)
            inst = replace(inst, next=())
        return inst

    def _node(self, prop: Property, path: tuple[int, ...], age: int, launch: int) -> _Inst:
        match prop:
            case SequenceProperty(sequence=sequence):
                return self._sequence(sequence, path, age, launch)
            case Not(operand=operand):
                child = self._build(operand, (*path, 0), age, launch)
                return replace(child, true=child.false, false=child.true)
            case PropAnd(operands=operands):
                return self._junction(prop, operands, path, age, launch, conjunction=True)
            case PropOr(operands=operands):
                return self._junction(prop, operands, path, age, launch, conjunction=False)
            case PropIf():
                return self._branching(prop, path, age, launch)
            case Implication():
                return self._implication(prop, path, age, launch)
            case Nexttime() | Always() | Eventually() | Until():
                raise ValueError("a temporal operator must be lowered before construction")

    def _run(
        self, automaton: Automaton, path: tuple[int, ...], part: str, age: int, launch: int
    ) -> _Run:
        nl = self.netlist
        if id(automaton) in self._warmed:
            plan = self._frontier_plan(automaton, age)
            self._work.charge(len(plan.letters) + sum(len(s) for _, s in plan.following))
            frontier = tuple(
                launch if age == 0 else self._read(self._alpha, path, part, index)
                for index in plan.letters
            )
            held = {
                index: nl.and_((value, *(nl.input(g) for g in automaton.letters[index].guards)))
                for index, value in zip(plan.letters, frontier, strict=True)
            }
            accepting = nl.or_(
                held[index] for index in plan.letters if automaton.letters[index].accepting
            )
            successors = tuple(
                (target, nl.or_(held[source] for source in sources))
                for target, sources in plan.following
            )
            return _Run(frontier, accepting, successors)

        # Prime condition and constant gates in their established order once. Later
        # runs omit structurally absent positions, whose gates fold to this same zero.
        self._warmed.add(id(automaton))
        width = len(automaton.letters)
        self._work.charge(width + sum(len(sources) for sources in automaton.incoming))
        starts = set(automaton.start)
        frontier = tuple(
            (launch if index in starts else nl.const(False))
            if age == 0
            else self._read(self._alpha, path, part, index)
            for index in range(len(automaton.letters))
        )
        dense_held = [
            nl.and_((frontier[index], *(nl.input(g) for g in letter.guards)))
            for index, letter in enumerate(automaton.letters)
        ]
        accepting = nl.or_(
            dense_held[index] for index, letter in enumerate(automaton.letters) if letter.accepting
        )
        successors = tuple(
            (target, nl.or_(dense_held[index] for index in sources))
            for target, sources in enumerate(automaton.incoming)
        )
        self._work.charge(width)
        zero = nl.const(False)
        return _Run(frontier, accepting, tuple(entry for entry in successors if entry[1] != zero))

    def _sequence(self, sequence: Sequence, path: tuple[int, ...], age: int, launch: int) -> _Inst:
        nl = self.netlist
        run = self._run(self._automaton(sequence), path, "letter", age, launch)
        more = nl.or_(gate for _, gate in run.successors)
        done = nl.not_(run.accepting)
        live = launch if age == 0 else nl.or_(run.frontier)
        return _Inst(
            true=run.accepting,
            false=nl.and_((live, done, nl.not_(more))),
            nonvacuous=nl.const(True),
            pending=nl.and_((done, more)),
            live=live,
            next=tuple(
                ((path, "letter", target), nl.and_((successor, done)))
                for target, successor in run.successors
            ),
        )

    def _junction(
        self,
        prop: PropAnd | PropOr,
        operands: tuple[Property, ...],
        path: tuple[int, ...],
        age: int,
        launch: int,
        *,
        conjunction: bool,
    ) -> _Inst:
        """``and`` (``conjunction``) or ``or`` of the operands. Per operand ``done_i``
        latches that the operand continued the junction (true for ``and``, false for
        ``or``); the opposite outcome of any operand decides it at once."""
        nl = self.netlist
        children = [
            self._build(operand, (*path, index), age, launch)
            for index, operand in enumerate(operands)
        ]
        known_nv = launch_nv(prop)
        continued: list[int] = []
        for index, child in enumerate(children):
            outcome = child.true if conjunction else child.false
            continued.append(nl.or_((self._state(path, "done", index, age), outcome)))
        decisive = nl.or_(c.false if conjunction else c.true for c in children)
        combined = nl.and_((nl.not_(decisive), *continued))
        true, false = (combined, decisive) if conjunction else (decisive, combined)
        keep = nl.not_(nl.or_((true, false)))
        next_entries: list[tuple[_Key, int]] = []
        nonvacuous: list[int] = []
        for index, child in enumerate(children):
            next_entries.append(((path, "done", index), nl.and_((continued[index], keep))))
            if not known_nv:
                seen = nl.or_((self._state(path, "nv", index, age), child.nonvacuous))
                nonvacuous.append(seen)
                next_entries.append(((path, "nv", index), nl.and_((seen, keep))))
            next_entries.extend(self._killed(child.next, keep))
        return _Inst(
            true=true,
            false=false,
            nonvacuous=nl.const(True) if known_nv else nl.or_(nonvacuous),
            pending=nl.and_((keep, nl.or_(c.pending for c in children))),
            live=nl.or_(c.live for c in children),
            next=tuple(next_entries),
        )

    def _branch_nonvacuous(self, prop: Property, inst: _Inst) -> int:
        return inst.live if launch_nv(prop) else inst.nonvacuous

    def _branching(self, prop: PropIf, path: tuple[int, ...], age: int, launch: int) -> _Inst:
        """``if (e) then else otherwise``: ``e`` is sampled when the instance is launched."""
        nl = self.netlist
        zero = nl.const(False)
        then_launch = else_launch = zero
        if age == 0:
            condition = self._condition(prop.condition)
            then_launch = nl.and_((launch, condition))
            else_launch = nl.and_((launch, nl.not_(condition)))
        chosen = self._build(prop.then, (*path, 0), age, then_launch)
        parts = [chosen]
        nonvacuous = [self._branch_nonvacuous(prop.then, chosen)]
        vacuous = zero
        if prop.otherwise is not None:
            other = self._build(prop.otherwise, (*path, 1), age, else_launch)
            parts.append(other)
            nonvacuous.append(self._branch_nonvacuous(prop.otherwise, other))
        else:
            vacuous = else_launch  # without else, the attempt is vacuously true
        return _Inst(
            true=nl.or_((*(p.true for p in parts), vacuous)),
            false=nl.or_(p.false for p in parts),
            nonvacuous=nl.or_(nonvacuous),
            pending=nl.or_(p.pending for p in parts),
            live=nl.or_((*(p.live for p in parts), vacuous)),
            next=tuple(entry for p in parts for entry in p.next),
        )

    def _implication(
        self, prop: Implication, path: tuple[int, ...], age: int, launch: int
    ) -> _Inst:
        nl = self.netlist
        antecedent = self._antecedent(prop)
        run = self._run(antecedent.automaton, path, "ant", age, launch)
        trigger = run.accepting
        more = nl.or_(gate for _, gate in run.successors)
        consequent_life = self._life(prop.consequent)
        subs: list[_Inst] = []
        for offset in range(min(antecedent.last_end, age) + 1):
            if age - offset >= consequent_life:
                continue
            start = trigger if age == offset else nl.const(False)
            subs.append(self._build(prop.consequent, (*path, offset), age - offset, start))
        false = self._implication_false([s.false for s in subs])
        pending_subs = nl.or_(s.pending for s in subs)
        live = launch if age == 0 else nl.or_((*run.frontier, *(s.live for s in subs)))
        true = nl.and_((live, nl.not_(false), nl.not_(more), nl.not_(pending_subs)))
        keep = nl.not_(nl.or_((true, false)))
        nonvacuous = nl.or_(
            (
                self._state(path, "acc", 0, age),
                *(self._branch_nonvacuous(prop.consequent, s) for s in subs),
            )
        )
        next_entries: list[tuple[_Key, int]] = [
            ((path, "ant", target), nl.and_((successor, keep)))
            for target, successor in run.successors
        ]
        next_entries.append(((path, "acc", 0), nl.and_((nonvacuous, keep))))
        for sub in subs:
            next_entries.extend(self._killed(sub.next, keep))
        return _Inst(
            true=true,
            false=false,
            nonvacuous=nonvacuous,
            pending=nl.and_((keep, nl.or_((more, pending_subs)))),
            live=live,
            next=tuple(next_entries),
        )


def build_attempt_machine(
    directive: Directive,
    budgets: Budgets = DEFAULT_BUDGETS,
    *,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> AttemptMachine:
    """Build the attempt machine of ``directive`` (see the module docstring).

    The caller has validated the directive; its property is lowered here. The disable
    condition is registered first in the condition table.

    Raises:
        BudgetError: A resource of ``budgets`` is exceeded.
        ValueError: A sequence is unbounded (the caller's validation rules this out).
    """
    work = preflight(directive.property, limits)
    if directive.disable is not None:
        check_expressions((directive.disable,), limits, work=work)
    check_letter_budget(directive.property, budgets)
    lowered = replace(directive, property=lower(directive.property))
    table = ConditionTable()
    disable = table.identify(lowered.disable) if lowered.disable is not None else None
    root_life = life(lowered.property)
    construction = _Construction(lowered.property, table, budgets, work)
    netlist = construction.construct(root_life)
    conditions = tuple(table.conditions)
    return AttemptMachine(
        directive=lowered,
        conditions=conditions,
        past_depth=tuple(gated_samples(c) for c in conditions),
        disable=disable,
        life=root_life,
        netlist=netlist,
        limits=limits,
    )


def initial_attempt_state(machine: AttemptMachine) -> tuple[bool, ...]:
    """The all-zero register state before the first sample."""
    return (False,) * len(machine.netlist.registers)


def step(
    machine: AttemptMachine,
    state: tuple[bool, ...],
    elapsed: int,
    raw: Callable[[int], bool],
) -> tuple[tuple[bool, ...], AttemptEvents]:
    """Perform one sample. Conditions are gated exactly as
    :func:`sva2yosys.temporal.machine.step` (``raw`` is not called for a condition while
    ``elapsed < past_depth``). If the disable condition holds, the next state is all 0 and
    no other event is true (attempts starting at this sample are not launched). Otherwise
    the netlist is evaluated.

    Raises:
        MachineError: The overflow output is true (an internal error).
    """
    known: dict[int, bool] = {}

    def holds(condition: int) -> bool:
        if elapsed < machine.past_depth[condition]:
            return False
        if condition not in known:
            known[condition] = raw(condition)
        return known[condition]

    netlist = machine.netlist
    if machine.disable is not None and holds(machine.disable):
        idle = AgeEvent(False, False, False, False)
        return initial_attempt_state(machine), AttemptEvents(
            disabled=True, failure=False, success=False, ages=(idle,) * machine.life
        )
    values = evaluate(netlist, state, holds)
    if values[netlist.overflow]:
        raise MachineError("an attempt is still pending at the end of its life")
    ages = tuple(
        AgeEvent(values[age.true], values[age.false], values[age.nonvacuous], values[age.pending])
        for age in netlist.ages
    )
    events = AttemptEvents(
        disabled=False,
        failure=values[netlist.fail],
        success=values[netlist.success],
        ages=ages,
    )
    return next_state(netlist, values), events


def attempt_size(machine: AttemptMachine) -> AttemptSize:
    """Size statistics of ``machine``."""
    return AttemptSize(
        conditions=len(machine.conditions),
        life=machine.life,
        registers=len(machine.netlist.registers),
        gates=sum(isinstance(g, NNot | NAnd | NOr) for g in machine.netlist.gates),
    )
