"""The Yosys frontend that reads the generated files (``--yosys-frontend``, ``docs/usage.md``).

The generated monitors are read by ``read_verilog -formal -sv`` (the default) or by the
``read_slang`` plugin. The two differ in what the output may contain:

- ``read_verilog`` does not support ``bind`` (before Yosys 0.70 it silently dropped it), so a
  bound module disappears. With this frontend each module ``bind`` is lowered into an instance
  written into a copy of its target file.
- ``read_slang`` applies ``bind``, so with this frontend the binds are kept and no design
  file is copied. It does not support the strong forms that a liveness part is generated
  with (``assert property (s_eventually x)``, ``$anyseq``), so a directive with a liveness
  part cannot be checked with it.

:func:`frontend_problem` is the one rule that refuses such a directive. Check mode and
generation both call it, so they agree.
"""

from __future__ import annotations

from enum import Enum

from sva2yosys.temporal.compile import Monitor
from sva2yosys.temporal.configurations import ConfigMachine

LIVENESS_PROBLEM = (
    "a strong unbounded property (liveness) cannot be checked with --yosys-frontend slang: "
    "the read_slang frontend does not support the strong forms that the generated liveness "
    "monitors use; use the default --yosys-frontend verilog (docs/usage.md)"
)


class YosysFrontend(Enum):
    """The Yosys frontend that reads the generated files; the values are the option's."""

    VERILOG = "verilog"
    SLANG = "slang"


def frontend_problem(monitors: tuple[Monitor, ...], frontend: YosysFrontend) -> str | None:
    """Why ``monitors`` (the parts of one directive) cannot be read with ``frontend``, or
    ``None`` when they can.

    ``VERILOG`` accepts every monitor. ``SLANG`` refuses a directive when some part has a
    liveness part (``ConfigMachine.liveness`` is set: a strong unbounded ``assert`` or
    ``assume``, or a strong ``assume`` read as a fairness constraint); the message names
    the option, says that ``read_slang`` does not support the strong forms and that the
    default frontend does, and points to ``docs/usage.md``. A ``cover``, and any directive
    without a liveness part, is accepted.
    """
    if frontend is YosysFrontend.VERILOG:
        return None
    if any(isinstance(monitor, ConfigMachine) and monitor.liveness for monitor in monitors):
        return LIVENESS_PROBLEM
    return None
