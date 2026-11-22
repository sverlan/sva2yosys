"""Unit contract of the reference evaluator for the unbounded constructs (unbounded ranges,
goto / nonconsecutive repetition, unbounded ``always`` / ``s_eventually``, the ``until`` family):
the laws of ``docs/semantics.md`` §9.6, each checked on every trace of up to
``MAX_LENGTH`` samples of two signals, comparing the per-attempt results (outcome, decision,
nonvacuity) and the outcomes at the end of the simulation of the two sides.

The two sides of a law are built independently: the native node on one side, its definition
written with the older sequence nodes on the other, so a bug in either shows as a difference.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import pytest

from support.reference.evaluate import (
    Outcome,
    attempt_results,
    finalize_attempts,
    match_ends,
    sequence_states,
)
from support.reference.trace import Trace
from sva2yosys.ir.expr import (
    BIT,
    Binary,
    BinaryOp,
    Const,
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
    Not,
    Property,
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
    SeqAnd,
    SeqConcat,
    SeqIntersect,
    Sequence,
    Throughout,
    Within,
)

MAX_LENGTH = 3
SLOW_LENGTH = 5
A = Signal("t.a", BIT)
B = Signal("t.b", BIT)


def _not(expr: Expr) -> Expr:
    return Unary(UnaryOp.LOGICAL_NOT, expr, BIT)


def _traces(max_length: int = MAX_LENGTH) -> list[Trace]:
    rows = [{"t.a": a, "t.b": b} for a, b in itertools.product((0, 1), repeat=2)]
    return [
        Trace(tuple(combination))
        for length in range(1, max_length + 1)
        for combination in itertools.product(rows, repeat=length)
    ]


TRACES = _traces()
SLOW_TRACES = _traces(SLOW_LENGTH)


def _weak(sequence: Sequence) -> SequenceProperty:
    return SequenceProperty(sequence, Strength.WEAK)


def _strong(sequence: Sequence) -> SequenceProperty:
    return SequenceProperty(sequence, Strength.STRONG)


def _gap(condition: Expr) -> Sequence:
    """``!condition[*0:$]``."""
    return Repeat(Bool(_not(condition)), 0, None)


def _goto_once(condition: Expr) -> Sequence:
    """``!condition[*0:$] ##1 condition``, the definition of ``condition[->1]``."""
    return SeqConcat(_gap(condition), Delay(1, 1), Bool(condition))


def _goto_expansion(condition: Expr, low: int, high: int | None) -> Sequence:
    """``(!condition[*0:$] ##1 condition)[*low:high]``."""
    return Repeat(_goto_once(condition), low, high)


def _nonconsecutive_expansion(condition: Expr, low: int, high: int | None) -> Sequence:
    """``condition[->low:high] ##1 !condition[*0:$]``."""
    return SeqConcat(_goto_expansion(condition, low, high), Delay(1, 1), _gap(condition))


@dataclass(frozen=True)
class Law:
    name: str
    left: Property
    right: Property
    nonvacuity: bool = True
    """Whether the nonvacuity results are compared too. A window starting at offset ``m``
    is nonvacuous only from the sample ``start + m``, while a sequence is nonvacuous from its
    start, so the two sides of ``s_eventually [m:$] b`` differ there by design (9.2); likewise
    ``a |=> p`` has its first consequent one sample later than ``a |-> p``."""
    cover_end: bool = True
    """Whether the end-of-simulation outcomes of a ``cover`` are compared too. ``a |=> p``
    on a trace ending at the trigger has no consequent attempt (its antecedent match ends
    after the trace), while ``a |-> ##1 p`` has a pending strong one."""


_AND_AB = Binary(BinaryOp.LOGICAL_AND, A, B, BIT)
_PAST_B = Sampled(SampledFunction.PAST, B, BIT, 1)


def _until_pairs() -> list[Law]:
    laws: list[Law] = []
    for strong, inclusive in itertools.product((False, True), repeat=2):
        tail: Sequence = Bool(_AND_AB if inclusive else B)
        sequence = SeqConcat(Repeat(Bool(A), 0, None), Delay(1, 1), tail)
        name = ("s_" if strong else "") + ("until_with" if inclusive else "until")
        laws.append(
            Law(
                f"{name}_is_a_sequence",
                Until(A, B, strong, inclusive),
                _strong(sequence) if strong else _weak(sequence),
            )
        )
    return laws


LAWS: list[Law] = [
    Law(
        "not_s_eventually_is_always_not",
        Not(Eventually(0, None, True, SequenceProperty(Bool(B)))),
        Always(0, None, False, SequenceProperty(Bool(_not(B)))),
    ),
    Law(
        "always_is_not_a_strong_search_for_a_counterexample",
        Always(0, None, False, SequenceProperty(Bool(B))),
        Not(_strong(SeqConcat(TRUE, Delay(0, None), Bool(_not(B))))),
    ),
    Law(
        "s_eventually_is_a_strong_sequence",
        Eventually(0, None, True, SequenceProperty(Bool(B))),
        _strong(SeqConcat(TRUE, Delay(0, None), Bool(B))),
    ),
    Law(
        "s_eventually_from_two_is_a_strong_sequence",
        Eventually(2, None, True, SequenceProperty(Bool(B))),
        _strong(SeqConcat(TRUE, Delay(2, None), Bool(B))),
        nonvacuity=False,
    ),
    Law(
        "s_eventually_of_a_strong_sequence",
        Eventually(0, None, True, _strong(SeqConcat(Bool(A), Delay(1, 1), Bool(B)))),
        _strong(SeqConcat(TRUE, Delay(0, None), SeqConcat(Bool(A), Delay(1, 1), Bool(B)))),
    ),
    Law(
        "nonoverlapping_zero_to_dollar_is_overlapping_one_to_dollar",
        Implication(Bool(A), SequenceProperty(SeqConcat(TRUE, Delay(0, None), Bool(B))), False),
        Implication(Bool(A), SequenceProperty(SeqConcat(TRUE, Delay(1, None), Bool(B))), True),
        nonvacuity=False,
        cover_end=False,
    ),
    Law(
        "goto_one",
        SequenceProperty(Goto(B, 1, 1)),
        SequenceProperty(_goto_once(B)),
    ),
    *[
        Law(
            f"goto_{low}_{'dollar' if high is None else high}",
            Implication(Bool(A), SequenceProperty(Goto(B, low, high)), True),
            Implication(Bool(A), SequenceProperty(_goto_expansion(B, low, high)), True),
        )
        for low, high in [(1, 1), (2, 2), (1, 3), (2, None), (1, None), (3, 3)]
    ],
    *[
        Law(
            f"nonconsecutive_{low}_{'dollar' if high is None else high}",
            Implication(Bool(A), SequenceProperty(NonConsecutive(B, low, high)), True),
            Implication(Bool(A), SequenceProperty(_nonconsecutive_expansion(B, low, high)), True),
        )
        for low, high in [(1, 1), (2, 2), (1, 3), (2, None), (1, None)]
    ],
    Law(
        "goto_in_an_antecedent",
        Implication(
            SeqConcat(Goto(B, 1, 2), Delay(1, 1), Bool(A)), SequenceProperty(Bool(B)), True
        ),
        Implication(
            SeqConcat(_goto_expansion(B, 1, 2), Delay(1, 1), Bool(A)),
            SequenceProperty(Bool(B)),
            True,
        ),
    ),
    Law(
        "nonconsecutive_in_an_antecedent",
        Implication(
            SeqConcat(NonConsecutive(B, 1, None), Delay(1, 1), Bool(A)),
            SequenceProperty(Bool(B)),
            True,
        ),
        Implication(
            SeqConcat(_nonconsecutive_expansion(B, 1, None), Delay(1, 1), Bool(A)),
            SequenceProperty(Bool(B)),
            True,
        ),
    ),
    Law(
        "nonconsecutive_zero",
        SequenceProperty(SeqConcat(Bool(A), Delay(1, 1), NonConsecutive(B, 0, 1))),
        SequenceProperty(SeqConcat(Bool(A), Delay(1, 1), _nonconsecutive_expansion(B, 0, 1))),
    ),
    Law(
        "goto_of_a_past_condition",
        SequenceProperty(Goto(_PAST_B, 1, 2)),
        SequenceProperty(_goto_expansion(_PAST_B, 1, 2)),
    ),
    Law(
        "nonconsecutive_of_a_past_condition",
        SequenceProperty(NonConsecutive(_PAST_B, 1, None)),
        SequenceProperty(_nonconsecutive_expansion(_PAST_B, 1, None)),
    ),
    *_until_pairs(),
]


def _compare(law: Law, traces: list[Trace]) -> None:
    for trace in traces:
        left, right = attempt_results(law.left, trace), attempt_results(law.right, trace)
        if law.nonvacuity:
            assert left == right, trace
        else:
            assert [(a.outcome, a.decided) for a in left] == [
                (a.outcome, a.decided) for a in right
            ], trace
        for cover in (False, True) if law.cover_end else (False,):
            assert finalize_attempts(law.left, trace, None, cover) == finalize_attempts(
                law.right, trace, None, cover
            ), (trace, cover)


@pytest.mark.extended
@pytest.mark.parametrize("law", LAWS, ids=[law.name for law in LAWS])
def test_both_sides_of_a_law_give_the_same_results(law: Law) -> None:
    _compare(law, TRACES)


@pytest.mark.slow
@pytest.mark.parametrize("law", LAWS, ids=[law.name for law in LAWS])
def test_both_sides_of_a_law_give_the_same_results_on_longer_traces(law: Law) -> None:
    _compare(law, SLOW_TRACES)


@pytest.mark.parametrize("law", LAWS[:6], ids=[law.name for law in LAWS[:6]])
def test_the_laws_are_not_trivial(law: Law) -> None:
    """Some trace decides an attempt and some leaves one pending, so the comparison is not
    about all-pending results."""
    outcomes = {result.outcome for trace in TRACES for result in attempt_results(law.left, trace)}
    assert Outcome.PENDING in outcomes
    assert len(outcomes) >= 2


def test_goto_and_nonconsecutive_with_a_false_constant() -> None:
    from sva2yosys.ir.expr import Const

    zero = Const(0, BIT)
    one = Const(1, BIT)
    trace = Trace(({"t.a": 0},) * 3)
    assert match_ends(Goto(zero, 1, None), trace, 0) == frozenset()
    assert match_ends(NonConsecutive(zero, 1, 2), trace, 0) == frozenset()
    # the condition is never true: zero occurrences, any number of samples
    assert match_ends(NonConsecutive(zero, 0, 0), trace, 0) == {0, 1, 2}
    # a constant true condition: every sample is an occurrence
    assert match_ends(Goto(one, 2, 2), trace, 0) == {1}
    assert match_ends(NonConsecutive(one, 2, 2), trace, 0) == {1}


def test_an_alive_unbounded_sequence_never_fails_early() -> None:
    """``a ##[1:$] b`` after ``a``: pending at every prefix, never FALSE; ``$`` is not a
    bound."""
    prop = SequenceProperty(SeqConcat(Bool(A), Delay(1, None), Bool(B)))
    long_trace = Trace(tuple({"t.a": int(i == 0), "t.b": 0} for i in range(40)))
    results = attempt_results(prop, long_trace)
    assert results[0].outcome.value == "pending"
    assert results[0].decided is None


def test_a_far_match_of_a_long_trace_is_found() -> None:
    prop = SequenceProperty(SeqConcat(Bool(A), Delay(1, None), Bool(B)))
    long_trace = Trace(tuple({"t.a": int(i == 0), "t.b": int(i == 39)} for i in range(40)))
    result = attempt_results(prop, long_trace)[0]
    assert (result.outcome.value, result.decided) == ("true", 39)


def test_unbounded_goto_far_from_the_start() -> None:
    long_trace = Trace(tuple({"t.a": 0, "t.b": int(i % 10 == 9)} for i in range(40)))
    assert match_ends(Goto(B, 3, None), long_trace, 0) == {
        i for i in range(40) if i % 10 == 9 and i >= 29
    }


# --- review fixes: the exploration horizon is a structural bound, never a guess ---------------

_ONE = Const(1, BIT)


def _spaced(gap: int) -> Sequence:
    """``(1 ##gap 1)[*1:$]``: matches of every length that is a multiple of ``gap + 1``."""
    unit = SeqConcat(Bool(_ONE), Delay(gap, gap), Bool(_ONE))
    return Repeat(unit, 1, None)


_LONG_INTERSECT = SeqIntersect(_spaced(10), _spaced(12))
_LONG_AND = SeqAnd(_spaced(10), _spaced(12))
_LONG_WITHIN = Within(_spaced(10), _spaced(12))
_LONG_THROUGHOUT = Throughout(_ONE, _LONG_INTERSECT)


@pytest.mark.parametrize(
    "sequence",
    [_LONG_INTERSECT, _LONG_AND, _LONG_WITHIN, _LONG_THROUGHOUT],
    ids=["intersect", "and", "within", "throughout"],
)
def test_a_live_long_match_is_pending_not_false(sequence: Sequence) -> None:
    result = attempt_results(SequenceProperty(sequence), Trace(({"t.a": 0},)))[0]
    assert result.outcome is Outcome.PENDING


def test_the_long_intersection_matches_at_its_common_length() -> None:
    ones = Trace(tuple({"t.a": 1} for _ in range(143)))
    assert match_ends(_LONG_INTERSECT, ones, 0) == {142}
    assert match_ends(_LONG_THROUGHOUT, ones, 0) == {142}
    shorter = Trace(tuple({"t.a": 1} for _ in range(142)))
    assert match_ends(_LONG_INTERSECT, shorter, 0) == frozenset()


@pytest.mark.parametrize(
    "sequence",
    [
        _LONG_INTERSECT,
        _LONG_AND,
        _LONG_WITHIN,
        SeqConcat(TRUE, Delay(7, None), Bool(_ONE)),
        Repeat(SeqConcat(Bool(_ONE), Delay(3, 3), Bool(_ONE)), 3, None),
        Goto(_ONE, 6, None),
        NonConsecutive(_ONE, 6, 9),
    ],
)
def test_the_state_bound_covers_the_shortest_completion(sequence: Sequence) -> None:
    """Pumping: a completion of a live match exists within ``sequence_states`` samples."""
    ones = Trace(tuple({"t.a": 1} for _ in range(200)))
    ends = match_ends(sequence, ones, 0)
    assert ends
    assert min(ends) + 1 <= sequence_states(sequence)


def test_the_bound_is_tight_for_a_plain_delay() -> None:
    sequence = SeqConcat(TRUE, Delay(7, None), Bool(_ONE))
    ones = Trace(tuple({"t.a": 1} for _ in range(30)))
    assert min(match_ends(sequence, ones, 0)) + 1 == 8  # 1 ##7 1 is 8 samples long
    assert sequence_states(sequence) >= 8


def test_vacuity_is_known_through_an_unbounded_s_eventually() -> None:
    """``s_eventually (a |-> b)``, a = b = 0, one sample: the first offset holds vacuously
    and excludes the later ones, so the attempt is TRUE and known vacuous at sample 0."""
    prop = Eventually(0, None, True, Implication(Bool(A), SequenceProperty(Bool(B)), True))
    result = attempt_results(prop, Trace(({"t.a": 0, "t.b": 0},)))[0]
    assert (result.outcome, result.nonvacuous, result.nonvacuity_known) == (Outcome.TRUE, False, 0)


def test_vacuity_is_known_through_an_unbounded_always() -> None:
    """``always (a |-> b)`` with a = 1, b = 0: the first offset fails, nonvacuously."""
    prop = Always(0, None, False, Implication(Bool(A), SequenceProperty(Bool(B)), True))
    result = attempt_results(prop, Trace(({"t.a": 1, "t.b": 0},)))[0]
    assert (result.outcome, result.nonvacuous) == (Outcome.FALSE, True)
    # a = 0 holds vacuously, so later offsets stay possible: still unknown
    held = attempt_results(prop, Trace(({"t.a": 0, "t.b": 0},)))[0]
    assert (held.outcome, held.nonvacuous) == (Outcome.PENDING, None)
