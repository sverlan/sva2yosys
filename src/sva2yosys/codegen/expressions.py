"""Bounded local SystemVerilog expressions, shared by every monitor emitter.

Every Boolean operand is one bit. Concatenation operands carry their exact positive
width and are ordered most-significant first. Inputs are named references or the
emitters' fixed-depth fragments, never unbounded inline expressions. Joins preserve
operand order and use at most MAX_FANIN operands per expression; larger joins introduce
unsigned, exactly sized combinational wires, without adding clocked state. Empty OR
is zero, empty AND is one, and an empty bit vector is the one-bit zero. Concatenation
requires at least one operand; a singleton retains its width and bit pattern.

With these helpers and the fixed-depth shell/emitter fragments, emitted local syntax
has depth at most MAX_LOCAL_DEPTH, independent of sequence ranges. This bounds syntax,
not combinational graph depth, accepted ranges or resource consumption.
"""

from __future__ import annotations

from collections.abc import Sequence

MAX_FANIN = 8
"""Maximum number of operands in an emitted join or concatenation."""

MAX_LOCAL_DEPTH = 32
"""Upper bound on local expression syntax depth, including fixed emitter wrappers."""


class Expressions:
    """One module's bounded expressions and their intermediate declarations.

    ``declarations`` must precede every use, and ``assignments`` must follow the
    declarations of their operands. ``lines`` is an inspection snapshot for tests;
    emitters must use the two lists separately. Names ``<prefix><N>`` are reserved for
    this builder. Each module uses one builder, plus a differently prefixed builder
    inside its optional liveness define block.
    """

    def __init__(self, prefix: str = "join") -> None:
        self._declarations: list[str] = []
        self._assignments: list[str] = []
        self._prefix = prefix
        self._count = 0

    @property
    def declarations(self) -> list[str]:
        """Intermediate declarations, emitted before all uses in the same scope."""
        return self._declarations

    @property
    def assignments(self) -> list[str]:
        """Topologically ordered assignments, after all operand declarations."""
        return self._assignments

    @property
    def lines(self) -> list[str]:
        """Test inspection only; emitters must place declarations and assignments separately."""
        return [*self.declarations, *self.assignments]

    def _wire(self, value: str, width: int) -> str:
        name = f"{self._prefix}{self._count}"
        self._count += 1
        self._declarations.append(f"    logic [{width - 1}:0] {name};")
        self._assignments.append(f"    assign {name} = {value};")
        return name

    def _boolean(self, operands: Sequence[str], operator: str, identity: str) -> str:
        pending = list(operands)
        if not pending:
            return identity
        while len(pending) > MAX_FANIN:
            pending = [
                self._wire(operator.join(pending[i : i + MAX_FANIN]), 1)
                for i in range(0, len(pending), MAX_FANIN)
            ]
        return operator.join(pending)

    def conjunction(self, operands: Sequence[str]) -> str:
        """The bitwise AND of one-bit operands, or one for an empty sequence."""
        return self._boolean(operands, " & ", "1'b1")

    def disjunction(self, operands: Sequence[str]) -> str:
        """The bitwise OR of one-bit operands, or zero for an empty sequence."""
        return self._boolean(operands, " | ", "1'b0")

    def concat(self, operands: Sequence[tuple[str, int]]) -> str:
        """Concatenate nonempty, positive-width operands, most-significant first.

        The result is unsigned, including a singleton, as in a SystemVerilog
        concatenation. Widths and bit order are preserved across every chunk.
        Raises ValueError for empty operands or a nonpositive width.
        """
        if not operands or any(width <= 0 for _, width in operands):
            raise ValueError("concatenation requires nonempty positive-width operands")
        pending = list(operands)
        while len(pending) > MAX_FANIN:
            following: list[tuple[str, int]] = []
            for i in range(0, len(pending), MAX_FANIN):
                chunk = pending[i : i + MAX_FANIN]
                width = sum(width for _, width in chunk)
                value = "{" + ", ".join(value for value, _ in chunk) + "}"
                following.append((self._wire(value, width), width))
            pending = following
        return "{" + ", ".join(value for value, _ in pending) + "}"

    def vector(self, bits: Sequence[str]) -> str:
        """Concatenate one-bit operands, last element most significant; empty is zero."""
        return self.concat([(bit, 1) for bit in reversed(bits)]) if bits else "1'b0"
