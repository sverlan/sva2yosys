"""Layer 2: the temporal machine against the trace tables and the reference evaluator.

See CONTRACT.md in this folder.
"""

from __future__ import annotations

import functools
import itertools
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from support.ir_strategies import (
    SIGNALS,
    attempt_directives,
    directives,
    duration,
    has_unbounded_part,
    signals_of,
    traces,
)
from support.reference.evaluate import Outcome, Verdict, evaluate, evaluate_attempts
from support.reference.trace import Trace
from support.simulate import (
    AttemptOutcome,
    AttemptSimulation,
    Simulation,
    simulate,
    simulate_attempts,
    simulate_configurations,
)
from support.tracefile import TraceCase, load_cases, sv_source
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Expr, Signal
from sva2yosys.ir.property import (
    Always,
    Eventually,
    Implication,
    Nexttime,
    Not,
    PropAnd,
    Property,
    PropIf,
    PropOr,
    SequenceProperty,
    Until,
    conjuncts,
)
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat, SeqOr, Sequence
from sva2yosys.ir.source import SourceRange
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal import attempts as attempts_module
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.automaton import longest_path
from sva2yosys.temporal.bounds import sequence_bounds
from sva2yosys.temporal.budgets import Budgets
from sva2yosys.temporal.compile import (
    CompileError,
    compile_attempt_machine,
    compile_directive,
    compile_monitors,
    conjunct_directives,
    validate_directive,
)
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.life import life
from sva2yosys.temporal.lower import lower
from sva2yosys.temporal.machine import Machine, MachineError, antecedent_reach, slot_reach
from sva2yosys.temporal.nonvacuity import cover_problem, nv_at_false

# Generated IR values have long reprs; the warning carries no information here.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Generating overly large repr:hypothesis.errors.HypothesisWarning"
)

TABLES = Path(__file__).resolve().parents[1] / "reference_semantics" / "fixtures"
MODULE = "tc"
CASES = [case for path in sorted(TABLES.glob("*.trace")) for case in load_cases(path)]


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


# --- 1: trace tables ---------------------------------------------------------------------


def _table_directive(case: TraceCase) -> Directive:
    doc = export_texts([(f"{case.name}.sv", sv_source(case, MODULE))], SlangOptions(top=(MODULE,)))
    conversion = convert(doc)
    assert conversion.rejections == ()
    return conversion.directives[0]


def _table_trace(case: TraceCase) -> Trace:
    return Trace(tuple({f"{MODULE}.{name}": v for name, v in row.items()} for row in case.samples))


@pytest.mark.parametrize("case", CASES, ids=[f"{c.file}::{c.name}" for c in CASES])
def test_trace_table(case: TraceCase) -> None:
    """1."""
    directive = _table_directive(case)
    if case.oracle_only:
        # only the reference evaluator runs it: the pipeline rejects the property, with
        # the declared message when the case names one
        declared = re.escape(case.rejected_by) if case.rejected_by is not None else None
        with pytest.raises(CompileError, match=declared):
            validate_directive(directive)
        with pytest.raises(CompileError, match=declared):
            compile_monitors(directive)
        with pytest.raises(CompileError, match=declared):
            compile_attempt_machine(directive)
        return
    if case.attempt_scope:
        (monitor,) = compile_monitors(directive)
        if isinstance(monitor, AttemptMachine):
            _check_attempt_rows(case, simulate_attempts(monitor, _table_trace(case)))
            return
        # the canonical machine reports per obligation: the first failure is the table's, and
        # every failure of the table is among its failures; a cover's successes are the table's
        if isinstance(monitor, ConfigMachine):
            result = simulate_configurations(monitor, _table_trace(case))
            if case.kind == "cover":
                # a cover consumes the successes, exactly
                assert set(result.successes) == case.passes, "successes"
                return
            assert sorted(result.failures)[:1] == sorted(case.fails)[:1], "first failure"
            assert case.fails <= set(result.failures), "table failures"
            assert case.passes <= set(result.successes), "table successes"
            return
        simulation = simulate(monitor, _table_trace(case))
        if case.kind == "cover":
            assert set(simulation.successes) == case.passes, "successes"
        else:
            assert sorted(simulation.failures)[:1] == sorted(case.fails)[:1], "first failure"
            assert case.fails <= set(simulation.failures), "table failures"
        return
    if case.first_failure_only:
        # A property 'and': one machine per conjunct; the first failure of any is the
        # failure of the conjunction, and nothing after it is claimed.
        failures: set[int] = set()
        for part in conjunct_directives(directive):
            failures |= set(simulate(compile_directive(part), _table_trace(case)).failures)
        assert set(sorted(failures)[:1]) == case.fails, "first failure"
        return
    simulation = simulate(compile_directive(directive), _table_trace(case))
    if case.kind != "cover":
        assert set(simulation.failures) == case.fails, "failures"
    assert set(simulation.successes) == case.passes, "successes"
    if case.pending is not None:
        assert simulation.pending == case.pending, "pending"


# --- 2: differential against the reference, every prefix ---------------------------------

_DIFFERENTIAL = settings(
    max_examples=400,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


@pytest.mark.extended
@_DIFFERENTIAL
@given(directive=directives(), trace=traces())
def test_machine_equals_reference_on_every_prefix(directive: Directive, trace: Trace) -> None:
    """2: short traces, any generated property."""
    _check_every_prefix(directive, trace, simulate(compile_directive(directive), trace))


@pytest.mark.slow
@_DIFFERENTIAL
@given(data=st.data())
def test_machine_equals_reference_beyond_the_longest_match(data: st.DataObject) -> None:
    """2: traces longer than the property's longest attempt, so that late behaviour (upper
    ends of delay ranges and repetitions, the last slot) is exercised."""
    directive = data.draw(directives().filter(lambda d: duration(d) <= 14))
    span = duration(directive)
    trace = data.draw(traces(span + 1, span + 4))
    _check_every_prefix(directive, trace, simulate(compile_directive(directive), trace))


@pytest.mark.extended
@settings(max_examples=150, deadline=None, derandomize=True)
@given(first=directives(), second=directives(), trace=traces())
def test_property_and_first_failure_equals_reference(
    first: Directive, second: Directive, trace: Trace
) -> None:
    """2, property 'and': the earliest failure of the conjunct machines is the earliest
    failure of the reference evaluation of ``p and q`` (up to the first failure only)."""
    both = replace(first, property=PropAnd((first.property, second.property)))
    result = evaluate(both.property, trace, both.disable)
    # the conjunction's failing attempts, by their decision samples (not the union of the
    # operands' failures, which is what the machines report)
    expected = sorted(
        a.decided for a in result.attempts if a.verdict is Verdict.FAIL and a.decided is not None
    )
    failures: set[int] = set()
    for part in conjunct_directives(both):
        failures |= set(simulate(compile_directive(part), trace).failures)
    assert sorted(failures)[:1] == list(expected[:1])


# --- 3: slot bound ---------------------------------------------------------------------------


@pytest.mark.slow
@settings(max_examples=200, deadline=None, derandomize=True)
@given(directive=directives())
def test_no_obligation_outlives_the_slot_bound(directive: Directive) -> None:
    """3."""
    machine = compile_directive(directive)
    # An obligation alive at age slots + 1 would need a path of slots + 2 letters.
    assert longest_path(machine.consequent) <= machine.slots + 1
    length = machine.slots + 2
    for level in (0, 1):
        rows = tuple({f"m.{name}": level for name in SIGNALS} for _ in range(length))
        simulate(machine, Trace(rows))
    used = signals_of(directive)
    exhaustive = max(1, 12 // max(1, len(used)))
    for bits in itertools.product((0, 1), repeat=exhaustive * len(used)):
        rows = tuple(
            {path: bits[t * len(used) + i] for i, path in enumerate(used)}
            | {f"m.{name}": 0 for name in SIGNALS if f"m.{name}" not in used}
            for t in range(exhaustive)
        )
        simulate(machine, Trace(rows))


# --- 4: cover uses the same machine ------------------------------------------------------------


@pytest.mark.extended
@settings(max_examples=50, deadline=None, derandomize=True)
@given(directive=directives())
def test_cover_compiles_to_the_same_machine(directive: Directive) -> None:
    """4."""
    asserted = compile_directive(replace(directive, kind=DirectiveKind.ASSERT))
    covered = compile_directive(replace(directive, kind=DirectiveKind.COVER))
    assert replace(covered, directive=asserted.directive) == asserted


@pytest.mark.extended
@_DIFFERENTIAL
@given(directive=directives(), trace=traces())
def test_states_stay_within_the_reachable_letters(directive: Directive, trace: Trace) -> None:
    """6: every reachable state is covered by slot_reach / antecedent_reach."""
    machine = compile_directive(directive)
    slots = slot_reach(machine)
    antecedent = antecedent_reach(machine)
    for snapshot in simulate(machine, trace).snapshots:
        assert snapshot.after.antecedent <= antecedent
        for frontier, reach in zip(snapshot.after.slots, slots, strict=True):
            assert frontier <= reach


# =============================================================================================
# The per-attempt machine (CONTRACT points 7-14)
# =============================================================================================

ATTEMPT_CASES = [case for case in CASES if case.attempt_scope]
ACCEPTED_ATTEMPT_CASES = [case for case in ATTEMPT_CASES if not case.oracle_only]
# accepted (not oracle-only) cases outside the attempt scope
NON_ATTEMPT_CASES = [case for case in CASES if not case.attempt_scope and not case.oracle_only]

_OUTCOME_OF_TOKEN = {
    "T": AttemptOutcome.TRUE,
    "t": AttemptOutcome.TRUE,
    "U": AttemptOutcome.TRUE,
    "F": AttemptOutcome.FALSE,
    "f": AttemptOutcome.FALSE,
    "X": AttemptOutcome.FALSE,
    "p": AttemptOutcome.PENDING,
    "d": AttemptOutcome.DISABLED,
}


# --- 7: the compilation boundary changes nothing for accepted directives -----------------------


@pytest.mark.parametrize(
    "case", NON_ATTEMPT_CASES, ids=[f"{c.file}::{c.name}" for c in NON_ATTEMPT_CASES]
)
def test_accepted_tables_compile_to_the_canonical_machines(case: TraceCase) -> None:
    """7."""
    directive = _table_directive(case)
    assert compile_monitors(directive) == tuple(
        compile_directive(part) for part in conjunct_directives(directive)
    )


LEGACY_ATTEMPT_CASES = [c for c in ACCEPTED_ATTEMPT_CASES if c.file.startswith("attempts")]
# lasso.trace: the few accepted (bounded) cases, run as the finite traces they also are
TEMPORAL_FILES = (
    "temporal.trace",
    "lasso.trace",
    "unbounded.trace",
    "liveness.trace",
    "until.trace",
    "unbounded_consequents.trace",
    "constants.trace",
    "until_machine.trace",
    "unbounded_covers.trace",
    "counterexamples.trace",
    "strong_liveness.trace",
    "not_liveness.trace",
    "ieee_sequences.trace",
    "ieee_properties.trace",
    "ieee_directives.trace",
    "fairness.trace",
    "initial.trace",
    "composition.trace",
)
TEMPORAL_CASES = [c for c in ACCEPTED_ATTEMPT_CASES if c.file in TEMPORAL_FILES]


def test_the_attempt_table_files_are_the_expected_ones() -> None:
    assert LEGACY_ATTEMPT_CASES and TEMPORAL_CASES
    assert {c.file for c in ACCEPTED_ATTEMPT_CASES} == {c.file for c in LEGACY_ATTEMPT_CASES} | set(
        TEMPORAL_FILES
    )


@pytest.mark.parametrize(
    "case", LEGACY_ATTEMPT_CASES, ids=[f"{c.file}::{c.name}" for c in LEGACY_ATTEMPT_CASES]
)
def test_attempt_tables_compile_to_attempt_machines_only(case: TraceCase) -> None:
    """7."""
    monitors = compile_monitors(_table_directive(case))
    assert monitors
    assert all(isinstance(monitor, AttemptMachine) for monitor in monitors)


def _weak_unbounded_consequent(prop: Property, cover: bool) -> bool:
    """Whether a lowered property is a sequence, ``not`` of one, or an
    implication into one of those, with an unbounded sequence as the consequent (any strength
    in an assert; strong in a cover) (judged from the IR shape alone)."""
    match prop:
        case (
            SequenceProperty(sequence=body)
            | Not(operand=SequenceProperty(sequence=body))
            | Implication(consequent=SequenceProperty(sequence=body))
            | Implication(consequent=Not(operand=SequenceProperty(sequence=body)))
        ):
            return has_unbounded_part(SequenceProperty(body))
        case _:
            return False


def _canonical_shape(prop: Property, cover: bool) -> bool:
    """Whether a lowered property is a sequence, ``not`` of one, or an implication into one
    (for a cover with a single-length antecedent), judged from the IR shape alone."""
    match prop:
        case SequenceProperty() | Not(operand=SequenceProperty()):
            return True
        case Implication(
            antecedent=antecedent,
            consequent=SequenceProperty() | Not(operand=SequenceProperty()),
        ):
            if not cover:
                return True
            bounds = sequence_bounds(antecedent)
            return bounds.min_length == bounds.max_length
        case _:
            return False


@pytest.mark.parametrize(
    "case", TEMPORAL_CASES, ids=[f"{c.file}::{c.name}" for c in TEMPORAL_CASES]
)
def test_temporal_tables_compile_to_the_machine_that_decides_them(case: TraceCase) -> None:
    """7: canonical exactly when the lowered property has a canonical shape (and no weak
    unbounded consequent, which gets the configuration-set machine)."""
    directive = _table_directive(case)
    cover = directive.kind is DirectiveKind.COVER
    lowered = lower(directive.property)
    parts = (lowered,) if cover else conjuncts(lowered)
    monitors = compile_monitors(directive)
    assert len(monitors) == len(parts)
    for part, monitor in zip(parts, monitors, strict=True):
        if _weak_unbounded_consequent(part, cover):
            assert isinstance(monitor, ConfigMachine)
        elif _canonical_shape(part, cover):
            assert isinstance(monitor, Machine)
        else:
            assert isinstance(monitor, AttemptMachine)


def test_the_temporal_tables_cover_both_machines() -> None:
    kinds = {
        isinstance(compile_monitors(_table_directive(case))[0], Machine) for case in TEMPORAL_CASES
    }
    assert kinds == {True, False}


def _operand_properties() -> list[Property]:
    return [
        SequenceProperty(_seq("b")),
        SequenceProperty(_then(_seq("a"), 1, 1, _seq("b"))),
        Implication(_seq("a"), SequenceProperty(_seq("b")), True),
        Not(Implication(_seq("a"), SequenceProperty(_seq("b")), True)),
    ]


def _operator_pairs(operand: Property) -> list[tuple[Property, Property]]:
    """The (weak, strong) forms of every temporal operator over ``operand``."""
    return [
        *((Nexttime(n, False, operand), Nexttime(n, True, operand)) for n in (0, 1, 2)),
        *(
            (Always(m, n, False, operand), Always(m, n, True, operand))
            for m, n in ((0, 0), (1, 2), (0, 2))
        ),
        *(
            (Eventually(m, n, False, operand), Eventually(m, n, True, operand))
            for m, n in ((0, 0), (1, 2), (0, 2))
        ),
    ]


def _monitors_or_error(directive: Directive) -> tuple[object, ...]:
    try:
        return tuple(replace(m, directive=_SOURCE_DIRECTIVE) for m in compile_monitors(directive))
    except CompileError as error:
        return ("rejected", str(error))


_SOURCE_DIRECTIVE = Directive(
    DirectiveKind.ASSERT,
    "p",
    "m",
    Clock(Signal("m.clk", BIT), Edge.POSEDGE),
    None,
    SequenceProperty(Bool(Signal("m.a", BIT))),
    SourceRange("attempts.sv", 1, 1, 1, 2),
)


@pytest.mark.parametrize("kind", [DirectiveKind.ASSERT, DirectiveKind.COVER])
@pytest.mark.parametrize("operand_index", range(4))
def test_weak_and_strong_operators_compile_to_equal_monitors(
    kind: DirectiveKind, operand_index: int
) -> None:
    """7: weak and strong forms give equal ``compile_monitors`` results (the monitors, or the
    same rejection)."""
    operand = _operand_properties()[operand_index]
    accepted = 0
    for weak, strong in _operator_pairs(operand):
        weak_result = _monitors_or_error(replace(_directive(weak), kind=kind))
        assert weak_result == _monitors_or_error(replace(_directive(strong), kind=kind))
        accepted += weak_result[:1] != ("rejected",)
    if kind is DirectiveKind.ASSERT:
        assert accepted == len(_operator_pairs(operand))


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(directive=directives())
def test_generated_directives_compile_to_the_canonical_machines(directive: Directive) -> None:
    """7."""
    assert compile_monitors(directive) == tuple(
        compile_directive(part) for part in conjunct_directives(directive)
    )


# --- 8, 9: trace tables through the per-attempt machine ----------------------------------------


def _check_attempt_rows(case: TraceCase, result: AttemptSimulation) -> None:
    """The ``fail`` (not for a cover), ``pass``, ``outcome`` (class only), ``decided`` and
    ``pending`` rows of an attempt-scope table."""
    if case.kind != "cover":
        assert set(result.failures) == case.fails, "failures"
    assert set(result.successes) == case.passes, "successes"
    if case.outcomes is not None:
        assert tuple(a.outcome for a in result.attempts) == tuple(
            _OUTCOME_OF_TOKEN[token] for token in case.outcomes
        ), "outcome row"
    if case.decisions is not None:
        assert tuple(a.decided for a in result.attempts) == case.decisions, "decided row"
    if case.pending is not None:
        assert result.pending == case.pending, "pending attempts"


@pytest.mark.parametrize("case", ATTEMPT_CASES, ids=[f"{c.file}::{c.name}" for c in ATTEMPT_CASES])
def test_attempt_table_through_the_attempt_machine(case: TraceCase) -> None:
    """8. A case the cover rule rejects is rejected with its message; an unbounded
    construct (unbounded range, goto, nonconsecutive, unbounded always / s_eventually,
    until) with its validation message."""
    directive = _table_directive(case)
    if case.rejected_by is not None:
        with pytest.raises(CompileError, match=re.escape(case.rejected_by)):
            compile_attempt_machine(directive)
        return
    if case.oracle_only:
        message = (
            "unbounded ranges|not supported"
            if case.file
            in (
                "unbounded.trace",
                "liveness.trace",
                "until.trace",
                "lasso.trace",
                "constants.trace",
            )
            else "cover of this property"
        )
        with pytest.raises(CompileError, match=message):
            compile_attempt_machine(directive)
        return
    if has_unbounded_part(directive.property):
        # accepted, but by the canonical machine; never by the per-attempt one
        with pytest.raises(CompileError, match="unbounded"):
            compile_attempt_machine(directive)
        (monitor,) = compile_monitors(directive)
        assert isinstance(monitor, Machine | ConfigMachine)
        return
    machine = compile_attempt_machine(directive)
    _check_attempt_rows(case, simulate_attempts(machine, _table_trace(case)))


@pytest.mark.parametrize(
    "case", NON_ATTEMPT_CASES, ids=[f"{c.file}::{c.name}" for c in NON_ATTEMPT_CASES]
)
def test_other_table_through_the_attempt_machine(case: TraceCase) -> None:
    """9. A property 'and' goes into one machine as a whole (a property with an unbounded part
    never does: it is refused, and the canonical machine decides it)."""
    directive = _table_directive(case)
    if has_unbounded_part(directive.property):
        with pytest.raises(CompileError, match="unbounded"):
            compile_attempt_machine(directive)
        assert all(isinstance(m, Machine | ConfigMachine) for m in compile_monitors(directive))
        return
    machine = compile_attempt_machine(directive)
    result = simulate_attempts(machine, _table_trace(case))
    if case.kind == "cover":
        assert set(result.successes) == case.passes, "successes"
    else:
        assert sorted(result.failures)[:1] == sorted(case.fails)[:1], "first failure"


# --- 10: differential, per root age -------------------------------------------------------------


@dataclass(frozen=True)
class Exactness:
    """Where the machine's nonvacuity is exact (``docs/semantics.md`` §9.5): at a true
    decision when the cover rule holds, at a false decision also when the rule holds for
    the false decision."""

    at_true: bool
    at_false: bool
    offset_ordered: bool = True
    """False for a tree containing ``always`` / ``eventually`` (at any depth): lowered to an
    implication, its nonvacuity is that of any offset rather than offset-ordered, so the
    machine may claim a nonvacuity that the reference does not know by then."""


def _has_window_operator(prop: Property) -> bool:
    match prop:
        case Always() | Eventually():
            return True
        case SequenceProperty() | Until():
            return False
        case Implication(consequent=inner) | Not(operand=inner) | Nexttime(operand=inner):
            return _has_window_operator(inner)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return any(_has_window_operator(o) for o in operands)
        case PropIf(then=then, otherwise=otherwise):
            return _has_window_operator(then) or (
                otherwise is not None and _has_window_operator(otherwise)
            )


def _exactness(prop: Property) -> Exactness:
    return Exactness(cover_problem(prop) is None, nv_at_false(prop), not _has_window_operator(prop))


def _known_by_decision(reference_nonvacuous: bool | None, known: int | None, decided: int) -> bool:
    return reference_nonvacuous is True and known is not None and known <= decided


def _attempt_check(
    directive: Directive, trace: Trace, result: AttemptSimulation, exact: Exactness
) -> None:
    """The machine's per-attempt results against the per-attempt reference."""
    expected = evaluate_attempts(directive.property, trace, directive.disable)
    assert len(result.attempts) == len(trace)
    for record, reference in zip(result.attempts, expected.attempts, strict=True):
        where = (reference.start, reference.outcome)
        assert record.start == reference.start, where
        assert record.outcome is AttemptOutcome[reference.outcome.name], where
        assert record.decided == reference.decided, where
        if reference.decided is not None and reference.outcome in (Outcome.TRUE, Outcome.FALSE):
            known = _known_by_decision(
                reference.nonvacuous, reference.nonvacuity_known, reference.decided
            )
            precise = exact.at_true and (reference.outcome is Outcome.TRUE or exact.at_false)
            if precise:
                assert record.nonvacuous is known, where
            elif record.nonvacuous and exact.offset_ordered:
                assert known, where  # never a nonvacuity the reference does not know by then
        else:
            assert record.nonvacuous is None, where
    assert result.failures == expected.failures
    if exact.at_true:
        assert result.successes == expected.successes
    elif exact.offset_ordered:
        # (the same claim as above, as an output: with always / eventually the machine may
        # report a success the reference does not know, which only a rejected cover consumes)
        assert set(result.successes) <= set(expected.successes)
    pending = sum(1 for a in expected.attempts if a.outcome is Outcome.PENDING)
    assert result.pending == pending


def _check_every_prefix_of_attempts(
    directive: Directive, machine: AttemptMachine, trace: Trace, exact: Exactness
) -> None:
    for end in range(1, len(trace) + 1):
        prefix = Trace(trace.samples[:end])
        _attempt_check(directive, prefix, simulate_attempts(machine, prefix), exact)


def _attempt_machine(directive: Directive) -> tuple[Directive, AttemptMachine]:
    """Compile ``directive``; a cover that fails the cover rule must be rejected, and is then
    compiled as an assert (the machine does not depend on the kind)."""
    if directive.kind is DirectiveKind.COVER and cover_problem(directive.property) is not None:
        with pytest.raises(CompileError):
            compile_attempt_machine(directive)
        directive = replace(directive, kind=DirectiveKind.ASSERT)
    return directive, compile_attempt_machine(directive)


def _attempt_differential(data: st.DataObject) -> None:
    directive = data.draw(attempt_directives())
    span = life(directive.property)
    trace = data.draw(st.one_of(traces(1, 6), traces(span + 1, span + 4)))
    directive, machine = _attempt_machine(directive)
    assert machine.life == life(directive.property)
    _check_every_prefix_of_attempts(directive, machine, trace, _exactness(directive.property))


def _attempt_settings(max_examples: int) -> settings:
    return settings(
        max_examples=max_examples,
        deadline=None,
        derandomize=True,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )


@pytest.mark.extended
@_attempt_settings(60)
@given(data=st.data())
def test_attempt_machine_equals_reference_per_root_age(data: st.DataObject) -> None:
    """10."""
    _attempt_differential(data)


@pytest.mark.slow
@_attempt_settings(800)
@given(data=st.data())
def test_attempt_machine_equals_reference_per_root_age_many(data: st.DataObject) -> None:
    """10, a larger search."""
    _attempt_differential(data)


# --- 11: exhaustive on a fixed list of small trees ----------------------------------------------


def _sig(name: str) -> Expr:
    return Signal(f"m.{name}", BIT)


def _seq(name: str) -> Sequence:
    return Bool(_sig(name))


def _delayed(sequence: Sequence, low: int, high: int) -> Property:
    """``##[low:high] sequence`` as a property."""
    return SequenceProperty(SeqConcat(TRUE, Delay(low, high), sequence))


def _then(left: Sequence, low: int, high: int, right: Sequence) -> Sequence:
    return SeqConcat(left, Delay(low, high), right)


_A, _B = SequenceProperty(_seq("a")), SequenceProperty(_seq("b"))
_A_IMPLIES_B = Implication(_seq("a"), _B, True)
_B_IMPLIES_A = Implication(_seq("b"), _A, True)

TREES: list[tuple[str, Property]] = [
    ("a |-> (b |-> a)", Implication(_seq("a"), _B_IMPLIES_A, True)),
    ("not (a |-> b)", Not(_A_IMPLIES_B)),
    (
        "(a |-> b) or (b |-> ##1 a)",
        PropOr((_A_IMPLIES_B, Implication(_seq("b"), _delayed(_seq("a"), 1, 1), True))),
    ),
    (
        "(a |-> ##1 b) and (b |-> a)",
        PropAnd((Implication(_seq("a"), _delayed(_seq("b"), 1, 1), True), _B_IMPLIES_A)),
    ),
    (
        "if (a) (b |-> ##1 a) else b",
        PropIf(_sig("a"), Implication(_seq("b"), _delayed(_seq("a"), 1, 1), True), _B),
    ),
    (
        "a ##[0:1] b |-> (b |-> ##1 a)",
        Implication(
            _then(_seq("a"), 0, 1, _seq("b")),
            Implication(_seq("b"), _delayed(_seq("a"), 1, 1), True),
            True,
        ),
    ),
    ("a |=> not (b |-> a)", Implication(_seq("a"), Not(_B_IMPLIES_A), False)),
    (
        "a |=> (b |-> ##1 a)",
        Implication(_seq("a"), Implication(_seq("b"), _delayed(_seq("a"), 1, 1), True), False),
    ),
    (
        "(a |-> b) or (a ##2 b)",
        PropOr((_A_IMPLIES_B, SequenceProperty(_then(_seq("a"), 2, 2, _seq("b"))))),
    ),
    (
        "not ((a |-> ##[1:2] b) and b)",
        Not(PropAnd((Implication(_seq("a"), _delayed(_seq("b"), 1, 2), True), _B))),
    ),
    (
        "a ##[1:2] b |-> a",
        Implication(_then(_seq("a"), 1, 2, _seq("b")), _A, True),
    ),
    (
        "if (a) (b |-> a)",
        PropIf(_sig("a"), _B_IMPLIES_A, None),
    ),
    ("nexttime [2] a", Nexttime(2, False, _A)),
    ("always [0:1] (a |-> b)", Always(0, 1, False, _A_IMPLIES_B)),
    ("s_always [1:2] a", Always(1, 2, True, _A)),
    (
        "eventually [0:1] (a ##1 b)",
        Eventually(0, 1, False, SequenceProperty(_then(_seq("a"), 1, 1, _seq("b")))),
    ),
    ("s_eventually [1:2] a", Eventually(1, 2, True, _A)),
    (
        "eventually [0:1] (((a ##2 b) or d) |-> c)",
        Eventually(
            0,
            1,
            False,
            Implication(
                SeqOr(_then(_seq("a"), 2, 2, _seq("b")), _seq("d")),
                SequenceProperty(_seq("c")),
                True,
            ),
        ),
    ),
    ("not (if (a) b)", Not(PropIf(_sig("a"), _B, None))),
    (
        "(if (a) b) and (b ##1 a)",
        PropAnd((PropIf(_sig("a"), _B, None), SequenceProperty(_then(_seq("b"), 1, 1, _seq("a"))))),
    ),
]
_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("attempts.sv", 1, 1, 1, 2)


@functools.cache
def _tree_exactness() -> tuple[Exactness, ...]:
    """Computed once, with the unmutated predicates (the mutant tests warm it up before they
    patch the module)."""
    return tuple(_exactness(prop) for _, prop in TREES)


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


_WIDE_TREE = "eventually [0:1] (((a ##2 b) or d) |-> c)"
"""The one tree over four signals (the others use ``a`` and ``b``): its traces are far more
numerous, so it has its own lengths."""


def _exhaustive_mismatches(
    plain_length: int,
    disabled_length: int,
    limit: int | None = None,
    wide_lengths: tuple[int, int] = (3, 2),
) -> int:
    """The number of (tree, disable, trace) cases on which the machine and the reference
    disagree (or the machine raises): all traces up to ``plain_length`` samples without
    ``disable iff`` and up to ``disabled_length`` with ``disable iff (d)``; the four-signal
    tree (``_WIDE_TREE``) uses ``wide_lengths`` instead. Stops at ``limit``."""
    mismatches = 0
    for (name, prop), exact in zip(TREES, _tree_exactness(), strict=True):
        wide = name == _WIDE_TREE
        for disable, signals, longest in (
            (
                None,
                ("a", "b", "c", "d") if wide else ("a", "b"),
                wide_lengths[0] if wide else plain_length,
            ),
            (
                _sig("d"),
                ("a", "b", "c", "d") if wide else ("a", "b", "d"),
                wide_lengths[1] if wide else disabled_length,
            ),
        ):
            directive = _directive(prop, disable)
            try:
                machine = compile_attempt_machine(directive)
            except (CompileError, MachineError):
                mismatches += 1
                continue
            for length in range(1, longest + 1):
                for trace in _all_traces(signals, length):
                    try:
                        _attempt_check(directive, trace, simulate_attempts(machine, trace), exact)
                    except (AssertionError, MachineError):
                        mismatches += 1
                        if limit is not None and mismatches >= limit:
                            return mismatches
    return mismatches


def test_the_fixed_trees_compile() -> None:
    for name, prop in TREES:
        machine = compile_attempt_machine(_directive(prop))
        assert machine.life >= 1, name


def test_exhaustive_short_traces_equal_the_reference() -> None:
    """11: every trace up to length 3 (up to 2 with a disable condition); the four-signal
    tree up to length 3 (2 with a disable condition) too."""
    assert _exhaustive_mismatches(3, 2) == 0


@pytest.mark.slow
def test_exhaustive_traces_up_to_length_4_equal_the_reference() -> None:
    """11: every trace up to length 4 (up to 3 with a disable condition), the four-signal
    tree included."""
    assert _exhaustive_mismatches(4, 3, wide_lengths=(4, 3)) == 0


# --- 12: hand cases -------------------------------------------------------------------------------


def _trace(rows: dict[str, str]) -> Trace:
    """``{"a": "01."}``: one character per sample, ``.`` and ``0`` are 0."""
    length = len(next(iter(rows.values())))
    return Trace(
        tuple(
            {f"m.{name}": int(text[t] == "1") for name, text in rows.items()} for t in range(length)
        )
    )


def test_a_failure_and_a_success_of_different_attempts_in_one_sample() -> None:
    """12. (a |-> ##1 b) and (c |-> ##2 d). The attempt starting at 0 is true at 2 (a low,
    d two samples after c), the one starting at 1 is false at 2 (a, no b), the one starting
    at 2 is true and vacuous at 2."""
    c = _seq("c")
    prop = PropAnd(
        (
            Implication(_seq("a"), _delayed(_seq("b"), 1, 1), True),
            Implication(c, _delayed(_seq("d"), 2, 2), True),
        )
    )
    directive = _directive(prop)
    trace = _trace({"a": ".1.", "b": "...", "c": "1..", "d": "..1"})
    machine = compile_attempt_machine(directive)
    result = simulate_attempts(machine, trace)
    assert [(r.outcome, r.decided, r.nonvacuous) for r in result.attempts] == [
        (AttemptOutcome.TRUE, 2, True),
        (AttemptOutcome.FALSE, 2, True),
        (AttemptOutcome.TRUE, 2, False),
    ]
    assert result.failures == (2,)
    assert result.successes == (2,)
    event = result.events[2]
    assert event.failure and event.success
    _check_every_prefix_of_attempts(directive, machine, trace, _exactness(prop))


def test_overlapping_parents_with_a_consequent_at_the_same_sample_stay_separate() -> None:
    """12. a ##[1:2] b |-> c. The parents starting at 0 and at 1 both have a consequent
    attempt starting at sample 2; the parent starting at 0 also has one at sample 1, which
    fails. So one parent fails (at 1) while the other passes (at 3, when its antecedent is
    over)."""
    prop = Implication(_then(_seq("a"), 1, 2, _seq("b")), SequenceProperty(_seq("c")), True)
    directive = _directive(prop)
    trace = _trace({"a": "11..", "b": ".11.", "c": "..1."})
    machine = compile_attempt_machine(directive)
    result = simulate_attempts(machine, trace)
    assert [(r.outcome, r.decided, r.nonvacuous) for r in result.attempts] == [
        (AttemptOutcome.FALSE, 1, True),
        (AttemptOutcome.TRUE, 3, True),
        (AttemptOutcome.TRUE, 2, False),
        (AttemptOutcome.TRUE, 3, False),
    ]
    assert result.failures == (1,)
    assert result.successes == (3,)
    _check_every_prefix_of_attempts(directive, machine, trace, _exactness(prop))


def test_a_disabled_attempt_neither_fails_nor_succeeds() -> None:
    """12. a |-> (b |-> ##1 c) with d at sample 1: the attempt started at 0 is disabled at 1."""
    prop = Implication(_seq("a"), Implication(_seq("b"), _delayed(_seq("c"), 1, 1), True), True)
    directive = _directive(prop, _sig("d"))
    trace = _trace({"a": "1..", "b": "1..", "c": "...", "d": ".1."})
    machine = compile_attempt_machine(directive)
    result = simulate_attempts(machine, trace)
    assert result.attempts[0].outcome is AttemptOutcome.DISABLED
    assert result.attempts[0].decided == 1
    assert result.failures == () and result.successes == ()
    _check_every_prefix_of_attempts(directive, machine, trace, _exactness(prop))


# --- 13: mutants are caught -----------------------------------------------------------------------


@pytest.mark.extended
def test_mutant_two_root_ages_merged_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """13: the state of root age ``n`` is read as that of ages ``n`` or ``n - 1``."""
    construction: Any = vars(attempts_module)["_Construction"]
    original: Callable[..., int] = construction._read

    def merged(self: Any, age: int, path: tuple[int, ...], part: str, index: int) -> int:
        gate = original(self, age, path, part, index)
        if age >= 2:
            lower = original(self, age - 1, path, part, index)
            return int(self.netlist.or_((gate, lower)))
        return gate

    _tree_exactness()
    monkeypatch.setattr(construction, "_read", merged)
    assert _exhaustive_mismatches(4, 2, limit=1) > 0


def test_mutant_nonvacuity_forced_true_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """13: every node is nonvacuous from its launch."""
    _tree_exactness()
    monkeypatch.setattr(attempts_module, "launch_nv", lambda prop: True)
    assert _exhaustive_mismatches(4, 2, limit=1) > 0


def test_mutant_nonvacuity_dropped_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """13: an implication or ``if`` never learns the nonvacuity of its consequent or branch
    attempts (successes are missed)."""
    construction: Any = vars(attempts_module)["_Construction"]

    def dropped(self: Any, prop: Any, inst: Any) -> int:
        return int(self.netlist.const(False))

    _tree_exactness()
    monkeypatch.setattr(construction, "_branch_nonvacuous", dropped)
    assert _exhaustive_mismatches(4, 2, limit=1) > 0


def test_mutant_existential_implication_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """13: an implication is false when all its consequent attempts are (instead of when
    one is)."""
    construction: Any = vars(attempts_module)["_Construction"]

    def existential(self: Any, falses: list[int]) -> int:
        return int(self.netlist.and_(tuple(falses)))

    _tree_exactness()
    monkeypatch.setattr(construction, "_implication_false", existential)
    assert _exhaustive_mismatches(4, 2, limit=1) > 0


# --- 14: budgets ---------------------------------------------------------------------------------

_BIG = Implication(
    _then(_seq("a"), 1, 1, _seq("b")), SequenceProperty(_then(_seq("c"), 1, 3, _seq("d"))), True
)


@pytest.mark.parametrize(
    ("budgets", "name"),
    [
        (Budgets(letters=2), "letters"),
        (Budgets(instances=1), "instances"),
        (Budgets(registers=1), "registers"),
        (Budgets(gates=2), "gates"),
    ],
)
def test_a_budget_is_enforced_by_name(budgets: Budgets, name: str) -> None:
    """14."""
    with pytest.raises(CompileError, match=name):
        compile_attempt_machine(_directive(_BIG), budgets)
    compile_attempt_machine(_directive(_BIG))


@pytest.mark.parametrize("range_end", [10**6, 10**9])
def test_a_huge_range_is_rejected_before_any_unbounded_work(range_end: int) -> None:
    """14."""
    consequent = SequenceProperty(_then(TRUE, 1, range_end, _seq("b")))
    with pytest.raises(CompileError, match="letters"):
        compile_attempt_machine(_directive(Implication(TRUE, consequent, True)))
    empty_repeat = Implication(
        Repeat(SeqConcat(TRUE, Delay(1, range_end), TRUE), 0, 0), SequenceProperty(_seq("b")), True
    )
    with pytest.raises(CompileError, match="letters"):
        compile_attempt_machine(_directive(empty_repeat))


@pytest.mark.parametrize("range_end", [10**6, 10**9])
def test_a_huge_range_is_rejected_on_the_dispatch_path(range_end: int) -> None:
    """14: through ``compile_monitors``, which chooses the machine first."""
    covers = (
        Always(0, range_end, False, SequenceProperty(_seq("b"))),
        Implication(_then(_seq("a"), 1, range_end, _seq("b")), SequenceProperty(_seq("c")), True),
    )
    for prop in covers:
        with pytest.raises(CompileError, match="letters"):
            compile_monitors(replace(_directive(prop), kind=DirectiveKind.COVER))
