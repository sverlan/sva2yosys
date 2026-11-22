"""Layer 2: weak ``until`` / ``until_with`` in the configuration-set machine
against the reference evaluator, which evaluates ``Until`` natively. See CONTRACT.md in this
folder, points 34-40.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings

from support.ir_strategies import traces, until_directives
from support.reference.evaluate import Verdict, evaluate, evaluate_attempts
from support.reference.trace import Trace
from support.simulate import ConfigSimulation, simulate_configurations
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Binary, BinaryOp, Const, Expr, Sampled, SampledFunction, Signal
from sva2yosys.ir.property import (
    Always,
    Implication,
    Nexttime,
    Not,
    PropAnd,
    Property,
    PropIf,
    PropOr,
    SequenceProperty,
    Strength,
    Until,
)
from sva2yosys.ir.sequence import Bool, Delay, Repeat, SeqConcat, Sequence
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal import automaton as automaton_module
from sva2yosys.temporal import lower as lower_module
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.compile import (
    CompileError,
    compile_attempt_machine,
    compile_configuration_machine,
    compile_monitors,
)
from sva2yosys.temporal.configurations import ConfigMachine, config_size
from sva2yosys.temporal.machine import Machine, MachineError

pytestmark = pytest.mark.filterwarnings(
    "ignore:Generating overly large repr:hypothesis.errors.HypothesisWarning"
)

_DIFFERENTIAL = settings(
    max_examples=150,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
_CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)
_SOURCE = SourceRange("until.sv", 1, 1, 1, 2)


def _sig(name: str) -> Signal:
    return Signal(f"m.{name}", BIT)


_A, _B, _C, _D = (_sig(name) for name in "abcd")
_ZERO, _ONE = Const(0, BIT), Const(1, BIT)


def _past(signal: Signal) -> Expr:
    return Sampled(SampledFunction.PAST, signal, BIT)


def _until(left: Expr, right: Expr, inclusive: bool = False) -> Until:
    return Until(left, right, False, inclusive)


def _directive(
    prop: Property, disable: Expr | None = None, kind: DirectiveKind = DirectiveKind.ASSERT
) -> Directive:
    return Directive(kind, "p", "m", _CLOCK, disable, prop, _SOURCE)


def _config(directive: Directive) -> ConfigMachine:
    (monitor,) = compile_monitors(directive)
    assert isinstance(monitor, ConfigMachine)
    return monitor


def _desugar(prop: Property) -> Property:
    """``prop`` with every ``Until`` written out as the weak sequence of IEEE 1800 §16.12.12
    (``p[*0:$] ##1 q``, ``p[*0:$] ##1 (p && q)``). Written here, not taken from the product's
    lowering, so that the obligation-based reference does not share the mapping under test."""
    match prop:
        case Until(left=left, right=right, strong=strong, inclusive=inclusive):
            tail: Expr = Binary(BinaryOp.LOGICAL_AND, left, right, BIT) if inclusive else right
            body = SeqConcat(Repeat(Bool(left), 0, None), Delay(1, 1), Bool(tail))
            return SequenceProperty(body, Strength.STRONG if strong else Strength.WEAK)
        case SequenceProperty():
            return prop
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            return Implication(antecedent, _desugar(consequent), overlapping)
        case Not(operand=operand):
            return Not(_desugar(operand))
        case Nexttime(count=count, strong=strong, operand=operand):
            return Nexttime(count, strong, _desugar(operand))
        case Always(low=low, high=high, strong=strong, operand=operand):
            return Always(low, high, strong, _desugar(operand))
        case PropAnd(operands=operands):
            return PropAnd(tuple(_desugar(o) for o in operands))
        case PropOr(operands=operands):
            return PropOr(tuple(_desugar(o) for o in operands))
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            return PropIf(
                condition, _desugar(then), _desugar(otherwise) if otherwise is not None else None
            )
        case _:
            raise AssertionError(f"not generated: {prop!r}")


def _reference_pending(directive: Directive, trace: Trace) -> bool:
    result = evaluate(_desugar(directive.property), trace, directive.disable)
    return any(
        obligation.verdict is Verdict.PENDING
        for attempt in result.attempts
        for obligation in attempt.obligations
    )


def _compare(directive: Directive, trace: Trace, simulation: ConfigSimulation) -> None:
    """On every prefix of ``trace`` the machine's failures and successes equal the
    obligation-based reference of the desugared property, and "some obligation is live"
    equals "some obligation is pending". On the whole trace the first failure also equals that
    of the native per-attempt evaluation of the ``Until`` (which does not desugar), and each of
    its failure samples is one of the machine's."""
    for end in range(1, len(trace) + 1):
        prefix = Trace(trace.samples[:end])
        expected = evaluate(_desugar(directive.property), prefix, directive.disable)
        assert [t for t in simulation.failures if t < end] == list(expected.failures)
        assert [t for t in simulation.successes if t < end] == list(expected.successes)
        assert bool(simulation.states[end - 1].live) == _reference_pending(directive, prefix)
    native = evaluate_attempts(directive.property, trace, directive.disable).failures
    assert list(native[:1]) == list(simulation.failures[:1])
    assert set(native) <= set(simulation.failures)


# --- 35: differential, every prefix ------------------------------------------------------------


@pytest.mark.extended
@_DIFFERENTIAL
@given(directive=until_directives(), trace=traces(1, 10))
def test_until_machine_equals_reference_on_every_prefix(directive: Directive, trace: Trace) -> None:
    """35."""
    _compare(directive, trace, simulate_configurations(_config(directive), trace))


@pytest.mark.slow
@settings(
    max_examples=60,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(directive=until_directives(), trace=traces(11, 16))
def test_until_machine_equals_reference_on_long_traces(directive: Directive, trace: Trace) -> None:
    """35: traces long enough for the loop to run many times."""
    _compare(directive, trace, simulate_configurations(_config(directive), trace))


def test_generated_until_directives_compile_to_one_configuration_machine() -> None:
    """35."""

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(directive=until_directives())
    def check(directive: Directive) -> None:
        monitors = compile_monitors(directive)
        assert len(monitors) == 1 and isinstance(monitors[0], ConfigMachine)
        assert monitors[0] == compile_configuration_machine(directive)
        with pytest.raises(CompileError):
            compile_attempt_machine(directive)

    check()


# --- 36: exhaustive on fixed properties --------------------------------------------------------

_IMP = Implication
_WEAK_C = SequenceProperty(Bool(_C), Strength.WEAK)

# (name, property, signals, longest trace): every trace of every length up to the longest
FIXED: list[tuple[str, Property, tuple[str, ...], int]] = [
    ("b until c", _until(_B, _C), ("b", "c"), 7),
    ("b until_with c", _until(_B, _C, True), ("b", "c"), 7),
    ("a |-> b until c", _IMP(Bool(_A), _until(_B, _C), True), ("a", "b", "c"), 5),
    ("a |-> b until_with c", _IMP(Bool(_A), _until(_B, _C, True), True), ("a", "b", "c"), 5),
    ("a |=> b until c", _IMP(Bool(_A), _until(_B, _C), False), ("a", "b", "c"), 5),
    ("a |=> b until_with c", _IMP(Bool(_A), _until(_B, _C, True), False), ("a", "b", "c"), 5),
    (
        "always (a |-> b until c)",
        Always(0, None, False, _IMP(Bool(_A), _until(_B, _C), True)),
        ("a", "b", "c"),
        5,
    ),
    (
        "always [1:$] (a |-> b until_with c)",
        Always(1, None, False, _IMP(Bool(_A), _until(_B, _C, True), True)),
        ("a", "b", "c"),
        5,
    ),
    (
        "always (a |=> b until c)",
        Always(0, None, False, _IMP(Bool(_A), _until(_B, _C), False)),
        ("a", "b", "c"),
        5,
    ),
    ("always (b until c)", Always(0, None, False, _until(_B, _C)), ("b", "c"), 6),
    (
        "a |-> always (b until_with c)",
        _IMP(Bool(_A), Always(0, None, False, _until(_B, _C, True)), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a[*1:$] ##1 d |-> b until c",
        _IMP(SeqConcat(Repeat(Bool(_A), 1, None), Delay(1, 1), Bool(_D)), _until(_B, _C), True),
        ("a", "b", "c", "d"),
        3,
    ),
    ("a |-> $past(b) until c", _IMP(Bool(_A), _until(_past(_B), _C), True), ("a", "b", "c"), 5),
    ("a |-> b until $past(c)", _IMP(Bool(_A), _until(_B, _past(_C)), True), ("a", "b", "c"), 5),
    (
        "a |-> b until_with $past(c)",
        _IMP(Bool(_A), _until(_B, _past(_C), True), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a |-> $past(b) until_with c",
        _IMP(Bool(_A), _until(_past(_B), _C, True), True),
        ("a", "b", "c"),
        5,
    ),
    (
        "a |-> $past(b, 2) until_with $past(c)",
        _IMP(
            Bool(_A),
            _until(Sampled(SampledFunction.PAST, _B, BIT, 2), _past(_C), True),
            True,
        ),
        ("a", "b", "c"),
        4,
    ),
    ("a |-> b until 1'b0", _IMP(Bool(_A), _until(_B, _ZERO), True), ("a", "b"), 7),
    ("a |-> b until 1'b1", _IMP(Bool(_A), _until(_B, _ONE), True), ("a", "b"), 6),
    ("a |-> 1'b0 until c", _IMP(Bool(_A), _until(_ZERO, _C), True), ("a", "c"), 7),
    ("a |-> 1'b0 until_with c", _IMP(Bool(_A), _until(_ZERO, _C, True), True), ("a", "c"), 7),
    ("a |-> 1'b1 until c", _IMP(Bool(_A), _until(_ONE, _C), True), ("a", "c"), 7),
    ("a |-> 1'b1 until_with 1'b0", _IMP(Bool(_A), _until(_ONE, _ZERO, True), True), ("a",), 8),
    ("nexttime (b until c)", Nexttime(1, False, _until(_B, _C)), ("b", "c"), 6),
    ("nexttime [2] (b until_with c)", Nexttime(2, False, _until(_B, _C, True)), ("b", "c"), 6),
    ("always [0:1] (b until c)", Always(0, 1, False, _until(_B, _C)), ("b", "c"), 6),
    ("if (d) b until c", PropIf(_D, _until(_B, _C), None), ("b", "c", "d"), 5),
    (
        "if (d) b until_with c else c",
        PropIf(_D, _until(_B, _C, True), _WEAK_C),
        ("b", "c", "d"),
        5,
    ),
    (
        "(a |-> b until c) and (a |=> c until_with b)",
        PropAnd(
            (
                _IMP(Bool(_A), _until(_B, _C), True),
                _IMP(Bool(_A), _until(_C, _B, True), False),
            )
        ),
        ("a", "b", "c"),
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


def _machines(directive: Directive) -> list[ConfigMachine]:
    machines = compile_monitors(directive)
    assert all(isinstance(m, ConfigMachine) for m in machines)
    return [m for m in machines if isinstance(m, ConfigMachine)]


def _exhaustive_mismatches(
    longest: Callable[[int], int], limit: int | None = None, disabled: bool = False
) -> int:
    """Traces on which some conjunct's machine differs from the reference of that conjunct
    (a property ``and`` compiles to one machine per conjunct)."""
    mismatches = 0
    for _, prop, signals, longest_trace in FIXED:
        names = (*signals, "z") if disabled else signals
        directive = _directive(prop, _sig("z") if disabled else None)
        parts = [
            (replace(directive, property=conjunct), machine)
            for conjunct, machine in zip(
                prop.operands if isinstance(prop, PropAnd) else (prop,),
                _machines(directive),
                strict=True,
            )
        ]
        for length in range(1, min(longest_trace, longest(len(names))) + 1):
            for trace in _all_traces(names, length):
                for part, machine in parts:
                    try:
                        _compare(part, trace, simulate_configurations(machine, trace))
                    except (AssertionError, MachineError):
                        mismatches += 1
                        if limit is not None and mismatches >= limit:
                            return mismatches
    return mismatches


def test_the_fixed_until_properties_compile_to_the_configuration_machine() -> None:
    for name, prop, _, _ in FIXED:
        for kind in (DirectiveKind.ASSERT, DirectiveKind.ASSUME):
            assert _machines(_directive(prop, kind=kind)), name


@pytest.mark.extended
def test_exhaustive_short_traces_equal_the_reference() -> None:
    """36: up to 3 samples (2 with four signals or ``disable iff``)."""
    short = lambda signals: 3 if signals <= 3 else 2  # noqa: E731
    assert _exhaustive_mismatches(short) == 0
    assert _exhaustive_mismatches(short, disabled=True) == 0


@pytest.mark.slow
def test_exhaustive_traces_equal_the_reference() -> None:
    """36: up to the per-property length of ``FIXED`` (bounded by the bits per trace)."""
    assert _exhaustive_mismatches(lambda signals: 11 // signals) == 0
    assert _exhaustive_mismatches(lambda signals: 11 // signals, disabled=True) == 0


# --- 37: law -----------------------------------------------------------------------------------


@pytest.mark.parametrize("inclusive", [False, True])
@pytest.mark.parametrize("wrap", ["property", "consequent", "always"])
def test_an_until_compiles_to_the_machine_of_the_written_sequence(
    inclusive: bool, wrap: str
) -> None:
    """37: ``p until q`` is ``weak(p[*0:$] ##1 q)`` and ``p until_with q`` is
    ``weak(p[*0:$] ##1 (p && q))``, written out independently of the lowering."""
    right: Expr = Binary(BinaryOp.LOGICAL_AND, _B, _C, BIT) if inclusive else _C
    written = SequenceProperty(
        SeqConcat(Repeat(Bool(_B), 0, None), Delay(1, 1), Bool(right)), Strength.WEAK
    )
    node = _until(_B, _C, inclusive)

    def wrapped(inner: Property) -> Property:
        match wrap:
            case "property":
                return inner
            case "consequent":
                return Implication(Bool(_A), inner, True)
            case _:
                return Always(0, None, False, inner)

    assert _config(_directive(wrapped(node))) == _config(_directive(wrapped(written)))


# --- 38: the first-sample rule on every condition after lowering ------------------------------


@pytest.mark.extended
@pytest.mark.parametrize("inclusive", [False, True])
def test_a_past_operand_does_not_hold_at_the_first_sample_in_each_position(
    inclusive: bool,
) -> None:
    """38: ``$past`` in ``p``, in ``q`` and in both; a ``p`` or ``q`` made of ``$past`` never
    holds at sample 0, also inside the generated ``p && q`` (the machine equals the
    reference, which applies the rule to the written operands)."""
    for left, right in ((_past(_B), _C), (_B, _past(_C)), (_past(_B), _past(_C))):
        directive = _directive(_IMP(Bool(_A), _until(left, right, inclusive), True))
        machine = _config(directive)
        for length in range(1, 5):
            for trace in _all_traces(("a", "b", "c"), length):
                _compare(directive, trace, simulate_configurations(machine, trace))


def test_the_generated_conjunction_is_a_condition_the_machine_sees() -> None:
    machine = _config(_directive(_until(_B, _C, True)))
    conjunction = Binary(BinaryOp.LOGICAL_AND, _B, _C, BIT)
    assert conjunction in machine.conditions


# --- 39: mutants are caught --------------------------------------------------------------------

# Anchored: "x" starts the attempt; a long run of a signal or one disturbance decides.
_ANCHORED: list[tuple[str, Property, tuple[str, ...]]] = [
    ("x |-> a until b", _IMP(Bool(_sig("x")), _until(_A, _B), True), ("x", "a", "b")),
    ("x |-> a until_with b", _IMP(Bool(_sig("x")), _until(_A, _B, True), True), ("x", "a", "b")),
    ("x |=> a until b", _IMP(Bool(_sig("x")), _until(_A, _B), False), ("x", "a", "b")),
    ("x |=> a until_with b", _IMP(Bool(_sig("x")), _until(_A, _B, True), False), ("x", "a", "b")),
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
        except (AssertionError, CompileError):
            return 10**9  # a mutant that changes the machine kind is caught
        for trace in _shape_traces(signals):
            try:
                _compare(directive, trace, simulate_configurations(machine, trace))
            except (AssertionError, MachineError):
                count += 1
                if limit is not None and count >= limit:
                    return count
    return count


@pytest.mark.extended
def test_the_anchored_until_properties_pass_unmutated() -> None:
    assert _anchored_mismatches() == 0


def _mutant_sequence(inclusive: Callable[[bool], bool], loop_high: int | None) -> Any:
    def mutated(prop: Until) -> Sequence:
        condition: Expr = prop.right
        if inclusive(prop.inclusive):
            condition = Binary(BinaryOp.LOGICAL_AND, prop.left, prop.right, BIT)
        return SeqConcat(Repeat(Bool(prop.left), 0, loop_high), Delay(1, 1), Bool(condition))

    return mutated


def test_mutant_until_built_as_until_with_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """39: ``p`` is required at the sample where ``q`` holds for a plain ``until``."""
    monkeypatch.setattr(lower_module, "until_sequence", _mutant_sequence(lambda _: True, None))
    assert _anchored_mismatches(limit=1) > 0


@pytest.mark.extended
def test_mutant_until_with_built_as_until_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """39: ``p`` is not required at the sample where ``q`` holds for ``until_with``."""
    monkeypatch.setattr(lower_module, "until_sequence", _mutant_sequence(lambda _: False, None))
    assert _anchored_mismatches(limit=1) > 0


def test_mutant_until_loop_read_as_a_finite_limit_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """39: ``p[*0:$]`` written as ``p[*0:2]``."""
    monkeypatch.setattr(lower_module, "until_sequence", _mutant_sequence(lambda w: w, 2))
    assert _anchored_mismatches(limit=1) > 0


def test_mutant_repetition_to_dollar_read_as_a_finite_limit_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """39: the automaton builds ``[*m:$]`` as ``[*m:m+2]``."""
    builder: Any = vars(automaton_module)["_Builder"]

    def finite(self: Any, inner: Sequence, low: int) -> Any:
        return builder._repeat(self, inner, low, low + 2)

    monkeypatch.setattr(builder, "_repeat_unbounded", finite)
    assert _anchored_mismatches(limit=1) > 0


# --- 40: rejections ----------------------------------------------------------------------------


def test_still_rejected_until_shapes() -> None:
    """40: covers and the shapes that need the per-attempt machine; the strong forms are
    accepted for an ``assert`` and for an ``assume``, as
    is ``not`` of a weak until (strong after negation)."""
    weak = _until(_B, _C)
    assume = DirectiveKind.ASSUME
    for strong in (
        _directive(Until(_B, _C, True, False), kind=assume),
        _directive(Until(_B, _C, True, True), kind=assume),
        _directive(_IMP(Bool(_A), Until(_B, _C, True, False), True), kind=assume),
        _directive(Not(weak), kind=assume),
        _directive(_IMP(Bool(_A), Not(_until(_B, _C, True)), True), kind=assume),
    ):
        (monitor,) = compile_monitors(strong)
        assert isinstance(monitor, ConfigMachine)
        assert len(monitor.configurations) == 1 and monitor.liveness is not None
    rejected: list[Directive] = [
        _directive(weak, kind=DirectiveKind.COVER),
        _directive(_IMP(Bool(_A), weak, True), kind=DirectiveKind.COVER),
        _directive(Always(0, None, False, weak), kind=DirectiveKind.COVER),
        _directive(PropOr((SequenceProperty(Bool(_A)), weak))),
        _directive(_IMP(Bool(_A), _IMP(Bool(_D), weak, True), True)),
        _directive(_IMP(Bool(_A), PropOr((SequenceProperty(Bool(_D)), weak)), True)),
        _directive(_IMP(Bool(_A), Nexttime(1, False, weak), True)),
    ]
    for directive in rejected:
        with pytest.raises(CompileError, match=r"until|not supported"):
            compile_monitors(directive)
    for directive in rejected[:1]:
        with pytest.raises(CompileError, match="until"):
            compile_monitors(directive)


def test_an_until_never_reaches_the_slot_or_the_attempt_machine() -> None:
    directive = _directive(_IMP(Bool(_A), _until(_B, _C), True))
    with pytest.raises(CompileError):
        compile_attempt_machine(directive)
    monitor = compile_monitors(directive)[0]
    assert not isinstance(monitor, (Machine, AttemptMachine))


# --- model sizes of the hand cases -------------------------------------------------------------


@pytest.mark.parametrize(
    ("prop", "configurations", "antecedent_bits"),
    [
        (_IMP(Bool(_A), _until(_B, _C), True), 1, 0),
        (_IMP(Bool(_A), _until(_B, _C, True), True), 1, 0),
        (_IMP(Bool(_A), _until(_B, _C), False), 1, 1),
        (_until(_B, _C), 1, 0),
        (Always(0, None, False, _IMP(Bool(_A), _until(_B, _C), True)), 1, 2),
    ],
)
def test_the_model_sizes_of_the_hand_cases(
    prop: Property, configurations: int, antecedent_bits: int
) -> None:
    size = config_size(_config(_directive(prop)))
    assert size.configurations == configurations
    assert size.antecedent_bits == antecedent_bits
