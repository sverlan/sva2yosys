"""Unit tests of ``support.simulate.liveness_fails`` (the documented contract in its
docstring): the exact liveness verdict of a configuration machine on a lasso.

Hand-derived expectations (IEEE 1800 16.12.2: a strong sequence with no match is false; the
check fails when some obligation stays pending for ever). Samples are written ``(a, b)``
(``(a, b, d)`` with a ``disable iff``).
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from support.reference.lasso import Lasso
from support.simulate import lasso_first_failure, liveness_fails
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Sampled, SampledFunction, Signal
from sva2yosys.ir.property import Implication, Property, SequenceProperty, Strength, Until
from sva2yosys.ir.sequence import TRUE, Bool, Delay, SeqConcat
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.compile import compile_configuration_machine
from sva2yosys.temporal.configurations import (
    ConfigMachine,
    configuration_step,
    initial_config_state,
)
from sva2yosys.temporal.liveness import Liveness

_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("t.sv", 1, 1, 1, 2)
_A, _B, _D = (Signal(f"m.{n}", BIT) for n in "abd")
_STRONG = Strength.STRONG


def _machine(prop: Property, disable: Signal | None = None) -> ConfigMachine:
    directive = Directive(DirectiveKind.ASSERT, "p", "m", _CLOCK, disable, prop, _SOURCE)
    return compile_configuration_machine(directive)


def _lasso(rows: Sequence[tuple[int, ...]], loop: int, names: str = "ab") -> Lasso:
    return Lasso(
        tuple({f"m.{n}": v for n, v in zip(names, row, strict=True)} for row in rows), loop
    )


def _delayed(consequent_delay: int, signal: Signal = _B) -> SequenceProperty:
    return SequenceProperty(SeqConcat(TRUE, Delay(consequent_delay, None), Bool(signal)), _STRONG)


_UNTIL = _machine(Until(_A, _B, True, False))  # a s_until b: one configuration (pending bit)
_WITNESS = _machine(Implication(Bool(_A), _delayed(1), True))  # a |-> strong(##[1:$] b)
_DISABLED = _machine(Implication(Bool(_A), _delayed(1), True), disable=_D)


def test_the_machines_have_the_kinds_the_cases_assume() -> None:
    assert _UNTIL.liveness is Liveness.PENDING and len(_UNTIL.configurations) == 1
    assert _WITNESS.liveness is Liveness.WITNESS and len(_WITNESS.configurations) == 2
    assert _DISABLED.liveness is Liveness.WITNESS


def test_a_machine_without_a_liveness_part_is_refused() -> None:
    weak = SequenceProperty(SeqConcat(TRUE, Delay(1, None), Bool(_B)), Strength.WEAK)
    machine = _machine(Implication(Bool(_A), weak, True))
    with pytest.raises(ValueError, match="liveness"):
        liveness_fails(machine, _lasso([(1, 0)], 0))


@pytest.mark.parametrize(
    ("rows", "loop", "fails"),
    [
        # a for ever, b never: the obligation of every sample stays live for ever
        ([(1, 0)], 0, True),
        # b at every other sample: every obligation ends at the next b, the signal drops at
        # the samples where b holds (at sample 1 all live ones match and the new one matches
        # at once)
        ([(1, 0), (0, 1)], 0, False),
        # a dies at once: a failure, no pending obligation
        ([(0, 0)], 0, False),
        # a prefix with a pending obligation that the loop ends: b arrives in the loop
        ([(1, 0), (1, 0), (0, 1)], 2, False),
        # a and not b in the loop after the prefix: pending for ever
        ([(0, 1), (1, 0)], 1, True),
    ],
)
def test_the_pending_bit_fails_when_it_is_one_at_every_sample_of_the_cycle(
    rows: list[tuple[int, ...]], loop: int, fails: bool
) -> None:
    assert liveness_fails(_UNTIL, _lasso(rows, loop)) is fails


@pytest.mark.parametrize(
    ("rows", "loop", "fails"),
    [
        # a every sample, b never: the obligation launched at 0 is pending for ever
        ([(1, 0)], 0, True),
        # a every sample, b every sample: every obligation ends one sample after its launch,
        # although some obligation is live after every sample
        ([(1, 1)], 0, False),
        # one request, b never afterwards
        ([(1, 0), (0, 0)], 1, True),
        # one request, b at the next sample
        ([(1, 0), (0, 1)], 1, False),
        # b high only in the prefix, a recurring in the loop: the loop's requests never end
        ([(0, 1), (1, 0)], 1, True),
        # no request at all
        ([(0, 0), (0, 1)], 0, False),
        # the request launched in the prefix is answered in the loop, which has no requests
        ([(1, 0), (0, 0), (0, 1)], 1, False),
    ],
)
def test_a_witness_fails_when_some_single_obligation_is_pending_for_ever(
    rows: list[tuple[int, ...]], loop: int, fails: bool
) -> None:
    assert liveness_fails(_WITNESS, _lasso(rows, loop)) is fails


def test_the_pending_bit_would_be_wrong_for_the_witness_machine() -> None:
    """a = b = 1 at every sample: the configuration machine has a live obligation after every
    sample (the one just launched), yet no obligation stays pending: why the machine is a
    witness machine."""
    lasso = _lasso([(1, 1)], 0)
    names = ("m.a", "m.b")  # the condition table order of this property
    state = initial_config_state()
    for sample in range(5):
        state, _ = configuration_step(
            _WITNESS, state, sample, lambda condition: lasso.samples[0][names[condition]] != 0
        )
        assert state.live
    assert liveness_fails(_WITNESS, lasso) is False


@pytest.mark.parametrize(
    ("rows", "loop", "fails"),
    [
        # (a, b, d): the request at 0 is disabled at 1 (d), again and again: never pending
        ([(1, 0, 0), (0, 0, 1)], 0, False),
        # d never: the same requests stay pending
        ([(1, 0, 0), (0, 0, 0)], 0, True),
        # d at the launch sample itself disables the attempt: no obligation
        ([(1, 0, 1)], 0, False),
    ],
)
def test_disable_frees_the_witness(rows: list[tuple[int, ...]], loop: int, fails: bool) -> None:
    assert liveness_fails(_DISABLED, _lasso(rows, loop, "abd")) is fails


def test_a_past_condition_is_read_across_the_loop() -> None:
    """``a |-> strong(##[1:$] $past(b))`` with ``a`` and ``b`` high at every sample: from
    position 1 on ``$past(b)`` is 1, so every obligation matches one sample after its launch;
    with ``b`` low in the loop it never does."""
    past_b = Sampled(SampledFunction.PAST, _B, BIT)
    machine = _machine(
        Implication(
            Bool(_A),
            SequenceProperty(SeqConcat(TRUE, Delay(1, None), Bool(past_b)), _STRONG),
            True,
        )
    )
    assert machine.liveness is Liveness.WITNESS
    assert liveness_fails(machine, _lasso([(1, 1)], 0)) is False
    assert liveness_fails(machine, _lasso([(1, 1), (1, 0)], 1)) is True
    # b high at the loop's last sample only: $past(b) is high at the loop's first sample
    assert liveness_fails(machine, _lasso([(1, 0), (1, 0), (1, 1)], 0)) is False


@pytest.mark.parametrize(
    ("rows", "loop", "first"),
    [
        ([(1, 0)], 0, None),  # a for ever, b never: pending, no failure
        ([(1, 1)], 0, None),  # b at every sample: every obligation succeeds
        ([(0, 0)], 0, 0),  # neither a nor b: the obligation of sample 0 dies at once
        ([(1, 0), (0, 0)], 0, 1),  # a, then neither: the obligation of 0 dies at 1
        ([(0, 1), (0, 0)], 1, 1),  # b at 0 is a success; at 1 the new obligation dies at once
        ([(1, 0), (1, 0), (0, 0)], 2, 2),  # a prefix of pending obligations, killed at 2
    ],
)
def test_the_first_failure_is_found_on_the_whole_infinite_trace(
    rows: list[tuple[int, ...]], loop: int, first: int | None
) -> None:
    assert lasso_first_failure(_UNTIL, _lasso(rows, loop)) == first


def test_the_first_failure_of_a_witness_machine_is_the_configuration_machines() -> None:
    """``a |-> strong(##[1:$] b)`` never fails by itself (``never_fails``): pending only."""
    for rows, loop in (([(1, 0)], 0), ([(1, 0), (0, 1)], 1), ([(0, 0)], 0)):
        assert lasso_first_failure(_WITNESS, _lasso(rows, loop)) is None
