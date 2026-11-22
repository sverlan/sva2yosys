"""Layer 2: the never-fails analysis of the configuration-set machine against
the reference evaluator. See CONTRACT.md in this folder, points 49-51.

``never_fails(machine)`` may only say ``True`` when no trace makes the reference report a
failure of that part. The tests enumerate every trace of the part's signals up to a small
length, so a wrong ``True`` shows as a failing trace; the converse (``False`` while nothing
fails) is only counted, as a measure of how conservative the criterion is.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import replace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from support.ir_strategies import (
    all_signals,
    config_directives,
    nullable,
    sequences,
    unbounded_sequences,
)
from support.reference.evaluate import evaluate_attempts
from support.reference.trace import Trace
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Signal
from sva2yosys.ir.property import Implication, Not, Property, SequenceProperty, Strength
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat, SeqIntersect, Sequence
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.compile import compile_configuration_machine, compile_monitors
from sva2yosys.temporal.configurations import ConfigMachine, never_fails

pytestmark = pytest.mark.filterwarnings(
    "ignore:Generating overly large repr:hypothesis.errors.HypothesisWarning"
)

_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("never_fails.sv", 1, 1, 1, 2)
_A, _B, _C = (Bool(Signal(f"m.{n}", BIT)) for n in "abc")

type Predicate = Callable[[ConfigMachine], bool]

# traces enumerated per part: the longest length whose number of traces fits the budget
_DEFAULT_TRACES = 3000
_SLOW_TRACES = 12000


def _cat(left: Sequence, low: int, high: int | None, right: Sequence) -> SeqConcat:
    return SeqConcat(left, Delay(low, high), right)


def _length_for(signals: int, budget: int) -> int:
    """The longest trace length (2 to 8) whose number of traces over ``signals`` stays
    within ``budget``."""
    length = 2
    while length < 8 and (2**signals) ** (length + 1) <= budget:
        length += 1
    return length


def _failing_trace(machine: ConfigMachine, budget: int) -> Trace | None:
    """A trace (of every length up to the budget's) on which the reference reports a failure
    of the machine's part, or ``None``."""
    part = machine.directive
    names = all_signals(part)
    longest = _length_for(len(names), budget)
    for length in range(1, longest + 1):
        for values in itertools.product((0, 1), repeat=len(names) * length):
            rows = tuple(
                {name: values[sample * len(names) + i] for i, name in enumerate(names)}
                for sample in range(length)
            )
            trace = Trace(rows)
            if evaluate_attempts(part.property, trace, part.disable).failures:
                return trace
    return None


# --- the oracle ---------------------------------------------------------------------------------

_COUNTS = {"true": 0, "false": 0, "false_but_never_fails": 0}


def _check(directive: Directive, budget: int, predicate: Predicate = never_fails) -> None:
    for monitor in compile_monitors(directive):
        if not isinstance(monitor, ConfigMachine) or monitor.liveness is not None:
            continue  # a part with a liveness part gets no warning
        claimed = predicate(monitor)
        failing = _failing_trace(monitor, budget)
        if claimed:
            _COUNTS["true"] += 1
            assert failing is None, (monitor.directive, failing.samples if failing else None)
        else:
            _COUNTS["false"] += 1
            if failing is None:
                _COUNTS["false_but_never_fails"] += 1


_SETTINGS = settings(
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


@pytest.mark.parametrize(
    "consequent",
    [_cat(TRUE, 1, None, _B), Repeat(_B, 1, None)],
)
def test_never_fails_soundness_on_small_hand_constructed_parts(consequent: Sequence) -> None:
    """49: a live filler cannot die; a holding repeat can fail at its first sample."""
    directive = Directive(
        DirectiveKind.ASSERT,
        "p",
        "m",
        _CLOCK,
        None,
        Implication(_A, SequenceProperty(consequent), True),
        _SOURCE,
    )
    _check(directive, 16)


@pytest.mark.extended
@settings(parent=_SETTINGS, max_examples=60)
@given(directive=config_directives())
def test_never_fails_is_sound_on_generated_directives(directive: Directive) -> None:
    """49: whenever the analysis says "never fails", no short trace makes the reference fail."""
    _check(directive, _DEFAULT_TRACES)


def _kept_alive_directives() -> st.SearchStrategy[Directive]:
    """Directives whose consequent starts with an always-held filler, ``1 ##[1:$] x`` with a
    generated ``x`` (so the analysis often says "never fails"), under both implications, an
    unbounded antecedent, or as a property."""
    tails = st.one_of(sequences(2), unbounded_sequences(1)).filter(lambda x: not nullable(x))
    consequents = tails.map(lambda x: _cat(TRUE, 1, None, x))
    antecedents = st.one_of(st.just(_A), st.just(_B), st.just(Repeat(_A, 1, None)))
    properties = st.one_of(
        st.builds(Implication, antecedents, consequents.map(SequenceProperty), st.booleans()),
        consequents.map(SequenceProperty),
    )
    kinds = st.sampled_from([DirectiveKind.ASSERT, DirectiveKind.ASSUME])
    return st.builds(
        lambda kind, prop: Directive(kind, "p", "m", _CLOCK, None, prop, _SOURCE), kinds, properties
    )


@pytest.mark.extended
def test_never_fails_is_sound_where_it_often_says_yes() -> None:
    """49: consequents `1 ##[1:$] x`. Most are proved (a non-vacuous check of the "never
    fails" answers); some are not, correctly: an `x` that can never match leaves no
    configuration, and the obligation fails at its trigger."""
    before = _COUNTS["true"]

    @settings(parent=_SETTINGS, max_examples=60)
    @given(directive=_kept_alive_directives())
    def check(directive: Directive) -> None:
        _check(directive, _DEFAULT_TRACES)

    check()
    assert _COUNTS["true"] - before >= 30


@pytest.mark.slow
@settings(parent=_SETTINGS, max_examples=150)
@given(directive=config_directives())
def test_never_fails_is_sound_on_more_and_longer_traces(directive: Directive) -> None:
    """49: the same, more directives and longer traces."""
    _check(directive, _SLOW_TRACES)


def test_how_conservative_the_analysis_is(capsys: pytest.CaptureFixture[str]) -> None:
    """49 (informative, never fails): the share of "not proved" parts that, on the short
    traces, never fail either. It reports the counters of the soundness tests of this module
    run before it (alone it reports zeros)."""
    with capsys.disabled():
        total_false = _COUNTS["false"]
        ratio = _COUNTS["false_but_never_fails"] / total_false if total_false else 0.0
        print(
            f"\nnever_fails: proved {_COUNTS['true']}, not proved {total_false}, of which "
            f"{_COUNTS['false_but_never_fails']} never fail on the short traces "
            f"({ratio:.1%}: the conservativeness)"
        )


# --- the fixed cases the criterion is derived from (hand-derived) ---------------------------------


def _directive(prop: Property) -> Directive:
    return Directive(DirectiveKind.ASSERT, "p", "m", _CLOCK, None, prop, _SOURCE)


def _imp(consequent: Sequence) -> Directive:
    return _directive(Implication(_A, SequenceProperty(consequent), True))


_LOOP = _cat(TRUE, 1, None, _B)
# Consequents that can fail: with a high and b low, b[*1:$] and its relatives fail.
_CAN_FAIL = [
    _imp(Repeat(_B, 1, None)),
    _imp(_cat(Repeat(_B, 1, None), 1, 1, _C)),
    _imp(_cat(_B, 1, None, _C)),
    _imp(_cat(_B, 1, 1, _cat(_C, 1, None, _B))),
]
# A consequent that can never match (the intersect of lengths that cannot meet) has no
# letter: its configurations are empty and the obligation fails at its trigger.
_EMPTY = _imp(SeqIntersect(_cat(_B, 1, None, _C), _A))


def _all_letters_held(machine: ConfigMachine) -> bool:
    """MUTANT: every letter of a configuration counts as held (instead of only the letters
    without guards)."""
    letters = machine.consequent.letters
    return all(
        any(letters[i].accepting or letters[i].next for i in configuration)
        for configuration in machine.configurations
    )


def _ignores_the_empty_machine(machine: ConfigMachine) -> bool:
    """MUTANT: the right criterion, but a machine without configurations counts as proved."""
    letters = machine.consequent.letters
    return all(
        any(
            not letters[i].guards and (letters[i].accepting or bool(letters[i].next))
            for i in configuration
        )
        for configuration in machine.configurations
    )


def test_the_hand_derived_cases_have_the_expected_answers() -> None:
    assert never_fails(compile_configuration_machine(_imp(_LOOP)))
    for directive in _CAN_FAIL:
        assert not never_fails(compile_configuration_machine(directive)), directive.property
    assert compile_configuration_machine(_EMPTY).configurations == ()
    assert not never_fails(compile_configuration_machine(_EMPTY))


def test_the_oracle_catches_the_all_letters_held_mutant() -> None:
    """50: `a |-> b[*1:$]` is claimed to never fail by the mutant, and the reference fails it
    at once (a high, b low)."""
    machine = compile_configuration_machine(_CAN_FAIL[0])
    assert _all_letters_held(machine)
    assert _failing_trace(machine, 200) is not None
    with pytest.raises(AssertionError):
        _check(_CAN_FAIL[0], 200, _all_letters_held)


def test_the_oracle_catches_the_empty_machine_mutant() -> None:
    """50: an empty consequent fails at its trigger; the mutant claims it never fails."""
    machine = compile_configuration_machine(_EMPTY)
    assert _ignores_the_empty_machine(machine)
    assert _failing_trace(machine, 200) is not None
    with pytest.raises(AssertionError):
        _check(_EMPTY, 200, _ignores_the_empty_machine)


def test_the_correct_analysis_passes_the_oracle_on_the_fixed_cases() -> None:
    for directive in (_imp(_LOOP), *_CAN_FAIL, _EMPTY):
        _check(directive, 400)


# --- 68: a negated machine fails on a match ----------------------------------------

_NOT_LOOP = _directive(Not(SequenceProperty(_LOOP, Strength.STRONG)))


def _ignores_the_negation(machine: ConfigMachine) -> bool:
    """MUTANT: the criterion of the unnegated machine (frontiers that cannot die)."""
    return never_fails(replace(machine, negated=False))


def test_never_fails_is_false_for_a_negated_machine() -> None:
    """68: ``not strong(1 ##[1:$] b)`` is weak (no liveness part), and its frontier cannot die,
    but it fails as soon as ``b`` holds: no warning."""
    machine = compile_configuration_machine(_NOT_LOOP)
    assert machine.negated and machine.liveness is None
    assert not never_fails(machine)
    assert _failing_trace(machine, 200) is not None
    assert never_fails(replace(machine, negated=False))  # the mutant's answer


def test_the_oracle_catches_the_negation_ignoring_mutant() -> None:
    """68: the mutant claims ``not strong(1 ##[1:$] b)`` never fails; the reference fails it at
    the first ``b``."""
    with pytest.raises(AssertionError):
        _check(_NOT_LOOP, 200, _ignores_the_negation)
    _check(_NOT_LOOP, 200)


def test_never_fails_stays_false_for_every_negated_fixed_case() -> None:
    """68: a negated sequence fails where it matches, a death is a success."""
    for sequence in (_LOOP, Repeat(_B, 1, None), _cat(_A, 1, None, _B)):
        for strength in (Strength.STRONG, Strength.WEAK):
            directive = _directive(Not(SequenceProperty(sequence, strength)))
            machine = compile_configuration_machine(directive)
            assert not never_fails(machine), (sequence, strength)
            _check(directive, 400)
