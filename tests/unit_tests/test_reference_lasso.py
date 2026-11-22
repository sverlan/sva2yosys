"""Unit contract of the lasso evaluator (``support.reference.lasso``): exact results of
attempts on infinite, ultimately periodic traces (``docs/semantics.md`` §9.2, §9.6).

The hand-derived cases are the trace tables ``reference_semantics/fixtures/lasso.trace``.
Here, laws checked on every lasso of two signals:

- *consistency with the finite evaluator*: an attempt decided online on the written samples
  has the same outcome and decision sample on the infinite trace, and an attempt disabled
  there is disabled at the same sample;
- *weak and strong agree*: on an infinite trace the strength of a bounded property never
  matters (every offset exists), so the weak and strong forms give identical results;
- *unrolling*: on a long finite unrolling of the lasso, the finite evaluator decides every
  attempt that the infinite evaluation decides online at the same sample, and the
  finalized outcome of the others (for properties whose operands are decided by their own
  sample) equals the infinite outcome.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator

import pytest

from support.reference.evaluate import (
    EvaluationError,
    Outcome,
    attempt_results,
    finalize_attempts,
)
from support.reference.lasso import Lasso, LassoAttemptResult, lasso_attempt_results
from support.reference.trace import Trace
from sva2yosys.ir.expr import (
    BIT,
    Expr,
    Sampled,
    SampledFunction,
    Signal,
    Unary,
    UnaryOp,
)
from sva2yosys.ir.property import (
    Always,
    Eventually,
    Implication,
    Nexttime,
    Not,
    PropAnd,
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
    Repeat,
    SeqConcat,
    Sequence,
)

A = Signal("t.a", BIT)
B = Signal("t.b", BIT)
D = Signal("t.d", BIT)


def _not(expr: Expr) -> Expr:
    return Unary(UnaryOp.LOGICAL_NOT, expr, BIT)


def _seq(strength: Strength, sequence: Sequence) -> SequenceProperty:
    return SequenceProperty(sequence, strength)


def _later(low: int, high: int | None, condition: Expr) -> Sequence:
    """``##[low:high] condition``."""
    return SeqConcat(TRUE, Delay(low, high), Bool(condition))


def _lassos(max_prefix: int, max_loop: int, signals: tuple[str, ...]) -> Iterator[Lasso]:
    rows = [
        dict(zip(signals, values, strict=True))
        for values in itertools.product((0, 1), repeat=len(signals))
    ]
    for prefix in range(max_prefix + 1):
        for loop in range(1, max_loop + 1):
            for samples in itertools.product(rows, repeat=prefix + loop):
                yield Lasso(samples, prefix)


AB = ("t.a", "t.b")
ABD = ("t.a", "t.b", "t.d")
SMALL = list(_lassos(1, 2, AB))
LARGE = list(_lassos(2, 2, AB))
SMALL_D = list(_lassos(1, 2, ABD))


# --- the lasso itself ---------------------------------------------------------------------


def test_the_infinite_trace_repeats_the_loop() -> None:
    lasso = Lasso(tuple({"t.a": v} for v in (0, 1, 2, 3)), 1)
    assert [row["t.a"] for row in lasso.unroll(9).samples] == [0, 1, 2, 3, 1, 2, 3, 1, 2]
    assert [lasso.index(q) for q in range(8)] == [0, 1, 2, 3, 1, 2, 3, 1]
    assert lasso.period == 3


def test_a_loop_outside_the_samples_is_rejected() -> None:
    samples = ({"t.a": 0}, {"t.a": 1})
    for loop in (-1, 2):
        with pytest.raises(EvaluationError, match="outside"):
            Lasso(samples, loop)
    with pytest.raises(EvaluationError, match="outside"):
        Lasso((), 0)


# --- laws ---------------------------------------------------------------------------------

WEAK, STRONG = Strength.WEAK, Strength.STRONG
A_THEN_B = SeqConcat(Bool(A), Delay(1, None), Bool(B))
PAST_B2 = Sampled(SampledFunction.PAST, B, BIT, 2)
ROSE_B = Sampled(SampledFunction.ROSE, B, BIT)

#: properties the infinite evaluation accepts (all unbounded forms, bounded ones too)
PROPERTIES: dict[str, Property] = {
    "weak a then b": _seq(WEAK, A_THEN_B),
    "strong a then b": _seq(STRONG, A_THEN_B),
    "a |-> strong later b": Implication(Bool(A), _seq(STRONG, _later(1, None, B)), True),
    "several triggers": Implication(Repeat(Bool(A), 1, 2), _seq(STRONG, _later(1, None, B)), True),
    "unbounded antecedent": Implication(
        Repeat(Bool(A), 1, None), _seq(STRONG, _later(1, None, B)), True
    ),
    "a |=> strong later b": Implication(Bool(A), _seq(STRONG, _later(0, None, B)), False),
    "a |-> weak later b": Implication(Bool(A), _seq(WEAK, _later(1, None, B)), True),
    "a |-> bounded b": Implication(Bool(A), _seq(STRONG, _later(1, 2, B)), True),
    "s_eventually b": Eventually(0, None, True, _seq(WEAK, Bool(B))),
    "s_eventually from 2": Eventually(2, None, True, _seq(WEAK, Bool(B))),
    "always b": Always(0, None, False, _seq(WEAK, Bool(B))),
    "always from 1": Always(1, None, False, _seq(WEAK, Bool(B))),
    "always (a |-> s_eventually b)": Always(
        0, None, False, Implication(Bool(A), Eventually(0, None, True, _seq(WEAK, Bool(B))), True)
    ),
    "always s_eventually b": Always(0, None, False, Eventually(0, None, True, _seq(WEAK, Bool(B)))),
    "s_eventually always b": Eventually(0, None, True, Always(0, None, False, _seq(WEAK, Bool(B)))),
    "a until b": Until(A, B, False, False),
    "a s_until b": Until(A, B, True, False),
    "a until_with b": Until(A, B, False, True),
    "a s_until_with b": Until(A, B, True, True),
    "not weak a then b": Not(_seq(WEAK, A_THEN_B)),
    "not strong a then b": Not(_seq(STRONG, A_THEN_B)),
    "a and s_eventually b": PropAnd(
        (_seq(WEAK, Bool(A)), Eventually(0, None, True, _seq(WEAK, Bool(B))))
    ),
    "a or s_eventually b": PropOr(
        (_seq(WEAK, Bool(A)), Eventually(0, None, True, _seq(WEAK, Bool(B))))
    ),
    "goto 2": _seq(STRONG, Goto(B, 2, 2)),
    "repeat a": _seq(STRONG, Repeat(Bool(A), 2, None)),
    "a |-> always b": Implication(Bool(A), Always(0, None, False, _seq(WEAK, Bool(B))), True),
    "past b strong": _seq(STRONG, _later(1, None, PAST_B2)),
    "s_eventually rose b": Eventually(0, None, True, _seq(WEAK, Bool(ROSE_B))),
    "nexttime always b": Nexttime(1, False, Always(0, None, False, _seq(WEAK, Bool(B)))),
    "always not a or b": Always(0, None, False, _seq(WEAK, Bool(_not(A)))),
}


def _same_when_decided(
    prop: Property, lasso: Lasso, disable: Expr | None, infinite: tuple[LassoAttemptResult, ...]
) -> None:
    finite = attempt_results(prop, Trace(lasso.samples), disable)
    assert len(finite) == len(infinite)
    for online, result in zip(finite, infinite, strict=True):
        match online.outcome:
            case Outcome.PENDING:
                continue
            case Outcome.TRUE | Outcome.FALSE | Outcome.DISABLED:
                assert (result.outcome, result.decided) == (online.outcome, online.decided), (
                    lasso,
                    online,
                    result,
                )


@pytest.mark.parametrize("name", PROPERTIES)
def test_a_decision_on_the_written_samples_is_the_infinite_decision(name: str) -> None:
    prop = PROPERTIES[name]
    for lasso in SMALL:
        _same_when_decided(prop, lasso, None, lasso_attempt_results(prop, lasso))


@pytest.mark.slow
@pytest.mark.parametrize("name", PROPERTIES)
def test_a_decision_on_longer_lassos_is_the_infinite_decision(name: str) -> None:
    prop = PROPERTIES[name]
    for lasso in LARGE:
        _same_when_decided(prop, lasso, None, lasso_attempt_results(prop, lasso))


@pytest.mark.parametrize(
    "name", ["s_eventually b", "always b", "a |-> strong later b", "a until b"]
)
def test_a_disable_decision_on_the_written_samples_is_the_infinite_one(name: str) -> None:
    prop = PROPERTIES[name]
    for lasso in SMALL_D:
        infinite = lasso_attempt_results(prop, lasso, D)
        _same_when_decided(prop, lasso, D, infinite)
        for result in infinite:
            if result.outcome is Outcome.DISABLED:
                assert result.decided is not None
                assert lasso.unroll(result.decided + 1).samples[result.decided]["t.d"] == 1
            assert result.outcome is not Outcome.PENDING


def _bounded_pairs() -> dict[str, tuple[Property, Property]]:
    def both(build: object) -> tuple[Property, Property]:
        assert callable(build)
        return build(WEAK), build(STRONG)

    ab = SeqConcat(Bool(A), Delay(1, 2), Bool(B))
    return {
        "sequence": both(lambda s: _seq(s, ab)),
        "implication": both(lambda s: Implication(Bool(A), _seq(s, _later(1, 3, B)), True)),
        "non-overlapping": both(lambda s: Implication(Bool(A), _seq(s, _later(0, 2, B)), False)),
        "not": both(lambda s: Not(_seq(s, ab))),
        "past": both(lambda s: _seq(s, _later(1, 2, PAST_B2))),
        "nexttime": (
            Nexttime(2, False, _seq(WEAK, Bool(A))),
            Nexttime(2, True, _seq(STRONG, Bool(A))),
        ),
        "always window": (
            Always(1, 3, False, _seq(WEAK, Bool(B))),
            Always(1, 3, True, _seq(STRONG, Bool(B))),
        ),
        "eventually window": (
            Eventually(1, 2, False, _seq(WEAK, Bool(B))),
            Eventually(1, 2, True, _seq(STRONG, Bool(B))),
        ),
        "nested windows": (
            Always(0, 2, False, Eventually(0, 1, False, _seq(WEAK, Bool(B)))),
            Always(0, 2, True, Eventually(0, 1, True, _seq(STRONG, Bool(B)))),
        ),
        "nexttime of an implication": (
            Nexttime(1, False, Implication(Bool(A), _seq(WEAK, _later(1, 2, B)), True)),
            Nexttime(1, True, Implication(Bool(A), _seq(STRONG, _later(1, 2, B)), True)),
        ),
    }


BOUNDED = _bounded_pairs()


def _compare_bounded(name: str, lassos: list[Lasso]) -> None:
    weak, strong = BOUNDED[name]
    for lasso in lassos:
        assert lasso_attempt_results(weak, lasso) == lasso_attempt_results(strong, lasso), lasso


@pytest.mark.parametrize("name", BOUNDED)
def test_weak_and_strong_bounded_properties_agree_on_infinite_traces(name: str) -> None:
    _compare_bounded(name, SMALL)


@pytest.mark.slow
@pytest.mark.parametrize("name", BOUNDED)
def test_weak_and_strong_agree_on_every_lasso_up_to_prefix_and_loop_two(name: str) -> None:
    _compare_bounded(name, LARGE)


@pytest.mark.parametrize("name", BOUNDED)
def test_a_bounded_property_is_always_decided_online(name: str) -> None:
    """No finite property stays undecided for ever on an infinite trace: every attempt of a
    bounded property is decided at a finite sample."""
    weak, _ = BOUNDED[name]
    for lasso in SMALL:
        for result in lasso_attempt_results(weak, lasso):
            assert result.decided is not None, (lasso, result)


#: properties whose pending parts are decided by their own sample or are leaf sequences, so
#: the finalized outcome at the end of a long unrolling equals the infinite outcome (not an
#: unbounded antecedent: the triggers near the end of the unrolling have pending consequents
#: that finalize FALSE, while on the infinite trace they are satisfied later)
UNROLLED = [
    "weak a then b",
    "strong a then b",
    "a |-> strong later b",
    "a |-> weak later b",
    "several triggers",
    "s_eventually b",
    "s_eventually from 2",
    "always b",
    "always from 1",
    "a until b",
    "a s_until b",
    "a s_until_with b",
    "not weak a then b",
    "goto 2",
    "repeat a",
    "past b strong",
    "s_eventually rose b",
]
UNROLLED_LENGTH = 26


def _unrolled_check(name: str, lassos: list[Lasso]) -> None:
    prop = PROPERTIES[name]
    for lasso in lassos:
        infinite = lasso_attempt_results(prop, lasso)
        trace = lasso.unroll(UNROLLED_LENGTH)
        finite = attempt_results(prop, trace)
        final = finalize_attempts(prop, trace, None, False)
        for start, result in enumerate(infinite):
            online = finite[start]
            if result.decided is not None:
                assert (online.outcome, online.decided) == (
                    Outcome.TRUE if result.outcome is Outcome.TRUE else Outcome.FALSE,
                    result.decided,
                ), (lasso, start)
            else:
                assert online.outcome is Outcome.PENDING, (lasso, start)
                assert final[start] is result.outcome, (lasso, start)


@pytest.mark.extended
@pytest.mark.parametrize("name", UNROLLED)
def test_the_infinite_results_match_a_long_unrolling(name: str) -> None:
    _unrolled_check(name, SMALL)


@pytest.mark.slow
@pytest.mark.parametrize("name", UNROLLED)
def test_the_infinite_results_match_a_long_unrolling_on_longer_lassos(name: str) -> None:
    _unrolled_check(name, LARGE)


def test_the_results_have_one_entry_per_written_sample() -> None:
    lasso = Lasso(({"t.b": 0}, {"t.b": 1}, {"t.b": 0}), 1)
    results = lasso_attempt_results(PROPERTIES["s_eventually b"], lasso)
    assert [r.start for r in results] == [0, 1, 2]
    assert [(r.outcome, r.decided) for r in results] == [(Outcome.TRUE, 1)] * 2 + [
        (Outcome.TRUE, 3)
    ]


def test_cover_makes_a_plain_sequence_strong() -> None:
    lasso = Lasso(({"t.a": 1, "t.b": 0},), 0)
    prop = SequenceProperty(A_THEN_B)
    assert lasso_attempt_results(prop, lasso)[0].outcome is Outcome.TRUE
    assert lasso_attempt_results(prop, lasso, None, True)[0].outcome is Outcome.FALSE


def test_a_shift_by_a_period_shifts_the_decision() -> None:
    """The results of attempts one period apart in the loop differ by that period only."""
    lasso = Lasso(({"t.b": 0}, {"t.b": 0}, {"t.b": 0}, {"t.b": 1}), 1)
    results = lasso_attempt_results(PROPERTIES["s_eventually b"], lasso)
    assert [r.decided for r in results] == [3, 3, 3, 3]
    shifted = lasso_attempt_results(Nexttime(3, False, PROPERTIES["s_eventually b"]), lasso)
    assert [r.decided for r in shifted] == [3, 6, 6, 6]
