"""Letter-frontier helpers shared by the emitters of the automaton-based machines.

A frontier is a bit vector with one bit per letter of an
:class:`~sva2yosys.temporal.automaton.Automaton`. The canonical machine
(:mod:`~sva2yosys.codegen.emit_slots`) and the configuration-set machine
(:mod:`~sva2yosys.codegen.emit_configurations`) both run the antecedent automaton this way
(:func:`trigger`); the canonical machine also runs the consequent per obligation age.
"""

from __future__ import annotations

from collections.abc import Callable

from sva2yosys.codegen.core import AnyMachine
from sva2yosys.codegen.expressions import Expressions
from sva2yosys.temporal.automaton import Automaton
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.machine import Machine, successor_reach


def held(
    expressions: Expressions,
    prefix: str,
    automaton: Automaton,
    frontier: str,
    condition: Callable[[int], str] = lambda index: f"c{index}",
) -> list[str]:
    """Declare ``<prefix>_held`` = frontier letters whose guards hold; ``condition``
    names the value of a condition (default: at the current sample)."""
    width = len(automaton.letters)
    guards = [
        expressions.conjunction([condition(g) for g in letter.guards])
        for letter in automaton.letters
    ]
    return [
        f"    logic [{width - 1}:0] {prefix}_held;",
        f"    assign {prefix}_held = {frontier} & {expressions.vector(guards)};",
    ]


def next_bits(expressions: Expressions, prefix: str, automaton: Automaton) -> str:
    """The successor frontier of the held letters ``<prefix>_held``."""
    bits = []
    for sources in automaton.incoming:
        bits.append(expressions.disjunction([f"{prefix}_held[{i}]" for i in sources]))
    return expressions.vector(bits)


def accepting(expressions: Expressions, prefix: str, automaton: Automaton) -> str:
    """Whether some held letter ``<prefix>_held`` is accepting."""
    bits = [f"{prefix}_held[{i}]" for i, letter in enumerate(automaton.letters) if letter.accepting]
    return "(" + expressions.disjunction(bits) + ")" if bits else "1'b0"


def start(expressions: Expressions, automaton: Automaton) -> str:
    """The start letters of ``automaton`` as a constant frontier."""
    width = len(automaton.letters)
    bits = ["1'b1" if i in automaton.start else "1'b0" for i in range(width)]
    return expressions.vector(bits)


def state(expressions: Expressions, name: str, width: int, keep: list[int]) -> list[str]:
    """Declare the frontier ``name`` (``width`` bits) whose register stores only the
    letters ``keep`` (:func:`~sva2yosys.temporal.machine.slot_reach`,
    :func:`~sva2yosys.temporal.machine.antecedent_reach`); the others are constant 0."""
    lines = [f"    logic [{width - 1}:0] {name};"]
    if keep:
        lines.append(f"    logic [{len(keep) - 1}:0] {name}_r = '0;")
    bits = [f"{name}_r[{keep.index(i)}]" if i in keep else "1'b0" for i in range(width)]
    lines.append(f"    assign {name} = {expressions.vector(bits)};  // reachable letters: {keep}")
    return lines


def update(
    expressions: Expressions,
    name: str,
    width: int,
    keep: list[int],
    value: str,
    registers: list[tuple[str, str]],
) -> list[str]:
    """Register the ``keep`` bits of ``value`` as the next state of ``name``."""
    if not keep:
        return []
    registers.append((f"{name}_r", expressions.vector([f"{name}_d[{i}]" for i in keep])))
    return [f"    logic [{width - 1}:0] {name}_d;", f"    assign {name}_d = {value};"]


def trigger(
    expressions: Expressions, machine: AnyMachine, registers: list[tuple[str, str]]
) -> list[str]:
    """Declare ``trigger``: an antecedent match ends at the current sample."""
    assert isinstance(machine, Machine | ConfigMachine)
    antecedent = machine.antecedent
    if antecedent is None:
        return ["    logic trigger;", "    assign trigger = 1'b1;  // every sample"]
    if not antecedent.letters:
        return ["    logic trigger;", "    assign trigger = 1'b0;  // never matches"]
    width = len(antecedent.letters)
    keep = sorted(successor_reach(antecedent))
    lines = state(expressions, "ant_q", width, keep)
    lines.extend(
        held(expressions, "ant", antecedent, f"(ant_q | {start(expressions, antecedent)})")
    )
    lines.append("    logic trigger;")
    lines.append(f"    assign trigger = {accepting(expressions, 'ant', antecedent)};")
    lines.extend(
        update(
            expressions, "ant_q", width, keep, next_bits(expressions, "ant", antecedent), registers
        )
    )
    return lines
