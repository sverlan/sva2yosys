"""Monitor forms and their selection (``docs/strategies.md``, *When sva2yosys uses which*).

A **form** is a way of registering a machine's temporal state. Every form has the same
ports and the same condition logic as the canonical (direct) monitor; only the temporal
state differs:

- ``DIRECT``: the canonical machine, positions pruned (``temporal.machine.slot_reach``).
  Exact at every sample.
- ``WINDOW``: for ``T |-> ##[m:n] b`` with ``1 <= m <= n`` after the compiler's
  normalisation (``temporal.compile.operands``: ``a |=> c`` has trigger ``a ##1 1`` and
  bounds those of ``c``; a sequence property has ``T = 1``), where ``b`` is one condition.
  It registers the triggers of the last ``m`` samples and, in a counter, the age of the
  oldest pending obligation that has reached its window: ``m + ceil(log2(n + 1))`` bits.

  All obligations inside their window wait for the same next ``b``, so the oldest one
  decides the next failure. When it fails, the age of the next oldest is lost. The form
  is therefore exact **up to and including the first failure**. That is the only part an
  ``assert`` or ``assume`` consumes (``docs/semantics.md`` §6), so it is never used for
  ``cover``.
- ``HISTORY``: the history frontier. It registers the triggers of the last ``slots``
  samples and the value history, over ``slots`` samples, of each condition the
  consequent reads. Each age's frontier is recomputed combinationally by running the
  automaton over that history. Failures are reported at the earliest failure, never at
  the window's end. Exact at every sample.

Every form also registers the pruned antecedent frontier, as the direct form does.

An unbounded antecedent (a cyclic automaton: ``a ##[1:$] b |-> c``, ``a[*1:$]``, goto,
nonconsecutive repetition) has the ``DIRECT`` form only: ``WINDOW`` and
``HISTORY`` do not apply to it (:func:`unapplicable`), so ``AUTO`` selects ``DIRECT`` and a
forced ``WINDOW`` or ``HISTORY`` is refused by the code generator with that reason.

The forms are proven equivalent to ``DIRECT`` within their scope by SBY in the test
suite (``tests/functional_tests/optimized_monitors``). ``sva2yosys`` can also emit the
equivalence check for each optimised monitor it generates (:mod:`sva2yosys.codegen.equivalence`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sva2yosys.ir.directive import DirectiveKind
from sva2yosys.ir.expr import Const
from sva2yosys.ir.sequence import Bool, SeqConcat
from sva2yosys.temporal.automaton import has_cycle
from sva2yosys.temporal.compile import operands
from sva2yosys.temporal.machine import Machine, antecedent_reach, machine_size


class Form(Enum):
    DIRECT = "direct"
    WINDOW = "window"
    HISTORY = "history"


class Strategy(Enum):
    """What the user asks for. ``DIRECT`` (the default) turns optimisation off: every
    monitor is canonical and reports every failure and success. ``AUTO`` picks ``WINDOW``
    where it is expected to give less logic than ``DIRECT`` (:func:`choose`), and
    ``DIRECT`` elsewhere:
    ``HISTORY`` saves registers but replays each age's history combinationally, so it has
    more logic than ``DIRECT`` and proves no faster in the measurements of
    ``docs/strategies.md``; it is used only when forced. ``WINDOW`` and ``HISTORY``
    force that form wherever it applies (``DIRECT`` elsewhere). No form reports any
    event later than ``DIRECT``."""

    AUTO = "auto"
    DIRECT = "direct"
    WINDOW = "window"
    HISTORY = "history"


class Scope(Enum):
    """Where a form equals the canonical monitor."""

    EVERY_SAMPLE = "every sample"
    UNTIL_FIRST_FAILURE = "every sample up to and including the first failure"


@dataclass(frozen=True)
class Window:
    """``T |-> ##[low:high] b`` as an effective window: ``b`` (condition index
    ``condition``) must hold at some age in ``low … high`` of the obligation."""

    low: int
    high: int
    condition: int


def scope(form: Form) -> Scope:
    return Scope.UNTIL_FIRST_FAILURE if form is Form.WINDOW else Scope.EVERY_SAMPLE


def window(machine: Machine) -> Window | None:
    """The window of ``machine``, or ``None`` if the ``WINDOW`` form does not apply."""
    directive = machine.directive
    if directive.kind is DirectiveKind.COVER:
        return None
    if machine.negated:
        return None
    # The machine's own normalisation: its triggers are those of the normalised antecedent.
    _, body, _ = operands(directive.property)
    offset = 0
    match body:
        case Bool(expr=expr) if not isinstance(expr, Const):
            low = high = offset
        case SeqConcat(left=Bool(expr=Const(value=1)), delay=delay, right=Bool(expr=expr)) if (
            not isinstance(expr, Const) and delay.maximum is not None
        ):
            low, high = delay.minimum + offset, delay.maximum + offset
        case _:
            return None
    if not 1 <= low <= high:
        return None
    return Window(low, high, machine.conditions.index(expr))


def unapplicable(machine: Machine, form: Form) -> str | None:
    """Why ``form`` cannot be used for ``machine`` regardless of its shape, or ``None``:
    the optimised forms do not apply to an unbounded antecedent."""
    if form is not Form.DIRECT and machine.antecedent is not None and has_cycle(machine.antecedent):
        return f"the {form.value} form does not apply to an unbounded antecedent (direct only)"
    return None


def state_bits(machine: Machine, form: Form) -> int | None:
    """Registered temporal bits of ``machine`` in ``form``, or ``None`` if the form does
    not apply. Sampled-value history registers are not counted (they are the same in
    every form)."""
    if unapplicable(machine, form) is not None:
        return None
    antecedent = len(antecedent_reach(machine))
    match form:
        case Form.DIRECT:
            return machine_size(machine).temporal_bits
        case Form.WINDOW:
            found = window(machine)
            if found is None:
                return None
            return antecedent + found.low + found.high.bit_length()
        case Form.HISTORY:
            if not machine.consequent.letters or machine.slots == 0 or machine.negated:
                return None
            read = {guard for letter in machine.consequent.letters for guard in letter.guards}
            return antecedent + machine.slots * (1 + len(read))


# ``auto`` takes ``window`` only when ``direct`` needs at least
# ``WINDOW_FACTOR * window + WINDOW_OFFSET`` state bits. The window's counter costs a
# fixed amount of logic (an adder and comparators), so a few saved registers do not pay
# for it: the rule is a conservative fit to Yosys gate counts (docs/strategies.md, rule 4),
# checked by tests/functional_tests/optimized_monitors (point 6).
WINDOW_FACTOR = 3
WINDOW_OFFSET = 4


def choose(machine: Machine, strategy: Strategy) -> Form:
    """The form used for ``machine`` under ``strategy``: a forced form where it applies
    (``DIRECT`` elsewhere); under ``AUTO``, ``WINDOW`` where it applies and ``DIRECT``
    needs at least ``WINDOW_FACTOR * window bits + WINDOW_OFFSET`` state bits, otherwise
    ``DIRECT`` (never ``HISTORY``)."""
    if strategy is not Strategy.AUTO:
        forced = Form(strategy.value)
        return forced if state_bits(machine, forced) is not None else Form.DIRECT
    best = Form.DIRECT
    best_bits = state_bits(machine, Form.DIRECT)
    assert best_bits is not None
    bits = state_bits(machine, Form.WINDOW)
    if bits is not None and best_bits >= WINDOW_FACTOR * bits + WINDOW_OFFSET:
        best = Form.WINDOW
    return best
