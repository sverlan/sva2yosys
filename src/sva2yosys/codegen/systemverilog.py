"""Generate a SystemVerilog monitor module from a machine: the public entry points.

The module is a mechanical translation of the machine's normative step function, in the
portable subset accepted by both ``read_verilog -formal -sv`` and ``read_slang``
(``docs/architecture.md``). This module only dispatches on the machine representation:

- :mod:`sva2yosys.codegen.core`: what every monitor shares (ports, expression wires,
  sampled-value history, ``valid`` / ``dis``, checks), as a :class:`~sva2yosys.codegen.core.Shell`;
- one emitter per representation for the temporal state, each a function from the shell to
  lines: :mod:`~sva2yosys.codegen.emit_slots` (canonical
  :class:`~sva2yosys.temporal.machine.Machine`, forms ``direct`` / ``window`` / ``history``),
  :mod:`~sva2yosys.codegen.emit_configurations` (configuration-set machine) and
  :mod:`~sva2yosys.codegen.emit_attempts` (per-attempt netlist);
- :mod:`sva2yosys.codegen.frontier`: letter-frontier helpers shared by the first two.

A new representation (for example a liveness machine) is one more ``emit_*`` module and one
more case in :func:`monitor_module`.

Names are deterministic; the output depends only on the machine and ``module_name``.
"""

from __future__ import annotations

from sva2yosys.codegen import emit_attempts, emit_configurations, emit_slots
from sva2yosys.codegen.core import AnyMachine, Shell, declare, signals
from sva2yosys.codegen.resources import check_emission
from sva2yosys.ir.expr import ValueType
from sva2yosys.optimize.forms import Form, state_bits, unapplicable
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.budgets import ResourceLimits
from sva2yosys.temporal.configurations import ConfigMachine

__all__ = ["declare", "monitor_module", "monitor_port_types", "monitor_ports"]


def monitor_module(
    machine: AnyMachine,
    module_name: str,
    form: Form = Form.DIRECT,
    *,
    limits: ResourceLimits | None = None,
    written: str = "",
) -> str:
    """The monitor module for ``machine`` named ``module_name``, its temporal state in
    ``form`` (:mod:`sva2yosys.optimize.forms`; it must apply to ``machine``).

    ``written`` is the assertion's source text with its whitespace folded to single
    spaces (no newline); when not empty, the header repeats it as ``// as written:``. The
    header also shows the effective clock and ``disable iff`` condition of the directive.

    An :class:`~sva2yosys.temporal.attempts.AttemptMachine` has the ``DIRECT`` form only
    (``ValueError`` otherwise); its module has the same ports, conditions and history as a
    ``Machine``'s, and its state is the netlist's registers (``docs/semantics.md`` §9.4). A
    :class:`~sva2yosys.temporal.configurations.ConfigMachine` has the ``DIRECT`` form only too:
    its state is one bit per configuration (``docs/strategies.md``)."""
    policy = machine.limits if limits is None else limits
    if isinstance(machine, AttemptMachine | ConfigMachine):
        if form is not Form.DIRECT:
            kind = "an attempt" if isinstance(machine, AttemptMachine) else "a configuration-set"
            raise ValueError(f"{kind} machine has the direct form only")
        check_emission(machine, form, policy)
        if isinstance(machine, AttemptMachine):
            return Shell(machine, module_name, written).module(
                emit_attempts.FORM_LINE, emit_attempts.temporal
            )
        return Shell(machine, module_name, written).module(
            emit_configurations.FORM_LINE, emit_configurations.temporal
        )
    check_emission(machine, form, policy)
    if form is not Form.DIRECT and state_bits(machine, form) is None:
        raise ValueError(
            unapplicable(machine, form) or f"the {form.value} form does not apply to this machine"
        )
    return Shell(machine, module_name, written).module(
        emit_slots.form_line(form), lambda shell: emit_slots.temporal(shell, form)
    )


def monitor_ports(machine: AnyMachine) -> tuple[str, ...]:
    """Hierarchical paths of the design signals, in the order of the ports ``i0, i1, …``."""
    return tuple(path for path, _ in signals(machine))


def monitor_port_types(machine: AnyMachine) -> tuple[ValueType, ...]:
    """The types of the ports ``i0, i1, …`` (as :func:`monitor_ports`)."""
    return tuple(value_type for _, value_type in signals(machine))
