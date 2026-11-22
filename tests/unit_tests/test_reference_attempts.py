"""Unit contract of the per-attempt reference evaluator (``support.reference.evaluate``):
prefix monotonicity, algebraic laws, and a few hand-derived cases."""

from __future__ import annotations

import itertools

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from support.reference.evaluate import (
    AttemptResult,
    EvaluationError,
    Outcome,
    attempt_results,
    evaluate_attempts,
    finalize_attempts,
)
from support.reference.trace import Trace
from sva2yosys.ir.expr import BIT, Expr, Signal, Unary, UnaryOp
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
    Strength,
)
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat, SeqOr, Sequence

NAMES = ("a", "b", "c")


def _sig(name: str) -> Expr:
    return Signal(f"t.{name}", BIT)


def _not(expr: Expr) -> Expr:
    return Unary(UnaryOp.LOGICAL_NOT, expr, BIT)


def _seq(name: str) -> Sequence:
    return Bool(_sig(name))


def _then(left: Sequence, low: int, high: int, right: Sequence) -> Sequence:
    return SeqConcat(left, Delay(low, high), right)


def _prop(sequence: Sequence, strength: Strength = Strength.DEFAULT) -> Property:
    return SequenceProperty(sequence, strength)


def _trace(rows: dict[str, str]) -> Trace:
    """``{"a": "1.1"}``: one character per sample, ``.`` is 0."""
    length = len(next(iter(rows.values())))
    return Trace(
        tuple(
            {f"t.{name}": int(text[i] == "1") for name, text in rows.items()} for i in range(length)
        )
    )


def _all_traces(max_length: int, names: tuple[str, ...] = NAMES) -> list[Trace]:
    traces: list[Trace] = []
    for length in range(1, max_length + 1):
        for bits in itertools.product((0, 1), repeat=length * len(names)):
            traces.append(
                Trace(
                    tuple(
                        {f"t.{n}": bits[t * len(names) + i] for i, n in enumerate(names)}
                        for t in range(length)
                    )
                )
            )
    return traces


# --- generated property trees ------------------------------------------------------------

_SIGNAL_NAMES = st.sampled_from(NAMES)
_LEAF_SEQUENCES: st.SearchStrategy[Sequence] = st.one_of(
    _SIGNAL_NAMES.map(_seq),
    st.builds(
        lambda x, y: _then(_seq(x), 1, 1, _seq(y)),
        _SIGNAL_NAMES,
        _SIGNAL_NAMES,
    ),
    st.builds(
        lambda x, y: _then(_seq(x), 0, 1, _seq(y)),
        _SIGNAL_NAMES,
        _SIGNAL_NAMES,
    ),
)


def _windows() -> st.SearchStrategy[tuple[int, int]]:
    return st.integers(0, 2).flatmap(lambda low: st.integers(low, 2).map(lambda high: (low, high)))


def _properties(depth: int) -> st.SearchStrategy[Property]:
    leaf: st.SearchStrategy[Property] = _LEAF_SEQUENCES.map(_prop)
    if depth <= 0:
        return leaf
    inner = _properties(depth - 1)
    return st.one_of(
        leaf,
        st.builds(Nexttime, st.integers(0, 2), st.booleans(), inner),
        st.builds(lambda w, s, p: Always(w[0], w[1], s, p), _windows(), st.booleans(), inner),
        st.builds(lambda w, s, p: Eventually(w[0], w[1], s, p), _windows(), st.booleans(), inner),
        inner.map(Not),
        st.builds(lambda p, q: PropAnd((p, q)), inner, inner),
        st.builds(lambda p, q: PropOr((p, q)), inner, inner),
        st.builds(lambda n, p: PropIf(_sig(n), p, None), _SIGNAL_NAMES, inner),
        st.builds(lambda n, p, q: PropIf(_sig(n), p, q), _SIGNAL_NAMES, inner, inner),
        st.builds(Implication, _LEAF_SEQUENCES, inner, st.booleans()),
    )


def _generated_traces() -> st.SearchStrategy[Trace]:
    row = st.fixed_dictionaries({f"t.{n}": st.integers(0, 1) for n in NAMES})
    return st.lists(row, min_size=1, max_size=6).map(lambda rows: Trace(tuple(rows)))


@pytest.mark.extended
@settings(
    max_examples=300,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(prop=_properties(3), trace=_generated_traces())
def test_decisions_and_nonvacuity_are_prefix_monotone(prop: Property, trace: Trace) -> None:
    """A decision at ``d`` (and the sample ``k`` at which nonvacuity is known) is the same on
    every prefix that contains it, and the attempt is pending (unknown) on shorter ones."""
    full = attempt_results(prop, trace)
    for length in range(1, len(trace) + 1):
        prefix = attempt_results(prop, Trace(trace.samples[:length]))
        assert len(prefix) == length
        for seen, final in zip(prefix, full, strict=False):
            if final.decided is not None and final.decided < length:
                assert (seen.outcome, seen.decided) == (final.outcome, final.decided)
            else:
                assert (seen.outcome, seen.decided) == (Outcome.PENDING, None)
            known = final.nonvacuity_known
            if known is not None and known < length:
                assert seen.nonvacuity_known == known
                assert seen.nonvacuous == final.nonvacuous
            else:
                assert (seen.nonvacuity_known, seen.nonvacuous) == (None, None)


# --- laws, exhaustively on short traces -----------------------------------------------------

_A, _B, _C = _prop(_seq("a")), _prop(_seq("b")), _prop(_seq("c"))
_BASES: list[Property] = [
    _A,
    _prop(_then(_seq("a"), 1, 1, _seq("b"))),
    _prop(_then(_seq("b"), 0, 1, _seq("c"))),
    Implication(_seq("a"), _B, True),
    Implication(_seq("a"), _prop(_then(_seq("b"), 1, 1, _seq("c"))), False),
    Implication(_then(_seq("a"), 0, 1, _seq("b")), _C, True),
    Not(Implication(_seq("b"), _C, True)),
    PropOr((Implication(_seq("a"), _B, True), _C)),
]
_PAIRS = [(p, q) for p in (_BASES[1], _BASES[3], _BASES[6]) for q in (_A, _BASES[4])]


def _check_laws(max_length: int) -> None:
    traces = _all_traces(max_length)
    cases: list[tuple[str, Property, Property]] = []
    for p in _BASES:
        cases.append(("not not", Not(Not(p)), p))
    for p, q in _PAIRS:
        cases.append(("de morgan and", Not(PropAnd((p, q))), PropOr((Not(p), Not(q)))))
        cases.append(("de morgan or", Not(PropOr((p, q))), PropAnd((Not(p), Not(q)))))
    for name in NAMES:
        for p, q in _PAIRS[::2]:
            expr = _sig(name)
            cases.append(
                ("if without else", PropIf(expr, p, None), Implication(Bool(expr), p, True))
            )
            cases.append(
                (
                    "if with else",
                    PropIf(expr, p, q),
                    PropAnd(
                        (
                            Implication(Bool(expr), p, True),
                            Implication(Bool(_not(expr)), q, True),
                        )
                    ),
                )
            )
    for first in (_seq("a"), _then(_seq("a"), 0, 1, _seq("b"))):
        for consequent in (_C, _prop(_then(_seq("c"), 1, 1, _seq("a"))), _BASES[3]):
            cases.append(
                (
                    "nested implication",
                    Implication(first, Implication(_seq("b"), consequent, True), True),
                    Implication(_then(first, 0, 0, _seq("b")), consequent, True),
                )
            )
    for law, left, right in cases:
        for trace in traces:
            assert attempt_results(left, trace) == attempt_results(right, trace), (law, left, trace)


@pytest.mark.extended
def test_laws_on_traces_up_to_length_3() -> None:
    _check_laws(3)


@pytest.mark.slow
def test_laws_on_traces_up_to_length_4() -> None:
    _check_laws(4)


# --- nexttime / always / eventually (native evaluation) --------------------------------------

_OPERANDS: list[Property] = [
    _A,
    _prop(_then(_seq("a"), 1, 1, _seq("b"))),
    Implication(_seq("a"), _prop(_then(_seq("b"), 1, 1, _seq("b"))), True),
    Not(Implication(_seq("a"), _B, True)),
    PropOr((Implication(_seq("a"), _B, True), _prop(_then(_seq("a"), 1, 1, _seq("b"))))),
]
_WINDOWS = [(0, 1), (1, 2), (0, 2)]


def _modal_cases() -> list[tuple[str, Property, Property]]:
    cases: list[tuple[str, Property, Property]] = []
    for p in _OPERANDS:
        for strong in (False, True):
            cases.append(("always [0:0]", Always(0, 0, strong, p), p))
            cases.append(("eventually [0:0]", Eventually(0, 0, strong, p), p))
            cases.append(("nexttime 0", Nexttime(0, strong, p), p))
            for n in (1, 2):
                cases.append(
                    ("nexttime is always", Nexttime(n, strong, p), Always(n, n, strong, p))
                )
                cases.append(
                    ("nexttime is eventually", Nexttime(n, strong, p), Eventually(n, n, strong, p))
                )
                cases.append(
                    (
                        "not nexttime",
                        Not(Nexttime(n, strong, p)),
                        Nexttime(n, not strong, Not(p)),
                    )
                )
            for low, high in _WINDOWS:
                cases.append(
                    (
                        "not always",
                        Not(Always(low, high, strong, p)),
                        Eventually(low, high, not strong, Not(p)),
                    )
                )
                cases.append(
                    (
                        "not eventually",
                        Not(Eventually(low, high, strong, p)),
                        Always(low, high, not strong, Not(p)),
                    )
                )
    return cases


def _check_modal_laws(max_length: int) -> None:
    traces = _all_traces(max_length, ("a", "b"))
    for law, left, right in _modal_cases():
        for trace in traces:
            assert attempt_results(left, trace) == attempt_results(right, trace), (law, left, trace)
            for cover in (False, True):
                assert finalize_attempts(left, trace, None, cover) == finalize_attempts(
                    right, trace, None, cover
                ), (law, cover, left, trace)


@pytest.mark.extended
def test_modal_laws_on_traces_up_to_length_3() -> None:
    _check_modal_laws(3)


@pytest.mark.slow
def test_modal_laws_on_traces_up_to_length_4() -> None:
    _check_modal_laws(4)


# --- hand-derived cases ----------------------------------------------------------------------


def _only(prop: Property, rows: dict[str, str], disable: Expr | None = None) -> list[AttemptResult]:
    return list(attempt_results(prop, _trace(rows), disable))


def test_vacuous_implication_is_true_and_vacuous_at_its_start() -> None:
    prop = Implication(_seq("a"), _B, True)
    results = _only(prop, {"a": ".1", "b": ".."})
    assert results[0] == AttemptResult(0, Outcome.TRUE, 0, False, 0)
    assert results[1] == AttemptResult(1, Outcome.FALSE, 1, True, 1)


def test_not_of_a_vacuous_implication_is_false_and_vacuous() -> None:
    prop = Not(Implication(_seq("a"), _B, True))
    (first,) = _only(prop, {"a": ".", "b": "."})
    assert first == AttemptResult(0, Outcome.FALSE, 0, False, 0)


def test_nonvacuity_can_be_known_after_the_decision() -> None:
    # (a |-> b) or ((c ##1 b) |-> a): attempt 0 is TRUE at 0 through the vacuous first operand;
    # the second operand's antecedent matches at 1 (nonvacuous), so nonvacuity is known at 1
    # only, and the attempt is not a cover success.
    prop = PropOr(
        (Implication(_seq("a"), _B, True), Implication(_then(_seq("c"), 1, 1, _seq("b")), _A, True))
    )
    evaluation = evaluate_attempts(prop, _trace({"a": "..", "b": ".1", "c": "1."}))
    assert evaluation.attempts[0] == AttemptResult(0, Outcome.TRUE, 0, True, 1)
    assert evaluation.successes == ()


def test_a_disabled_attempt_reports_the_disable_sample() -> None:
    prop = Implication(_seq("a"), _prop(_then(_seq("b"), 1, 1, _seq("b"))), True)
    results = _only(prop, {"a": "1.1", "b": "1.1", "d": ".1."}, _sig("d"))
    assert [(r.outcome, r.decided) for r in results] == [
        (Outcome.DISABLED, 1),
        (Outcome.DISABLED, 1),
        (Outcome.PENDING, None),
    ]


def test_disable_after_the_decision_does_not_apply() -> None:
    results = _only(Implication(_seq("a"), _B, True), {"a": "1.", "b": "..", "d": ".1"}, _sig("d"))
    assert (results[0].outcome, results[0].decided) == (Outcome.FALSE, 0)


@pytest.mark.parametrize(
    ("strength", "cover", "expected"),
    [
        (Strength.DEFAULT, False, Outcome.TRUE),
        (Strength.DEFAULT, True, Outcome.FALSE),
        (Strength.WEAK, True, Outcome.TRUE),
        (Strength.STRONG, False, Outcome.FALSE),
    ],
)
def test_pending_sequence_finalizes_by_strength(
    strength: Strength, cover: bool, expected: Outcome
) -> None:
    prop = _prop(_then(_seq("a"), 2, 2, _seq("b")), strength)
    trace = _trace({"a": "11", "b": ".."})
    assert [r.outcome for r in attempt_results(prop, trace)] == [Outcome.PENDING] * 2
    assert finalize_attempts(prop, trace, None, cover) == (expected, expected)


def test_not_reverses_the_finalized_strength() -> None:
    prop = Not(_prop(_then(_seq("a"), 2, 2, _seq("b")), Strength.STRONG))
    assert finalize_attempts(prop, _trace({"a": "1", "b": "."}), None, False) == (Outcome.TRUE,)


def test_pending_implication_finalizes_its_started_consequents_only() -> None:
    consequent = _then(_seq("a"), 2, 2, _seq("b"))
    strong = Implication(_seq("a"), _prop(consequent, Strength.STRONG), True)
    trace = _trace({"a": "1.", "b": ".."})
    assert finalize_attempts(strong, trace, None, False) == (Outcome.FALSE, Outcome.TRUE)
    weak = Implication(_seq("a"), _prop(consequent, Strength.WEAK), True)
    assert finalize_attempts(weak, trace, None, False) == (Outcome.TRUE, Outcome.TRUE)
    # |=> at the last sample: its antecedent match ends after the trace, so it does not exist
    late = Implication(_seq("a"), _prop(_seq("b"), Strength.STRONG), False)
    assert finalize_attempts(late, _trace({"a": ".1", "b": ".."}), None, False) == (
        Outcome.TRUE,
        Outcome.TRUE,
    )


def test_successes_are_true_nonvacuous_attempts() -> None:
    prop = PropOr((Implication(_seq("a"), _B, True), _prop(_then(_seq("a"), 0, 1, _seq("c")))))
    evaluation = evaluate_attempts(prop, _trace({"a": "1.", "b": "1.", "c": ".."}))
    assert evaluation.attempts[0].outcome is Outcome.TRUE
    assert evaluation.successes == (0, 1)  # a sequence operand is always nonvacuous


def test_nonoverlapping_implication_with_a_nullable_antecedent_is_rejected() -> None:
    prop = Implication(Repeat(_seq("a"), 0, 1), _B, False)
    with pytest.raises(EvaluationError):
        attempt_results(prop, _trace({"a": "1", "b": "1"}))


@pytest.mark.parametrize(
    ("strong", "expected"),
    [(False, Outcome.TRUE), (True, Outcome.FALSE)],
)
def test_nexttime_beyond_the_trace_finalizes_by_its_strength(
    strong: bool, expected: Outcome
) -> None:
    # nexttime[2] a on 2 samples: both attempts wait for a sample that never comes; the
    # operand's own strength is irrelevant (a plain sequence would be weak under assert).
    prop = Nexttime(2, strong, _A)
    trace = _trace({"a": "11", "b": ".."})
    assert [r.outcome for r in attempt_results(prop, trace)] == [Outcome.PENDING] * 2
    assert finalize_attempts(prop, trace, None, False) == (expected, expected)


def test_nexttime_within_the_trace_is_the_operand_at_the_offset() -> None:
    # nexttime[1] a, a = 1 0: attempt 0 looks at sample 1 (a = 0): FALSE at 1.
    results = _only(Nexttime(1, False, _A), {"a": "10"})
    assert results[0] == AttemptResult(0, Outcome.FALSE, 1, True, 1)
    assert results[1].outcome is Outcome.PENDING


def test_s_always_with_missing_offsets_finalizes_false_and_always_true() -> None:
    # [0:2] a on a = 1 1: attempt 0 has offsets 0, 1 TRUE, offset 2 missing; attempt 1 has
    # offset 0 TRUE and offsets 1, 2 missing. Weak: the missing part is TRUE; strong: FALSE.
    trace = _trace({"a": "11"})
    weak, strong = Always(0, 2, False, _A), Always(0, 2, True, _A)
    assert [r.outcome for r in attempt_results(strong, trace)] == [Outcome.PENDING] * 2
    assert finalize_attempts(weak, trace, None, False) == (Outcome.TRUE, Outcome.TRUE)
    assert finalize_attempts(strong, trace, None, False) == (Outcome.FALSE, Outcome.FALSE)


def test_always_fails_at_the_first_failing_offset() -> None:
    # always[0:2] a on a = 1 0 1: attempt 0 fails at sample 1 (offset 1), before offset 2.
    results = _only(Always(0, 2, False, _A), {"a": "101"})
    assert (results[0].outcome, results[0].decided) == (Outcome.FALSE, 1)


def test_eventually_with_a_true_started_offset_is_true_whatever_its_strength() -> None:
    # eventually[0:1] (a ##2 b) on a = 1 1, b = 0 0: both operand attempts are pending
    # (weak sequence under assert: finalize TRUE), so the finalized parts hold a TRUE.
    operand = _prop(_then(_seq("a"), 2, 2, _seq("b")))
    trace = _trace({"a": "11", "b": ".."})
    for strong in (False, True):
        prop = Eventually(0, 1, strong, operand)
        assert finalize_attempts(prop, trace, None, False) == (Outcome.TRUE, Outcome.TRUE)
    # a decided TRUE offset: eventually[0:2] a on a = . 1 .: attempts 0 and 1 are TRUE at 1.
    results = _only(Eventually(0, 2, True, _A), {"a": ".1."})
    assert [(r.outcome, r.decided) for r in results[:2]] == [(Outcome.TRUE, 1)] * 2
    # attempt 2: offset 0 is FALSE, offsets 1, 2 are missing: strong FALSE, weak TRUE
    for strong, expected in ((True, Outcome.FALSE), (False, Outcome.TRUE)):
        trace = _trace({"a": ".1."})
        assert finalize_attempts(Eventually(0, 2, strong, _A), trace, None, False)[2] is expected


def test_always_whose_offset_zero_is_vacuously_false_is_false_and_vacuous() -> None:
    # Not(a |-> b) at an offset with a = 0 is FALSE and vacuous; always[0:1] of it is FALSE
    # at once, and the later offset cannot make it nonvacuous (offset 0 already decided it).
    prop = Always(0, 1, False, Not(Implication(_seq("a"), _B, True)))
    results = _only(prop, {"a": "..", "b": ".."})
    assert results[0] == AttemptResult(0, Outcome.FALSE, 0, False, 0)
    assert results[1] == AttemptResult(1, Outcome.FALSE, 1, False, 1)


def test_eventually_nonvacuity_respects_the_order_of_the_offsets() -> None:
    # I = ((a ##2 b) or d) |-> c. Trace (a, d, b, c):
    #   s0: 1 1? no -> s0: a=1 d=0 b=0 c=0;  s1: a=0 d=1 b=0 c=1;  s2: all 0.
    # I at 0: no match yet but (a ##2 b) may still match until sample 2 -> pending, nonvacuity
    #   unknown; at 2 it cannot match: TRUE and vacuous (known at 2).
    # I at 1: d matches at 1 and c holds: TRUE, nonvacuous (known at 1).
    # eventually[0:1] I at 0 is TRUE at 1 (offset 1). Its nonvacuity: term 1 needs offset 0
    # to be FALSE ("no earlier offset holds"); offset 0 is TRUE at 2, so term 1 is False and
    # term 0 is False: vacuous, known only at 2. (At 1 both terms are unknown.)
    antecedent = SeqOr(_then(_seq("a"), 2, 2, _seq("b")), _seq("d"))
    inner = Implication(antecedent, _C, True)
    rows = {"a": "1..", "d": ".1.", "b": "...", "c": ".1."}
    (first, *_) = _only(Eventually(0, 1, False, inner), rows)
    assert first == AttemptResult(0, Outcome.TRUE, 1, False, 2)
    # An implication over the same window triggers I at both offsets independently: the
    # trigger at 1 makes it nonvacuous, known at 1.
    window = _then(TRUE, 0, 1, TRUE)
    (first, *_) = _only(Implication(window, inner, True), rows)
    assert (first.outcome, first.nonvacuous, first.nonvacuity_known) == (Outcome.TRUE, True, 1)
