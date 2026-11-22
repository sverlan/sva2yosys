"""Emitter of the per-attempt machine
(:class:`~sva2yosys.temporal.attempts.AttemptMachine`, ``docs/semantics.md`` §9.4).

Its temporal state is the machine's netlist emitted gate by gate: the registers are the bits
of the state vector ``st``, cleared by the disable condition.
"""

from __future__ import annotations

from sva2yosys.codegen.core import Shell
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.netlist import NAnd, NConst, NInput, NNot, NOr, NReg

FORM_LINE = (
    "// form: attempt (per-attempt netlist, docs/semantics.md section 9.4): "
    "fail exact at every sample; success exact when the cover rule holds "
    "(docs/semantics.md section 9.5)"
)
"""The ``// form:`` header comment of a per-attempt monitor."""


def temporal(shell: Shell) -> list[str]:
    """The per-attempt netlist: registers ``st``, one wire per gate in the cone of the
    outputs and of the next-state gates, the clear-on-disable update, the outputs."""
    machine = shell.machine
    assert isinstance(machine, AttemptMachine)
    netlist = machine.netlist
    lines = ["    // temporal state (form: attempt; cleared by disable iff)"]
    needed: set[int] = set()
    pending = [netlist.fail, netlist.success, *(r.next for r in netlist.registers)]
    while pending:
        gate_id = pending.pop()
        if gate_id in needed:
            continue
        needed.add(gate_id)
        match netlist.gates[gate_id]:
            case NNot(operand=operand):
                pending.append(operand)
            case NAnd(operands=operands) | NOr(operands=operands):
                pending.extend(operands)
            case NConst() | NInput() | NReg():
                pass

    def ref(gate_id: int) -> str:
        match netlist.gates[gate_id]:
            case NConst(value=value):
                return "1'b1" if value else "1'b0"
            case NInput(condition=condition):
                return f"c{condition}"
            case NReg(register=register):
                return f"st[{register}]"
            case NNot() | NAnd() | NOr():
                return f"n{gate_id}"

    registers = len(netlist.registers)
    if registers:
        lines.append(f"    logic [{registers - 1}:0] st = '0;")
        for index, register in enumerate(netlist.registers):
            path = ".".join(str(step) for step in register.path) or "root"
            lines.append(
                f"    // st[{index}]: age {register.age}, path {path}, "
                f"part {register.part}, index {register.index}"
            )
    for gate_id, gate in enumerate(netlist.gates):
        if gate_id not in needed:
            continue
        value: str
        match gate:
            case NNot(operand=operand):
                value = f"!{ref(operand)}"
            case NAnd(operands=operands):
                value = shell.expressions.conjunction([ref(o) for o in operands])
            case NOr(operands=operands):
                value = shell.expressions.disjunction([ref(o) for o in operands])
            case NConst() | NInput() | NReg():
                continue
        lines.append(f"    logic n{gate_id};")
        lines.append(f"    assign n{gate_id} = {value};")
    if registers:
        lines.append(f"    logic [{registers - 1}:0] st_d;")
        for index, register in enumerate(netlist.registers):
            lines.append(f"    assign st_d[{index}] = {ref(register.next)};")
        lines.extend(shell.registers([("st", "st_d")]))
    lines.append(f"    assign success = !dis & ({ref(netlist.success)});")
    lines.append(f"    assign fail = !dis & ({ref(netlist.fail)});")
    return lines
