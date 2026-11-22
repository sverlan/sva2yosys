"""Guard selected-layout emission before allocating RTL or replay frontiers.

Emission units conservatively allow for scheduled letter/guard/successor references,
not exact RTL tokens or Yosys gates.
Compiler work separately includes frontier visits and list-membership probes.
Repeated dense/HISTORY evaluations are charged by closed-form multiplication. Compact
plans are charged only for their reachable per-age letters. The counters are policy,
not an execution-time or peak-memory guarantee.
"""

from sva2yosys.codegen import direct
from sva2yosys.codegen.core import AnyMachine
from sva2yosys.optimize.forms import Form, state_bits
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.automaton import Automaton
from sva2yosys.temporal.budgets import BudgetError, ResourceLimits, check_expressions, preflight
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.liveness import Liveness
from sva2yosys.temporal.machine import Machine, check_resources, slot_reach, successor_reach
from sva2yosys.temporal.netlist import NAnd, NOr


def check_emission(machine: AnyMachine, form: Form, limits: ResourceLimits) -> int:
    """Check the written IR, actual machine and selected form; return emitted references.

    BudgetError names the exhausted resource. Conditions/history of manually supplied
    machines are checked independently of their source directive. Explicit limits are
    used throughout planning, so no intermediate helper reverts to a default policy.
    """
    work = preflight(machine.directive.property, limits)
    check_expressions(machine.conditions, limits, work=work)

    def frontier(automaton: Automaton, repetitions: int = 1) -> None:
        width = len(automaton.letters)
        work.charge(width)
        edges = sum(len(letter.next) for letter in automaton.letters)
        guards = sum(len(letter.guards) for letter in automaton.letters)
        # Cached ascending incoming edges avoid a full source scan per target.
        work.charge(repetitions * (width + edges))
        work.emit(repetitions * (4 * width + guards + edges + 8))

    if isinstance(machine, AttemptMachine):
        work.charge(len(machine.netlist.gates) + len(machine.netlist.registers))
        for gate in machine.netlist.gates:
            operands = len(gate.operands) if isinstance(gate, NAnd | NOr) else 1
            work.charge(operands)
            work.emit(operands + 1)
        work.emit(2 * len(machine.netlist.registers))
        return work.emitted

    if isinstance(machine, Machine):
        check_resources(machine, work)
    else:
        letter_count = len(machine.consequent.letters)
        if machine.antecedent is not None:
            letter_count += len(machine.antecedent.letters)
        if letter_count > limits.expansion:
            raise BudgetError(f"the machine needs more than {limits.expansion} expanded letters")
        work.charge(len(machine.configurations))
        if len(machine.configurations) > limits.expansion:
            raise BudgetError(f"the machine exceeds the {limits.expansion} expansion budget")
    if machine.antecedent is not None:
        frontier(machine.antecedent)
        width = len(machine.antecedent.letters)
        keep = successor_reach(machine.antecedent)
        work.charge(2 * width * len(keep) + width * len(machine.antecedent.start))
        work.emit(width + 2 * len(keep))

    if isinstance(machine, ConfigMachine):
        consequent = machine.consequent
        work.charge(len(consequent.letters))
        for configuration in machine.configurations:
            work.charge(len(configuration))
            reached: set[int] = set()
            for index in configuration:
                letter = consequent.letters[index]
                work.charge(len(letter.guards) + len(letter.next) + 1)
                work.emit(4 + len(letter.guards) + len(letter.next))
                if not letter.accepting:
                    reached.update(letter.next)
            for target in machine.configurations:
                work.charge(len(target))
                if target <= reached:
                    work.charge(len(reached))
                    work.emit(len(reached) + 1)
            work.emit(8 + len(reached))
        if machine.liveness is Liveness.WITNESS:
            frontier(consequent)
        return work.emitted

    if not machine.consequent.letters:
        work.emit(4)
        return work.emitted

    count = state_bits(machine, form)
    if count is not None and count > limits.state_bits:
        raise BudgetError(f"the monitor needs more than {limits.state_bits} temporal state bits")
    match form:
        case Form.DIRECT:
            plan = direct.layout(machine, work=work)
            if plan is not None:
                for age, letters in enumerate(plan.ages):
                    work.charge(len(letters))
                    successors = sum(len(machine.consequent.letters[i].next) for i in letters)
                    guards = sum(len(machine.consequent.letters[i].guards) for i in letters)
                    following = len(plan.ages[age + 1]) if age < machine.slots else 0
                    work.charge(following * (len(letters) + successors))
                    work.emit(4 * len(letters) + guards + successors + 4 * following + 8)
            else:
                frontier(machine.consequent, machine.slots + 1)
                reach = slot_reach(machine)
                width = len(machine.consequent.letters)
                work.charge(2 * width * sum(len(age) for age in reach))
                work.emit(2 * sum(len(age) for age in reach))
        case Form.HISTORY:
            repeats = machine.slots + 1 + machine.slots * (machine.slots + 1) // 2
            frontier(machine.consequent, repeats)
            work.emit(machine.slots * (1 + len(machine.conditions)))
        case Form.WINDOW:
            work.emit(32 + machine.slots.bit_length())
    return work.emitted
