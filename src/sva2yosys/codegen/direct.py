"""Compact DIRECT pipelines selected from the compiled consequent automaton.

Only a single guard-bearing chain or a Boolean delay window qualifies. A window
has one unguarded continuation letter and one terminal accepting guard at each
eligible age, with contiguous eligible ages. Other shapes retain the dense emitter.

Each age stores exactly its structurally reachable letters, in increasing index
order. Their packed registers form a pipeline: age a's successors become age a+1's
bits, unless that obligation accepts, when all its successors are cleared. A failed
guard refutes only that obligation, at that sample. Different ages never merge.
Success and failure equal the canonical machine at every sample, after failures too.

The stored bits equal canonical slot_reach counts, so state-size reporting and
strategy selection remain unchanged. For fixed-delay, Boolean repetition and
Boolean delay-window families, each age has at most two letters and a constant
number of transitions. State bits, assignments and operator/transition terms are
linear in the range; identifier lengths can make emitted bytes O(N log N).
Antecedents, sampled history, disable handling and non-DIRECT forms are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sva2yosys.codegen.expressions import Expressions
from sva2yosys.temporal.automaton import has_cycle
from sva2yosys.temporal.budgets import Work
from sva2yosys.temporal.machine import Machine


class DirectKind(Enum):
    CHAIN = "chain"
    WINDOW = "window"


@dataclass(frozen=True)
class DirectLayout:
    """A recognized pipeline; ages includes the unregistered trigger age zero.

    ages[a] lists consequent letter indices reachable in exactly a steps, sorted.
    Registered bits concatenate ages 1..slots, with each age in that order.
    Empty or nonqualifying automata have no layout and use the dense fallback.
    """

    kind: DirectKind
    ages: tuple[tuple[int, ...], ...]


def layout(machine: Machine, *, work: Work | None = None) -> DirectLayout | None:
    """Recognize a chain or contiguous Boolean window, independently of source syntax.

    Return None for empty, cyclic or other nondeterministic shapes. Do not impose a
    finite range limit, inspect directive kind, or change the machine.
    """
    automaton = machine.consequent
    if work is not None:
        work.charge(
            len(automaton.letters)
            + sum(len(letter.next) + len(letter.guards) for letter in automaton.letters)
        )
    if not automaton.letters or has_cycle(automaton):
        return None
    chain = len(automaton.start) == 1 and all(len(letter.next) <= 1 for letter in automaton.letters)
    if not chain:
        accepting = [letter for letter in automaton.letters if letter.accepting]
        if not accepting or any(letter.next for letter in accepting):
            return None
        if any(letter.guards != accepting[0].guards for letter in accepting):
            return None
        if any(letter.guards for letter in automaton.letters if not letter.accepting):
            return None
    ages: list[tuple[int, ...]] = []
    matches: list[int] = []
    current = tuple(sorted(automaton.start))
    for age in range(machine.slots + 1):
        if work is not None:
            work.charge(1 + len(current) + sum(len(automaton.letters[i].next) for i in current))
        if not chain:
            hits = sum(automaton.letters[index].accepting for index in current)
            if hits > 1 or len(current) - hits > 1:
                return None
            if hits:
                matches.append(age)
        ages.append(current)
        current = tuple(sorted({n for index in current for n in automaton.letters[index].next}))
    if not chain and (not matches or matches != list(range(matches[0], matches[-1] + 1))):
        return None
    return DirectLayout(DirectKind.CHAIN if chain else DirectKind.WINDOW, tuple(ages))


def emit(
    expressions: Expressions,
    machine: Machine,
    plan: DirectLayout,
    registers: list[tuple[str, str]],
    accepts: list[str],
    fails: list[str],
) -> list[str]:
    """Declare a recognized packed DIRECT pipeline and append its updates and events.

    All joins use Expressions. Registers are initialized to zero and registered via
    the caller's shell, including asynchronous disable. Events are per age before
    the caller applies negation and disable gating. No temporal state is shared
    between obligation ages. A zero-slot plan declares no zero-width register.
    """
    consequent = machine.consequent
    bits = sum(len(age) for age in plan.ages[1:])
    lines = [f"    // compact direct pipeline: {plan.kind.value}"]
    if bits:
        lines.append(f"    logic [{bits - 1}:0] pipe_q = '0;")
    updates: list[str] = []
    offset = 0
    for age, letters in enumerate(plan.ages):
        prefix = f"pipe{age}"
        active = (
            ["trigger"] * len(letters)
            if age == 0
            else [f"pipe_q[{offset + i}]" for i in range(len(letters))]
        )
        if age:
            offset += len(letters)
        held: list[str] = []
        for position, index in enumerate(letters):
            name = f"{prefix}_h{position}"
            held.append(name)
            guard = expressions.conjunction([f"c{g}" for g in consequent.letters[index].guards])
            lines.extend(
                [f"    logic {name};", f"    assign {name} = {active[position]} & ({guard});"]
            )
        accept = expressions.disjunction(
            [held[i] for i, index in enumerate(letters) if consequent.letters[index].accepting]
        )
        lines.extend(
            [
                f"    logic {prefix}_accept, {prefix}_fail;",
                f"    assign {prefix}_accept = {accept};",
            ]
        )
        following: list[str] = []
        if age < machine.slots:
            for position, target in enumerate(plan.ages[age + 1]):
                value = expressions.disjunction(
                    [
                        held[i]
                        for i, index in enumerate(letters)
                        if target in consequent.letters[index].next
                    ]
                )
                name = f"{prefix}_n{position}"
                following.append(name)
                lines.extend(
                    [
                        f"    logic {name};",
                        f"    assign {name} = !{prefix}_accept & ({value});",
                    ]
                )
        alive = expressions.disjunction(active)
        next_alive = expressions.disjunction(following)
        lines.append(f"    assign {prefix}_fail = ({alive}) & !{prefix}_accept & !({next_alive});")
        accepts.append(f"{prefix}_accept")
        fails.append(f"{prefix}_fail")
        updates.extend(following)
    assert len(updates) == bits
    if bits:
        registers.append(("pipe_q", expressions.vector(updates)))
    return lines
