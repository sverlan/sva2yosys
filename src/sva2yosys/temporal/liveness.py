"""The liveness signal of a strong unbounded consequent.

Normative: ``docs/semantics.md`` §9.6. A strong unbounded consequent of an ``assert`` has two
parts: the safety part (a frontier that dies is a failure, reported by the configuration-set
machine, :mod:`sva2yosys.temporal.configurations`) and the liveness part: no obligation stays
pending for ever (16.12.2: a strong sequence with no match is false). The liveness part is
checked on a *busy* signal that must not be 1 at every sample from some point on. Two ways to
get it, chosen per machine by :func:`liveness_kind`:

- **L1, pending bit** (:attr:`Liveness.PENDING`): the machine has at most one configuration,
  so every live obligation is in that one configuration and they succeed, die or stay
  together; an obligation launched at sample ``t`` is evaluated at ``t`` with the same letters
  as the older ones. ``busy`` is "some obligation is live after this sample". It is exact:
  ``busy`` stays 1 from some sample on exactly when the obligations live there stay pending
  for ever.
- **L2, witness** (:attr:`Liveness.WITNESS`): every other machine. A free choice ``pick``
  selects, at a sample where the witness was idle, one obligation launched there, and
  :func:`witness_step` follows that obligation's exact frontier. ``busy`` is the witness
  state. Sound (a witness busy for ever follows one obligation pending for ever) and
  complete (if an obligation launched at ``t`` stays pending for ever, ``pick`` can be 0
  until ``t`` and 1 there). The witness is idle in the sample in which it is freed and never
  re-picks in it.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING

from sva2yosys.temporal.machine import StepEvents, held_letters

if TYPE_CHECKING:
    from sva2yosys.temporal.configurations import ConfigMachine


class Liveness(Enum):
    """How the liveness part of a strong directive is checked (module docstring)."""

    PENDING = "pending"
    WITNESS = "witness"


def liveness_kind(configurations: int) -> Liveness:
    """L1 (:attr:`Liveness.PENDING`) for at most one configuration, else L2
    (:attr:`Liveness.WITNESS`)."""
    return Liveness.PENDING if configurations <= 1 else Liveness.WITNESS


def witness_step(
    machine: ConfigMachine,
    frontier: frozenset[int] | None,
    events: StepEvents,
    pick: bool,
    elapsed: int,
    raw: Callable[[int], bool],
) -> frozenset[int] | None:
    """One sample of the witness: the frontier it follows after the sample, ``None`` when idle.

    ``frontier`` is the witness before the sample (``None``: idle), ``events`` those of
    :func:`~sva2yosys.temporal.configurations.configuration_step` for the same sample,
    ``elapsed`` and ``raw`` as there (a condition with past depth ``d`` does not hold while
    ``elapsed < d``). Rules:

    - ``events.disabled``: idle.
    - busy (``frontier`` not ``None``): the held letters of the frontier; an accepting one
      frees the witness, no successor frees it too (death is a safety failure, reported by
      the configuration machine; for a negated machine the match is the
      failure and death the success), else the successor set. A busy witness never picks in the
      same sample, even when it is freed in it.
    - idle: when ``events.triggered``, ``pick`` and the machine has configurations, the
      witness starts from the start frontier ``machine.configurations[0]`` and this sample is
      processed as above (an obligation is evaluated at its trigger sample); otherwise it
      stays idle.
    """
    if events.disabled:
        return None
    if frontier is None:
        if not (events.triggered and pick and machine.configurations):
            return None
        frontier = machine.configurations[0]
    known: dict[int, bool] = {}

    def holds(condition: int) -> bool:
        if elapsed < machine.past_depth[condition]:
            return False
        if condition not in known:
            known[condition] = raw(condition)
        return known[condition]

    consequent = machine.consequent
    held = held_letters(consequent, frontier, holds)
    if any(consequent.letters[letter].accepting for letter in held):
        return None
    following = frozenset(n for letter in held for n in consequent.letters[letter].next)
    return following if following else None
