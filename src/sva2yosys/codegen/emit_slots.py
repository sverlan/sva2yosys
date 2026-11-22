"""Emitter of the canonical machine (:class:`~sva2yosys.temporal.machine.Machine`).

A mechanical translation of :func:`sva2yosys.temporal.machine.step`, in one of the forms of
:mod:`sva2yosys.optimize.forms`.

- **Temporal state** (cleared asynchronously by the disable condition): the antecedent
  frontier ``ant_q`` and one frontier ``slotK_q`` per obligation age ``K``; bit ``i`` is
  letter ``i`` of the automaton. Only the letters a frontier can ever hold
  (``temporal.machine.antecedent_reach`` / ``slot_reach``) are registered (``<name>_r``);
  the other bits are the constant 0, so the state is exact and linear in the window
  length rather than quadratic.
- **Forms.** ``direct``: a packed reachable-letter pipeline for recognized chains
  and Boolean delay windows (``codegen.direct``), otherwise the dense slots.
  ``window`` and ``history``: the optimised forms (``docs/strategies.md``).
"""

from __future__ import annotations

from sva2yosys.codegen import direct, frontier
from sva2yosys.codegen.core import Shell
from sva2yosys.codegen.expressions import Expressions
from sva2yosys.optimize.forms import Form, scope, window
from sva2yosys.temporal.machine import Machine, slot_reach


def form_line(form: Form) -> str:
    """The ``// form:`` header comment of a monitor in ``form``."""
    return f"// form: {form.value} (exact at {scope(form).value})"


def temporal(shell: Shell, form: Form) -> list[str]:
    """The temporal state, ``success`` and ``fail`` of the canonical machine of ``shell``
    in ``form`` (which must apply to the machine)."""
    machine = shell.machine
    assert isinstance(machine, Machine)
    lines = [f"    // temporal state (form: {form.value}; cleared by disable iff)"]
    registers: list[tuple[str, str]] = []
    expressions = shell.expressions
    lines.extend(frontier.trigger(expressions, machine, registers))
    accepts: list[str] = []
    fails: list[str] = []
    if not machine.consequent.letters:
        fails.append("trigger")  # the consequent can never match
    else:
        match form:
            case Form.DIRECT:
                plan = direct.layout(machine)
                if plan is None:
                    lines.extend(_direct(expressions, machine, registers, accepts, fails))
                else:
                    lines.extend(direct.emit(expressions, machine, plan, registers, accepts, fails))
            case Form.WINDOW:
                lines.extend(_window(machine, registers, accepts, fails))
            case Form.HISTORY:
                lines.extend(_history_frontier(expressions, machine, registers, accepts, fails))
    lines.extend(shell.registers(registers))
    any_accept = expressions.disjunction(accepts)
    any_fail = expressions.disjunction(fails)
    if machine.negated:  # not s: a match fails, a dead obligation succeeds
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


def _window(
    machine: Machine, registers: list[tuple[str, str]], accepts: list[str], fails: list[str]
) -> list[str]:
    """``T |-> ##[low:high] b`` with the triggers of the last ``low`` samples and the
    age of the oldest obligation inside its window (``optimize.forms``). Exact up to
    and including the first failure."""
    found = window(machine)
    assert found is not None
    low, high, b = found.low, found.high, f"c{found.condition}"
    bits = high.bit_length()
    lines = [
        f"    logic [{low - 1}:0] win_t = '0;  // win_t[a - 1]: a trigger a samples ago",
        f"    logic [{bits - 1}:0] win_c = '0;  // age of the oldest obligation in its window",
        f"    logic [{bits - 1}:0] win_oldest;",
        f"    assign win_oldest = (win_c != {bits}'d0) ? win_c"
        f" : (win_t[{low - 1}] ? {bits}'d{low} : {bits}'d0);",
        "    logic win_accept, win_fail;",
        f"    assign win_accept = {b} & (win_oldest != {bits}'d0);",
        f"    assign win_fail = !{b} & (win_oldest == {bits}'d{high});",
    ]
    shifted = "trigger" if low == 1 else f"{{win_t[{low - 2}:0], trigger}}"
    registers.append(("win_t", shifted))
    registers.append(
        (
            "win_c",
            f"({b} || win_oldest == {bits}'d0 || win_oldest == {bits}'d{high})"
            f" ? {bits}'d0 : win_oldest + {bits}'d1",
        )
    )
    accepts.append("win_accept")
    fails.append("win_fail")
    return lines


def _history_frontier(
    expressions: Expressions,
    machine: Machine,
    registers: list[tuple[str, str]],
    accepts: list[str],
    fails: list[str],
) -> list[str]:
    """The history frontier: the triggers of the last ``slots`` samples and the value
    history of each condition the consequent reads; each age's frontier is the
    automaton run over that history (``optimize.forms``). Exact at every sample."""
    consequent = machine.consequent
    width = len(consequent.letters)
    slots = machine.slots
    read = sorted({g for letter in consequent.letters for g in letter.guards})
    lines = [f"    logic [{slots - 1}:0] his_t = '0;  // his_t[a - 1]: a trigger a samples ago"]
    registers.append(("his_t", "trigger" if slots == 1 else f"{{his_t[{slots - 2}:0], trigger}}"))
    for index in read:
        # Condition history: never cleared (disable clears the triggers instead).
        lines.append(
            f"    logic [{slots - 1}:0] his_c{index};  // [a - 1]: c{index}, a samples ago"
        )
        value = f"c{index}" if slots == 1 else f"{{his_c{index}[{slots - 2}:0], c{index}}}"
        lines.append(f"    always @(posedge clk) his_c{index} <= {value};")
    start = frontier.start(expressions, consequent)
    lines.extend(
        _age(expressions, machine, "age0", f"(trigger ? {start} : {width}'d0)", accepts, fails)
    )
    for age in range(1, slots + 1):
        # Replay the obligation triggered ``age`` samples ago up to the previous sample.
        value = f"(his_t[{age - 1}] ? {start} : {width}'d0)"
        for step in range(age):
            prefix = f"his{age}_{step}"
            ago = age - step  # the samples ago at which this step was taken

            def at(index: int, ago: int = ago) -> str:
                return f"his_c{index}[{ago - 1}]"

            lines.append(f"    logic [{width - 1}:0] {prefix}_frontier;")
            lines.append(f"    assign {prefix}_frontier = {value};")
            lines.extend(frontier.held(expressions, prefix, consequent, f"{prefix}_frontier", at))
            value = (
                f"({frontier.accepting(expressions, prefix, consequent)} ? {width}'d0"
                f" : {frontier.next_bits(expressions, prefix, consequent)})"
            )
        lines.extend(_age(expressions, machine, f"age{age}", value, accepts, fails))
    return lines
