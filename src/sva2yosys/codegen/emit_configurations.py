"""Emitter of the configuration-set machine
(:class:`~sva2yosys.temporal.configurations.ConfigMachine`, ``docs/strategies.md``).

The state is one bit ``cfg_q[k]`` per reachable consequent frontier (configuration ``k``): set
when some live obligation is in it. Each configuration's held letters, match, failure and
successor letters are wires; the next register bit of ``m`` is set by every configuration
whose successor frontier equals ``m`` exactly. A negated machine (``ConfigMachine.negated``,
``not s``) has the same registers; only the two outputs swap: ``success`` is
the OR of the failure wires of the configurations and ``fail`` the OR of their match wires.

A machine with a liveness part (:attr:`~sva2yosys.temporal.configurations.ConfigMachine.liveness`,
a strong unbounded consequent of an ``assert``, or ``not`` of a weak one, which is strong after
negation, ``docs/semantics.md`` §9.6) gets one more block,
entirely inside ``ifdef SVA2YOSYS_LIVE_<part>`` (the macro of this monitor, :func:`live_define`;
``SVA2YOSYS_LIVE`` defines it for every monitor, and ``SVA2YOSYS_LIVE_FAIR`` also for an
``assume``: a fairness constraint holds in every run), so that without the define the text is the
monitor of the weak machine (the ``prove`` task sees the safety part only; the liveness check is
read by the ``live`` task, ``docs/usage.md``; one task per part, because SBY decides a ``live``
task by its first justice verdict). It declares ``busy``: the configuration register for
:attr:`~sva2yosys.temporal.liveness.Liveness.PENDING`, or ``|wit_q`` for
:attr:`~sva2yosys.temporal.liveness.Liveness.WITNESS`, where ``wit_q`` is the witness frontier
(one bit per consequent letter, the step of :func:`~sva2yosys.temporal.liveness.witness_step`)
driven by the free choice ``pick = $anyseq``. The check is
``always @(posedge clk) assert property (s_eventually !busy);`` under ``CHECK``; for an
``assume`` (always ``PENDING``) it is ``assume property``, a fairness constraint
(``FLAVOR "fair"``): the traces on which no obligation stays pending for ever.
"""

from __future__ import annotations

from sva2yosys.codegen import frontier
from sva2yosys.codegen.core import Shell
from sva2yosys.codegen.expressions import Expressions
from sva2yosys.ir.directive import DirectiveKind
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.liveness import Liveness

LIVE_DEFINE = "SVA2YOSYS_LIVE"
"""The macro that enables the liveness part of every monitor."""

FAIR_DEFINE = "SVA2YOSYS_LIVE_FAIR"
"""The macro that enables the liveness part of the fairness ``assume`` monitors only (a fairness
constraint holds in every run, so a task for one liveness part defines it too)."""


def live_part(module_name: str) -> str:
    """The name of a monitor's liveness part: the module name without its ``__sva2yosys_``
    prefix (``__sva2yosys_p3_c1`` gives ``p3_c1``); a name without the prefix stays whole, so
    two module names never share a part. The generated names are ``p``, ``i`` or ``x`` and a
    number, so no part is called ``FAIR``, the fairness selector."""
    return module_name.removeprefix("__sva2yosys_")


def live_define(module_name: str) -> str:
    """The macro that enables the liveness part of the monitor ``module_name`` alone:
    ``SVA2YOSYS_LIVE_<part>`` (:func:`live_part`)."""
    return f"{LIVE_DEFINE}_{live_part(module_name)}"


FORM_LINE = (
    "// form: configurations (one register per reachable consequent frontier, "
    "docs/strategies.md): fail and success exact at every sample"
)
"""The ``// form:`` header comment of a configuration-set monitor."""


def temporal(shell: Shell) -> list[str]:
    """The configuration-set machine: ``cfg_q[k]`` is set when some live obligation is in
    configuration ``k`` (``temporal.configurations``). Each configuration's held letters,
    match, failure and successor letters are wires; the next register bit of ``m`` is set
    by every configuration whose successor frontier equals ``m`` exactly."""
    machine = shell.machine
    assert isinstance(machine, ConfigMachine)
    expressions = shell.expressions
    lines = ["    // temporal state (form: configurations; cleared by disable iff)"]
    registers: list[tuple[str, str]] = []
    lines.extend(frontier.trigger(expressions, machine, registers))
    configurations = machine.configurations
    consequent = machine.consequent
    accepts: list[str] = []
    fails: list[str] = []
    if not configurations:
        fails.append("trigger")  # the consequent can never match
    else:
        count = len(configurations)
        lines.append(
            f"    logic [{count - 1}:0] cfg_q = '0;  // cfg_q[k]: a live obligation is in "
            "configuration k"
        )
        moves: list[list[str]] = [[] for _ in range(count)]
        for k, configuration in enumerate(configurations):
            lines.append(f"    // configuration {k}: letters {sorted(configuration)}")
            lines.append(f"    logic act{k};")
            extra = " | trigger" if k == 0 else ""
            lines.append(f"    assign act{k} = cfg_q[{k}]{extra};")
            held: dict[int, str] = {}
            for letter in sorted(configuration):
                guard_terms = [f"c{g}" for g in consequent.letters[letter].guards]
                guards = expressions.conjunction([f"act{k}", *guard_terms])
                held[letter] = f"h{k}_{letter}"
                lines.append(f"    logic {held[letter]};")
                lines.append(f"    assign {held[letter]} = {guards};")
            matching = [held[i] for i in sorted(configuration) if consequent.letters[i].accepting]
            lines.append(f"    logic accept{k};")
            lines.append(f"    assign accept{k} = {expressions.disjunction(matching)};")
            accepts.append(f"accept{k}")
            reached: dict[int, list[str]] = {}
            for letter in sorted(configuration):
                if consequent.letters[letter].accepting:
                    continue
                for target in consequent.letters[letter].next:
                    reached.setdefault(target, []).append(held[letter])
            for target, sources in sorted(reached.items()):
                lines.append(f"    logic x{k}_{target};")
                lines.append(f"    assign x{k}_{target} = {expressions.disjunction(sources)};")
            lines.append(f"    logic fail{k};")
            anything = expressions.disjunction([f"x{k}_{t}" for t in sorted(reached)])
            lines.append(f"    assign fail{k} = act{k} & !accept{k} & !({anything});")
            fails.append(f"fail{k}")
            union = set(reached)
            for m, target_configuration in enumerate(configurations):
                if not target_configuration <= union:
                    continue
                terms = [f"x{k}_{t}" for t in sorted(target_configuration)]
                terms += [f"!x{k}_{t}" for t in sorted(union - target_configuration)]
                moves[m].append(f"({expressions.conjunction([f'!accept{k}', *terms])})")
        lines.append(f"    logic [{count - 1}:0] cfg_d;")
        for m in range(count):
            lines.append(f"    assign cfg_d[{m}] = {expressions.disjunction(moves[m])};")
        registers.append(("cfg_q", "cfg_d"))
    lines.extend(shell.registers(registers))
    any_accept = expressions.disjunction(accepts)
    any_fail = expressions.disjunction(fails)
    if machine.negated:  # not s: a match fails, a dead obligation succeeds
        any_accept, any_fail = any_fail, any_accept
    lines.append(f"    assign success = !dis & ({any_accept});")
    lines.append(f"    assign fail = !dis & ({any_fail});")
    if machine.liveness is not None:
        lines.extend(_liveness(shell, machine, machine.liveness))
    return lines


def _liveness(shell: Shell, machine: ConfigMachine, kind: Liveness) -> list[str]:
    """The liveness block and its switch (module docstring): ``SVA2YOSYS_LIVE`` (and, for an
    ``assume``, ``SVA2YOSYS_LIVE_FAIR``) define the monitor's own macro, which guards the block.
    SBY's ``live`` mode decides a task by its first justice verdict, so one task checks one part."""
    own = live_define(shell.name)
    switches = [LIVE_DEFINE]
    if machine.directive.kind is DirectiveKind.ASSUME:
        switches.append(FAIR_DEFINE)
    lines = [
        *(line for switch in switches for line in (f"`ifdef {switch}", f"`define {own}", "`endif")),
        f"`ifdef {own}",
        f"    // liveness ({kind.value}): no obligation stays pending for ever",
        "    logic busy;",
    ]
    match kind:
        case Liveness.PENDING:
            # one configuration: every live obligation is in it (docs/semantics.md 9.6, L1)
            source = "cfg_q[0]" if machine.configurations else "1'b0"
            lines.append(f"    assign busy = {source};")
        case Liveness.WITNESS:
            expressions = Expressions("live_join")
            witness_lines = _witness(shell, machine, expressions)
            lines.extend(expressions.declarations)
            lines.extend(witness_lines)
            lines.extend(expressions.assignments)
    # an assume is the fairness constraint of its obligations (docs/semantics.md §9.6)
    verb = "assume" if machine.directive.kind is DirectiveKind.ASSUME else "assert"
    lines += [
        "    generate",
        "        if (CHECK) begin : g_live",
        f"            always @(posedge clk) {verb} property (s_eventually !busy);",
        "        end",
        "    endgenerate",
        "`endif",
    ]
    return lines


def _witness(shell: Shell, machine: ConfigMachine, expressions: Expressions) -> list[str]:
    """The witness (docs/semantics.md 9.6, L2): follows one obligation, picked by ``pick`` at a
    sample where it was idle, through its exact frontier ``wit_q``; frees itself when the
    obligation matches or its frontier dies; never re-picks in the sample it is freed."""
    consequent = machine.consequent
    width = len(consequent.letters)
    start = expressions.vector(
        ["1'b1" if i in machine.configurations[0] else "1'b0" for i in range(width)]
    )
    lines = [
        "    wire pick = $anyseq;",
        f"    logic [{width - 1}:0] wit_q = '0;  // wit_q[l]: the obligation may be at letter l",
        "    assign busy = |wit_q;",
        f"    logic [{width - 1}:0] wit_f;",
        f"    assign wit_f = busy ? wit_q : ((trigger & pick) ? {start} : '0);",
    ]
    lines.extend(frontier.held(expressions, "wit", consequent, "wit_f"))
    lines.append(f"    logic [{width - 1}:0] wit_d;")
    lines.append(
        f"    assign wit_d = {frontier.accepting(expressions, 'wit', consequent)} ? '0 : "
        f"{frontier.next_bits(expressions, 'wit', consequent)};"
    )
    lines.extend(shell.registers([("wit_q", "wit_d")]))
    return lines
