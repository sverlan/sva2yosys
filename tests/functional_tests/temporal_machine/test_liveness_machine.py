"""Layer 2: strong liveness for ``assert`` and ``not`` of an unbounded sequence: the liveness
kind of the configuration-set machine, the witness step and the liveness verdict against the
reference evaluator on lassos. See CONTRACT.md in this folder, points 52-60 and 61-65, 67.

The verdict of a machine on a lasso (``support.simulate.liveness_fails`` and
``lasso_first_failure``, exact by cycle detection) is compared with the reference's lasso
verdict (``lasso_attempt_results``, which evaluates ``s_eventually`` and ``s_until``
natively and shares no lowering with the product):

(a) when no attempt is FALSE at a finite sample, the machine reports no failure and the
    liveness check fails exactly when some attempt is FALSE (then only in the limit);
(b) otherwise the machine reports a failure at a finite sample, at or before the earliest
    finite FALSE decision, and the liveness verdict is not compared (one attempt can fail at a
    finite sample while another of its consequent obligations stays pending for ever).
"""

from __future__ import annotations

import functools
import itertools
import re
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from support.reference.evaluate import Outcome, value
from support.reference.lasso import Lasso, lasso_attempt_results
from support.reference.lasso import _depth as _condition_depth
from support.reference.trace import Trace
from support.simulate import lasso_first_failure, liveness_fails, simulate_configurations
from support.tracefile import TraceCase, load_cases, sv_source
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.passes.validate import validation_problems
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import CompileError, compile_monitors
from sva2yosys.temporal.configurations import (
    ConfigMachine,
    ConfigState,
    configuration_step,
    initial_config_state,
)
from sva2yosys.temporal.liveness import Liveness, witness_step
from sva2yosys.temporal.machine import StepEvents

TABLES = Path(__file__).resolve().parents[1] / "reference_semantics" / "fixtures"
MODULE = "tc"

type WitnessStep = Callable[
    [ConfigMachine, frozenset[int] | None, StepEvents, bool, int, Callable[[int], bool]],
    frozenset[int] | None,
]


# --- building directives and machines --------------------------------------------------------


@functools.cache
def _compiled_text(
    prop: str, disable: str | None, kind: str = "assert"
) -> tuple[Directive, ConfigMachine]:
    """The directive of ``<kind> property (@(posedge clk) [disable iff (disable)] prop)``
    through the real front end (Slang), and its single configuration-set machine."""
    guard = f"disable iff ({disable}) " if disable is not None else ""
    source = (
        f"module {MODULE}(input logic clk, input logic a, input logic b, input logic c, "
        f"input logic d);\n  p: {kind} property (@(posedge clk) {guard}{prop});\nendmodule\n"
    )
    directive, monitor = _from_source(f"{abs(hash(source))}.sv", source)
    assert directive.kind.value == kind
    return directive, monitor


def _from_source(name: str, source: str) -> tuple[Directive, ConfigMachine]:
    conversion = convert(export_texts([(name, source)], SlangOptions(top=(MODULE,))))
    assert conversion.rejections == ()
    directive = conversion.directives[0]
    (monitor,) = compile_monitors(directive)
    assert isinstance(monitor, ConfigMachine)
    return directive, monitor


def _table_machine(case: TraceCase) -> tuple[Directive, ConfigMachine] | None:
    """The directive and configuration machine of a table case; ``None`` when the case has
    no liveness part (a weak or bounded property, several monitors, or one rejected)."""
    conversion = convert(
        export_texts([(f"{case.name}.sv", sv_source(case, MODULE))], SlangOptions(top=(MODULE,)))
    )
    assert conversion.rejections == ()
    directive = conversion.directives[0]
    try:
        monitors = compile_monitors(directive)
    except CompileError:
        return None
    if len(monitors) != 1:
        return None
    (monitor,) = monitors
    if not isinstance(monitor, ConfigMachine) or monitor.liveness is None:
        return None
    return directive, monitor


def _lasso(rows: list[dict[str, int]], loop: int) -> Lasso:
    return Lasso(tuple({f"{MODULE}.{name}": v for name, v in row.items()} for row in rows), loop)


def _extended(lasso: Lasso, machine: ConfigMachine) -> Lasso:
    """The same infinite trace with more written samples (the loop unrolled), so that every
    class of attempt start (the reference classifies starts below ``loop + depth + 2 *
    period``) is a written start."""
    depth = max(
        (*machine.past_depth, *(_condition_depth(c) for c in machine.conditions)), default=0
    )
    wanted = lasso.loop + depth + 2 * lasso.period + 1
    samples = list(lasso.samples)
    while len(samples) < wanted:
        samples.extend(lasso.samples[lasso.loop :])
    return Lasso(tuple(samples), lasso.loop)


def _reads_history(machine: ConfigMachine) -> bool:
    return any(_condition_depth(c) > 0 for c in machine.conditions)


def _check_verdict(directive: Directive, machine: ConfigMachine, lasso: Lasso) -> None:
    """Points 57 and 58 for one lasso."""
    results = lasso_attempt_results(
        directive.property, _extended(lasso, machine), directive.disable
    )
    false = [r for r in results if r.outcome is Outcome.FALSE]
    finite = [r.decided for r in false if r.decided is not None]
    first = lasso_first_failure(machine, lasso)
    if not finite:
        assert first is None, "the machine fails although no attempt is FALSE at a finite sample"
        assert liveness_fails(machine, lasso) == bool(false), "liveness verdict"
    else:
        assert first is not None, "no failure although an attempt is FALSE at a finite sample"
        assert first <= min(finite), "first failure"


# --- 52: the liveness kind --------------------------------------------------------------------

# (property, configurations, kind), the counts derived by hand from the subset construction of
# the lowered strong sequence (docs/strategies.md): `b[*0:$]`-like loops with a start letter that
# is its own successor give one configuration (the pending bit); a delay `##[m:$]` with `m` or the
# `##0` fusion of `s_eventually` puts a start letter in front of the loop frontier.
KINDS: list[tuple[str, int, Liveness]] = [
    ("a s_until b", 1, Liveness.PENDING),  # a[*0:$] ##1 b: {a-loop, b} is a fixed point
    ("a s_until_with b", 1, Liveness.PENDING),  # a[*0:$] ##1 (a && b)
    ("strong(b[*1:$])", 1, Liveness.PENDING),
    ("s_eventually b", 1, Liveness.PENDING),  # 1[*0:$] ##1 b: {loop, b} is a fixed point
    ("a |-> s_eventually b", 1, Liveness.PENDING),
    ("a |=> s_eventually b", 1, Liveness.PENDING),
    ("always (a |-> s_eventually b)", 1, Liveness.PENDING),
    ("a |-> strong(##[1:$] b)", 2, Liveness.WITNESS),  # {start} then {filler, b}
    ("strong(a ##[1:$] b)", 2, Liveness.WITNESS),
    ("s_eventually [2:$] b", 3, Liveness.WITNESS),  # two start repetitions, then {loop, b}
    ("a |-> s_eventually $past(b)", 1, Liveness.PENDING),
    ("a[*1:2] |-> strong(c ##[1:$] b)", 2, Liveness.WITNESS),
]


@pytest.mark.parametrize(("prop", "count", "kind"), KINDS, ids=[k[0] for k in KINDS])
def test_the_liveness_kind_follows_the_number_of_configurations(
    prop: str, count: int, kind: Liveness
) -> None:
    """52."""
    _, machine = _compiled_text(prop, None)
    assert len(machine.configurations) == count
    assert machine.liveness is kind


def test_a_weak_or_cover_directive_has_no_liveness_part() -> None:
    """52: weak consequents, and a cover of a strong sequence."""
    for prop in ("a |-> ##[1:$] b", "weak(a ##[1:$] b)", "a until b"):
        _, machine = _compiled_text(prop, None)
        assert machine.liveness is None, prop
    source = (
        f"module {MODULE}(input logic clk, input logic a, input logic b);\n"
        "  p: cover property (@(posedge clk) a ##[1:$] b);\nendmodule\n"
    )
    _, machine = _from_source("cover.sv", source)
    assert machine.liveness is None


# --- 57: table cases --------------------------------------------------------------------------

LASSO_CASES = [
    case
    for path in sorted(TABLES.glob("*.trace"))
    for case in load_cases(path)
    if case.loop is not None and case.kind in ("assert", "assume") and not case.oracle_only
]
LIVENESS_CASES = [case for case in LASSO_CASES if _table_machine(case) is not None]


def test_the_liveness_table_cases_are_the_expected_ones() -> None:
    """57: the promoted cases of lasso.trace, the strong case of constants.trace and the new
    strong_liveness.trace."""
    names = {f"{case.file}::{case.name}" for case in LIVENESS_CASES}
    assert {
        "lasso.trace::s_eventually_b_periodic",
        "lasso.trace::s_until_never_b_is_false_when_strong",
        "lasso.trace::overlapping_triggers_with_a_rare_b",
        "lasso.trace::always_eventually_b_only_once",
        "lasso.trace::disable_in_the_loop_hides_a_pending_attempt",
        "constants.trace::lasso_strong_constant_false_after_an_unbounded_delay_is_false_for_ever",
    } <= names
    assert len([c for c in LIVENESS_CASES if c.file == "strong_liveness.trace"]) >= 16
    # the strong assumptions of fairness.trace have a liveness part too
    assert len([c for c in LIVENESS_CASES if c.file == "fairness.trace"]) == 14


@pytest.mark.parametrize(
    "case", LIVENESS_CASES, ids=[f"{c.file}::{c.name}" for c in LIVENESS_CASES]
)
def test_table_lassos_have_the_reference_liveness_verdict(case: TraceCase) -> None:
    """57: against the reference evaluator, and against the table's own hand-derived
    ``inf-outcome`` / ``inf-decided`` rows."""
    loaded = _table_machine(case)
    assert loaded is not None and case.loop is not None
    directive, machine = loaded
    lasso = _lasso([dict(row) for row in case.samples], case.loop)
    _check_verdict(directive, machine, lasso)
    assert case.inf_outcomes is not None and case.inf_decisions is not None
    rows = list(zip(case.inf_outcomes, case.inf_decisions, strict=True))
    finite = [decided for outcome, decided in rows if outcome == "F" and decided is not None]
    first = lasso_first_failure(machine, lasso)
    if not finite:
        assert first is None
        undecided = any(outcome == "F" for outcome, _ in rows)
        if undecided:
            assert liveness_fails(machine, lasso) is True
        elif not _reads_history(machine):
            # the table lists the starts below the loop's end; without sampled-value functions
            # every later start behaves as one of them, so "no F" there is the whole truth
            # (with a history read, a start below loop + depth is a class of its own, and the
            # reference verdict above, on the extended lasso, is the check)
            assert liveness_fails(machine, lasso) is False
    else:
        assert first is not None and first <= min(finite)


def test_a_failing_attempt_and_a_pending_obligation_can_coexist() -> None:
    """57 (b): ``a[*1:2] |-> strong(c ##[1:$] b)`` on (a, c, b) = (1,1,0), (1,0,0), (0,0,0)...
    fails at sample 1, and the obligation launched at sample 0 stays pending for ever: the
    liveness signal fails too, but the two are not compared."""
    (case,) = [
        c
        for c in LIVENESS_CASES
        if c.name == "several_triggers_one_decided_false_one_pending_for_ever"
    ]
    loaded = _table_machine(case)
    assert loaded is not None and case.loop == 2
    _, machine = loaded
    lasso = _lasso([dict(row) for row in case.samples], case.loop)
    assert lasso_first_failure(machine, lasso) == 1
    assert liveness_fails(machine, lasso) is True


# --- 58: exhaustive lassos --------------------------------------------------------------------

PROPERTIES: list[tuple[str, str | None]] = [
    ("s_eventually b", None),
    ("a |-> s_eventually b", None),
    ("a |=> s_eventually b", None),
    ("s_eventually [2:$] b", None),
    ("a s_until b", None),
    ("a s_until_with b", None),
    ("strong(a ##[1:$] b)", None),
    ("a |-> strong(##[1:$] b)", None),
    ("strong(b[->2])", None),
    ("always (a |-> s_eventually b)", None),
    ("s_eventually b", "a"),
    ("a |-> s_eventually $past(b)", None),
    # not weak(S), strong after the negation (the liveness part is that of the strong forms)
    ("not (a until b)", None),
    ("not (a until_with b)", None),
    ("not (a ##[1:$] b)", None),
    ("not weak(a ##[1:$] b)", None),
    ("a |-> not (b ##[1:$] a)", None),
    ("a |=> not (##[1:$] b)", None),
    ("not (b[->2])", None),
    ("always (a |-> not (b ##[1:$] a))", None),
    ("not (a ##[1:$] b)", "a"),
    ("a |-> not (b until a)", "b"),
]
_IDS = [f"{prop}{' disable ' + d if d else ''}" for prop, d in PROPERTIES]


def _lassos(longest: int) -> Iterator[Lasso]:
    """Every lasso of the signals ``a`` and ``b`` with 1 to ``longest`` written samples and
    every loop index."""
    for length in range(1, longest + 1):
        for rows in itertools.product(itertools.product((0, 1), repeat=2), repeat=length):
            samples = tuple({f"{MODULE}.a": a, f"{MODULE}.b": b} for a, b in rows)
            for loop in range(length):
                yield Lasso(samples, loop)


def _exhaustive(prop: str, disable: str | None, longest: int) -> None:
    directive, machine = _compiled_text(prop, disable)
    assert machine.liveness is not None
    for lasso in _lassos(longest):
        try:
            _check_verdict(directive, machine, lasso)
        except AssertionError as error:
            rows = [(s[f"{MODULE}.a"], s[f"{MODULE}.b"]) for s in lasso.samples]
            raise AssertionError(f"{prop} (a, b)={rows} loop={lasso.loop}: {error}") from error


@pytest.mark.parametrize(("prop", "disable"), PROPERTIES, ids=_IDS)
def test_every_short_lasso_has_the_reference_liveness_verdict(
    prop: str, disable: str | None
) -> None:
    """58: lassos of 1 and 2 written samples (the representative subset; 3 under slow)."""
    _exhaustive(prop, disable, 2)


@pytest.mark.slow
@pytest.mark.parametrize(("prop", "disable"), PROPERTIES, ids=_IDS)
def test_every_lasso_of_up_to_three_samples_has_the_reference_liveness_verdict(
    prop: str, disable: str | None
) -> None:
    """58: the full set, 1 to 3 written samples and every loop index."""
    _exhaustive(prop, disable, 3)


# --- 59: mutants ------------------------------------------------------------------------------


def _row(a: int, b: int, d: int = 0) -> dict[str, int]:
    return {"a": a, "b": b, "d": d}


def test_the_witness_verdict_passes_the_unmutated_mutant_traces() -> None:
    """The traces of the mutant tests below pass the check with the product's witness."""
    directive, machine = _compiled_text("a |-> strong(##[1:$] b)", "d")
    _check_verdict(directive, machine, _lasso([_row(1, 0, 0), _row(0, 0, 1)], 0))
    directive, machine = _compiled_text("a |-> strong(##[1:$] b)", None)
    _check_verdict(directive, machine, _lasso([_row(1, 1)], 0))


def test_mutant_witness_ignoring_disable_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """59: ``disable iff (d) a |-> strong(##[1:$] b)`` with ``b`` never and ``d`` recurring: no
    attempt is FALSE (every request is disabled), so liveness must pass; a witness that
    ignores ``disable iff`` stays busy for ever."""
    directive, machine = _compiled_text("a |-> strong(##[1:$] b)", "d")
    lasso = _lasso([_row(1, 0, 0), _row(0, 0, 1)], 0)
    original: WitnessStep = witness_step

    def ignoring(
        m: ConfigMachine,
        frontier: frozenset[int] | None,
        events: StepEvents,
        pick: bool,
        elapsed: int,
        raw: Callable[[int], bool],
    ) -> frozenset[int] | None:
        return original(m, frontier, replace(events, disabled=False), pick, elapsed, raw)

    monkeypatch.setattr("support.simulate.witness_step", ignoring)
    with pytest.raises(AssertionError, match="liveness verdict"):
        _check_verdict(directive, machine, lasso)


def _busy_flags(
    step: WitnessStep, machine: ConfigMachine, lasso: Lasso, samples: int
) -> list[bool]:
    """The witness register after each sample when the free choice picks whenever the witness
    is idle (the choice of a monitor that wants to keep it busy)."""
    trace = lasso.unroll(samples)
    state = initial_config_state()
    frontier: frozenset[int] | None = None
    flags: list[bool] = []
    for sample in range(samples):

        def raw(condition: int, sample: int = sample) -> bool:
            return value(machine.conditions[condition], trace, sample) != 0

        state, events = configuration_step(machine, state, sample, raw)
        frontier = step(machine, frontier, events, True, sample, raw)
        flags.append(frontier is not None)
    return flags


def test_mutant_witness_repicking_in_the_freeing_sample_is_caught() -> None:
    """59: ``a |-> strong(##[1:$] b)`` with ``a = b = 1`` at every sample is a witness machine
    whose every obligation ends one sample after its launch: the witness is idle at the sample
    after it matches, so it is not busy at every sample, even with a picking choice at every
    idle sample. A witness that picks again in the sample it is freed is busy for ever."""
    _, machine = _compiled_text("a |-> strong(##[1:$] b)", None)
    assert machine.liveness is Liveness.WITNESS
    lasso = _lasso([_row(1, 1)], 0)
    correct = _busy_flags(witness_step, machine, lasso, 12)
    assert correct == [True, False] * 6

    def repicking(
        m: ConfigMachine,
        frontier: frozenset[int] | None,
        events: StepEvents,
        pick: bool,
        elapsed: int,
        raw: Callable[[int], bool],
    ) -> frozenset[int] | None:
        after = witness_step(m, frontier, events, pick, elapsed, raw)
        if frontier is not None and after is None and not events.disabled:
            return witness_step(m, None, events, pick, elapsed, raw)
        return after

    assert all(_busy_flags(repicking, machine, lasso, 12))


def test_mutant_pending_bit_outside_its_class_is_caught() -> None:
    """59: the same property and trace with the pending bit as the liveness signal: "some
    obligation is live" holds after every sample (the one just launched), although no
    obligation is pending for ever."""
    directive, machine = _compiled_text("a |-> strong(##[1:$] b)", None)
    lasso = _lasso([_row(1, 1)], 0)
    _check_verdict(directive, machine, lasso)
    wrong = replace(machine, liveness=Liveness.PENDING)
    assert liveness_fails(wrong, lasso) is True
    with pytest.raises(AssertionError, match="liveness verdict"):
        _check_verdict(directive, wrong, lasso)


# --- 61: negated machines and their liveness kind --------------------------------

# (property, configurations, kind): the configurations are those of the negated sequence
# (docs/strategies.md), so the counts are those of the unnegated rows of KINDS; the kind follows the
# strength after the negation (not weak(s) is strong, not strong(s) is weak: no liveness part).
NEGATED_KINDS: list[tuple[str, int, Liveness | None]] = [
    ("not (a until b)", 1, Liveness.PENDING),
    ("not (a until_with b)", 1, Liveness.PENDING),
    ("always (a |-> not (b until c))", 1, Liveness.PENDING),
    ("not (a ##[1:$] b)", 2, Liveness.WITNESS),
    ("not weak(a ##[1:$] b)", 2, Liveness.WITNESS),
    ("a |-> not (##[1:$] b)", 2, Liveness.WITNESS),
    ("not strong(a ##[1:$] b)", 2, None),
    ("not (a s_until b)", 1, None),
    ("not (a s_until_with b)", 1, None),
    ("not s_eventually b", 1, None),
    ("not s_eventually [2:$] b", 3, None),
    ("a |-> not strong(##[1:$] b)", 2, None),
    ("always (a |-> not s_eventually b)", 1, None),
]


@pytest.mark.parametrize(
    ("prop", "count", "kind"), NEGATED_KINDS, ids=[k[0] for k in NEGATED_KINDS]
)
def test_a_negated_machine_has_the_liveness_kind_of_its_strength_after_negation(
    prop: str, count: int, kind: Liveness | None
) -> None:
    """61."""
    _, machine = _compiled_text(prop, None)
    assert machine.negated is True
    assert len(machine.configurations) == count
    assert machine.liveness is kind


def test_a_machine_without_a_negation_is_not_negated() -> None:
    """61."""
    for prop, _, _ in KINDS:
        assert _compiled_text(prop, None)[1].negated is False, prop
    assert _compiled_text("a |-> ##[1:$] b", None)[1].negated is False


def _machine_of(kind: str, prop: str) -> ConfigMachine:
    source = (
        f"module {MODULE}(input logic clk, input logic a, input logic b, input logic c, "
        f"input logic d);\n  p: {kind} property (@(posedge clk) {prop});\nendmodule\n"
    )
    return _from_source(f"{kind}_{abs(hash(source))}.sv", source)[1]


def test_a_cover_of_a_negated_unbounded_sequence_stays_rejected() -> None:
    """61, 62: a cover with an unbounded part is judged as written, not accepted by the
    negation (the generic message of 4d's rule 5, unchanged)."""
    for prop in ("not (a ##[1:$] b)", "not strong(a ##[1:$] b)", "not (a until b)"):
        with pytest.raises(CompileError, match="not supported"):
            _machine_of("cover", prop)


# --- 62: acceptance ----------------------------------------------------------------------------

_ASSUME_LIVENESS = "a strong unbounded property (liveness) in 'assume'"


@pytest.mark.parametrize(
    "prop",
    [
        "not (a ##[1:$] b)",
        "not weak(a ##[1:$] b)",
        "a |-> not (b ##[1:$] c)",
        "always (a |-> not (b[->2]))",
    ],
)
def test_an_assume_of_a_negated_weak_sequence_needing_a_witness_is_rejected(
    prop: str,
) -> None:
    """62: ``not weak(s)`` is strong, so it is a liveness assumption; with several
    configurations (the L2 class) the compiler rejects it."""
    with pytest.raises(CompileError, match=re.escape(_ASSUME_LIVENESS)):
        _machine_of("assume", prop)
    assert _machine_of("assert", prop).liveness is Liveness.WITNESS


@pytest.mark.parametrize(
    "prop",
    ["not (a until b)", "not (a until_with b)", "always (a |-> not (b until c))"],
)
def test_an_assume_of_a_negated_weak_sequence_with_one_configuration_is_accepted(
    prop: str,
) -> None:
    """62: the L1 class of ``not weak(s)`` is a fairness assumption (point 69)."""
    machine = _machine_of("assume", prop)
    assert machine.negated is True and machine.liveness is Liveness.PENDING


@pytest.mark.parametrize(
    "prop",
    [
        "not strong(a ##[1:$] b)",
        "not (a s_until b)",
        "not s_eventually b",
        "a |-> not strong(##[1:$] b)",
        "a ##[1:$] b |-> not strong(c[*1:$])",
    ],
)
def test_an_assume_of_a_negated_strong_sequence_is_accepted(prop: str) -> None:
    """62: ``not strong(s)`` is weak: safety only, so it is a valid assumption."""
    machine = _machine_of("assume", prop)
    assert machine.negated is True and machine.liveness is None


# --- 63: the events of a negated machine -------------------------------------------------------


def _trace(rows: list[tuple[int, int]]) -> Trace:
    return Trace(tuple({f"{MODULE}.a": a, f"{MODULE}.b": b} for a, b in rows))


def test_the_events_of_a_negated_machine_are_those_of_its_sequence_swapped() -> None:
    """63: ``not (a ##[1:$] b)`` fails where ``a ##[1:$] b`` matches and succeeds where its
    frontier dies; every sample launches an obligation (no antecedent)."""
    _, negated = _compiled_text("not (a ##[1:$] b)", None)
    _, plain = _compiled_text("a ##[1:$] b", None)
    assert negated.negated and not plain.negated
    # a = 1 throughout, b = 0, 0, 1: the obligations launched at 0 and 1 match at 2; the one
    # launched at 2 is pending; nothing dies
    matching = _trace([(1, 0), (1, 0), (1, 1)])
    simulated = simulate_configurations(negated, matching)
    assert (simulated.failures, simulated.successes) == ((2,), ())
    twin = simulate_configurations(plain, matching)
    assert (twin.failures, twin.successes) == ((), (2,))
    # a = 0 throughout: every obligation dies at its own sample
    dying = _trace([(0, 0), (0, 0), (0, 0)])
    simulated = simulate_configurations(negated, dying)
    assert (simulated.failures, simulated.successes) == ((), (0, 1, 2))
    twin = simulate_configurations(plain, dying)
    assert (twin.failures, twin.successes) == ((0, 1, 2), ())


def test_an_empty_consequent_under_not_succeeds_at_the_trigger_and_is_never_busy() -> None:
    """63: ``a |-> not ((a ##[1:$] b) intersect c)``: the operands never end together (c is one
    sample long, the other at least two), so the consequent automaton has no configuration; the
    obligation of a trigger is discharged at once: success at the trigger, no failure, ever."""
    for prop in (
        "a |-> not ((a ##[1:$] b) intersect c)",
        "a |-> not strong((a ##[1:$] b) intersect c)",
    ):
        _, machine = _compiled_text(prop, None)
        assert machine.negated and machine.configurations == (), prop
        trace = Trace(
            tuple({f"{MODULE}.a": a, f"{MODULE}.b": 1, f"{MODULE}.c": 1} for a in (1, 0, 1, 1))
        )
        simulated = simulate_configurations(machine, trace)
        assert simulated.failures == (), prop
        assert simulated.successes == (0, 2, 3), prop
        assert not any(state.live for state in simulated.states), prop
    weak_machine = _compiled_text("not ((a ##[1:$] b) intersect c)", None)[1]
    strong_machine = _compiled_text("not strong((a ##[1:$] b) intersect c)", None)[1]
    assert weak_machine.liveness is not None and strong_machine.liveness is None
    lasso = Lasso(({f"{MODULE}.a": 1, f"{MODULE}.b": 1, f"{MODULE}.c": 1},), 0)
    assert lasso_first_failure(weak_machine, lasso) is None
    assert liveness_fails(weak_machine, lasso) is False
    assert lasso_first_failure(strong_machine, lasso) is None


# --- 64: table cases ---------------------------------------------------------------------------


def _safety_only_machine(case: TraceCase) -> tuple[Directive, ConfigMachine] | None:
    """The directive and machine of a table case whose single monitor is a negated machine
    with no liveness part (``not strong(s)``), else ``None``."""
    conversion = convert(
        export_texts([(f"{case.name}.sv", sv_source(case, MODULE))], SlangOptions(top=(MODULE,)))
    )
    assert conversion.rejections == ()
    directive = conversion.directives[0]
    try:
        monitors = compile_monitors(directive)
    except CompileError:
        return None
    if len(monitors) != 1:
        return None
    (monitor,) = monitors
    if not isinstance(monitor, ConfigMachine) or not monitor.negated or monitor.liveness:
        return None
    return directive, monitor


NEGATED_SAFETY_CASES = [case for case in LASSO_CASES if _safety_only_machine(case) is not None]


def _check_safety(directive: Directive, machine: ConfigMachine, lasso: Lasso) -> None:
    """Point 64 (b) for a part with no liveness check: no attempt is FALSE only in the limit,
    and the machine fails exactly when some attempt is FALSE at a finite sample, at or before
    the earliest one."""
    results = lasso_attempt_results(
        directive.property, _extended(lasso, machine), directive.disable
    )
    false = [r for r in results if r.outcome is Outcome.FALSE]
    finite = [r.decided for r in false if r.decided is not None]
    assert len(finite) == len(false), "a weak part has a FALSE attempt in the limit"
    first = lasso_first_failure(machine, lasso)
    if not finite:
        assert first is None, "the machine fails although no attempt is FALSE"
    else:
        assert first is not None, "no failure although an attempt is FALSE at a finite sample"
        assert first <= min(finite), "first failure"


def test_the_negated_table_cases_are_the_expected_ones() -> None:
    """64: the promoted not-of-unbounded cases and not_liveness.trace."""
    weak = {f"{c.file}::{c.name}" for c in LIVENESS_CASES}
    assert {
        "lasso.trace::not_of_a_weak_unbounded_sequence_never_b",
        "lasso.trace::not_of_a_sequence_that_matches_in_the_loop",
        "lasso.trace::not_of_a_dead_sequence",
    } <= weak
    assert {c.name for c in LIVENESS_CASES if c.file == "not_liveness.trace"} == {
        "not_delay_dies",
        "not_delay_matches",
        "not_delay_pending_for_ever_is_false_in_the_limit",
        "not_explicit_weak_delay_pending_for_ever",
        "not_until_matches_or_dies",
        "not_until_pending_for_ever_is_false_in_the_limit",
        "not_until_matches_at_the_b_sample",
        "not_until_with_needs_a_at_the_b_sample",
        "implication_into_not_goto_matches",
        "implication_into_not_goto_pending_for_ever",
        "always_implication_into_not_delay_overlapping_obligations",
        "always_implication_into_not_delay_pending_for_ever",
        "disable_clears_a_pending_not_obligation",
        "several_triggers_one_decided_false_one_pending_for_ever_under_not",
        "not_of_an_empty_consequent_weak",
        "implication_into_not_of_an_empty_consequent",
    }
    assert {c.name for c in NEGATED_SAFETY_CASES} == {
        "not_strong_delay_pending_for_ever_is_true_in_the_limit",
        "not_strong_delay_matches",
        "not_s_until_with_dies",
        "not_s_until_pending_for_ever_is_true_in_the_limit",
        "not_s_eventually_fails_at_the_first_b",
        "not_s_eventually_never_b_is_true_in_the_limit",
        "not_s_eventually_past_b",
        "not_of_an_empty_consequent_strong",
    }


@pytest.mark.parametrize(
    "case", NEGATED_SAFETY_CASES, ids=[f"{c.file}::{c.name}" for c in NEGATED_SAFETY_CASES]
)
def test_negated_strong_table_lassos_have_the_reference_safety_verdict(case: TraceCase) -> None:
    """64: ``not strong(s)`` has no liveness part: pending for ever is TRUE, so the table's
    ``inf-outcome`` has an F only where ``inf-decided`` names a sample, and the machine fails
    exactly at a finite FALSE."""
    loaded = _safety_only_machine(case)
    assert loaded is not None and case.loop is not None
    directive, machine = loaded
    lasso = _lasso([dict(row) for row in case.samples], case.loop)
    _check_safety(directive, machine, lasso)
    assert case.inf_outcomes is not None and case.inf_decisions is not None
    rows = list(zip(case.inf_outcomes, case.inf_decisions, strict=True))
    assert all(decided is not None for outcome, decided in rows if outcome == "F")
    finite = [decided for outcome, decided in rows if outcome == "F" and decided is not None]
    first = lasso_first_failure(machine, lasso)
    assert (first is None) == (not finite)
    if finite:
        assert first is not None and first <= min(finite)


def test_a_negated_failing_attempt_and_a_pending_obligation_can_coexist() -> None:
    """64 (b): ``a[*1:2] |-> not (c ##[1:$] b)`` fails at sample 1 (the obligation of trigger 0
    matches) while the obligation of trigger 1 stays pending for ever: the liveness signal fails
    too, and the two are not compared."""
    (case,) = [
        c
        for c in LIVENESS_CASES
        if c.file == "not_liveness.trace" and c.name.startswith("several_triggers")
    ]
    loaded = _table_machine(case)
    assert loaded is not None and case.loop == 2
    _, machine = loaded
    lasso = _lasso([dict(row) for row in case.samples], case.loop)
    assert lasso_first_failure(machine, lasso) == 1
    assert liveness_fails(machine, lasso) is True


# --- 65: exhaustive lassos, negated forms ------------------------------------------------------

# the weak parts of not strong(s): safety only (point 64 (b)) on every lasso
NEGATED_STRONG_PROPERTIES: list[tuple[str, str | None]] = [
    ("not strong(a ##[1:$] b)", None),
    ("not (a s_until b)", None),
    ("not (a s_until_with b)", None),
    ("not s_eventually b", None),
    ("a |-> not strong(b[->2])", None),
    ("always (a |-> not s_eventually b)", None),
    ("not strong(a ##[1:$] b)", "a"),
]
_NEGATED_STRONG_IDS = [f"{p}{' disable ' + d if d else ''}" for p, d in NEGATED_STRONG_PROPERTIES]


def _exhaustive_safety(prop: str, disable: str | None, longest: int) -> None:
    directive, machine = _compiled_text(prop, disable)
    assert machine.negated and machine.liveness is None
    for lasso in _lassos(longest):
        try:
            _check_safety(directive, machine, lasso)
        except AssertionError as error:
            rows = [(s[f"{MODULE}.a"], s[f"{MODULE}.b"]) for s in lasso.samples]
            raise AssertionError(f"{prop} (a, b)={rows} loop={lasso.loop}: {error}") from error


@pytest.mark.parametrize(("prop", "disable"), NEGATED_STRONG_PROPERTIES, ids=_NEGATED_STRONG_IDS)
def test_every_short_lasso_has_the_reference_safety_verdict_for_a_negated_strong_part(
    prop: str, disable: str | None
) -> None:
    """65: lassos of 1 and 2 written samples (the representative subset; 3 under slow)."""
    _exhaustive_safety(prop, disable, 2)


@pytest.mark.slow
@pytest.mark.parametrize(("prop", "disable"), NEGATED_STRONG_PROPERTIES, ids=_NEGATED_STRONG_IDS)
def test_every_lasso_of_up_to_three_samples_has_the_reference_safety_verdict_when_negated(
    prop: str, disable: str | None
) -> None:
    """65: the full set, 1 to 3 written samples and every loop index."""
    _exhaustive_safety(prop, disable, 3)


# --- 67: mutants -------------------------------------------------------------------------------


def _property_fails(machine: ConfigMachine, lasso: Lasso) -> bool:
    """The machine's whole verdict on a lasso: a failure at a finite sample, or the liveness
    check (when there is one)."""
    if lasso_first_failure(machine, lasso) is not None:
        return True
    return machine.liveness is not None and liveness_fails(machine, lasso)


def _reference_false(directive: Directive, machine: ConfigMachine, lasso: Lasso) -> bool:
    results = lasso_attempt_results(
        directive.property, _extended(lasso, machine), directive.disable
    )
    return any(r.outcome is Outcome.FALSE for r in results)


def _flat(a: int, b: int) -> Lasso:
    """``a`` and ``b`` constant for ever."""
    return _lasso([{"a": a, "b": b}], 0)


# (property, a, b constant for ever, the property fails): hand-derived
NEGATED_VERDICTS: list[tuple[str, int, int, bool]] = [
    ("not (a ##[1:$] b)", 1, 1, True),  # every obligation matches: FALSE at a sample
    ("not (a ##[1:$] b)", 0, 0, False),  # every obligation dies: TRUE
    ("not (a ##[1:$] b)", 1, 0, True),  # pending for ever: FALSE in the limit
    ("not (a until b)", 1, 0, True),  # pending for ever: FALSE in the limit
    ("not (a until b)", 0, 0, False),  # dies at once
    ("not strong(a ##[1:$] b)", 1, 0, False),  # pending for ever: TRUE
    ("not strong(a ##[1:$] b)", 1, 1, True),  # matches
    ("not (a s_until b)", 1, 0, False),  # pending for ever: TRUE
]


@pytest.mark.parametrize(
    ("prop", "a", "b", "fails"),
    NEGATED_VERDICTS,
    ids=[f"{p} a={a} b={b}" for p, a, b, _ in NEGATED_VERDICTS],
)
def test_the_negated_machine_gives_the_hand_derived_verdict(
    prop: str, a: int, b: int, fails: bool
) -> None:
    """67: the traces of the mutant tests, with the unmutated machine."""
    directive, machine = _compiled_text(prop, None)
    assert _property_fails(machine, _flat(a, b)) is fails
    assert _reference_false(directive, machine, _flat(a, b)) is fails


def test_mutant_negation_ignored_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """67: the events not swapped (a match is a success, a death a failure): ``not (a ##[1:$]
    b)`` with every obligation matching passes, and with every obligation dying fails."""
    original = configuration_step

    def unswapped(
        machine: ConfigMachine, state: ConfigState, elapsed: int, raw: Callable[[int], bool]
    ) -> tuple[ConfigState, StepEvents]:
        new_state, events = original(machine, state, elapsed, raw)
        if machine.negated:
            events = replace(events, success=events.failure, failure=events.success)
        return new_state, events

    directive, machine = _compiled_text("not (a ##[1:$] b)", None)
    matching, dying = _flat(1, 1), _flat(0, 0)
    monkeypatch.setattr("support.simulate.configuration_step", unswapped)
    assert _property_fails(machine, matching) is False  # the reference says it fails
    assert _property_fails(machine, dying) is True  # the reference says it passes
    with pytest.raises(AssertionError, match="no failure although"):
        _check_verdict(directive, machine, matching)
    with pytest.raises(AssertionError, match="the machine fails although"):
        _check_verdict(directive, machine, dying)


def test_mutant_no_liveness_part_for_not_weak_is_caught() -> None:
    """67: ``not (a until b)`` with ``a = 1``, ``b = 0`` for ever: no check fails without a
    liveness part, although every obligation stays pending for ever."""
    directive, machine = _compiled_text("not (a until b)", None)
    lasso = _flat(1, 0)
    assert machine.liveness is Liveness.PENDING and _property_fails(machine, lasso)
    assert _reference_false(directive, machine, lasso)
    mutant = replace(machine, liveness=None)
    assert _property_fails(mutant, lasso) is False  # but the reference says it fails


def test_mutant_liveness_part_for_not_strong_is_caught() -> None:
    """67: ``not strong(a ##[1:$] b)`` with ``a = 1``, ``b = 0`` for ever passes (a strong
    sequence pending for ever is false, so the negation true); a liveness part on it fails."""
    directive, machine = _compiled_text("not strong(a ##[1:$] b)", None)
    lasso = _flat(1, 0)
    assert machine.liveness is None and not _property_fails(machine, lasso)
    assert not _reference_false(directive, machine, lasso)
    mutant = replace(machine, liveness=Liveness.WITNESS)
    assert _property_fails(mutant, lasso) is True  # but the reference says it passes


# --- 69-75: strong `assume` in the one-configuration class -----------------------

# An assumption restricts the traces to those on which the property holds (16.14.2), so the
# configuration machine of an `assume` admits a lasso exactly when it reports no failure at a finite
# sample and its liveness check (the fairness constraint `s_eventually !busy`) holds.


def _admits(machine: ConfigMachine, lasso: Lasso) -> bool:
    """The assumption admits the trace: no safety failure and the fairness part holds."""
    if lasso_first_failure(machine, lasso) is not None:
        return False
    return not (machine.liveness is not None and liveness_fails(machine, lasso))


def _reference_admits(directive: Directive, machine: ConfigMachine, lasso: Lasso) -> bool:
    """No attempt of the reference is FALSE, at a finite sample or in the limit."""
    return not _reference_false(directive, machine, lasso)


def _check_admits(directive: Directive, machine: ConfigMachine, lasso: Lasso) -> None:
    assert directive.kind is DirectiveKind.ASSUME
    assert machine.directive.kind is DirectiveKind.ASSUME
    assert _admits(machine, lasso) == _reference_admits(directive, machine, lasso), "admits"


# (property, configurations): hand-derived as for KINDS and NEGATED_KINDS
ASSUME_L1: list[tuple[str, int]] = [
    ("s_eventually b", 1),
    ("a |-> s_eventually b", 1),
    ("a |=> s_eventually b", 1),
    ("always (a |-> s_eventually b)", 1),
    ("a s_until b", 1),
    ("a s_until_with b", 1),
    ("not (a until b)", 1),
    ("not (a until_with b)", 1),
]
# (property, configurations): a witness would be needed
ASSUME_L2: list[tuple[str, int]] = [
    ("a |-> strong(##[1:$] b)", 2),
    ("strong(a ##[1:$] b)", 2),
    ("s_eventually [2:$] b", 3),
    ("strong(b[->2])", 3),
    ("not (a ##[1:$] b)", 2),
    ("a[*1:2] |-> strong(c ##[1:$] b)", 2),
]


@pytest.mark.parametrize(("prop", "count"), ASSUME_L1, ids=[k[0] for k in ASSUME_L1])
def test_an_assume_with_one_configuration_has_a_pending_bit_liveness_part(
    prop: str, count: int
) -> None:
    """69: ``ConfigMachine.liveness`` is ``PENDING`` for an ``assume`` of the L1 class, as for
    the ``assert`` of the same property."""
    _, machine = _compiled_text(prop, None, "assume")
    assert len(machine.configurations) == count
    assert machine.liveness is Liveness.PENDING
    assert _compiled_text(prop, None, "assert")[1].liveness is Liveness.PENDING


@pytest.mark.parametrize(("prop", "count"), ASSUME_L2, ids=[k[0] for k in ASSUME_L2])
def test_an_assume_needing_a_witness_is_rejected_by_the_compiler(prop: str, count: int) -> None:
    """70: the L2 class is rejected from ``compile_monitors`` with the located message (a witness
    would constrain only the obligation the solver picks); the ``assert`` is accepted."""
    with pytest.raises(CompileError, match=re.escape(_ASSUME_LIVENESS)):
        _machine_of("assume", prop)
    asserted = _compiled_text(prop, None)[1]
    assert len(asserted.configurations) == count
    assert asserted.liveness is Liveness.WITNESS


def test_a_weak_assume_and_a_safety_only_negation_have_no_liveness_part() -> None:
    """69: weak parts and ``not strong(s)`` stay without a liveness part under ``assume``."""
    for prop in ("a |-> ##[1:$] b", "a until b", "not strong(a ##[1:$] b)", "not s_eventually b"):
        assert _compiled_text(prop, None, "assume")[1].liveness is None, prop


def test_the_rejection_message_says_why() -> None:
    """70: the message names the witness problem, not just the construct."""
    with pytest.raises(CompileError, match="witness"):
        _machine_of("assume", "a |-> strong(##[1:$] b)")


def test_the_fairness_table_cases_are_the_expected_ones() -> None:
    """71: every case of ``fairness.trace`` is an accepted ``assume`` with a liveness part."""
    cases = load_cases(TABLES / "fairness.trace")
    assert len(cases) == 14
    assert all(c.kind == "assume" and not c.oracle_only and c.loop is not None for c in cases)
    assert {c.name for c in LIVENESS_CASES if c.file == "fairness.trace"} == {c.name for c in cases}


@pytest.mark.parametrize(
    "case",
    [c for c in LIVENESS_CASES if c.file == "fairness.trace"],
    ids=lambda c: c.name,
)
def test_fairness_table_lassos_are_admitted_exactly_when_no_attempt_is_false(
    case: TraceCase,
) -> None:
    """71: the assumption admits the lasso exactly when the reference finds no FALSE attempt
    (at a finite sample or in the limit), and as the table's own ``inf-outcome`` row says."""
    loaded = _table_machine(case)
    assert loaded is not None and case.loop is not None
    directive, machine = loaded
    assert directive.kind is DirectiveKind.ASSUME
    lasso = _lasso([dict(row) for row in case.samples], case.loop)
    _check_admits(directive, machine, lasso)
    assert case.inf_outcomes is not None
    assert _admits(machine, lasso) == ("F" not in case.inf_outcomes)


ASSUME_PROPERTIES: list[tuple[str, str | None]] = [
    ("s_eventually b", None),
    ("a |-> s_eventually b", None),
    ("a |=> s_eventually b", None),
    ("a s_until b", None),
    ("a s_until_with b", None),
    ("always (a |-> s_eventually b)", None),
    ("not (a until b)", None),
    ("not (a until_with b)", None),
    ("s_eventually b", "a"),
    ("a |-> s_eventually b", "b"),
    ("a |-> s_eventually $past(b)", None),
    ("$past(a) |-> s_eventually b", None),
    ("not (a until b)", "a"),
]
_ASSUME_IDS = [f"{p}{' disable ' + d if d else ''}" for p, d in ASSUME_PROPERTIES]


def _exhaustive_admits(prop: str, disable: str | None, longest: int) -> None:
    directive, machine = _compiled_text(prop, disable, "assume")
    assert machine.liveness is Liveness.PENDING
    for lasso in _lassos(longest):
        try:
            _check_admits(directive, machine, lasso)
        except AssertionError as error:
            rows = [(s[f"{MODULE}.a"], s[f"{MODULE}.b"]) for s in lasso.samples]
            raise AssertionError(f"{prop} (a, b)={rows} loop={lasso.loop}: {error}") from error


@pytest.mark.parametrize(("prop", "disable"), ASSUME_PROPERTIES, ids=_ASSUME_IDS)
def test_every_short_lasso_is_admitted_exactly_when_no_attempt_is_false(
    prop: str, disable: str | None
) -> None:
    """72: lassos of 1 and 2 written samples (the representative subset; 3 under slow)."""
    _exhaustive_admits(prop, disable, 2)


@pytest.mark.slow
@pytest.mark.parametrize(("prop", "disable"), ASSUME_PROPERTIES, ids=_ASSUME_IDS)
def test_every_lasso_of_up_to_three_samples_is_admitted_exactly_when_no_attempt_is_false(
    prop: str, disable: str | None
) -> None:
    """72: the full set, 1 to 3 written samples and every loop index."""
    _exhaustive_admits(prop, disable, 3)


def test_mutant_fairness_part_missing_is_caught() -> None:
    """73: ``assume a |-> s_eventually b`` must rule out the lasso ``a = 1``, ``b = 0`` for ever
    (the request is never answered); without the liveness part (only the safety assumption) the
    trace is admitted."""
    directive, machine = _compiled_text("a |-> s_eventually b", None, "assume")
    lasso = _flat(1, 0)
    assert not _admits(machine, lasso) and not _reference_admits(directive, machine, lasso)
    mutant = replace(machine, liveness=None)
    assert _admits(mutant, lasso) is True  # but the reference says it is ruled out
    with pytest.raises(AssertionError, match="admits"):
        _check_admits(directive, mutant, lasso)


def test_mutant_fairness_part_missing_is_caught_for_a_negated_until() -> None:
    """73: ``assume not (a until b)`` with ``a = 1``, ``b = 0`` for ever is ruled out."""
    directive, machine = _compiled_text("not (a until b)", None, "assume")
    lasso = _flat(1, 0)
    assert not _admits(machine, lasso)
    with pytest.raises(AssertionError, match="admits"):
        _check_admits(directive, replace(machine, liveness=None), lasso)


def test_the_safety_part_of_a_strong_assume_is_that_of_the_assert() -> None:
    """73: a pending obligation is not a failure, a match of ``not (a until b)`` is."""
    _, assumed = _compiled_text("not (a until b)", None, "assume")
    assert lasso_first_failure(assumed, _flat(1, 0)) is None
    assert lasso_first_failure(assumed, _flat(1, 1)) == 0


# the boundary cases (plan review), hand-derived. d is the `disable iff` signal


def test_disable_recurring_admits_an_unanswered_request_but_a_single_pulse_does_not() -> None:
    """74: ``disable iff (d) a |-> s_eventually b`` with ``a = 1``, ``b = 0`` for ever. With ``d``
    at every second sample (1 0 1 0 ...) each obligation launched at ``q`` is aborted at ``q`` or
    ``q + 1`` (16.15: an attempt whose evaluation interval contains a sample where ``d`` holds is
    disabled): no attempt is FALSE, the trace is admitted. With ``d`` at sample 0 only the
    obligations launched from sample 1 on are never aborted and never answered: pending for ever,
    FALSE in the limit, the trace is not admitted."""
    directive, machine = _compiled_text("a |-> s_eventually b", "d", "assume")
    recurring = _lasso([_row(1, 0, 1), _row(1, 0, 0)], 0)
    assert _admits(machine, recurring) is True
    _check_admits(directive, machine, recurring)
    once = _lasso([_row(1, 0, 1), _row(1, 0, 0)], 1)
    assert _admits(machine, once) is False
    assert lasso_first_failure(machine, once) is None and liveness_fails(machine, once)
    _check_admits(directive, machine, once)


def test_a_past_antecedent_is_false_at_the_first_sample() -> None:
    """74: ``$past(a) |-> s_eventually b`` with ``a = 0`` and ``b = 0`` for ever has no trigger at
    all (not at sample 0, where ``$past`` is unknown and the antecedent does not hold, 16.9.3, nor
    later): admitted. With ``a = 1`` for ever the antecedent holds from sample 1 and ``b`` never
    answers: not admitted (attempt 0 alone would be vacuous)."""
    directive, machine = _compiled_text("$past(a) |-> s_eventually b", None, "assume")
    quiet = _flat(0, 0)
    assert _admits(machine, quiet) is True
    _check_admits(directive, machine, quiet)
    loud = _flat(1, 0)
    assert _admits(machine, loud) is False
    _check_admits(directive, machine, loud)


def test_the_first_sample_rule_still_rejects_a_strong_assume_part() -> None:
    """74: ``s_eventually ($past(a) || b)`` is rejected by the first-sample rule (validation
    still runs on a strong ``assume`` part once the liveness rejection moves to the compiler),
    as the ``assert`` of it is."""
    for kind in ("assume", "assert"):
        source = (
            f"module {MODULE}(input logic clk, input logic a, input logic b);\n"
            f"  p: {kind} property (@(posedge clk) s_eventually ($past(a) || b));\nendmodule\n"
        )
        conversion = convert(export_texts([("fs.sv", source)], SlangOptions(top=(MODULE,))))
        problems = validation_problems(conversion.directives[0])
        assert any("first sample" in p for p in problems), (kind, problems)
