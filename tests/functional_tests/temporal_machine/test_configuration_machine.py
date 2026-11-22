"""Layer 2: the configuration-set machine (weak unbounded consequents) against
the reference evaluator. See CONTRACT.md in this folder, points 21-27.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings

from support.ir_strategies import config_directives, has_unbounded_part, traces
from support.reference.evaluate import Verdict, evaluate
from support.reference.trace import Trace
from support.simulate import ConfigSimulation, simulate_configurations
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Const, Expr, Signal
from sva2yosys.ir.property import (
    Always,
    Implication,
    Not,
    Property,
    SequenceProperty,
    Strength,
)
from sva2yosys.ir.sequence import (
    TRUE,
    Bool,
    Delay,
    Goto,
    NonConsecutive,
    Repeat,
    SeqConcat,
    SeqIntersect,
    SeqOr,
    Sequence,
)
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal import automaton as automaton_module
from sva2yosys.temporal import compile as compile_module
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.budgets import Budgets
from sva2yosys.temporal.compile import (
    CompileError,
    compile_attempt_machine,
    compile_configuration_machine,
    compile_directive,
    compile_monitors,
)
from sva2yosys.temporal.configurations import ConfigMachine, config_size
from sva2yosys.temporal.machine import Machine, MachineError

pytestmark = pytest.mark.filterwarnings(
    "ignore:Generating overly large repr:hypothesis.errors.HypothesisWarning"
)

_DIFFERENTIAL = settings(
    max_examples=120,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("config.sv", 1, 1, 1, 2)


def _sig(name: str) -> Signal:
    return Signal(f"m.{name}", BIT)


def _b(name: str) -> Bool:
    return Bool(_sig(name))


def _cat(left: Sequence, low: int, high: int | None, right: Sequence) -> SeqConcat:
    return SeqConcat(left, Delay(low, high), right)


def _seq(sequence: Sequence, strength: Strength = Strength.DEFAULT) -> SequenceProperty:
    return SequenceProperty(sequence, strength)


_A, _B, _C, _D = (_b(name) for name in "abcd")


def _directive(
    prop: Property, disable: Expr | None = None, kind: DirectiveKind = DirectiveKind.ASSERT
) -> Directive:
    return Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE)


def _config(directive: Directive) -> ConfigMachine:
    (monitor,) = compile_monitors(directive)
    assert isinstance(monitor, ConfigMachine)
    return monitor


def _reference_pending(directive: Directive, trace: Trace) -> bool:
    """Whether some (non-disabled) obligation is still pending after the trace."""
    result = evaluate(directive.property, trace, directive.disable)
    return any(
        obligation.verdict is Verdict.PENDING
        for attempt in result.attempts
        for obligation in attempt.obligations
    )


def _compare(directive: Directive, trace: Trace, simulation: ConfigSimulation) -> None:
    """Failures, successes and "some obligation is live" equal the reference's, on every
    prefix of ``trace``."""
    for end in range(1, len(trace) + 1):
        prefix = Trace(trace.samples[:end])
        expected = evaluate(directive.property, prefix, directive.disable)
        assert [t for t in simulation.failures if t < end] == list(expected.failures)
        assert [t for t in simulation.successes if t < end] == list(expected.successes)
        assert bool(simulation.states[end - 1].live) == _reference_pending(directive, prefix)


# --- 21: differential, every prefix -----------------------------------------------------------


@pytest.mark.extended
@_DIFFERENTIAL
@given(directive=config_directives(), trace=traces(1, 10))
def test_configuration_machine_equals_reference_on_every_prefix(
    directive: Directive, trace: Trace
) -> None:
    """21."""
    _compare(directive, trace, simulate_configurations(_config(directive), trace))


@pytest.mark.slow
@settings(
    max_examples=60,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(directive=config_directives(), trace=traces(11, 16))
def test_configuration_machine_equals_reference_on_long_traces(
    directive: Directive, trace: Trace
) -> None:
    """21: traces long enough for loops to run many times."""
    _compare(directive, trace, simulate_configurations(_config(directive), trace))


def test_generated_directives_compile_to_one_configuration_machine() -> None:
    """21."""
    seen = {"always": 0, "sequence": 0, "implication": 0}

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(directive=config_directives())
    def check(directive: Directive) -> None:
        monitors = compile_monitors(directive)
        assert len(monitors) == 1 and isinstance(monitors[0], ConfigMachine)
        assert monitors[0] == compile_configuration_machine(directive)
        key = (
            "always"
            if isinstance(directive.property, Always)
            else "sequence"
            if isinstance(directive.property, SequenceProperty)
            else "implication"
        )
        seen[key] += 1

    check()
    assert all(seen.values()), seen


# --- 22: exhaustive on fixed properties -------------------------------------------------------

_ALWAYS_C = Always(0, None, False, Implication(_A, _seq(_cat(Repeat(_B, 1, None), 1, 1, _C)), True))

# (name, property, signals, longest trace): every trace of every length up to the longest
FIXED: list[tuple[str, Property, tuple[str, ...], int]] = [
    ("a |-> ##[1:$] b", Implication(_A, _seq(_cat(TRUE, 1, None, _B)), True), ("a", "b"), 7),
    ("a |-> ##[0:$] b", Implication(_A, _seq(_cat(TRUE, 0, None, _B)), True), ("a", "b"), 7),
    (
        "a |-> b[*1:$] ##1 c",
        Implication(_A, _seq(_cat(Repeat(_B, 1, None), 1, 1, _C)), True),
        ("a", "b", "c"),
        5,
    ),
    ("a |-> b[*2:$]", Implication(_A, _seq(Repeat(_B, 2, None)), True), ("a", "b"), 7),
    ("a |=> b[->2]", Implication(_A, _seq(Goto(_sig("b"), 2, 2)), False), ("a", "b"), 7),
    (
        "a |-> b[=2] ##1 c",
        Implication(_A, _seq(_cat(NonConsecutive(_sig("b"), 2, 2), 1, 1, _C)), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a |-> (b ##[1:$] c)",
        Implication(_A, _seq(_cat(_B, 1, None, _C)), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a ##1 b |-> c[*2:$]",
        Implication(_cat(_A, 1, 1, _B), _seq(Repeat(_C, 2, None)), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a[*1:$] |-> b[*1:$] ##1 c",
        Implication(Repeat(_A, 1, None), _seq(_cat(Repeat(_B, 1, None), 1, 1, _C)), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a |-> (b[*1:$] or c ##2 d)",
        Implication(_A, _seq(SeqOr(Repeat(_B, 1, None), _cat(_C, 2, 2, _D))), True),
        ("a", "b", "c", "d"),
        3,
    ),
    ("always (a |-> b[*1:$] ##1 c)", _ALWAYS_C, ("a", "b", "c"), 5),
    ("b[*1:$] ##1 c", _seq(_cat(Repeat(_B, 1, None), 1, 1, _C)), ("b", "c"), 7),
    (
        "a |=> ##[2:$] b ##1 c",
        Implication(_A, _seq(_cat(_cat(TRUE, 2, None, _B), 1, 1, _C)), False),
        ("a", "b", "c"),
        5,
    ),
    (
        "a |-> (b[*1:$] intersect c[*2:$])",
        Implication(_A, _seq(SeqIntersect(Repeat(_B, 1, None), Repeat(_C, 2, None))), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "always [2:$] (a |-> ##[1:$] b ##1 c)",
        Always(
            2, None, False, Implication(_A, _seq(_cat(_cat(TRUE, 1, None, _B), 1, 1, _C)), True)
        ),
        ("a", "b", "c"),
        5,
    ),
    (
        "a |-> weak(b[*1:$] ##1 c)",
        Implication(_A, _seq(_cat(Repeat(_B, 1, None), 1, 1, _C), Strength.WEAK), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a |-> ##[1:$] b ##3 c",
        Implication(_A, _seq(_cat(_cat(TRUE, 1, None, _B), 3, 3, _C)), True),
        ("a", "b", "c"),
        5,
    ),
]

# not of an unbounded sequence (a weak sequence negates to a strong one, a strong
# one to a weak one); the events of the machine are swapped. Not an assume (not weak(s) is strong).
FIXED_NEGATED: list[tuple[str, Property, tuple[str, ...], int]] = [
    ("not (a ##[1:$] b)", Not(_seq(_cat(_A, 1, None, _B))), ("a", "b"), 7),
    ("not strong(a ##[1:$] b)", Not(_seq(_cat(_A, 1, None, _B), Strength.STRONG)), ("a", "b"), 7),
    (
        "a |-> not (b[*1:$] ##1 c)",
        Implication(_A, Not(_seq(_cat(Repeat(_B, 1, None), 1, 1, _C))), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a |=> not strong(b[->2])",
        Implication(_A, Not(_seq(Goto(_sig("b"), 2, 2), Strength.STRONG)), False),
        ("a", "b"),
        7,
    ),
    (
        "always (a |-> not (b ##[1:$] c))",
        Always(0, None, False, Implication(_A, Not(_seq(_cat(_B, 1, None, _C))), True)),
        ("a", "b", "c"),
        5,
    ),
    (
        "a |-> not ((b ##[1:$] c) intersect d)",
        Implication(_A, Not(_seq(SeqIntersect(_cat(_B, 1, None, _C), _D))), True),
        ("a", "b", "c", "d"),
        4,
    ),
]


def _all_traces(signals: tuple[str, ...], length: int) -> list[Trace]:
    return [
        Trace(
            tuple(
                {f"m.{name}": bits[t * len(signals) + i] for i, name in enumerate(signals)}
                for t in range(length)
            )
        )
        for bits in itertools.product((0, 1), repeat=length * len(signals))
    ]


def _exhaustive_mismatches(
    longest: Callable[[int], int], limit: int | None = None, disabled: bool = False
) -> int:
    mismatches = 0
    for _, prop, signals, longest_trace in (*FIXED, *FIXED_NEGATED):
        names = (*signals, "z") if disabled else signals
        directive = _directive(prop, _sig("z") if disabled else None)
        machine = _config(directive)
        for length in range(1, min(longest_trace, longest(len(names))) + 1):
            for trace in _all_traces(names, length):
                try:
                    _compare(directive, trace, simulate_configurations(machine, trace))
                except (AssertionError, MachineError):
                    mismatches += 1
                    if limit is not None and mismatches >= limit:
                        return mismatches
    return mismatches


def test_the_fixed_properties_compile_to_the_configuration_machine() -> None:
    for name, prop, _, _ in FIXED:
        assert isinstance(compile_monitors(_directive(prop))[0], ConfigMachine), name
        assert isinstance(
            compile_monitors(_directive(prop, kind=DirectiveKind.ASSUME))[0], ConfigMachine
        )


def test_the_fixed_negated_properties_compile_to_negated_configuration_machines() -> None:
    """61, 66: one negated configuration machine each (an assume too when the negated
    sequence is strong: ``not strong(s)`` is weak)."""
    for name, prop, _, _ in FIXED_NEGATED:
        (machine,) = compile_monitors(_directive(prop))
        assert isinstance(machine, ConfigMachine) and machine.negated, name
        if "strong" in name:
            (assumed,) = compile_monitors(_directive(prop, kind=DirectiveKind.ASSUME))
            assert (
                isinstance(assumed, ConfigMachine) and assumed.negated and assumed.liveness is None
            )


@pytest.mark.extended
def test_exhaustive_short_traces_equal_the_reference() -> None:
    """22: up to 3 samples (2 with ``disable iff``); the negated forms included."""
    assert _exhaustive_mismatches(lambda signals: 3 if signals <= 3 else 2) == 0
    assert _exhaustive_mismatches(lambda signals: 3 if signals <= 3 else 2, disabled=True) == 0


@pytest.mark.slow
def test_exhaustive_traces_equal_the_reference() -> None:
    """22: up to the per-property length of ``FIXED``."""
    assert _exhaustive_mismatches(lambda signals: 12 // signals) == 0
    assert _exhaustive_mismatches(lambda signals: 12 // signals, disabled=True) == 0


# --- 23: configurations -----------------------------------------------------------------------


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(directive=config_directives(), trace=traces(1, 12))
def test_states_stay_within_the_configurations(directive: Directive, trace: Trace) -> None:
    """23."""
    machine = _config(directive)
    assert machine.configurations[:1] == (frozenset(machine.consequent.start),) or not (
        machine.consequent.letters
    )
    assert len(set(machine.configurations)) == len(machine.configurations)
    assert all(c for c in machine.configurations)
    for state in simulate_configurations(machine, trace).states:
        assert all(0 <= index < len(machine.configurations) for index in state.live)


def test_hand_configurations_of_a_loop_with_a_tail() -> None:
    """23: ``b[*1:$] ##1 c`` has the two configurations {b} and {b, c} (letter numbers by
    construction order: b is letter 0, c is letter 1)."""
    machine = _config(_directive(Implication(_A, _seq(_cat(Repeat(_B, 1, None), 1, 1, _C)), True)))
    assert len(machine.configurations) == 2
    assert machine.configurations[0] == frozenset(machine.consequent.start)
    assert config_size(machine).configurations == 2


def test_unbounded_counterexample_of_example_3_fails_at_the_references_sample() -> None:
    """23: ``a |-> b[*1:$] ##1 c``, a = 1 1 . ., b = 1 . . ., c = . 1 . .: the obligation of
    sample 0 is in {b, c} at sample 1 and matches there; the obligation of sample 1 is in {b}
    and fails there (b low), in the same sample: merged (their union {b, c}) it would match."""
    directive = _directive(Implication(_A, _seq(_cat(Repeat(_B, 1, None), 1, 1, _C)), True))
    trace = Trace(
        (
            {"m.a": 1, "m.b": 1, "m.c": 0},
            {"m.a": 1, "m.b": 0, "m.c": 1},
            {"m.a": 0, "m.b": 0, "m.c": 0},
        )
    )
    simulation = simulate_configurations(_config(directive), trace)
    assert simulation.failures == (1,) and simulation.successes == (1,)
    _compare(directive, trace, simulation)


# --- 24: mutants are caught -------------------------------------------------------------------

# Anchored: "x" starts the obligation; a long run of a signal or one disturbance decides.
_ANCHORED: list[tuple[str, Property, tuple[str, ...]]] = [
    (
        "x |-> a[*1:$] ##1 b",
        Implication(_b("x"), _seq(_cat(Repeat(_A, 1, None), 1, 1, _B)), True),
        ("x", "a", "b"),
    ),
    (
        "x |-> ##[1:$] a ##1 b",
        Implication(_b("x"), _seq(_cat(_cat(TRUE, 1, None, _A), 1, 1, _B)), True),
        ("x", "a", "b"),
    ),
    (
        "x |-> a[*3:$]",
        Implication(_b("x"), _seq(Repeat(_A, 3, None)), True),
        ("x", "a"),
    ),
    (
        "x |=> a[->3] ##1 b",
        Implication(_b("x"), _seq(_cat(Goto(_sig("a"), 3, 3), 1, 1, _B)), False),
        ("x", "a", "b"),
    ),
]
_LENGTH = 8
_POSITIONS = (0, 1, 3, 7)


def _shape_traces(signals: tuple[str, ...]) -> list[Trace]:
    shapes = [
        [1] * _LENGTH,
        [0] * _LENGTH,
        *([int(t == p) for t in range(_LENGTH)] for p in _POSITIONS),
        *([int(t != p) for t in range(_LENGTH)] for p in _POSITIONS),
    ]
    return [
        Trace(
            tuple(
                {f"m.{name}": rows[i][t] for i, name in enumerate(signals)} for t in range(_LENGTH)
            )
        )
        for rows in itertools.product(shapes, repeat=len(signals))
    ]


def _anchored_mismatches(limit: int | None = None) -> int:
    count = 0
    for _, prop, signals in _ANCHORED:
        directive = _directive(prop)
        try:
            machine = _config(directive)
        except CompileError:
            return 10**9
        for trace in _shape_traces(signals):
            try:
                _compare(directive, trace, simulate_configurations(machine, trace))
            except (AssertionError, MachineError):
                count += 1
                if limit is not None and count >= limit:
                    return count
    return count


@pytest.mark.extended
def test_the_anchored_properties_pass_unmutated() -> None:
    assert _anchored_mismatches() == 0


def test_mutant_merged_frontier_instead_of_configurations_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """24: every live obligation is kept in one merged frontier (the union of the live
    frontiers): the known-wrong design of ``docs/strategies.md`` Example 3."""
    from sva2yosys.temporal.machine import StepEvents

    def merged(machine: ConfigMachine, state: Any, elapsed: int, raw: Callable[[int], bool]) -> Any:
        automaton = machine.consequent
        known: dict[int, bool] = {}

        def holds(condition: int) -> bool:
            if elapsed < machine.past_depth[condition]:
                return False
            if condition not in known:
                known[condition] = raw(condition)
            return known[condition]

        if machine.disable is not None and holds(machine.disable):
            return replace(state, antecedent=frozenset(), live=frozenset()), StepEvents(
                True, False, False, False
            )
        antecedent = machine.antecedent
        assert antecedent is not None
        ant_held = {
            i
            for i in state.antecedent | frozenset(antecedent.start)
            if all(holds(g) for g in antecedent.letters[i].guards)
        }
        trigger = any(antecedent.letters[i].accepting for i in ant_held)
        next_ant = frozenset(n for i in ant_held for n in antecedent.letters[i].next)
        frontier: frozenset[int] = getattr(state, "merged", frozenset())
        if trigger:
            frontier |= frozenset(automaton.start)
        held = {i for i in frontier if all(holds(g) for g in automaton.letters[i].guards)}
        accept = any(automaton.letters[i].accepting for i in held)
        following = frozenset(n for i in held for n in automaton.letters[i].next)
        failure = bool(frontier) and not accept and not following
        after = following if not accept else frozenset()
        new = replace(state, antecedent=next_ant, live=frozenset({0}) if after else frozenset())
        object.__setattr__(new, "merged", after)
        return new, StepEvents(False, trigger, accept, failure)

    monkeypatch.setattr("support.simulate.configuration_step", merged)
    assert _anchored_mismatches(limit=1) > 0


def test_mutant_dropped_configuration_transition_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """24: the static construction forgets the configurations discovered last."""
    original = vars(compile_module)["configuration_sets"]

    def forgetful(consequent: Any, limit: int, *, work: Any = None) -> Any:
        found = original(consequent, limit, work=work)
        return found[:-1] if len(found) > 1 else found

    monkeypatch.setattr(compile_module, "configuration_sets", forgetful)
    assert _anchored_mismatches(limit=1) > 0


def test_mutant_repetition_to_dollar_read_as_a_finite_limit_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """24: ``[*m:$]`` built as ``[*m:m+2]``."""
    builder: Any = vars(automaton_module)["_Builder"]

    def finite(self: Any, inner: Sequence, low: int) -> Any:
        return builder._repeat(self, inner, low, low + 2)

    monkeypatch.setattr(builder, "_repeat_unbounded", finite)
    assert _anchored_mismatches(limit=1) > 0


@pytest.mark.extended
def test_mutant_delay_to_dollar_read_as_a_finite_limit_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """24: ``##[m:$]`` built as ``##[m:m+2]``."""
    builder: Any = vars(automaton_module)["_Builder"]

    def finite(self: Any, left: Any, right: Any, low: int) -> Any:
        return builder._concat(self, left, right, low, low + 2)

    monkeypatch.setattr(builder, "_concat_unbounded", finite)
    assert _anchored_mismatches(limit=1) > 0


# --- 25: budgets ------------------------------------------------------------------------------


def _blowup(width: int) -> Property:
    """``a |-> ##[1:$] b ##width c``: the width after ``b`` gives 2**width configurations."""
    return Implication(_A, _seq(_cat(_cat(TRUE, 1, None, _B), width, width, _C)), True)


def test_a_small_blowup_is_accepted_and_a_large_one_rejected_quickly() -> None:
    """25."""
    machine = _config(_directive(_blowup(3)))
    assert 4 <= len(machine.configurations) <= Budgets().configurations
    for width in (12, 64):
        started = time.monotonic()
        with pytest.raises(CompileError, match="configurations"):
            compile_monitors(_directive(_blowup(width)))
        assert time.monotonic() - started < 5


def test_the_configurations_budget_is_a_parameter() -> None:
    """25."""
    directive = _directive(_blowup(3))
    count = len(_config(directive).configurations)
    assert compile_configuration_machine(directive, Budgets(configurations=count)).configurations
    with pytest.raises(CompileError, match="configurations"):
        compile_configuration_machine(directive, Budgets(configurations=count - 1))


def test_a_huge_range_is_rejected_before_any_range_is_iterated() -> None:
    """25."""
    huge = Implication(_A, _seq(_cat(_cat(TRUE, 1, None, _B), 10**9, 10**9, _C)), True)
    started = time.monotonic()
    with pytest.raises(CompileError, match="letters"):
        compile_monitors(_directive(huge))
    assert time.monotonic() - started < 5


# --- 26: constants (Annex F), superseded by 29-33 ---------------------------------------------

_FALSE = Bool(Const(0, BIT))


@pytest.mark.parametrize(
    "consequent",
    [
        _cat(TRUE, 1, None, _FALSE),
        _cat(Repeat(_B, 1, None), 1, 1, _FALSE),
        SeqOr(Repeat(_B, 1, None), _FALSE),
        _cat(_FALSE, 1, 1, Repeat(_B, 1, None)),
        Goto(Const(0, BIT), 1, 1),
    ],
)
def test_a_constant_false_condition_in_an_unbounded_consequent_is_accepted(
    consequent: Sequence,
) -> None:
    """26: no longer rejected; the configuration-set machine is built."""
    for prop in (Implication(_A, _seq(consequent), True), _seq(consequent)):
        (monitor,) = compile_monitors(_directive(prop))
        assert isinstance(monitor, ConfigMachine)


def test_a_constant_true_condition_is_accepted() -> None:
    """26."""
    one = Bool(Const(1, BIT))
    machine = _config(_directive(Implication(_A, _seq(_cat(Repeat(_B, 1, None), 1, 1, one)), True)))
    assert machine.configurations


def test_a_constant_false_condition_in_a_bounded_consequent_is_a_slot_letter() -> None:
    """26."""
    (monitor,) = compile_monitors(_directive(Implication(_A, _seq(_cat(TRUE, 1, 2, _FALSE)), True)))
    assert isinstance(monitor, Machine)
    assert monitor.slots == 2


# --- 27: dispatch -----------------------------------------------------------------------------


def test_unbounded_consequents_never_reach_the_slot_or_the_attempt_machine() -> None:
    """27."""
    directive = _directive(Implication(_A, _seq(Repeat(_B, 1, None)), True))
    with pytest.raises(CompileError):
        compile_directive(directive)
    with pytest.raises(CompileError):
        compile_attempt_machine(directive)
    assert not isinstance(compile_monitors(directive)[0], (Machine, AttemptMachine))


def test_still_rejected_shapes() -> None:
    """27: a cover keeps its rejection; a strong unbounded
    property, also ``not`` of a weak one, is accepted for an ``assert``
    and for an ``assume`` only in the one-configuration class."""
    from sva2yosys.ir.property import Not, Until

    assume = DirectiveKind.ASSUME
    # A repetition that can match at its trigger is L1, including under negation.
    accepted = [
        _directive(Implication(_A, _seq(Repeat(_B, 1, None), Strength.STRONG), True), kind=assume),
        _directive(_seq(Repeat(_B, 1, None), Strength.STRONG), kind=assume),
        _directive(Implication(_A, Not(_seq(Repeat(_B, 1, None))), True), kind=assume),
    ]
    for directive in accepted:
        (monitor,) = compile_monitors(directive)
        assert isinstance(monitor, ConfigMachine)
        assert len(monitor.configurations) <= 1 and monitor.liveness is not None
    rejected: list[Directive] = [
        _directive(Not(_seq(_cat(TRUE, 1, None, _B))), kind=assume),
        _directive(Until(_sig("a"), _sig("b"), False, False), kind=DirectiveKind.COVER),
        # (a cover of a strong unbounded sequence is accepted; a weak one is not)
        _directive(
            Implication(_A, _seq(Repeat(_B, 1, None), Strength.WEAK), True),
            kind=DirectiveKind.COVER,
        ),
        _directive(_seq(Repeat(_B, 1, None), Strength.WEAK), kind=DirectiveKind.COVER),
    ]
    for directive in rejected:
        with pytest.raises(CompileError):
            compile_monitors(directive)


def test_generated_directives_have_unbounded_parts() -> None:
    @settings(max_examples=50, deadline=None, derandomize=True)
    @given(directive=config_directives())
    def check(directive: Directive) -> None:
        assert has_unbounded_part(directive.property)

    check()


def test_the_differential_oracle_does_not_share_the_product_lowering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """21: ``until`` lowered as ``until_with`` makes ``a |-> b until c`` fail on a=1, b=0, c=1;
    the comparison with ``evaluate()`` itself (no test-side desugaring) must notice."""
    from sva2yosys.ir.property import Until
    from sva2yosys.temporal import lower as lower_module

    directive = _directive(Implication(_A, Until(_sig("b"), _sig("c"), False, False), True))
    trace = Trace(({"m.a": 1, "m.b": 0, "m.c": 1},))
    _compare(directive, trace, simulate_configurations(_config(directive), trace))

    original = lower_module.until_sequence
    monkeypatch.setattr(
        lower_module, "until_sequence", lambda p: original(replace(p, inclusive=True))
    )
    with pytest.raises(AssertionError):
        _compare(directive, trace, simulate_configurations(_config(directive), trace))
