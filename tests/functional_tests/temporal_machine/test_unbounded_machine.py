"""Layer 2: the canonical machine for unbounded antecedents and unbounded ``always``,
against the reference evaluator. See CONTRACT.md in this folder, points 15-20.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings

from support.ir_strategies import has_unbounded_part, traces, unbounded_directives
from support.reference.evaluate import Verdict, evaluate
from support.reference.trace import Trace
from support.simulate import Simulation, simulate
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Const, Expr, Signal
from sva2yosys.ir.property import (
    Always,
    Implication,
    Not,
    Property,
    SequenceProperty,
)
from sva2yosys.ir.sequence import (
    Bool,
    Delay,
    Goto,
    NonConsecutive,
    Repeat,
    SeqConcat,
    Sequence,
)
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal import automaton as automaton_module
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.compile import (
    CompileError,
    compile_attempt_machine,
    compile_directive,
    compile_monitors,
)
from sva2yosys.temporal.machine import Machine, MachineError, antecedent_reach, slot_reach

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
_SOURCE = SourceRange("unbounded.sv", 1, 1, 1, 2)


def _reference_pending(directive: Directive, trace: Trace) -> int:
    """Distinct pending obligation starts; obligations sharing a start must agree."""
    result = evaluate(directive.property, trace, directive.disable)
    verdicts: dict[int, set[Verdict]] = {}
    for attempt in result.attempts:
        for obligation in attempt.obligations:
            if obligation.verdict is not Verdict.DISABLED:
                verdicts.setdefault(obligation.start, set()).add(obligation.verdict)
    assert all(len(v) == 1 for v in verdicts.values()), "obligations with one start disagree"
    return sum(1 for v in verdicts.values() if Verdict.PENDING in v)


def _check_every_prefix(directive: Directive, trace: Trace, simulation: Simulation) -> None:
    for end in range(1, len(trace) + 1):
        prefix = Trace(trace.samples[:end])
        expected = evaluate(directive.property, prefix, directive.disable)
        assert [t for t in simulation.failures if t < end] == list(expected.failures)
        assert [t for t in simulation.successes if t < end] == list(expected.successes)
        open_slots = sum(1 for slot in simulation.snapshots[end - 1].after.slots if slot)
        assert open_slots == _reference_pending(directive, prefix)


# --- 15: differential, every prefix ----------------------------------------------------------


@pytest.mark.extended
@_DIFFERENTIAL
@given(directive=unbounded_directives(), trace=traces(1, 10))
def test_unbounded_machine_equals_reference_on_every_prefix(
    directive: Directive, trace: Trace
) -> None:
    """15: failures, successes and pending obligations of the canonical machine equal the
    reference evaluation of every prefix, for generated unbounded antecedents and unbounded
    ``always`` (``assert`` and ``assume``), with and without ``disable iff``."""
    machine = compile_directive(directive)
    _check_every_prefix(directive, trace, simulate(machine, trace))


@pytest.mark.slow
@settings(
    max_examples=60,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(directive=unbounded_directives(), trace=traces(11, 16))
def test_unbounded_machine_equals_reference_on_long_traces(
    directive: Directive, trace: Trace
) -> None:
    """15: the same on traces long enough for loops to run many times."""
    _check_every_prefix(directive, trace, simulate(compile_directive(directive), trace))


def test_generated_unbounded_directives_are_unbounded_and_canonical() -> None:
    """15: the strategy's directives compile to exactly one canonical machine each, and
    it generates both forms (and bounded parts too, as the unbounded ones mix with them)."""
    seen = {"always": 0, "antecedent": 0, "unbounded": 0}

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(directive=unbounded_directives())
    def check(directive: Directive) -> None:
        seen["unbounded"] += has_unbounded_part(directive.property)
        monitors = compile_monitors(directive)
        assert len(monitors) == 1 and isinstance(monitors[0], Machine)
        assert monitors[0] == compile_directive(directive)
        if isinstance(directive.property, Always):
            seen["always"] += 1
        else:
            seen["antecedent"] += 1

    check()
    assert seen["always"] and seen["antecedent"] and seen["unbounded"] > 50


# --- 16: exhaustive on fixed properties --------------------------------------------------------


def _sig(name: str) -> Signal:
    return Signal(f"m.{name}", BIT)


def _b(name: str) -> Bool:
    return Bool(_sig(name))


def _cat(left: Sequence, low: int, high: int | None, right: Sequence) -> SeqConcat:
    return SeqConcat(left, Delay(low, high), right)


def _seq(sequence: Sequence) -> SequenceProperty:
    return SequenceProperty(sequence)


_A, _B, _C, _D = (_b(name) for name in "abcd")
_TRUE = Bool(Const(1, BIT))
_FALSE_SEQ = _seq(Bool(Const(0, BIT)))

# (name, property, signals, longest trace): every trace of every length up to the longest
FIXED: list[tuple[str, Property, tuple[str, ...], int]] = [
    ("a ##[1:$] b |-> c", Implication(_cat(_A, 1, None, _B), _seq(_C), True), ("a", "b", "c"), 5),
    ("a ##[0:$] b |-> c", Implication(_cat(_A, 0, None, _B), _seq(_C), True), ("a", "b", "c"), 5),
    (
        "a ##[2:$] b |=> c",
        Implication(_cat(_A, 2, None, _B), _seq(_C), False),
        ("a", "b", "c"),
        5,
    ),
    (
        "a[*1:$] ##1 b |=> c",
        Implication(_cat(Repeat(_A, 1, None), 1, 1, _B), _seq(_C), False),
        ("a", "b", "c"),
        5,
    ),
    (
        "a[*2:$] ##1 b |-> not c",
        Implication(_cat(Repeat(_A, 2, None), 1, 1, _B), Not(_seq(_C)), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "(a ##1 b)[*1:$] |-> c",
        Implication(Repeat(_cat(_A, 1, 1, _B), 1, None), _seq(_C), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a[*0:$] ##1 b |-> c",
        Implication(_cat(Repeat(_A, 0, None), 1, 1, _B), _seq(_C), True),
        ("a", "b", "c"),
        5,
    ),
    ("b[->2] |-> c", Implication(Goto(_sig("b"), 2, 2), _seq(_C), True), ("b", "c"), 7),
    ("b[->1:$] |=> c", Implication(Goto(_sig("b"), 1, None), _seq(_C), False), ("b", "c"), 7),
    (
        "b[=2] ##1 c |-> d",
        Implication(_cat(NonConsecutive(_sig("b"), 2, 2), 1, 1, _C), _seq(_D), True),
        ("b", "c", "d"),
        5,
    ),
    (
        "b[=0:$] ##1 c |-> d",
        Implication(_cat(NonConsecutive(_sig("b"), 0, None), 1, 1, _C), _seq(_D), True),
        ("b", "c", "d"),
        5,
    ),
    ("always b", Always(0, None, False, _seq(_B)), ("b",), 9),
    ("always [2:$] not b", Always(2, None, False, Not(_seq(_B))), ("b",), 9),
    (
        "always [1:$] (a |-> ##1 b)",
        Always(1, None, False, Implication(_A, _seq(_cat(_TRUE, 1, 1, _B)), True)),
        ("a", "b"),
        7,
    ),
    (
        "always (a ##1 b |=> c)",
        Always(0, None, False, Implication(_cat(_A, 1, 1, _B), _seq(_C), False)),
        ("a", "b", "c"),
        5,
    ),
    ("a |-> always b", Implication(_A, Always(0, None, False, _seq(_B)), True), ("a", "b"), 8),
    (
        "a ##1 b |=> always [2:$] c",
        Implication(_cat(_A, 1, 1, _B), Always(2, None, False, _seq(_C)), False),
        ("a", "b", "c"),
        5,
    ),
    (
        "a[*1:$] |-> always (b |-> ##[1:2] c)",
        Implication(
            Repeat(_A, 1, None),
            Always(0, None, False, Implication(_B, _seq(_cat(_TRUE, 1, 2, _C)), True)),
            True,
        ),
        ("a", "b", "c"),
        5,
    ),
]


def _directive(prop: Property, disable: Expr | None = None) -> Directive:
    return Directive(DirectiveKind.ASSERT, "p", "m", _CLOCK, disable, prop, _SOURCE)


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
    """The number of (property, trace) cases on which the machine and the reference disagree
    (failures, successes, pending obligations) or the machine raises, over all traces up to
    ``longest(len(signals))`` samples; ``disabled`` adds ``disable iff (d)`` (one more
    signal). Stops at ``limit``."""
    mismatches = 0
    for _, prop, signals, longest_trace in FIXED:
        names = (*signals, "d") if disabled else signals
        directive = _directive(prop, _sig("d") if disabled else None)
        machine = compile_directive(directive)
        for length in range(1, min(longest_trace, longest(len(names))) + 1):
            for trace in _all_traces(names, length):
                try:
                    _compare(directive, trace, simulate(machine, trace))
                except (AssertionError, MachineError):
                    mismatches += 1
                    if limit is not None and mismatches >= limit:
                        return mismatches
    return mismatches


def _compare(directive: Directive, trace: Trace, simulation: Simulation) -> None:
    expected = evaluate(directive.property, trace, directive.disable)
    assert list(simulation.failures) == list(expected.failures)
    assert list(simulation.successes) == list(expected.successes)
    open_slots = sum(1 for slot in simulation.snapshots[-1].after.slots if slot)
    assert open_slots == _reference_pending(directive, trace)


def test_the_fixed_properties_compile_to_the_canonical_machine() -> None:
    for name, prop, _, _ in FIXED:
        (monitor,) = compile_monitors(_directive(prop))
        assert isinstance(monitor, Machine), name


@pytest.mark.extended
def test_exhaustive_short_traces_equal_the_reference() -> None:
    """16: all traces of every fixed property, up to 3 samples (2 with ``disable iff``)."""
    assert _exhaustive_mismatches(lambda signals: 3 if signals <= 3 else 2) == 0
    assert _exhaustive_mismatches(lambda signals: 3 if signals <= 3 else 2, disabled=True) == 0


@pytest.mark.slow
def test_exhaustive_traces_equal_the_reference() -> None:
    """16: all traces up to the per-property length of ``FIXED`` (4 with ``disable iff``)."""
    assert _exhaustive_mismatches(lambda signals: 12 // signals) == 0
    assert _exhaustive_mismatches(lambda signals: 12 // signals, disabled=True) == 0


# --- 17: reachable letters and the slot bound ----------------------------------------------------


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(directive=unbounded_directives(), trace=traces(1, 12))
def test_states_stay_within_the_reachable_letters_of_unbounded_machines(
    directive: Directive, trace: Trace
) -> None:
    """17: the registered antecedent frontier stays within ``antecedent_reach`` (the code
    generator stores only those bits) and every obligation slot within ``slot_reach``."""
    machine = compile_directive(directive)
    antecedent, slots = antecedent_reach(machine), slot_reach(machine)
    for snapshot in simulate(machine, trace).snapshots:
        assert snapshot.after.antecedent <= antecedent
        for frontier, reach in zip(snapshot.after.slots, slots, strict=True):
            assert frontier <= reach


# --- 18: unbounded parts never reach the per-attempt machine --------------------------------------


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(directive=unbounded_directives())
def test_the_per_attempt_machine_refuses_unbounded_directives(directive: Directive) -> None:
    """18: ``compile_attempt_machine`` rejects a directive with an unbounded part, whatever
    the dispatch decided."""
    if has_unbounded_part(directive.property):
        with pytest.raises(CompileError):
            compile_attempt_machine(directive)
    assert all(not isinstance(m, AttemptMachine) for m in compile_monitors(directive))


# --- 19: mutants are caught -----------------------------------------------------------------------

# Anchored antecedents ("x" holds only where the match starts): a loop that drops a letter, or
# a "$" read as a finite limit, loses the matches of long runs, which no other start can supply.
_ANCHORED: list[tuple[str, Property, tuple[str, ...]]] = [
    (
        "x ##1 a[*1:$] ##1 b |-> 1'b0",
        Implication(_cat(_cat(_b("x"), 1, 1, Repeat(_A, 1, None)), 1, 1, _B), _FALSE_SEQ, True),
        ("x", "a", "b"),
    ),
    (
        "x ##[1:$] b |-> 1'b0",
        Implication(_cat(_b("x"), 1, None, _B), _FALSE_SEQ, True),
        ("x", "b"),
    ),
    (
        "x ##1 b[->3] |-> 1'b0",
        Implication(_cat(_b("x"), 1, 1, Goto(_sig("b"), 3, 3)), _FALSE_SEQ, True),
        ("x", "b"),
    ),
    (
        "x ##1 b[=2] ##1 c |-> 1'b0",
        Implication(
            _cat(_cat(_b("x"), 1, 1, NonConsecutive(_sig("b"), 2, 2)), 1, 1, _C), _FALSE_SEQ, True
        ),
        ("x", "b", "c"),
    ),
    (
        "always [3:$] b",
        Always(3, None, False, _seq(_B)),
        ("b",),
    ),
    (
        "x |-> always b",
        Implication(_b("x"), Always(0, None, False, _seq(_B)), True),
        ("x", "b"),
    ),
]
_LENGTH = 8
_POSITIONS = (0, 1, 3, 7)


def _shape_traces(signals: tuple[str, ...]) -> list[Trace]:
    """Traces of length ``_LENGTH`` in which each signal is all 1, all 0, 1 only at one
    sample, or 0 only at one sample: long runs of a signal and single disturbances."""
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
    """The number of anchored (property, trace) cases on which the machine and the reference
    disagree; stops at ``limit``."""
    count = 0
    for _, prop, signals in _ANCHORED:
        directive = _directive(prop)
        try:
            machine = compile_directive(directive)
        except CompileError:
            return 10**9
        for trace in _shape_traces(signals):
            try:
                _compare(directive, trace, simulate(machine, trace))
            except (AssertionError, MachineError):
                count += 1
                if limit is not None and count >= limit:
                    return count
    return count


@pytest.mark.extended
def test_the_anchored_properties_pass_unmutated() -> None:
    assert _anchored_mismatches() == 0


def _mutated_builder(monkeypatch: pytest.MonkeyPatch, name: str, replacement: Any) -> None:
    monkeypatch.setattr(vars(automaton_module)["_Builder"], name, replacement)


def test_mutant_repetition_to_dollar_read_as_a_finite_limit_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """19: ``[*m:$]`` built as ``[*m:m+2]`` (a ``$`` replaced by a bound)."""
    builder: Any = vars(automaton_module)["_Builder"]

    def finite(self: Any, inner: Sequence, low: int) -> Any:
        return builder._repeat(self, inner, low, low + 2)

    _mutated_builder(monkeypatch, "_repeat_unbounded", finite)
    assert _anchored_mismatches(limit=1) > 0


@pytest.mark.extended
def test_mutant_delay_to_dollar_read_as_a_finite_limit_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """19: ``##[m:$]`` built as ``##[m:m+2]``."""
    builder: Any = vars(automaton_module)["_Builder"]

    def finite(self: Any, left: Any, right: Any, low: int) -> Any:
        return builder._concat(self, left, right, low, low + 2)

    _mutated_builder(monkeypatch, "_concat_unbounded", finite)
    assert _anchored_mismatches(limit=1) > 0


@pytest.mark.extended
def test_mutant_delay_loop_that_drops_its_letter_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """19: the looping filler letter of ``##[m:$]`` loses its self edge."""
    builder: Any = vars(automaton_module)["_Builder"]
    original: Callable[..., Any] = builder._concat_unbounded

    def dropped(self: Any, left: Any, right: Any, low: int) -> Any:
        before = len(self._guards)
        fragment = original(self, left, right, low)
        loop = before + max(low, 1) - 1  # the looping filler follows the mandatory ones
        self._next[loop].discard(loop)
        return fragment

    _mutated_builder(monkeypatch, "_concat_unbounded", dropped)
    assert _anchored_mismatches(limit=1) > 0


def test_mutant_repetition_loop_that_drops_an_edge_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """19: the repeated operand's final letters no longer lead back to its start."""
    builder: Any = vars(automaton_module)["_Builder"]
    original: Callable[..., Any] = builder._repeat_unbounded

    def without_loop(self: Any, inner: Sequence, low: int) -> Any:
        fragment = original(self, inner, low)
        for final in fragment.finals:  # (for low 1 the fragment is the looping copy itself)
            self._next[final] -= fragment.start
        return fragment

    _mutated_builder(monkeypatch, "_repeat_unbounded", without_loop)
    assert _anchored_mismatches(limit=1) > 0


@pytest.mark.extended
def test_mutant_goto_without_the_skipped_samples_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """19: ``b[->n]`` read as ``b[*n]`` (the samples between occurrences are not skipped)."""

    def consecutive(node: Goto) -> Sequence:
        return Repeat(Bool(node.condition), node.minimum, node.maximum)

    monkeypatch.setattr(automaton_module, "goto_expansion", consecutive)
    assert _anchored_mismatches(limit=1) > 0


def test_mutant_antecedent_state_cleared_every_sample_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """19: the antecedent frontier does not survive to the next sample (matches of one
    sample only)."""
    from sva2yosys.temporal import machine as machine_module

    original = machine_module.step

    def forgetful(machine: Machine, state: Any, elapsed: int, raw: Callable[[int], bool]) -> Any:
        state = replace(state, antecedent=frozenset())
        return original(machine, state, elapsed, raw)

    monkeypatch.setattr("support.simulate.step", forgetful, raising=False)
    assert _anchored_mismatches(limit=1) > 0
