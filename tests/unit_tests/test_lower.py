"""Unit contract of sva2yosys.temporal.lower (module docstring)."""

from __future__ import annotations

from sva2yosys.ir.expr import BIT, Binary, BinaryOp, Signal, Unary, UnaryOp
from sva2yosys.ir.property import (
    Always,
    Eventually,
    Implication,
    Nexttime,
    Not,
    PropAnd,
    PropIf,
    PropOr,
    SequenceProperty,
    Strength,
    Until,
)
from sva2yosys.ir.sequence import TRUE, Bool, Delay, Repeat, SeqConcat, SeqOr
from sva2yosys.temporal.lower import lower, until_sequence

E, A, B, C = (Signal(f"m.{name}", BIT) for name in "eabc")
NOT_E = Unary(UnaryOp.LOGICAL_NOT, E, BIT)
SA, SB, SC = (SequenceProperty(Bool(s)) for s in (A, B, C))


def _fused(antecedent: Bool) -> SeqConcat:
    return SeqConcat(Bool(E), Delay(0, 0), antecedent)


def test_if_without_else_is_an_overlapping_implication() -> None:
    assert lower(PropIf(E, SB, None)) == Implication(Bool(E), SB, True)
    assert lower(PropIf(E, Not(SB), None)) == Implication(Bool(E), Not(SB), True)


def test_if_fuses_into_an_implication_antecedent() -> None:
    inner = Implication(Bool(A), SB, False)
    assert lower(PropIf(E, inner, None)) == Implication(_fused(Bool(A)), SB, False)


def test_if_maps_over_a_property_and() -> None:
    prop = PropIf(E, PropAnd((Implication(Bool(A), SB, True), SC)), None)
    assert lower(prop) == PropAnd(
        (Implication(_fused(Bool(A)), SB, True), Implication(Bool(E), SC, True))
    )


def test_if_else_of_two_sequences_is_a_sequence_or() -> None:
    expected = SequenceProperty(
        SeqOr(
            SeqConcat(Bool(E), Delay(0, 0), Bool(A)),
            SeqConcat(Bool(NOT_E), Delay(0, 0), Bool(B)),
        ),
        Strength.STRONG,
    )
    strong = Strength.STRONG
    assert (
        lower(PropIf(E, SequenceProperty(Bool(A), strong), SequenceProperty(Bool(B), strong)))
        == expected
    )


def test_if_else_of_two_negations_is_a_negation() -> None:
    lowered = lower(PropIf(E, Not(SA), Not(SB)))
    assert lowered == Not(
        SequenceProperty(
            SeqOr(
                SeqConcat(Bool(E), Delay(0, 0), Bool(A)),
                SeqConcat(Bool(NOT_E), Delay(0, 0), Bool(B)),
            )
        )
    )


def test_other_if_else_shapes_stay_if() -> None:
    strong = SequenceProperty(Bool(A), Strength.STRONG)
    implication = Implication(Bool(A), SB, True)
    for prop in (
        PropIf(E, strong, SB),
        PropIf(E, implication, SB),
        PropIf(E, SA, Not(SB)),
        PropIf(E, Not(strong), Not(SB)),
    ):
        assert lower(prop) == prop


def test_implication_distributes_over_and_with_flattening() -> None:
    prop = Implication(Bool(A), PropAnd((SB, PropAnd((SC, Not(SB))))), False)
    assert lower(prop) == PropAnd(
        (
            Implication(Bool(A), SB, False),
            Implication(Bool(A), SC, False),
            Implication(Bool(A), Not(SB), False),
        )
    )


def test_distribution_applies_to_nested_implications() -> None:
    prop = Implication(Bool(A), Implication(Bool(B), PropAnd((SB, SC)), True), True)
    inner = Implication(Bool(B), SB, True), Implication(Bool(B), SC, True)
    assert lower(prop) == PropAnd(tuple(Implication(Bool(A), i, True) for i in inner))


def test_children_are_lowered_first() -> None:
    prop = PropOr((PropIf(E, SB, None), Not(PropIf(E, SA, SB))))
    lowered = lower(prop)
    assert isinstance(lowered, PropOr)
    assert lowered.operands[0] == Implication(Bool(E), SB, True)
    assert isinstance(lowered.operands[1], Not)
    assert isinstance(lowered.operands[1].operand, SequenceProperty)


def _window(low: int, high: int) -> SeqConcat:
    return SeqConcat(TRUE, Delay(low, high), TRUE)


def test_nexttime_is_an_implication_from_a_delay_window() -> None:
    assert lower(Nexttime(2, False, SA)) == Implication(_window(2, 2), SA, True)
    assert lower(Nexttime(1, False, Not(SA))) == Implication(_window(1, 1), Not(SA), True)


def test_zero_windows_are_the_operand() -> None:
    assert lower(Nexttime(0, False, SA)) == SA
    assert lower(Always(0, 0, False, SA)) == SA
    assert lower(Eventually(0, 0, True, SA)) == SA
    assert lower(Nexttime(0, True, PropIf(E, SB, None))) == Implication(Bool(E), SB, True)


def test_always_is_an_implication_from_a_range_window() -> None:
    assert lower(Always(1, 3, False, SA)) == Implication(_window(1, 3), SA, True)
    assert lower(Always(0, 2, False, SA)) == Implication(_window(0, 2), SA, True)


def test_eventually_is_the_negation_of_always_not() -> None:
    assert lower(Eventually(0, 2, False, SA)) == Not(Implication(_window(0, 2), Not(SA), True))


def test_eventually_of_a_negation_removes_the_double_negation() -> None:
    assert lower(Eventually(1, 2, False, Not(SA))) == Not(Implication(_window(1, 2), SA, True))


def test_implication_distributes_through_temporal_operators() -> None:
    operand = PropAnd((SB, SC))
    expected = PropAnd((Implication(_window(1, 1), SB, True), Implication(_window(1, 1), SC, True)))
    assert lower(Nexttime(1, False, operand)) == expected
    both = PropAnd((Implication(_window(0, 2), SB, True), Implication(_window(0, 2), SC, True)))
    assert lower(Always(0, 2, False, operand)) == both


def test_temporal_operators_nest_and_lower_children_first() -> None:
    inner = Always(1, 2, False, PropIf(E, SB, None))
    assert lower(Nexttime(1, False, inner)) == Implication(
        _window(1, 1), Implication(_window(1, 2), Implication(Bool(E), SB, True), True), True
    )


def test_strength_does_not_change_the_lowering() -> None:
    for weak, strong in (
        (Nexttime(2, False, SA), Nexttime(2, True, SA)),
        (Always(1, 3, False, SA), Always(1, 3, True, SA)),
        (Eventually(1, 3, False, SA), Eventually(1, 3, True, SA)),
        (Always(1, 3, False, Not(SA)), Always(1, 3, True, Not(SA))),
    ):
        assert lower(weak) == lower(strong)


def test_temporal_operators_inside_if_fuse_the_condition() -> None:
    assert lower(PropIf(E, Always(1, 2, False, SA), None)) == Implication(
        SeqConcat(Bool(E), Delay(0, 0), _window(1, 2)), SA, True
    )


def test_invalid_windows_are_kept_for_validation() -> None:
    for prop in (
        Nexttime(-1, False, SA),
        Always(3, 1, False, SA),
        Always(-1, 1, False, SA),
        Eventually(2, 1, True, SA),
    ):
        assert lower(prop) == prop
    assert lower(Always(3, 1, False, PropIf(E, SB, None))) == Always(
        3, 1, False, Implication(Bool(E), SB, True)
    )


def test_everything_else_is_unchanged() -> None:
    for prop in (
        SA,
        Not(SA),
        Not(Not(SA)),
        Implication(Bool(A), SB, True),
        Implication(Bool(A), Not(SB), False),
        PropAnd((SA, SB)),
        PropOr((SA, Implication(Bool(A), SB, True))),
    ):
        assert lower(prop) == prop
        assert lower(lower(prop)) == lower(prop)


# --- unbounded always -----------------------------------------------------------


def _wnd(low: int) -> SeqConcat:
    return SeqConcat(TRUE, Delay(low, None), TRUE)


def _chain(outer: SeqConcat | Bool, inner: SeqConcat | Bool) -> SeqConcat:
    return SeqConcat(outer, Delay(0, 0), inner)


def test_unbounded_always_is_an_implication_from_an_unbounded_wnd() -> None:
    assert lower(Always(0, None, False, SA)) == Implication(_wnd(0), SA, True)
    assert lower(Always(2, None, False, Not(SB))) == Implication(_wnd(2), Not(SB), True)


def test_unbounded_always_of_an_implication_is_one_implication() -> None:
    """``w |-> (a |-> c)`` is ``(w ##0 a) |-> c`` and ``w |-> (a |=> c)`` is
    ``(w ##0 a ##1 1) |-> c``."""
    overlapping = Implication(Bool(A), SB, True)
    assert lower(Always(1, None, False, overlapping)) == Implication(
        _chain(_wnd(1), Bool(A)), SB, True
    )
    nonoverlapping = Implication(Bool(A), Not(SB), False)
    assert lower(Always(0, None, False, nonoverlapping)) == Implication(
        _chain(_wnd(0), SeqConcat(Bool(A), Delay(1, 1), TRUE)), Not(SB), True
    )


def test_an_implication_into_an_unbounded_always_is_one_implication() -> None:
    assert lower(Implication(Bool(A), Always(0, None, False, SB), True)) == Implication(
        _chain(Bool(A), _wnd(0)), SB, True
    )
    assert lower(Implication(Bool(A), Always(2, None, False, SB), False)) == Implication(
        _chain(SeqConcat(Bool(A), Delay(1, 1), TRUE), _wnd(2)), SB, True
    )


def test_nested_unbounded_always_flattens_in_order() -> None:
    inner = Implication(Bool(A), Always(0, None, False, SB), True)
    assert lower(Always(1, None, False, inner)) == Implication(
        _chain(_wnd(1), _chain(Bool(A), _wnd(0))), SB, True
    )


def test_unbounded_always_maps_over_a_property_and() -> None:
    assert lower(Always(0, None, False, PropAnd((SA, SB)))) == PropAnd(
        (Implication(_wnd(0), SA, True), Implication(_wnd(0), SB, True))
    )
    both = PropAnd((Implication(Bool(A), SB, True), SC))
    assert lower(Always(0, None, False, both)) == PropAnd(
        (
            Implication(_chain(_wnd(0), Bool(A)), SB, True),
            Implication(_wnd(0), SC, True),
        )
    )


def test_if_fuses_into_an_unbounded_always() -> None:
    assert lower(PropIf(E, Always(0, None, False, SB), None)) == Implication(_fused_wnd(), SB, True)


def _fused_wnd() -> SeqConcat:
    return SeqConcat(Bool(E), Delay(0, 0), _wnd(0))


def test_other_unbounded_always_shapes_are_kept_for_validation() -> None:
    """A strong or invalid unbounded always, and an operand that does not flatten, stay
    as written (the operand lowered): validation rejects them."""
    assert lower(Always(0, None, True, SA)) == Always(0, None, True, SA)
    assert lower(Always(-1, None, False, SA)) == Always(-1, None, False, SA)
    either = PropOr((SA, SB))
    assert lower(Always(0, None, False, either)) == Implication(_wnd(0), either, True)
    nested = Implication(Bool(A), either, True)
    assert lower(Always(0, None, False, nested)) == Implication(_wnd(0), nested, True)
    # (a weak unbounded eventually and a property operand are kept: validation rejects them)
    assert lower(Eventually(0, None, False, SA)) == Eventually(0, None, False, SA)
    assert lower(Eventually(0, None, True, either)) == Eventually(0, None, True, either)
    assert lower(Eventually(-1, None, True, SA)) == Eventually(-1, None, True, SA)
    unbounded = SequenceProperty(SeqConcat(Bool(A), Delay(1, None), Bool(B)))
    assert lower(Eventually(0, None, True, unbounded)) == Eventually(0, None, True, unbounded)


# --- strong liveness --------------------------------------------------------------


def test_s_eventually_is_a_strong_sequence_with_an_unbounded_delay() -> None:
    """``s_eventually [m:$] p`` is ``strong(1[*m:$] ##1 p)`` (§16.12.13, §16.9.2.1: for zero
    repetitions ``empty ##1 p`` is ``p`` at the first sample)."""
    for low in (0, 1, 3):
        assert lower(Eventually(low, None, True, SA)) == SequenceProperty(
            SeqConcat(Repeat(TRUE, low, None), Delay(1, 1), Bool(A)), Strength.STRONG
        )
    bounded = SeqConcat(Bool(A), Delay(1, 1), Bool(B))
    assert lower(Eventually(2, None, True, SequenceProperty(bounded))) == SequenceProperty(
        SeqConcat(Repeat(TRUE, 2, None), Delay(1, 1), bounded), Strength.STRONG
    )


def test_s_eventually_lowers_inside_every_context() -> None:
    node = Eventually(0, None, True, SA)
    seq = SequenceProperty(SeqConcat(Repeat(TRUE, 0, None), Delay(1, 1), Bool(A)), Strength.STRONG)
    assert lower(Implication(Bool(C), node, True)) == Implication(Bool(C), seq, True)
    assert lower(Always(0, None, False, node)) == Implication(_wnd(0), seq, True)


def test_bounded_nested_implications_and_always_are_not_flattened() -> None:
    """Only an unbounded always flattens: bounded forms keep their lowering (their monitors
    do not change)."""
    nested = Implication(Bool(A), Implication(Bool(B), SC, True), True)
    assert lower(nested) == nested
    assert lower(Always(0, 2, False, Implication(Bool(A), SB, True))) == Implication(
        SeqConcat(TRUE, Delay(0, 2), TRUE), Implication(Bool(A), SB, True), True
    )
    assert lower(Implication(Bool(A), Always(0, 2, False, SB), True)) == Implication(
        Bool(A), Implication(SeqConcat(TRUE, Delay(0, 2), TRUE), SB, True), True
    )


# --- weak until and until_with -------------------------------------------------

_LOOP_A = Repeat(Bool(A), 0, None)
_A_AND_B = Binary(BinaryOp.LOGICAL_AND, A, B, BIT)


def test_until_is_the_sequence_p_loop_then_q() -> None:
    """``a until b`` is ``a[*0:$] ##1 b``; ``$`` stays unbounded."""
    assert until_sequence(Until(A, B, False, False)) == SeqConcat(_LOOP_A, Delay(1, 1), Bool(B))


def test_until_with_is_the_sequence_p_loop_then_p_and_q() -> None:
    """``a until_with b`` is ``a[*0:$] ##1 (a && b)``."""
    assert until_sequence(Until(A, B, False, True)) == SeqConcat(
        _LOOP_A, Delay(1, 1), Bool(_A_AND_B)
    )


def test_the_strong_forms_have_the_same_sequences() -> None:
    assert until_sequence(Until(A, B, True, False)) == until_sequence(Until(A, B, False, False))
    assert until_sequence(Until(A, B, True, True)) == until_sequence(Until(A, B, False, True))


def test_a_weak_until_lowers_to_a_weak_sequence_property() -> None:
    for inclusive in (False, True):
        node = Until(A, B, False, inclusive)
        assert lower(node) == SequenceProperty(until_sequence(node), Strength.WEAK)


def test_a_strong_until_lowers_to_a_strong_sequence_property() -> None:
    """``a s_until b`` is ``strong(a[*0:$] ##1 b)`` (§16.12.12), likewise
    ``s_until_with``."""
    for inclusive in (False, True):
        node = Until(A, B, True, inclusive)
        assert lower(node) == SequenceProperty(until_sequence(node), Strength.STRONG)


def test_until_lowers_inside_every_context() -> None:
    node = Until(A, B, False, False)
    seq = SequenceProperty(until_sequence(node), Strength.WEAK)
    assert lower(Implication(Bool(C), node, True)) == Implication(Bool(C), seq, True)
    assert lower(Not(node)) == Not(seq)
    assert lower(PropIf(E, node, None)) == Implication(Bool(E), seq, True)
    assert lower(Always(0, None, False, node)) == Implication(
        SeqConcat(TRUE, Delay(0, None), TRUE), seq, True
    )
    assert lower(Nexttime(1, False, node)) == Implication(
        SeqConcat(TRUE, Delay(1, 1), TRUE), seq, True
    )
