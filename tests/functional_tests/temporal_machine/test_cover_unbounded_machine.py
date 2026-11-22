"""Layer 2: covers of unbounded sequences through the configuration-set machine
against the reference evaluator. See CONTRACT.md in this folder, points 41-48.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings

from support.ir_strategies import cover_config_directives, has_unbounded_part, traces
from support.reference.evaluate import Verdict, evaluate, evaluate_attempts
from support.reference.trace import Trace
from support.simulate import ConfigSimulation, simulate_configurations
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Const, Expr, Sampled, SampledFunction, Signal
from sva2yosys.ir.property import (
    Always,
    Eventually,
    Implication,
    Not,
    Property,
    PropOr,
    SequenceProperty,
    Strength,
    Until,
)
from sva2yosys.ir.sequence import (
    TRUE,
    Bool,
    Delay,
    Goto,
    NonConsecutive,
    Repeat,
    SeqConcat,
    SeqOr,
    Sequence,
)
from sva2yosys.ir.source import SourceRange
from sva2yosys.passes import validate as validate_module
from sva2yosys.passes.validate import CoverUnbounded, validation_problems
from sva2yosys.temporal.compile import (
    CompileError,
    compile_attempt_machine,
    compile_configuration_machine,
    compile_directive,
    compile_monitors,
)
from sva2yosys.temporal.configurations import (
    ConfigMachine,
    ConfigState,
    config_size,
    configuration_step,
)
from sva2yosys.temporal.machine import Machine, MachineError, StepEvents

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
_SOURCE = SourceRange("cover.sv", 1, 1, 1, 2)


def _sig(name: str) -> Signal:
    return Signal(f"m.{name}", BIT)


def _b(name: str) -> Bool:
    return Bool(_sig(name))


def _cat(left: Sequence, low: int, high: int | None, right: Sequence) -> SeqConcat:
    return SeqConcat(left, Delay(low, high), right)


def _seq(sequence: Sequence, strength: Strength = Strength.DEFAULT) -> SequenceProperty:
    return SequenceProperty(sequence, strength)


_A, _B, _C, _D = (_b(name) for name in "abcd")
_PAST_A = Bool(Sampled(SampledFunction.PAST, _sig("a"), BIT))


def _cover(prop: Property, disable: Expr | None = None) -> Directive:
    return Directive(DirectiveKind.COVER, "p", "m", _CLOCK, disable, prop, _SOURCE)


def _config(directive: Directive) -> ConfigMachine:
    (monitor,) = compile_monitors(directive)
    assert isinstance(monitor, ConfigMachine)
    return monitor


def _reference_pending(directive: Directive, trace: Trace) -> bool:
    result = evaluate(directive.property, trace, directive.disable)
    return any(
        obligation.verdict is Verdict.PENDING
        for attempt in result.attempts
        for obligation in attempt.obligations
    )


def _compare(directive: Directive, trace: Trace, simulation: ConfigSimulation) -> None:
    """The successes a cover consumes (and "some obligation is live") equal the reference's
    on every prefix of ``trace``, and the successes equal the per-attempt oracle's (what a
    cover reports: one success per attempt, nonvacuous, at its first match)."""
    for end in range(1, len(trace) + 1):
        prefix = Trace(trace.samples[:end])
        expected = evaluate(directive.property, prefix, directive.disable)
        assert [t for t in simulation.successes if t < end] == list(expected.successes)
        assert bool(simulation.states[end - 1].live) == _reference_pending(directive, prefix)
    attempts = evaluate_attempts(directive.property, trace, directive.disable)
    assert set(simulation.successes) == set(attempts.successes), "per-attempt successes"


# --- 42: differential, every prefix -----------------------------------------------------------


@pytest.mark.extended
@_DIFFERENTIAL
@given(directive=cover_config_directives(), trace=traces(1, 10))
def test_cover_machine_equals_reference_on_every_prefix(directive: Directive, trace: Trace) -> None:
    """42."""
    _compare(directive, trace, simulate_configurations(_config(directive), trace))


@pytest.mark.slow
@settings(
    max_examples=60,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(directive=cover_config_directives(), trace=traces(11, 16))
def test_cover_machine_equals_reference_on_long_traces(directive: Directive, trace: Trace) -> None:
    """42: traces long enough for loops to run many times."""
    _compare(directive, trace, simulate_configurations(_config(directive), trace))


def test_generated_covers_compile_to_one_configuration_machine() -> None:
    """42."""
    seen = {"sequence": 0, "overlapping": 0, "nonoverlapping": 0}

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(directive=cover_config_directives())
    def check(directive: Directive) -> None:
        monitors = compile_monitors(directive)
        assert len(monitors) == 1 and isinstance(monitors[0], ConfigMachine)
        assert monitors[0] == compile_configuration_machine(directive)
        prop = directive.property
        if isinstance(prop, SequenceProperty):
            seen["sequence"] += 1
        else:
            assert isinstance(prop, Implication)
            seen["overlapping" if prop.overlapping else "nonoverlapping"] += 1

    check()
    assert all(seen.values()), seen


# --- 43: exhaustive on fixed covers -----------------------------------------------------------

# (name, property, signals, longest trace): every trace of every length up to the longest
FIXED: list[tuple[str, Property, tuple[str, ...], int]] = [
    ("a ##[1:$] b", _seq(_cat(_A, 1, None, _B)), ("a", "b"), 7),
    ("a ##[0:$] b", _seq(_cat(_A, 0, None, _B)), ("a", "b"), 7),
    ("a[*2:$] ##1 b", _seq(_cat(Repeat(_A, 2, None), 1, 1, _B)), ("a", "b"), 7),
    ("b[->2]", _seq(Goto(_sig("b"), 2, 2)), ("b",), 8),
    ("b[->1:$]", _seq(Goto(_sig("b"), 1, None)), ("b",), 8),
    (
        "b[=2] ##1 c",
        _seq(_cat(NonConsecutive(_sig("b"), 2, 2), 1, 1, _C)),
        ("b", "c"),
        6,
    ),
    ("b[*1:$]", _seq(Repeat(_B, 1, None)), ("b",), 8),
    (
        "a ##1 b[*1:$] ##1 c",
        _seq(_cat(_cat(_A, 1, 1, Repeat(_B, 1, None)), 1, 1, _C)),
        ("a", "b", "c"),
        5,
    ),
    ("strong(a ##[1:$] b)", _seq(_cat(_A, 1, None, _B), Strength.STRONG), ("a", "b"), 7),
    ("a |-> ##[1:$] b", Implication(_A, _seq(_cat(TRUE, 1, None, _B)), True), ("a", "b"), 7),
    ("a |-> ##[0:$] b", Implication(_A, _seq(_cat(TRUE, 0, None, _B)), True), ("a", "b"), 7),
    (
        "a |=> b[*1:$] ##1 c",
        Implication(_A, _seq(_cat(Repeat(_B, 1, None), 1, 1, _C)), False),
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
        "a |-> strong(b[->2] ##1 c)",
        Implication(_A, _seq(_cat(Goto(_sig("b"), 2, 2), 1, 1, _C), Strength.STRONG), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "$past(a) ##[1:$] b",
        _seq(_cat(_PAST_A, 1, None, _B)),
        ("a", "b"),
        7,
    ),
    (
        "a |-> ##[1:$] $past(b)",
        Implication(
            _A, _seq(_cat(TRUE, 1, None, Bool(Sampled(SampledFunction.PAST, _sig("b"), BIT)))), True
        ),
        ("a", "b"),
        7,
    ),
    ("a ##[1:$] 1'b0", _seq(_cat(_A, 1, None, Bool(Const(0, BIT)))), ("a",), 8),
    (
        "a |-> ##[1:$] 1'b0",
        Implication(_A, _seq(_cat(TRUE, 1, None, Bool(Const(0, BIT)))), True),
        ("a",),
        8,
    ),
    (
        "(b[*1:$] or c ##2 d)",
        _seq(SeqOr(Repeat(_B, 1, None), _cat(_C, 2, 2, _D))),
        ("b", "c", "d"),
        4,
    ),
    # a nullable antecedent with one non-empty length: the empty match is ignored
    (
        "a[*0:1] |-> ##[1:$] b",
        Implication(Repeat(_A, 0, 1), _seq(_cat(TRUE, 1, None, _B)), True),
        ("a", "b"),
        7,
    ),
    (
        "a[*0:1] |-> b[*1:$]",
        Implication(Repeat(_A, 0, 1), _seq(Repeat(_B, 1, None)), True),
        ("a", "b"),
        7,
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
    for _, prop, signals, longest_trace in FIXED:
        names = (*signals, "z") if disabled else signals
        directive = _cover(prop, _sig("z") if disabled else None)
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


def test_the_fixed_covers_compile_to_the_configuration_machine() -> None:
    for name, prop, _, _ in FIXED:
        assert isinstance(compile_monitors(_cover(prop))[0], ConfigMachine), name


@pytest.mark.extended
def test_exhaustive_short_traces_equal_the_reference() -> None:
    """43: up to 3 samples (2 with ``disable iff``)."""
    assert _exhaustive_mismatches(lambda signals: 3 if signals <= 3 else 2) == 0
    assert _exhaustive_mismatches(lambda signals: 3 if signals <= 3 else 2, disabled=True) == 0


@pytest.mark.slow
def test_exhaustive_traces_equal_the_reference() -> None:
    """43: up to the per-property length of ``FIXED``."""
    assert _exhaustive_mismatches(lambda signals: 12 // signals) == 0
    assert _exhaustive_mismatches(lambda signals: 12 // signals, disabled=True) == 0


# --- 44: counted once ----------------------------------------------------------------------------


def _rows(**signals: str) -> Trace:
    """A trace from rows of 0/1/. characters, one per signal."""
    length = len(next(iter(signals.values())))
    return Trace(
        tuple(
            {f"m.{name}": int(row[t] == "1") for name, row in signals.items()}
            for t in range(length)
        )
    )


def test_two_attempts_in_one_configuration_give_one_success_sample() -> None:
    """44: ``a ##1 b[*1:$] ##1 c``, a = 1 1 . . ., b = . 1 1 1 ., c = . . . . 1: the attempts of
    samples 0 and 1 are both in the configuration {b, c} at sample 3 (one live bit), and both
    match at 4."""
    directive = _cover(FIXED[7][1])
    trace = _rows(a="11...", b=".111.", c="....1")
    simulation = simulate_configurations(_config(directive), trace)
    assert simulation.successes == (4,)
    assert len(simulation.states[3].live) == 1  # merged: one configuration for two attempts
    _compare(directive, trace, simulation)


def test_two_attempts_in_different_configurations_give_one_success_sample() -> None:
    """44: ``(a ##[1:$] c) or (d ##1 c)``, a = 1 . . ., d = . 1 . ., c = . . 1: after sample 1
    the attempt of sample 0 is in {loop, c} and the attempt of sample 1 in {c}, two live
    configurations; both match at 2: one success sample."""
    prop = _seq(SeqOr(_cat(_A, 1, None, _C), _cat(_D, 1, 1, _C)))
    directive = _cover(prop)
    trace = _rows(a="1...", d=".1..", c="..1.")
    simulation = simulate_configurations(_config(directive), trace)
    assert simulation.successes == (2,)
    assert len(simulation.states[1].live) == 2
    _compare(directive, trace, simulation)


def test_attempts_entering_a_configuration_from_different_ones_merge() -> None:
    """44: ``a |=> b[*1:$] ##1 c``, a = 1 . 1 . . ., b = . 1 1 1 . ., c = . . . . 1 .: the
    obligation of sample 0 is in {b, c} when the one of sample 2 starts in {b}; after sample 3
    both are in {b, c} (one live bit) and both match at 4."""
    directive = _cover(FIXED[11][1])
    trace = _rows(a="1.1...", b=".111..", c="....1.")
    simulation = simulate_configurations(_config(directive), trace)
    assert simulation.successes == (4,)
    assert len(simulation.states[2].live) == 1 and len(simulation.states[3].live) == 1
    _compare(directive, trace, simulation)


def test_an_attempt_is_not_counted_again_at_a_later_match() -> None:
    """44: ``a ##[1:$] b``, a = 1 . . . ., b = . 1 . 1 .: the match at 1 is the attempt's first;
    the match at 3 belongs to the same attempt and is not counted."""
    directive = _cover(FIXED[0][1])
    trace = _rows(a="1....", b=".1.1.")
    simulation = simulate_configurations(_config(directive), trace)
    assert simulation.successes == (1,)
    assert not simulation.live
    _compare(directive, trace, simulation)


def test_a_frontier_that_dies_gives_no_success() -> None:
    """44: ``a[*2:$] ##1 b``, a = 1 1 . ., b = . . 1 .: the attempt of sample 1 dies at 2 (a
    run of two is impossible) although ``b`` holds there; the attempt of sample 0 matches."""
    directive = _cover(FIXED[2][1])
    trace = _rows(a="11.1.", b="..1..")
    simulation = simulate_configurations(_config(directive), trace)
    assert simulation.successes == (2,)
    _compare(directive, trace, simulation)


def test_a_disabled_pending_attempt_never_counts() -> None:
    """44: ``disable iff (d) a ##[1:$] b`` with ``d`` high while the attempt is pending."""
    directive = _cover(FIXED[0][1], _sig("d"))
    trace = _rows(a="1.1...", b=".1...1", d="...1..")
    simulation = simulate_configurations(_config(directive), trace)
    assert simulation.successes == (1,)
    assert simulation.states[3].live == frozenset()
    _compare(directive, trace, simulation)


def test_an_implication_counts_only_non_vacuous_successes() -> None:
    """44: ``a |-> ##[1:$] b`` with ``a`` low everywhere: no obligation, no success."""
    directive = _cover(FIXED[9][1])
    trace = _rows(a=".....", b="11111")
    simulation = simulate_configurations(_config(directive), trace)
    assert simulation.successes == () and not simulation.live
    _compare(directive, trace, simulation)


def test_constant_conditions() -> None:
    """44: ``a ##[1:$] 1'b0`` is never covered and stays pending; ``a ##[1:$] 1'b1`` matches at
    the next sample."""
    never = _cover(FIXED[16][1])
    trace = _rows(a="1.1.")
    simulation = simulate_configurations(_config(never), trace)
    assert simulation.successes == () and simulation.live
    _compare(never, trace, simulation)
    always = _cover(_seq(_cat(_A, 1, None, Bool(Const(1, BIT)))))
    simulation = simulate_configurations(_config(always), trace)
    assert simulation.successes == (1, 3)
    _compare(always, trace, simulation)


# --- 45: mutants are caught ---------------------------------------------------------------------

# Anchored: "x" starts the attempt; a long run of a signal or one disturbance decides.
_ANCHORED: list[tuple[str, Property, tuple[str, ...]]] = [
    (
        "x ##1 a[*1:$] ##1 b",
        _seq(_cat(_cat(_b("x"), 1, 1, Repeat(_A, 1, None)), 1, 1, _B)),
        ("x", "a", "b"),
    ),
    ("x ##[1:$] a", _seq(_cat(_b("x"), 1, None, _A)), ("x", "a")),
    ("x |-> a[*2:$]", Implication(_b("x"), _seq(Repeat(_A, 2, None)), True), ("x", "a")),
    (
        "x |=> a[->2] ##1 b",
        Implication(_b("x"), _seq(_cat(Goto(_sig("a"), 2, 2), 1, 1, _B)), False),
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


def _mismatches(
    cases: list[tuple[str, Property, tuple[str, ...]]], limit: int | None = None
) -> int:
    count = 0
    for _, prop, signals in cases:
        directive = _cover(prop)
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
def test_the_anchored_covers_pass_unmutated() -> None:
    assert _mismatches(_ANCHORED) == 0


def test_mutant_success_when_the_frontier_is_merely_alive_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """45: a pending obligation (a live frontier) is reported as a match."""
    original = configuration_step

    def alive(machine: ConfigMachine, state: ConfigState, elapsed: int, raw: Any) -> Any:
        after, events = original(machine, state, elapsed, raw)
        return after, replace(events, success=events.success or bool(after.live))

    monkeypatch.setattr("support.simulate.configuration_step", alive)
    assert _mismatches(_ANCHORED, limit=1) > 0


@pytest.mark.extended
def test_mutant_success_counted_for_a_non_first_match_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """45: an obligation that has matched is kept alive on the successors of its other held
    letters (and counted again at a later match)."""

    def keeps_going(machine: ConfigMachine, state: ConfigState, elapsed: int, raw: Any) -> Any:
        known: dict[int, bool] = {}

        def holds(condition: int) -> bool:
            if elapsed < machine.past_depth[condition]:
                return False
            if condition not in known:
                known[condition] = raw(condition)
            return known[condition]

        if machine.disable is not None and holds(machine.disable):
            return ConfigState(frozenset(), frozenset()), StepEvents(True, False, False, False)
        antecedent = machine.antecedent
        next_antecedent: frozenset[int] = frozenset()
        triggered = True
        if antecedent is not None:
            ant_held = frozenset(
                i
                for i in state.antecedent | frozenset(antecedent.start)
                if all(holds(g) for g in antecedent.letters[i].guards)
            )
            triggered = any(antecedent.letters[i].accepting for i in ant_held)
            next_antecedent = frozenset(n for i in ant_held for n in antecedent.letters[i].next)
        consequent = machine.consequent
        obligations = set(state.live) | ({0} if triggered else set())
        success = False
        live: set[int] = set()
        for index in sorted(obligations):
            held = [
                i
                for i in sorted(machine.configurations[index])
                if all(holds(g) for g in consequent.letters[i].guards)
            ]
            success = success or any(consequent.letters[i].accepting for i in held)
            following = frozenset(n for i in held for n in consequent.letters[i].next)
            if following:  # (the mutation: no discharge at a match)
                try:
                    live.add(machine.configurations.index(following))
                except ValueError:
                    raise MachineError("frontier outside the configurations") from None
        return ConfigState(next_antecedent, frozenset(live)), StepEvents(
            False, triggered, success, False
        )

    monkeypatch.setattr("support.simulate.configuration_step", keeps_going)
    assert _mismatches(_ANCHORED, limit=1) > 0


_MULTI_LENGTH: list[tuple[str, Property, tuple[str, ...]]] = [
    (
        "x ##[1:2] y |-> ##[1:$] z",
        Implication(_cat(_b("x"), 1, 2, _b("y")), _seq(_cat(TRUE, 1, None, _b("z"))), True),
        ("x", "y", "z"),
    ),
    (
        "x ##[1:2] y |-> z[*2:$]",
        Implication(_cat(_b("x"), 1, 2, _b("y")), _seq(Repeat(_b("z"), 2, None)), True),
        ("x", "y", "z"),
    ),
]


def test_mutant_antecedent_of_several_lengths_accepted_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """45: an attempt with several antecedent matches succeeds only when all their consequents
    do (the reference); a machine that merges them reports the first success."""
    for _, prop, _ in _MULTI_LENGTH:
        assert validation_problems(_cover(prop)), "rejected unmutated"
    real = validate_module.cover_unbounded

    def accepts_any_antecedent(written: Property) -> CoverUnbounded:
        if isinstance(written, Implication):
            return CoverUnbounded(True, ())
        return real(written)

    monkeypatch.setattr(validate_module, "cover_unbounded", accepts_any_antecedent)
    for _, prop, _ in _MULTI_LENGTH:
        assert validation_problems(_cover(prop)) == (), "accepted under the mutant"
    assert _mismatches(_MULTI_LENGTH, limit=1) > 0


_WEAK_COVERS: list[Property] = [
    _seq(_cat(_A, 1, None, _B), Strength.WEAK),
    Implication(_A, _seq(_cat(TRUE, 1, None, _B), Strength.WEAK), True),
    _seq(Goto(_sig("b"), 2, 2), Strength.WEAK),
]


def _weak_covers_rejected() -> bool:
    return all(
        any("weak unbounded sequence" in p for p in validation_problems(_cover(prop)))
        for prop in _WEAK_COVERS
    )


def test_mutant_weak_unbounded_cover_accepted_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """45: the weak-cover rejection is what the tests check; a validation that lets a weak
    unbounded sequence through is noticed by it."""
    assert _weak_covers_rejected()
    real = validate_module.cover_unbounded

    def ignores_strength(written: Property) -> CoverUnbounded:
        def strengthen(prop: Property) -> Property:
            match prop:
                case SequenceProperty(sequence=body):
                    return SequenceProperty(body, Strength.DEFAULT)
                case Implication(antecedent=a, consequent=c, overlapping=o):
                    return Implication(a, strengthen(c), o)
                case _:
                    return prop

        return real(strengthen(written))

    monkeypatch.setattr(validate_module, "cover_unbounded", ignores_strength)
    assert not _weak_covers_rejected()


# --- 46: rejections ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prop", "message"),
    [
        (_seq(_cat(_A, 1, None, _B), Strength.WEAK), "a cover of a weak unbounded sequence"),
        (Until(_sig("a"), _sig("b"), False, False), "a cover of 'until' is not supported"),
        (Until(_sig("a"), _sig("b"), False, True), "a cover of 'until_with' is not supported"),
        (
            Implication(Repeat(_A, 1, None), _seq(Repeat(_B, 1, None)), True),
            "a cover of an implication with an unbounded antecedent",
        ),
        (
            Implication(_cat(_A, 1, 2, _B), _seq(Repeat(_C, 1, None)), True),
            "a single match length",
        ),
        (Not(_seq(_cat(_A, 1, None, _B))), "not supported yet"),
        (Always(0, None, False, _seq(_B)), "unbounded 'always'"),
        (Eventually(0, None, True, _seq(_B)), "'s_eventually'"),
        (PropOr((_seq(_B), _seq(Repeat(_C, 1, None)))), "not supported yet"),
    ],
)
def test_the_rejected_covers_are_rejected_with_a_located_message(
    prop: Property, message: str
) -> None:
    with pytest.raises(CompileError, match=message):
        compile_monitors(_cover(prop))


# --- 47: dispatch ---------------------------------------------------------------------------------


def test_an_unbounded_cover_reaches_neither_the_slot_nor_the_attempt_machine() -> None:
    directive = _cover(Implication(_A, _seq(Repeat(_B, 1, None)), True))
    with pytest.raises(CompileError):
        compile_directive(directive)
    with pytest.raises(CompileError):
        compile_attempt_machine(directive)
    assert not isinstance(compile_monitors(directive)[0], Machine)


def test_a_bounded_cover_is_unchanged() -> None:
    directive = _cover(Implication(_A, _seq(_cat(TRUE, 1, 3, _B)), True))
    assert isinstance(compile_monitors(directive)[0], Machine)
    assert not has_unbounded_part(directive.property)


# --- 48: model sizes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "configurations", "antecedent_bits"),
    [
        (0, 2, 0),  # a ##[1:$] b: {a}, {loop, b}
        (9, 2, 0),  # a |-> ##[1:$] b
        (11, 2, 1),  # a |=> b[*1:$] ##1 c: {b}, {b, c}; the ##1 of |=>
        (3, 3, 0),  # b[->2]
        (2, 3, 0),  # a[*2:$] ##1 b
        (5, 6, 0),  # b[=2] ##1 c
    ],
)
def test_the_model_sizes_of_the_hand_cases(
    index: int, configurations: int, antecedent_bits: int
) -> None:
    size = config_size(_config(_cover(FIXED[index][1])))
    assert size.configurations == configurations, FIXED[index][0]
    assert size.antecedent_bits == antecedent_bits, FIXED[index][0]
    assert size.temporal_bits == configurations + antecedent_bits
