"""Frozen dense DIRECT oracle (the emitter before the compact layout).

Only tests import this module. The dense per-age lowering is frozen independently
of the compact emitter; common typed machines, shell and unchanged frontier helpers
supply the input and module wrapper. Events equal the canonical Machine.step at
every sample. CHECK can be disabled by the miter to observe both outputs freely.
"""

from sva2yosys.codegen import frontier
from sva2yosys.codegen.core import Shell
from sva2yosys.codegen.expressions import Expressions
from sva2yosys.temporal.machine import Machine, slot_reach


def module(machine: Machine, name: str) -> str:
    """Emit the frozen 6a dense DIRECT monitor, independent of compact selection."""
    return Shell(machine, name).module("// form: direct (exact at every sample)", _temporal)


def _temporal(shell: Shell) -> list[str]:
    machine = shell.machine
    assert isinstance(machine, Machine)
    lines = ["    // temporal state (form: direct; cleared by disable iff)"]
    registers: list[tuple[str, str]] = []
    expressions = shell.expressions
    lines.extend(frontier.trigger(expressions, machine, registers))
    accepts: list[str] = []
    fails: list[str] = []
    if not machine.consequent.letters:
        fails.append("trigger")
    else:
        lines.extend(_direct(expressions, machine, registers, accepts, fails))
    lines.extend(shell.registers(registers))
    any_accept = expressions.disjunction(accepts)
    any_fail = expressions.disjunction(fails)
    if machine.negated:
        any_accept, any_fail = any_fail, any_accept
    lines.append(f"    assign success = !dis & ({any_accept});")
    lines.append(f"    assign fail = !dis & ({any_fail});")
    return lines


def _age(
    expressions: Expressions,
    machine: Machine,
    prefix: str,
    frontier_value: str,
    accepts: list[str],
    fails: list[str],
) -> list[str]:
    """The events of one obligation age at the current sample (``step``)."""
    consequent = machine.consequent
    width = len(consequent.letters)
    lines = [
        f"    logic [{width - 1}:0] {prefix}_frontier;",
        f"    assign {prefix}_frontier = {frontier_value};",
    ]
    lines.extend(frontier.held(expressions, prefix, consequent, f"{prefix}_frontier"))
    lines.append(f"    logic {prefix}_accept, {prefix}_fail;")
    lines.append(f"    logic [{width - 1}:0] {prefix}_next;")
    lines.append(
        f"    assign {prefix}_accept = {frontier.accepting(expressions, prefix, consequent)};"
    )
    lines.append(
        f"    assign {prefix}_next = {frontier.next_bits(expressions, prefix, consequent)};"
    )
    lines.append(
        f"    assign {prefix}_fail = (|{prefix}_frontier) & !{prefix}_accept & !(|{prefix}_next);"
    )
    accepts.append(f"{prefix}_accept")
    fails.append(f"{prefix}_fail")
    return lines


def _direct(
    expressions: Expressions,
    machine: Machine,
    registers: list[tuple[str, str]],
    accepts: list[str],
    fails: list[str],
) -> list[str]:
    """The canonical per-age slots, positions pruned."""
    consequent = machine.consequent
    width = len(consequent.letters)
    reach = slot_reach(machine)
    lines: list[str] = []
    for slot in range(1, machine.slots + 1):
        lines.extend(frontier.state(expressions, f"slot{slot}_q", width, sorted(reach[slot - 1])))
    for age in range(machine.slots + 1):
        value = (
            f"(trigger ? {frontier.start(expressions, consequent)} : {width}'d0)"
            if age == 0
            else f"slot{age}_q"
        )
        lines.extend(_age(expressions, machine, f"age{age}", value, accepts, fails))
        if age < machine.slots:
            following = f"age{age}_accept ? {width}'d0 : age{age}_next"
            keep = sorted(reach[age])
            lines.extend(
                frontier.update(expressions, f"slot{age + 1}_q", width, keep, following, registers)
            )
    return lines
