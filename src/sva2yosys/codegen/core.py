"""The part of monitor generation shared by every machine representation.

The module shell of a monitor (``docs/architecture.md``), independent of how the temporal
state is represented:

- **Ports.** ``clk``, one input per design signal the conditions read (``i0``, ``i1``,
  … in order of first use; a comment gives each hierarchical path), and the outputs
  ``fail`` and ``success`` (the machine's events of the current sample, already
  suppressed while disabled).
- **Expressions.** One wire per IR node, declared with the node's type
  (``logic [signed] [w-1:0]``), so SystemVerilog's context-dependent sizing never
  changes a value: every operand already has the IR's type. Structurally equal nodes
  share a wire.
- **History** (never cleared by ``disable iff``): ``valid`` is 0 at the first sample and 1
  afterwards; each sampled-value function has a register holding its operand's previous
  sample. A history register has no initial value (formally unconstrained), so the
  first-sample results never depend on it: ``$rose``/``$fell``/``$stable``/``$changed``
  use ``valid``. A condition gated for ``d`` samples (``past_depth`` of the machine) is
  ANDed with ``valid`` (``d = 1``) or with ``seen >= d``, ``seen`` counting samples up to
  the largest ``d``. ``$past(e, n)`` is a chain of ``n`` history registers.
  ``FirstSample`` (the enabling condition of an ``initial`` assertion) is ``!valid``; the
  bit is emitted for every property, so no other use of ``valid`` is needed.
- **Checks** (``multiclock off`` style, ``docs/semantics.md`` §4): ``always @*`` with
  ``assert (!fail)`` for assert, ``assume (!fail)`` for assume, ``cover (success)`` for
  cover, inside ``if (CHECK)``. The parameter ``CHECK`` (default 1) removes the check
  statement when it is 0, leaving a monitor that only drives ``fail`` and ``success``.
  That is needed by the ``--equivalence`` jobs (the miter compares the outputs of two
  monitors, neither of which may assert), by the documented cover confirmation and by the
  trace-table tests, which compare the outputs with the tables (``docs/monitors.md``).

The temporal state is the business of one emitter per machine representation
(:mod:`~sva2yosys.codegen.emit_slots`, :mod:`~sva2yosys.codegen.emit_configurations`,
:mod:`~sva2yosys.codegen.emit_attempts`). The interface between them and this module is
:class:`Shell`: an emitter is a function ``Shell -> list[str]`` that declares its state
after the shell's conditions ``c<i>``, ``valid`` and ``dis`` exist and drives ``fail`` and
``success``; it registers state through :meth:`Shell.registers`, which clears it
asynchronously by the disable condition.
"""

from __future__ import annotations

from collections.abc import Callable

from sva2yosys.codegen.expressions import Expressions
from sva2yosys.codegen.render import fold_whitespace, render_expr
from sva2yosys.ir.directive import DirectiveKind
from sva2yosys.ir.expr import (
    Binary,
    BinaryOp,
    BitSelect,
    Concat,
    Conditional,
    Const,
    Convert,
    Expr,
    FirstSample,
    RangeSelect,
    Replicate,
    Sampled,
    SampledFunction,
    Signal,
    Unary,
    UnaryOp,
    ValueType,
)
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.machine import Machine

type AnyMachine = Machine | AttemptMachine | ConfigMachine
"""What a monitor is generated from."""

type Emitter = Callable[[Shell], list[str]]
"""The temporal part of a monitor: the lines declaring its state and driving ``fail`` and
``success``."""

_UNARY = {
    UnaryOp.PLUS: "+",
    UnaryOp.MINUS: "-",
    UnaryOp.BITWISE_NOT: "~",
    UnaryOp.LOGICAL_NOT: "!",
    UnaryOp.REDUCE_AND: "&",
    UnaryOp.REDUCE_OR: "|",
    UnaryOp.REDUCE_XOR: "^",
    UnaryOp.REDUCE_NAND: "~&",
    UnaryOp.REDUCE_NOR: "~|",
    UnaryOp.REDUCE_XNOR: "~^",
}

_BINARY = {
    BinaryOp.ADD: "+",
    BinaryOp.SUBTRACT: "-",
    BinaryOp.MULTIPLY: "*",
    BinaryOp.DIVIDE: "/",
    BinaryOp.MOD: "%",
    BinaryOp.BITWISE_AND: "&",
    BinaryOp.BITWISE_OR: "|",
    BinaryOp.BITWISE_XOR: "^",
    BinaryOp.BITWISE_XNOR: "~^",
    BinaryOp.EQUALITY: "==",
    BinaryOp.INEQUALITY: "!=",
    BinaryOp.CASE_EQUALITY: "==",  # two-state model
    BinaryOp.CASE_INEQUALITY: "!=",
    BinaryOp.LESS_THAN: "<",
    BinaryOp.LESS_THAN_EQUAL: "<=",
    BinaryOp.GREATER_THAN: ">",
    BinaryOp.GREATER_THAN_EQUAL: ">=",
    BinaryOp.LOGICAL_AND: "&&",
    BinaryOp.LOGICAL_OR: "||",
    BinaryOp.LOGICAL_SHIFT_LEFT: "<<",
    BinaryOp.LOGICAL_SHIFT_RIGHT: ">>",
    BinaryOp.ARITHMETIC_SHIFT_LEFT: "<<<",
    BinaryOp.ARITHMETIC_SHIFT_RIGHT: ">>>",
}


def signals(machine: AnyMachine) -> list[tuple[str, ValueType]]:
    """The design signals the conditions of ``machine`` read, in order of first use."""
    paths: list[tuple[str, ValueType]] = []

    def visit(expr: Expr) -> None:
        match expr:
            case Signal(path=path, type=value_type):
                if all(path != known for known, _ in paths):
                    paths.append((path, value_type))
            case Const() | FirstSample():
                pass
            case Unary(operand=operand) | Convert(operand=operand) | Sampled(operand=operand):
                visit(operand)
            case Binary(left=left, right=right):
                visit(left)
                visit(right)
            case Conditional(condition=condition, then=then, otherwise=otherwise):
                for part in (condition, then, otherwise):
                    visit(part)
            case BitSelect(value=inner) | RangeSelect(value=inner) | Replicate(value=inner):
                visit(inner)
            case Concat(parts=parts):
                for part in parts:
                    visit(part)

    for condition in machine.conditions:
        visit(condition)
    return paths


_COMMENT_LIMIT = 80


def _source_text(expr: Expr) -> str:
    """The comment of a condition wire: ``expr`` over the design's signal paths on one line,
    cut to ``_COMMENT_LIMIT`` characters (``...`` at the end of a cut one)."""
    if isinstance(expr, FirstSample):
        return "first sample"
    text = fold_whitespace(render_expr(expr))
    if len(text) <= _COMMENT_LIMIT:
        return text
    return text[: _COMMENT_LIMIT - 3] + "..."


def declare(value_type: ValueType, name: str) -> str:
    """A ``logic [signed] [w-1:0] name`` declaration."""
    signed = " signed" if value_type.signed else ""
    return f"logic{signed} [{value_type.width - 1}:0] {name}"


def _literal(value: int, value_type: ValueType) -> str:
    signed = "s" if value_type.signed else ""
    return f"{value_type.width}'{signed}h{value:x}"


def _sampled_value(function: SampledFunction, current: str, previous: str) -> str:
    match function:
        case SampledFunction.PAST:
            return previous
        case SampledFunction.ROSE:
            return f"{current}[0] & ~(valid & {previous}[0])"
        case SampledFunction.FELL:
            return f"~{current}[0] & (~valid | {previous}[0])"
        case SampledFunction.STABLE:
            return f"valid & ({current} == {previous})"
        case SampledFunction.CHANGED:
            return f"~valid | ({current} != {previous})"


class Shell:
    """The module shell of one monitor: ports, condition wires, history, checks.

    Attributes:
        machine: The machine the monitor is generated from (an emitter reads its own kind
            of machine from here, and its ``disable`` index).
    """

    def __init__(self, machine: AnyMachine, name: str, written: str = "") -> None:
        self.machine = machine
        self._name = name
        self._written = written
        self._ports = {path: f"i{index}" for index, (path, _) in enumerate(signals(machine))}
        self._wires: dict[Expr, str] = {}
        self._body: list[str] = []
        self._history: list[str] = []
        self.expressions = Expressions()

    @property
    def name(self) -> str:
        """The name of the monitor module."""
        return self._name

    # --- expressions -----------------------------------------------------------------------

    def _wire(self, expr: Expr, value: str) -> str:
        name = f"e{len(self._wires)}"
        self._wires[expr] = name
        self._body.append(f"    {declare(expr.type, name)};")
        self._body.append(f"    assign {name} = {value};  // {_source_text(expr)}")
        return name

    def expr(self, expr: Expr) -> str:
        known = self._wires.get(expr)
        if known is not None:
            return known
        match expr:
            case Signal(path=path):
                return self._wire(expr, self._ports[path])
            case Const(value=value, type=value_type):
                return self._wire(expr, _literal(value, value_type))
            case FirstSample():
                # 1 at the first sample of the clock, 0 after it (IEEE 1800-2017 16.14.6):
                # the complement of the per-property init bit, which disable iff never clears
                return self._wire(expr, "!valid")
            case Unary(op=op, operand=operand):
                return self._wire(expr, f"{_UNARY[op]}{self.expr(operand)}")
            case Binary(op=BinaryOp.LOGICAL_IMPLICATION, left=left, right=right):
                return self._wire(expr, f"!(|{self.expr(left)}) || (|{self.expr(right)})")
            case Binary(op=BinaryOp.LOGICAL_EQUIVALENCE, left=left, right=right):
                return self._wire(expr, f"(|{self.expr(left)}) == (|{self.expr(right)})")
            case Binary(op=op, left=left, right=right):
                return self._wire(expr, f"{self.expr(left)} {_BINARY[op]} {self.expr(right)}")
            case Conditional(condition=condition, then=then, otherwise=otherwise):
                text = f"{self.expr(condition)} ? {self.expr(then)} : {self.expr(otherwise)}"
                return self._wire(expr, text)
            case Convert(operand=operand):
                return self._wire(expr, self.expr(operand))
            case BitSelect(value=inner, index=index):
                return self._wire(expr, f"{self.expr(inner)}[{index}]")
            case RangeSelect(value=inner, msb=msb, lsb=lsb):
                return self._wire(expr, f"{self.expr(inner)}[{msb}:{lsb}]")
            case Concat(parts=parts):
                concatenated = self.expressions.concat(
                    [(self.expr(p), p.type.width) for p in parts]
                )
                return self._wire(expr, concatenated)
            case Replicate(count=count, value=inner):
                return self._wire(expr, f"{{{count}{{{self.expr(inner)}}}}}")
            case Sampled(function=function, operand=operand, depth=depth):
                return self._sampled(expr, function, operand, depth)

    def _sampled(self, expr: Expr, function: SampledFunction, operand: Expr, depth: int) -> str:
        """History registers: ``depth`` of them in a chain for ``$past(e, depth)``."""
        current = self.expr(operand)
        number = len(self._history)
        stage = current
        for k in range(1, depth + 1):
            register = f"h{number}" if depth == 1 else f"h{number}_{k}"
            self._history.append(
                f"    {declare(operand.type, register)};  // unconstrained before the first edge\n"
                f"    always @(posedge clk) {register} <= {stage};"
            )
            stage = register
        return self._wire(expr, _sampled_value(function, current, stage))

    # --- module ----------------------------------------------------------------------------

    def module(self, form_line: str, temporal: Emitter) -> str:
        """The module text: the header (``form_line`` is its ``// form:`` comment), ports,
        conditions ``c<i>``, history, ``valid`` and ``dis``, then the lines of
        ``temporal(self)`` and the checks."""
        machine = self.machine
        directive = machine.directive
        longest = max(machine.past_depth, default=0)
        width = longest.bit_length()
        for index, condition in enumerate(machine.conditions):
            value = f"(|{self.expr(condition)})"
            depth = machine.past_depth[index]
            if depth == 1:
                value = f"valid & {value}"
            elif depth > 1:
                value = f"(seen >= {width}'d{depth}) & {value}"
            self._body.append(f"    logic c{index};")
            self._body.append(f"    assign c{index} = {value};")
        disable = f"c{machine.disable}" if machine.disable is not None else "1'b0"
        temporal_lines = temporal(self)

        name = directive.name or "(unnamed)"
        label = f"{directive.scope}.{name}" if directive.scope else name
        lines = [
            f"// sva2yosys monitor: {directive.kind.value} {label}",
            f"// source: {directive.source}",
        ]
        if self._written:
            lines.append(f"// as written: {self._written}")
        clock = directive.clock
        enable = f" iff {render_expr(clock.enable)}" if clock.enable is not None else ""
        lines.append(f"// clock: {clock.edge.value} {clock.signal.path}{enable}")
        if directive.disable is not None:
            lines.append(f"// disable iff: {render_expr(directive.disable)}")
        lines += [
            "// generated by sva2yosys; do not edit",
            "// requires SBY 'multiclock off' (docs/semantics.md section 4)",
            form_line,
            f"module {self._name} #(parameter bit CHECK = 1'b1) (",
            "    input logic clk,",
        ]
        for path, port in self._ports.items():
            value_type = self._signal_type(path)
            lines.append(f"    input {declare(value_type, port)},  // {path}")
        lines.append("    output logic fail,")
        lines.append("    output logic success")
        lines.append(");")
        lines.extend(self.expressions.declarations)
        lines.append("    // first-sample flag (history: never cleared by disable iff)")
        if longest > 1:
            lines.append(f"    logic [{width - 1}:0] seen = '0;  // samples seen, up to {longest}")
            top = f"{width}'d{longest}"
            lines.append(f"    always @(posedge clk) if (seen != {top}) seen <= seen + {width}'d1;")
            lines.append("    logic valid;")
            lines.append(f"    assign valid = seen != {width}'d0;")
        else:
            lines.append("    logic valid = 1'b0;")
            lines.append("    always @(posedge clk) valid <= 1'b1;")
        lines.extend(self._body_with_history())
        lines.append("    logic dis;")
        lines.append(f"    assign dis = {disable};")
        lines.extend(temporal_lines)
        lines.extend(self.expressions.assignments)
        lines.extend(self._checks(directive.kind))
        lines.append("endmodule")
        return "\n".join(lines) + "\n"

    def _signal_type(self, path: str) -> ValueType:
        for expr in self._wires:
            if isinstance(expr, Signal) and expr.path == path:
                return expr.type
        raise AssertionError(f"no signal {path}")

    def _body_with_history(self) -> list[str]:
        lines = ["    // conditions"]
        lines.extend(self._body)
        if self._history:
            lines.append("    // sampled-value history (never cleared by disable iff)")
            lines.extend(self._history)
        return lines

    def registers(self, assignments: list[tuple[str, str]]) -> list[str]:
        """One ``always`` block registering ``(name, next value)`` pairs, each cleared
        asynchronously by the disable condition (``dis``)."""
        if not assignments:
            return []
        sensitivity = (
            "posedge clk or posedge dis" if self.machine.disable is not None else "posedge clk"
        )
        lines = [f"    always @({sensitivity}) begin", "        if (dis) begin"]
        lines.extend(f"            {name} <= '0;" for name, _ in assignments)
        lines.append("        end else begin")
        lines.extend(f"            {name} <= {value};" for name, value in assignments)
        lines.append("        end")
        lines.append("    end")
        return lines

    def _checks(self, kind: DirectiveKind) -> list[str]:
        statement = {
            DirectiveKind.ASSERT: "assert (!fail);",
            DirectiveKind.ASSUME: "assume (!fail);",
            DirectiveKind.COVER: "cover (success);",
        }[kind]
        return [
            "    // checks (multiclock off: evaluated in every step)",
            "    generate",
            "        if (CHECK) begin : g_check",
            f"            always @* {statement}",
            "        end",
            "    endgenerate",
        ]
