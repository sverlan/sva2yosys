"""A Boolean netlist with registers: the structure of the per-attempt machine.

The netlist knows nothing about SVA. It is a topologically ordered list of gates
(:data:`Gate`), a list of :class:`Register` (initial value 0, cleared asynchronously by
``disable iff``) and the output gates (:class:`Netlist`). ``docs/semantics.md`` §9.4
describes how the attempt machine uses it.

:class:`NetlistBuilder` builds one with hash-consing and constant folding, so structurally
equal subterms share one gate and the netlist stays small:

- ``and_``: a constant 0 operand gives 0; constant 1 operands are dropped; nested ANDs are
  flattened; duplicates are removed; ``x & !x`` gives 0; no operand gives 1; a single
  operand is returned itself; otherwise the operands are sorted.
- ``or_``: the dual (constant 1 gives 1; constant 0 dropped; ``x | !x`` gives 1; no
  operand gives 0).
- ``not_``: ``!!x`` gives ``x``; the negation of a constant is a constant.
- Equal gates (same class, same operands) are the same gate id.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class NConst:
    """A constant."""

    value: bool


@dataclass(frozen=True)
class NInput:
    """The value of condition-table entry ``condition`` at this sample, already gated by
    its ``past_depth``."""

    condition: int


@dataclass(frozen=True)
class NReg:
    """The value of register ``register`` before this sample."""

    register: int


@dataclass(frozen=True)
class NNot:
    """Negation of gate ``operand``."""

    operand: int


@dataclass(frozen=True)
class NAnd:
    """Conjunction; ``operands`` has at least 2 gate ids, sorted and distinct."""

    operands: tuple[int, ...]


@dataclass(frozen=True)
class NOr:
    """Disjunction; ``operands`` has at least 2 gate ids, sorted and distinct."""

    operands: tuple[int, ...]


type Gate = NConst | NInput | NReg | NNot | NAnd | NOr


@dataclass(frozen=True)
class Register:
    """A state bit: initial value 0, cleared asynchronously when ``disable iff`` holds.

    Attributes:
        next: Gate id of its value before the next sample.
        age: The root age at which the register is read (>= 1).
        path: Instance path in the property tree (``docs/semantics.md`` §9.4).
        part: ``"letter"``, ``"ant"``, ``"done"``, ``"nv"`` or ``"acc"``.
        index: Letter or operand index; 0 otherwise.
    """

    next: int
    age: int
    path: tuple[int, ...]
    part: str
    index: int


@dataclass(frozen=True)
class AgeOutputs:
    """Gate ids of the outcome of the root attempt of one root age at this sample."""

    true: int
    false: int
    nonvacuous: int
    pending: int


@dataclass(frozen=True)
class Netlist:
    """A finished netlist.

    Invariants: gate ``i`` references only gates ``< i`` (topological order); register
    indices in :class:`NReg` are valid; every register is in the cone of the outputs.

    Attributes:
        gates: The gates.
        registers: The registers.
        ages: Outputs per root age ``0 … life - 1``.
        fail: Some root attempt is decided false at this sample.
        success: Some root attempt is decided true and nonvacuous at this sample.
        overflow: Some attempt is still pending at the end of its life (a structural
            self-check: must never be true).
    """

    gates: tuple[Gate, ...]
    registers: tuple[Register, ...]
    ages: tuple[AgeOutputs, ...]
    fail: int
    success: int
    overflow: int


class NetlistBuilder:
    """Mutable construction helper (see the module docstring for the folding rules).

    Args:
        on_gate: Called once for each newly created gate (budget charging).
        on_register: Called once for each new register.
        on_work: Charged before operand visits and allocations, including cache hits,
            discarded operands and constant folding.

    A register's value gate (:meth:`register_value`) is created after
    :meth:`add_register`, which takes the already built ``next`` gate.
    """

    def __init__(
        self,
        on_gate: Callable[[], None] | None = None,
        on_register: Callable[[], None] | None = None,
        on_work: Callable[[int], None] | None = None,
    ) -> None:
        self._on_gate = on_gate
        self._on_register = on_register
        self._on_work = on_work
        self._gates: list[Gate] = []
        self._ids: dict[Gate, int] = {}
        self._registers: list[Register] = []

    def _charge(self, amount: int = 1) -> None:
        if self._on_work is not None:
            self._on_work(amount)

    @property
    def gate_count(self) -> int:
        """The number of gates created so far."""
        return len(self._gates)

    def _make(self, gate: Gate) -> int:
        self._charge(1 + len(gate.operands) if isinstance(gate, NAnd | NOr) else 1)
        known = self._ids.get(gate)
        if known is not None:
            return known
        if self._on_gate is not None:
            self._on_gate()
        self._gates.append(gate)
        self._ids[gate] = len(self._gates) - 1
        return len(self._gates) - 1

    def _is_const(self, gate: int, value: bool) -> bool:
        node = self._gates[gate]
        return isinstance(node, NConst) and node.value is value

    def const(self, value: bool) -> int:
        """Gate id of a constant."""
        return self._make(NConst(value))

    def input(self, condition: int) -> int:
        """Gate id of condition ``condition``."""
        return self._make(NInput(condition))

    def register_value(self, register: int) -> int:
        """Gate id of the value of register ``register`` before the sample."""
        return self._make(NReg(register))

    def not_(self, a: int) -> int:
        """Gate id of ``!a``."""
        node = self._gates[a]
        match node:
            case NConst(value=value):
                return self.const(not value)
            case NNot(operand=operand):
                return operand
            case NInput() | NReg() | NAnd() | NOr():
                return self._make(NNot(a))

    def and_(self, operands: Iterable[int]) -> int:
        """Gate id of the conjunction of ``operands`` (folded)."""
        return self._combine(operands, conjunction=True)

    def or_(self, operands: Iterable[int]) -> int:
        """Gate id of the disjunction of ``operands`` (folded)."""
        return self._combine(operands, conjunction=False)

    def _combine(self, operands: Iterable[int], *, conjunction: bool) -> int:
        """The shared folding of ``and_`` (``conjunction``) and ``or_``."""
        collected: set[int] = set()
        pending: list[int] = []
        for operand in operands:
            self._charge()
            pending.append(operand)
        while pending:
            self._charge()
            operand = pending.pop()
            node = self._gates[operand]
            if self._is_const(operand, conjunction):
                continue  # the neutral element
            if self._is_const(operand, not conjunction):
                return self.const(not conjunction)  # the absorbing element
            same_kind = isinstance(node, NAnd) if conjunction else isinstance(node, NOr)
            if same_kind and isinstance(node, NAnd | NOr):
                self._charge(len(node.operands))
                pending.extend(node.operands)
            else:
                collected.add(operand)
        self._charge(len(collected))
        for operand in collected:
            node = self._gates[operand]
            if isinstance(node, NNot) and node.operand in collected:
                return self.const(not conjunction)  # x & !x, x | !x
        if not collected:
            return self.const(conjunction)
        if len(collected) == 1:
            return next(iter(collected))
        ordered = tuple(sorted(collected))
        return self._make(NAnd(ordered) if conjunction else NOr(ordered))

    def add_register(
        self, next: int, age: int, path: tuple[int, ...], part: str, index: int
    ) -> int:
        """Add a register whose next value is gate ``next``; returns its index."""
        self._charge()
        if self._on_register is not None:
            self._on_register()
        self._registers.append(Register(next, age, path, part, index))
        return len(self._registers) - 1

    def finish(
        self, ages: tuple[AgeOutputs, ...], fail: int, success: int, overflow: int
    ) -> Netlist:
        """The netlist with the given outputs. Registers outside the cone of the outputs
        (transitively through the registers' ``next`` gates) are dropped, then registers
        and gates are renumbered, keeping topological order."""
        roots = [fail, success, overflow]
        for age in ages:
            roots.extend((age.true, age.false, age.nonvacuous, age.pending))
        kept_gates: set[int] = set()
        kept_registers: set[int] = set()
        todo = roots
        while todo:
            self._charge()
            index = todo.pop()
            if index in kept_gates:
                continue
            kept_gates.add(index)
            match self._gates[index]:
                case NReg(register=register):
                    if register not in kept_registers:
                        kept_registers.add(register)
                        todo.append(self._registers[register].next)
                case NNot(operand=operand):
                    todo.append(operand)
                case NAnd(operands=operands) | NOr(operands=operands):
                    self._charge(len(operands))
                    todo.extend(operands)
                case NConst() | NInput():
                    pass
        gate_number = {old: new for new, old in enumerate(sorted(kept_gates))}
        register_number = {old: new for new, old in enumerate(sorted(kept_registers))}
        gates: list[Gate] = []
        for old in sorted(kept_gates):
            self._charge()
            match self._gates[old]:
                case NReg(register=register):
                    gates.append(NReg(register_number[register]))
                case NNot(operand=operand):
                    gates.append(NNot(gate_number[operand]))
                case NAnd(operands=operands):
                    gates.append(NAnd(tuple(gate_number[o] for o in operands)))
                case NOr(operands=operands):
                    gates.append(NOr(tuple(gate_number[o] for o in operands)))
                case NConst() | NInput() as plain:
                    gates.append(plain)
        registers = tuple(
            Register(
                gate_number[self._registers[old].next],
                self._registers[old].age,
                self._registers[old].path,
                self._registers[old].part,
                self._registers[old].index,
            )
            for old in sorted(kept_registers)
        )
        return Netlist(
            gates=tuple(gates),
            registers=registers,
            ages=tuple(
                AgeOutputs(
                    gate_number[a.true],
                    gate_number[a.false],
                    gate_number[a.nonvacuous],
                    gate_number[a.pending],
                )
                for a in ages
            ),
            fail=gate_number[fail],
            success=gate_number[success],
            overflow=gate_number[overflow],
        )


def evaluate(
    netlist: Netlist, state: tuple[bool, ...], inputs: Callable[[int], bool]
) -> tuple[bool, ...]:
    """The value of every gate given the register values ``state`` (one per register)
    and the condition values ``inputs``, which is called at most once per
    :class:`NInput` gate."""
    values: list[bool] = []
    for gate in netlist.gates:
        match gate:
            case NConst(value=value):
                values.append(value)
            case NInput(condition=condition):
                values.append(inputs(condition))
            case NReg(register=register):
                values.append(state[register])
            case NNot(operand=operand):
                values.append(not values[operand])
            case NAnd(operands=operands):
                values.append(all(values[o] for o in operands))
            case NOr(operands=operands):
                values.append(any(values[o] for o in operands))
    return tuple(values)


def next_state(netlist: Netlist, values: tuple[bool, ...]) -> tuple[bool, ...]:
    """The register values after the sample, from the gate values of :func:`evaluate`."""
    return tuple(values[register.next] for register in netlist.registers)
