"""Layer 2: constants are evaluated at their own sample (CONTRACT points 29-33).

IEEE 1800 Annex F: an unknown future sample satisfies every Boolean, ``1'b0`` included, so a
constant-false letter refutes a partial match at its own sample and never before
(``docs/semantics.md`` §3). The expectations come from the reference evaluator and the trace
tables only (``support.reference``), never from the automaton, bounds or compile internals.
"""

from __future__ import annotations

import hashlib
import itertools
import random
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from support.monitor_text import without_wire_comments
from support.reference.evaluate import evaluate, evaluate_attempts
from support.reference.trace import Trace
from support.simulate import simulate, simulate_attempts, simulate_configurations
from support.tracefile import load_cases, sv_source
from sva2yosys.codegen.systemverilog import monitor_module
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Const, Expr, Signal
from sva2yosys.ir.property import (
    Implication,
    Not,
    PropAnd,
    Property,
    PropOr,
    SequenceProperty,
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
    Throughout,
)
from sva2yosys.ir.source import SourceRange
from sva2yosys.optimize.forms import Form, state_bits
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal import automaton as automaton_module
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.compile import CompileError, compile_monitors
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.machine import Machine

type AnyMachine = Machine | AttemptMachine | ConfigMachine

TABLES = Path(__file__).resolve().parents[1] / "reference_semantics" / "fixtures"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MODULE = "tc"
MONITOR = "__sva2yosys_p0"
_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("constants.sv", 1, 1, 1, 2)


def _s(name: str) -> Signal:
    return Signal(f"m.{name}", BIT)


def _b(name: str) -> Bool:
    return Bool(_s(name))


def _cat(left: Sequence, low: int, high: int | None, right: Sequence) -> SeqConcat:
    return SeqConcat(left, Delay(low, high), right)


def _seq(sequence: Sequence) -> SequenceProperty:
    return SequenceProperty(sequence)


def _imp(antecedent: Sequence, consequent: Property, overlapping: bool = True) -> Implication:
    return Implication(antecedent, consequent, overlapping)


A, B, C = _b("a"), _b("b"), _b("c")
ZERO = Bool(Const(0, BIT))
ONE = Bool(Const(1, BIT))
R = _s("r")
LATER = _seq(_cat(TRUE, 1, 1, ZERO))  # ##1 1'b0

# Constants at the first, middle and last position, in loops, goto, nonconsecutive repetition,
# intersect, or, not, throughout, antecedents, for every machine kind.
PROPERTIES: list[tuple[str, Property]] = [
    ("a |-> ##1 0", _imp(A, LATER)),
    ("a |-> 0", _imp(A, _seq(ZERO))),
    ("a |=> 0", _imp(A, _seq(ZERO), False)),
    ("a |-> b ##1 0", _imp(A, _seq(_cat(B, 1, 1, ZERO)))),
    ("a |-> 0 ##1 b", _imp(A, _seq(_cat(ZERO, 1, 1, B)))),
    ("a |-> b ##[1:2] 0", _imp(A, _seq(_cat(B, 1, 2, ZERO)))),
    ("a |-> b ##0 0", _imp(A, _seq(_cat(B, 0, 0, ZERO)))),
    ("a |-> (b ##1 0) or (c ##1 b)", _imp(A, _seq(SeqOr(_cat(B, 1, 1, ZERO), _cat(C, 1, 1, B))))),
    (
        "a |-> (b ##1 0) intersect (c ##1 b)",
        _imp(A, _seq(SeqIntersect(_cat(B, 1, 1, ZERO), _cat(C, 1, 1, B)))),
    ),
    ("a |-> (b ##1 c) intersect b", _imp(A, _seq(SeqIntersect(_cat(B, 1, 1, C), B)))),
    ("a |-> not (b ##1 0)", _imp(A, Not(_seq(_cat(B, 1, 1, ZERO))))),
    ("a |-> 0 throughout (b ##1 c)", _imp(A, _seq(Throughout(Const(0, BIT), _cat(B, 1, 1, C))))),
    ("a |-> 1 throughout (b ##1 0)", _imp(A, _seq(Throughout(Const(1, BIT), _cat(B, 1, 1, ZERO))))),
    ("a ##1 0 |-> b", _imp(_cat(A, 1, 1, ZERO), _seq(B))),
    ("a ##[1:$] 0 |-> b", _imp(_cat(A, 1, None, ZERO), _seq(B))),
    ("0 |-> b", _imp(ZERO, _seq(B))),
    ("not (a ##1 0)", Not(_seq(_cat(A, 1, 1, ZERO)))),
    ("a |-> ##[1:$] 0", _imp(A, _seq(_cat(TRUE, 1, None, ZERO)))),
    ("a |=> ##[1:$] 0", _imp(A, _seq(_cat(TRUE, 1, None, ZERO)), False)),
    ("a |-> b[*1:$] ##1 0", _imp(A, _seq(_cat(Repeat(B, 1, None), 1, 1, ZERO)))),
    ("a |-> 0[*1:$]", _imp(A, _seq(Repeat(ZERO, 1, None)))),
    ("a |-> (b ##1 0)[*1:$]", _imp(A, _seq(Repeat(_cat(B, 1, 1, ZERO), 1, None)))),
    (
        "a |-> b ##1 0[*0:$] ##1 c",
        _imp(A, _seq(_cat(_cat(B, 1, 1, Repeat(ZERO, 0, None)), 1, 1, C))),
    ),
    ("a |-> 0[->1]", _imp(A, _seq(Goto(Const(0, BIT), 1, 1)))),
    ("a |-> 1[->2]", _imp(A, _seq(Goto(Const(1, BIT), 2, 2)))),
    ("a |-> 1[=1] ##1 b", _imp(A, _seq(_cat(NonConsecutive(Const(1, BIT), 1, 1), 1, 1, B)))),
    ("a |-> 0[=0:$] ##1 b", _imp(A, _seq(_cat(NonConsecutive(Const(0, BIT), 0, None), 1, 1, B)))),
    ("a ##1 1[->2] |-> b", _imp(_cat(A, 1, 1, Goto(Const(1, BIT), 2, 2)), _seq(B))),
    (
        "1[=1] intersect (a ##[1:$] b)",
        _seq(SeqIntersect(NonConsecutive(Const(1, BIT), 1, 1), _cat(A, 1, None, B))),
    ),
    (
        "a |-> ##[1:$] (1[=1] intersect 1[*2])",
        _imp(
            A,
            _seq(
                _cat(
                    TRUE,
                    1,
                    None,
                    SeqIntersect(NonConsecutive(Const(1, BIT), 1, 1), Repeat(ONE, 2, 2)),
                )
            ),
        ),
    ),
    ("a |-> (b ##[1:$] 0) or c", _imp(A, _seq(SeqOr(_cat(B, 1, None, ZERO), C)))),
    ("(a |-> ##1 0) or (b |-> c)", PropOr((_imp(A, LATER), _imp(B, _seq(C))))),
    ("not (a |-> ##1 0)", Not(_imp(A, LATER))),
    ("(a |-> ##1 0) and (b |-> ##1 0)", PropAnd((_imp(A, LATER), _imp(B, LATER)))),
]
DISABLES: list[Expr | None] = [None, R]


def _directive(
    prop: Property, disable: Expr | None = None, kind: DirectiveKind = DirectiveKind.ASSERT
) -> Directive:
    return Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE)


def _signals(prop: Property, disable: Expr | None) -> tuple[str, ...]:
    """The names of the signals a property and its disable condition read."""
    names: set[str] = set()

    def visit(node: object) -> None:
        if isinstance(node, Signal):
            names.add(node.path.removeprefix("m."))
        elif isinstance(node, tuple):
            for child in node:
                visit(child)
        elif hasattr(node, "__dataclass_fields__"):
            for child in vars(node).values():
                visit(child)

    visit(prop)
    visit(disable)
    return tuple(sorted(names))


def _traces(names: tuple[str, ...], budget: int) -> list[Trace]:
    """Every trace over ``names`` of every length up to the largest ``L`` for which the
    number of traces of length at most ``L`` stays within ``budget`` (at least 2)."""
    width = max(1, len(names))
    longest, total = 0, 0
    while total + 2 ** (width * (longest + 1)) <= budget:
        longest += 1
        total += 2 ** (width * longest)
    longest = max(longest, 2)
    found: list[Trace] = []
    for length in range(1, longest + 1):
        for bits in itertools.product((0, 1), repeat=width * length):
            rows = tuple(
                {f"m.{name}": bits[t * width + i] for i, name in enumerate(names)}
                for t in range(length)
            )
            found.append(Trace(rows))
    return found


def _random_traces(names: tuple[str, ...], seed: str, count: int) -> list[Trace]:
    """``count`` seeded random traces of 3 to 7 samples."""
    rng = random.Random(seed)
    return [
        Trace(
            tuple(
                {f"m.{name}": rng.randint(0, 1) for name in names} for _ in range(rng.randint(3, 7))
            )
        )
        for _ in range(count)
    ]


def _full(trace: Trace) -> Trace:
    """``trace`` with the signals a, b, c, r and z (missing ones low)."""
    return Trace(
        tuple(
            {f"m.{n}": row.get(f"m.{n}", 0) for n in ("a", "b", "c", "r", "z")}
            for row in trace.samples
        )
    )


def _monitor_events(monitors: tuple[AnyMachine, ...], trace: Trace) -> tuple[list[int], list[int]]:
    """Failures and successes of the compiled ``monitors`` on ``trace`` (the union over the
    monitors of a conjunction)."""
    failures: set[int] = set()
    successes: set[int] = set()
    for monitor in monitors:
        if isinstance(monitor, Machine):
            result: Any = simulate(monitor, trace)
        elif isinstance(monitor, ConfigMachine):
            result = simulate_configurations(monitor, trace)
        else:
            result = simulate_attempts(monitor, trace)
        failures.update(result.failures)
        successes.update(result.successes)
    return sorted(failures), sorted(successes)


def _check(directive: Directive, monitors: tuple[AnyMachine, ...], trace: Trace) -> None:
    """The monitors report the reference's failures (all of them, and the successes, for an
    obligation-level machine; up to and including the first for a conjunction or a
    per-attempt machine)."""
    trace = _full(trace)
    failures, successes = _monitor_events(monitors, trace)
    prop = directive.property
    if any(isinstance(monitor, AttemptMachine) for monitor in monitors) or isinstance(
        prop, PropAnd
    ):
        reference = evaluate_attempts(prop, trace, directive.disable).failures
        assert failures[:1] == list(reference[:1]), (directive, trace.samples)
    else:
        reference_all = evaluate(prop, trace, directive.disable)
        assert failures == list(reference_all.failures), (directive, trace.samples)
        assert successes == list(reference_all.successes), (directive, trace.samples)


# --- 29, 30: constants at every position, against the reference --------------------------------


def _exhaustive(budget: int, random_traces: int) -> None:
    for name, prop in PROPERTIES:
        for disable in DISABLES:
            directive = _directive(prop, disable)
            monitors = compile_monitors(directive)
            names = _signals(prop, disable)
            for trace in _traces(names, budget) + _random_traces(names, name, random_traces):
                _check(directive, monitors, trace)


@pytest.mark.extended
def test_constants_at_every_position_equal_the_reference() -> None:
    """29, 30: every short trace and seeded random longer ones, with and without
    ``disable iff``."""
    _exhaustive(100, 40)


@pytest.mark.slow
def test_constants_at_every_position_equal_the_reference_exhaustively() -> None:
    """30: every trace up to length 3 (2 with ``disable iff`` and three signals), and more
    longer ones."""
    _exhaustive(1500, 100)


def test_long_traces_of_a_constant_loop_terminate_quickly() -> None:
    """The matcher explores a horizon fixed by the sequence's structure (a pumping bound),
    which a constant-false letter does not change: a long trace costs polynomial time."""
    import time

    prop = dict(PROPERTIES)["a |-> ##[1:$] (1[=1] intersect 1[*2])"]
    directive = _directive(prop)
    monitors = compile_monitors(directive)
    trace = Trace(tuple({"m.a": 1} for _ in range(30)))
    start = time.monotonic()
    _check(directive, monitors, trace)
    assert time.monotonic() - start < 30


def test_every_machine_kind_is_exercised() -> None:
    kinds = {
        type(monitor) for _, prop in PROPERTIES for monitor in compile_monitors(_directive(prop))
    }
    assert kinds == {Machine, ConfigMachine, AttemptMachine}


def test_a_cover_needs_a_match_on_known_samples() -> None:
    """Covers are unchanged: a constant-false letter never holds, so only the branch without
    it is ever covered."""
    for prop in (
        _seq(_cat(A, 1, 1, ZERO)),
        _seq(SeqOr(_cat(A, 1, 1, ZERO), _cat(B, 1, 1, C))),
        _seq(_cat(A, 0, 1, ZERO)),
    ):
        directive = _directive(prop, None, DirectiveKind.COVER)
        monitors = compile_monitors(directive)
        assert all(isinstance(monitor, Machine) for monitor in monitors)
        for trace in _traces(("a", "b", "c"), 700):
            full = _full(trace)
            _, successes = _monitor_events(monitors, full)
            assert successes == list(evaluate(prop, full).successes), (prop, trace.samples)


# --- 31: nothing depends on a constant before its sample ---------------------------------------


def _replace_false_constants(node: Any, signal: Signal) -> Any:
    """``node`` with every ``Const`` 0 condition replaced by ``signal``."""
    if isinstance(node, Const) and node.value == 0:
        return signal
    if isinstance(node, tuple):
        return tuple(_replace_false_constants(child, signal) for child in node)
    if hasattr(node, "__dataclass_fields__"):
        return replace(
            node,
            **{name: _replace_false_constants(value, signal) for name, value in vars(node).items()},
        )
    return node


def test_a_constant_false_letter_behaves_as_a_signal_that_is_low() -> None:
    """31: replacing every constant-false condition by a signal that is low at every sample
    of the trace changes no machine verdict: a constant is evaluated at its sample like any
    condition, never decided ahead of it."""
    low = _s("z")
    for name, prop in PROPERTIES:
        substitute = _replace_false_constants(prop, low)
        for disable in DISABLES:
            original = compile_monitors(_directive(prop, disable))
            replaced = compile_monitors(_directive(substitute, disable))
            names = _signals(prop, disable)
            for trace in _traces(names, 100) + _random_traces(names, name, 25):
                full = _full(trace)
                assert _monitor_events(original, full) == _monitor_events(replaced, full), (
                    name,
                    trace.samples,
                )


# --- 32: the mutant that prunes a constant-false letter ahead of time is caught ----------------


def _pruning_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-introduce the pre-4b behaviour: ``Bool(Const 0)`` builds no letter at all, so the
    paths through it vanish before their sample."""
    builder: Any = vars(automaton_module)["_Builder"]
    original: Callable[..., Any] = builder.sequence
    fragment: Any = vars(automaton_module)["_Fragment"]

    def pruning(self: Any, sequence: Sequence) -> Any:
        if (
            isinstance(sequence, Bool)
            and isinstance(sequence.expr, Const)
            and not sequence.expr.value
        ):
            return fragment(set(), set(), False)
        return original(self, sequence)

    monkeypatch.setattr(builder, "sequence", pruning)


def _disable_trace() -> tuple[Directive, Trace]:
    """``disable iff (r) a |-> ##1 1'b0`` with ``a`` at 0 and ``r`` at the decisive sample 1."""
    directive = _directive(_imp(A, LATER), R)
    trace = Trace(
        (
            {"m.a": 1, "m.b": 0, "m.c": 0, "m.r": 0},
            {"m.a": 0, "m.b": 0, "m.c": 0, "m.r": 1},
        )
    )
    return directive, trace


def test_the_disable_trace_is_checked_by_the_unmutated_machine() -> None:
    directive, trace = _disable_trace()
    monitors = compile_monitors(directive)
    assert _monitor_events(monitors, _full(trace)) == ([], [])
    _check(directive, monitors, trace)


def test_mutant_constant_false_letter_pruned_ahead_of_time_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """32: caught by the ``disable iff`` trace, and by the exhaustive comparison."""
    _pruning_builder(monkeypatch)
    directive, trace = _disable_trace()
    with pytest.raises(AssertionError):
        _check(directive, compile_monitors(directive), trace)
    with pytest.raises(AssertionError):
        _exhaustive(100, 40)


def test_mutant_pruned_constant_in_an_unbounded_consequent_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """32: the pruned loop around ``1'b0`` would fail ``a |-> ##[1:$] 1'b0`` at its trigger."""
    _pruning_builder(monkeypatch)
    directive = _directive(_imp(A, _seq(_cat(TRUE, 1, None, ZERO))))
    trace = Trace(({"m.a": 1, "m.b": 0, "m.c": 0, "m.r": 0},) * 3)
    with pytest.raises(AssertionError):
        _check(directive, compile_monitors(directive), trace)


# --- 33: monitors without a constant-false condition are unchanged -----------------------------

# Cases whose monitor text legitimately changed when a constant-false condition became a letter
# evaluated at its own sample, with the reason: each has a
# constant-false condition (written, or introduced by the expansion of a goto or
# nonconsecutive repetition of a constant) whose letter is no longer pruned.
_LETTER = "a written 1'b0 is a guarded letter (it was pruned); the verdict samples are unchanged"
CHANGED: dict[str, str] = {
    "fusion_empty.trace::constant_false_antecedent_never_triggers": _LETTER,
    "fusion_empty.trace::constant_false_consequent_fails_on_every_trigger": _LETTER,
    "fusion_empty.trace::constant_false_cover_is_never_reached": _LETTER,
    "fusion_empty.trace::constant_false_on_a_future_sample_fails_at_that_sample": (
        "a |-> b ##1 1'b0 now fails at the next sample (it failed at the trigger)"
    ),
    "implication.trace::nonoverlapping_impossible_consequent_disabled_at_the_next_tick": _LETTER,
    "implication.trace::nonoverlapping_impossible_consequent_fails_at_the_next_tick": _LETTER,
    "not.trace::not_after_nonoverlapping_implication_disabled_at_the_next_tick": _LETTER,
    "not.trace::not_after_nonoverlapping_implication_starts_at_the_next_tick": _LETTER,
}


# Cases for the weak until / until_with and for covers of unbounded sequences have no
# recorded digest. Everything else keeps its recorded digest, with the reviewed output-only
# updates noted in the fixture.
_ACCEPTED_IN_4C = {
    "until_machine.trace",
    "until.trace::until_decides",
    "until.trace::until_with_decides",
    "until.trace::until_with_needs_p_where_q_holds",
    "until.trace::until_q_at_the_first_sample_needs_no_p",
    "until.trace::until_never_q_weak",
    "until.trace::until_as_an_implication_consequent",
    "lasso.trace::until_never_b_is_true_when_weak",
    "lasso.trace::until_fails_when_a_drops",
    # covers of strong unbounded sequences
    "unbounded_covers.trace",
    "unbounded.trace::cover_unbounded_delay",
    "unbounded.trace::cover_unbounded_pending_finalizes_false",
    "unbounded.trace::cover_goto",
    "lasso.trace::cover_defaults_to_strong",
    # directives with an $isunknown part (folded to a constant), not part of the
    # earlier record
    "isunknown.trace",
    # counterexamples collected later (see the header of counterexamples.trace)
    "counterexamples.trace",
    # strong liveness for assert
    "strong_liveness.trace",
    "lasso.trace::past_depth_matches_in_a_one_sample_loop",
    "lasso.trace::past_depth_never_matches_when_b_is_low",
    "lasso.trace::past_depth_matches_once_from_the_prefix",
    "lasso.trace::s_eventually_b_periodic",
    "lasso.trace::s_eventually_b_only_in_the_prefix",
    "lasso.trace::s_eventually_b_never",
    "lasso.trace::eventually_b_in_the_loop_from_the_start",
    "lasso.trace::implication_into_a_strong_unbounded_consequent",
    "lasso.trace::overlapping_triggers_with_a_rare_b",
    "lasso.trace::s_until_never_b_is_false_when_strong",
    "lasso.trace::s_until_b_periodic",
    "lasso.trace::disable_in_the_loop_hides_a_pending_attempt",
    "lasso.trace::disable_only_in_the_prefix",
    "lasso.trace::always_of_an_implication_with_a_pending_consequent",
    "lasso.trace::always_eventually_infinitely_often",
    "lasso.trace::always_eventually_b_only_once",
    "lasso.trace::rose_across_the_loop",
    "lasso.trace::goto_count_spans_several_periods",
    "lasso.trace::several_triggers_one_never_satisfied",
    "lasso.trace::unbounded_antecedent_keeps_a_true_attempt_open",
    "constants.trace::lasso_strong_constant_false_after_an_unbounded_delay_is_false_for_ever",
    "liveness.trace::s_eventually_first_true",
    "liveness.trace::s_eventually_from_one",
    "liveness.trace::s_eventually_of_a_default_weak_sequence",
    "liveness.trace::s_eventually_of_an_explicit_weak_sequence",
    "liveness.trace::s_eventually_of_a_strong_sequence",
    "liveness.trace::s_eventually_with_disable",
    "liveness.trace::law_s_eventually_as_a_strong_sequence",
    "liveness.trace::law_s_eventually_from_one_as_a_strong_sequence",
    "unbounded.trace::strong_unbounded_consequent_pending_finalizes_false",
    "unbounded.trace::bare_unbounded_sequence_strong",
    "unbounded.trace::goto_pending_strong",
    "unbounded.trace::long_common_length_strong_finalizes_false",
    "until.trace::s_until_decides",
    "until.trace::s_until_with_decides",
    "until.trace::s_until_never_q_strong",
    "until.trace::until_with_disable",
    "until.trace::law_s_until_as_a_strong_sequence",
    "until.trace::law_s_until_with_as_a_strong_sequence",
    # strong assume in the one-configuration class
    "fairness.trace",
    # not of an unbounded sequence
    "not_liveness.trace",
    "lasso.trace::not_of_a_weak_unbounded_sequence_never_b",
    "lasso.trace::not_of_a_sequence_that_matches_in_the_loop",
    "lasso.trace::not_of_a_dead_sequence",
    "unbounded.trace::not_of_weak_unbounded_is_strong_at_the_end",
    "until.trace::not_of_a_weak_until",
    "until.trace::not_of_a_strong_until",
    "liveness.trace::law_not_s_eventually",
}


def _table_directive(name: str, source: str) -> Directive:
    conversion = convert(export_texts([(f"{name}.sv", source)], SlangOptions(top=(MODULE,))))
    assert conversion.rejections == ()
    return conversion.directives[0]


def monitor_digests() -> dict[str, str]:
    """``file::case`` -> sha256 of the generated monitor modules (every monitor of the
    directive, and the optimised forms that apply) of every trace case except
    ``constants.trace``, the weak-until and unbounded-cover ones and the ones the pipeline
    rejects."""
    digests: dict[str, str] = {}
    for path in sorted(TABLES.glob("*.trace")):
        if path.name == "constants.trace" or path.name in _ACCEPTED_IN_4C:
            continue
        for case in load_cases(path):
            if f"{path.name}::{case.name}" in _ACCEPTED_IN_4C:
                continue
            directive = _table_directive(case.name, sv_source(case, MODULE))
            try:
                monitors = compile_monitors(directive)
            except CompileError:
                continue
            texts = []
            for monitor in monitors:
                texts.append(monitor_module(monitor, MONITOR))
                if isinstance(monitor, Machine):
                    for form in (Form.WINDOW, Form.HISTORY):
                        if state_bits(monitor, form) is not None:
                            texts.append(monitor_module(monitor, MONITOR, form))
            digests[f"{path.name}::{case.name}"] = hashlib.sha256(
                without_wire_comments("\n--\n".join(texts)).encode()
            ).hexdigest()
    return digests


def _stored_digests() -> dict[str, str]:
    stored: dict[str, str] = {}
    for line in (FIXTURES / "monitor_digests.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, digest = line.rsplit(" ", 1)
            stored[key] = digest
    return stored


def test_monitors_of_directives_without_a_constant_false_condition_are_byte_identical() -> None:
    """33: all forms match the recorded baseline, including reviewed output changes."""
    stored = _stored_digests()
    current = monitor_digests()
    assert set(current) == set(stored), sorted(set(current) ^ set(stored))
    changed = sorted(key for key in stored if stored[key] != current[key])
    assert changed == sorted(CHANGED), changed
