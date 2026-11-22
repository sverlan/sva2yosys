"""Unit contract of the test-support IR strategies (module docstring of support.ir_strategies)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from hypothesis import find, given, settings

from support.ir_strategies import (
    MAX_ATTEMPT_SPAN,
    attempt_directives,
    directives,
    duration,
    match_lengths,
    max_length,
    min_length,
    nullable,
    property_trees,
    signals_of,
    tree_span,
)
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Const, Sampled, SampledFunction, Signal
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
)
from sva2yosys.ir.sequence import (
    Bool,
    Delay,
    Goto,
    NonConsecutive,
    Repeat,
    SeqAnd,
    SeqConcat,
    SeqIntersect,
    SeqOr,
    Sequence,
    Throughout,
    Within,
)
from sva2yosys.ir.source import SourceRange
from sva2yosys.passes.first_sample import first_sample_problems
from sva2yosys.passes.validate import validation_problems

# Generated IR values have long reprs; the warning carries no information here.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Generating overly large repr:hypothesis.errors.HypothesisWarning"
)

A = Bool(Signal("m.a", BIT))


@settings(max_examples=200, deadline=None, derandomize=True)
@given(directive=directives())
def test_generated_directives_are_valid(directive: Directive) -> None:
    assert first_sample_problems(directive) == ()
    match directive.property:
        case SequenceProperty(sequence=body) | Not(operand=SequenceProperty(sequence=body)):
            assert not nullable(body)
        case Implication(consequent=consequent):
            inner = consequent.operand if isinstance(consequent, Not) else consequent
            assert isinstance(inner, SequenceProperty)
            assert not nullable(inner.sequence)
        case (
            Not() | PropAnd() | PropOr() | PropIf() | Nexttime() | Always() | Eventually() | Until()
        ):
            raise AssertionError("not of a non-sequence, or a property 'and'")
    assert all(path.startswith("m.") for path in signals_of(directive))


def _sequences(directive: Directive) -> list[Sequence]:
    found: list[Sequence] = []

    def walk(node: Sequence) -> None:
        found.append(node)
        match node:
            case SeqConcat(left=left, right=right):
                walk(left)
                walk(right)
            case Repeat(sequence=inner):
                walk(inner)
            case (
                SeqOr(left=left, right=right)
                | SeqAnd(left=left, right=right)
                | SeqIntersect(left=left, right=right)
                | Within(inner=left, outer=right)
            ):
                walk(left)
                walk(right)
            case Throughout(sequence=inner):
                walk(inner)
            case Bool() | Goto() | NonConsecutive():
                pass

    match directive.property:
        case SequenceProperty(sequence=body) | Not(operand=SequenceProperty(sequence=body)):
            walk(body)
        case Implication(antecedent=antecedent, consequent=consequent):
            walk(antecedent)
            inner = consequent.operand if isinstance(consequent, Not) else consequent
            if isinstance(inner, SequenceProperty):
                walk(inner.sequence)
        case (
            Not() | PropAnd() | PropOr() | PropIf() | Nexttime() | Always() | Eventually() | Until()
        ):
            pass
    return found


def _has(predicate: Callable[[Sequence], bool]) -> Callable[[Directive], bool]:
    return lambda d: any(predicate(s) for s in _sequences(d))


@pytest.mark.slow
@pytest.mark.parametrize(
    "predicate",
    [
        _has(lambda s: isinstance(s, SeqConcat) and s.delay == Delay(0, 0)),
        _has(lambda s: isinstance(s, SeqConcat) and s.delay.minimum == 0 and s.delay.maximum == 2),
        _has(lambda s: isinstance(s, Repeat) and isinstance(s.sequence, SeqConcat)),
        _has(lambda s: isinstance(s, Repeat) and s.minimum == 0),
        _has(lambda s: isinstance(s, Bool) and isinstance(s.expr, Const)),
        _has(lambda s: isinstance(s, Bool) and isinstance(s.expr, Sampled)),
        lambda d: isinstance(d.property, Implication) and not d.property.overlapping,
        lambda d: d.disable is not None,
        lambda d: d.kind is DirectiveKind.COVER,
    ],
)
def test_hard_constructs_are_reachable(predicate: Callable[[Directive], bool]) -> None:
    reach = settings(database=None, derandomize=True, max_examples=5000)
    assert predicate(find(directives(), predicate, settings=reach))


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        (A, False),
        (Repeat(A, 0, 2), True),
        (Repeat(Repeat(A, 0, 1), 2, 2), True),
        (SeqConcat(Repeat(A, 0, 1), Delay(1, 1), Repeat(A, 0, 1)), True),
        (SeqConcat(Repeat(A, 0, 1), Delay(0, 0), Repeat(A, 0, 1)), False),
        (SeqConcat(Repeat(A, 0, 1), Delay(2, 2), Repeat(A, 0, 1)), False),
    ],
)
def test_nullable(sequence: Sequence, expected: bool) -> None:
    assert nullable(sequence) is expected


def test_signals_of() -> None:
    rose = Sampled(SampledFunction.ROSE, Signal("m.d", BIT), BIT)
    directive = find(directives(), lambda d: d.disable == rose)
    assert "m.d" in signals_of(directive)


@pytest.mark.parametrize(
    ("sequence", "length"),
    [
        (A, 1),
        (SeqConcat(A, Delay(0, 2), A), 3),  # 1 + (2 - 1) + 1
        (SeqConcat(A, Delay(1, 3), A), 4),
        (Repeat(SeqConcat(A, Delay(1, 2), A), 2, 3), 9),
        (Repeat(A, 0, 0), 0),
    ],
)
def test_max_length(sequence: Sequence, length: int) -> None:
    assert max_length(sequence) == length


@pytest.mark.parametrize(
    ("sequence", "lengths"),
    [
        (A, {1}),
        (SeqConcat(A, Delay(0, 0), A), {1}),  # ##0 fuses
        (SeqConcat(A, Delay(1, 2), A), {2, 3}),
        (Repeat(A, 2, 3), {2, 3}),
        (SeqOr(A, SeqConcat(A, Delay(1, 1), A)), {1, 2}),
        (SeqIntersect(SeqConcat(A, Delay(1, 2), A), SeqConcat(A, Delay(2, 2), A)), {3}),
        (Repeat(A, 0, 1), None),
    ],
)
def test_match_lengths(sequence: Sequence, lengths: set[int] | None) -> None:
    assert match_lengths(sequence) == (None if lengths is None else frozenset(lengths))
    expected_min = None if lengths is None else min(lengths)
    assert min_length(sequence) == expected_min


@pytest.mark.extended
@settings(max_examples=200, deadline=None, derandomize=True)
@given(directive=directives())
def test_generated_covers_of_implications_have_at_most_one_antecedent_length(
    directive: Directive,
) -> None:
    if directive.kind is DirectiveKind.COVER and isinstance(directive.property, Implication):
        lengths = match_lengths(directive.property.antecedent)
        assert lengths is not None
        assert len(lengths) <= 1  # no match at all counts as a single length


def _tree_nodes(prop: Property) -> list[Property]:
    """Every node of a property tree, the root first."""
    found: list[Property] = [prop]
    match prop:
        case SequenceProperty() | Until():
            pass
        case (
            Implication(consequent=inner)
            | Not(operand=inner)
            | Nexttime(operand=inner)
            | Always(operand=inner)
            | Eventually(operand=inner)
        ):
            found += _tree_nodes(inner)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            for operand in operands:
                found += _tree_nodes(operand)
        case PropIf(then=then, otherwise=otherwise):
            found += _tree_nodes(then)
            if otherwise is not None:
                found += _tree_nodes(otherwise)
    return found


def _leaf_sequences_of(prop: Property) -> list[Sequence]:
    found: list[Sequence] = []
    for node in _tree_nodes(prop):
        match node:
            case SequenceProperty(sequence=body):
                found.append(body)
            case Implication(antecedent=antecedent):
                found.append(antecedent)
            case (
                Not()
                | PropAnd()
                | PropOr()
                | PropIf()
                | Nexttime()
                | Always()
                | Eventually()
                | Until()
            ):
                pass
    return found


@pytest.mark.extended
@settings(max_examples=200, deadline=None, derandomize=True)
@given(directive=attempt_directives())
def test_generated_attempt_directives_are_structurally_valid(directive: Directive) -> None:
    assert validation_problems(directive, shapes=False) == ()
    for sequence in _leaf_sequences_of(directive.property):
        assert not nullable(sequence)
    assert directive.kind in (DirectiveKind.ASSERT, DirectiveKind.COVER)


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(prop=property_trees(0))
def test_depth_zero_trees_are_sequence_properties(prop: Property) -> None:
    assert isinstance(prop, SequenceProperty)


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(prop=property_trees(2))
def test_tree_depth_is_bounded(prop: Property) -> None:
    def depth(node: Property) -> int:
        match node:
            case SequenceProperty() | Until():
                return 0
            case (
                Implication(consequent=inner)
                | Not(operand=inner)
                | Nexttime(operand=inner)
                | Always(operand=inner)
                | Eventually(operand=inner)
            ):
                return 1 + depth(inner)
            case PropAnd(operands=operands) | PropOr(operands=operands):
                return 1 + max(depth(o) for o in operands)
            case PropIf(then=then, otherwise=otherwise):
                return 1 + max(depth(then), depth(otherwise) if otherwise is not None else 0)

    assert depth(prop) <= 2


@pytest.mark.extended
@pytest.mark.slow
@pytest.mark.parametrize(
    "predicate",
    [
        lambda p: isinstance(p, SequenceProperty),
        lambda p: isinstance(p, Not),
        lambda p: isinstance(p, PropAnd),
        lambda p: isinstance(p, PropOr),
        lambda p: isinstance(p, PropIf) and p.otherwise is None,
        lambda p: isinstance(p, PropIf) and p.otherwise is not None,
        lambda p: isinstance(p, Implication) and p.overlapping,
        lambda p: isinstance(p, Implication) and not p.overlapping,
        lambda p: isinstance(p, Implication) and isinstance(p.consequent, Implication),
        lambda p: isinstance(p, Nexttime) and not p.strong and p.count == 0,
        lambda p: isinstance(p, Nexttime) and p.strong and p.count == 2,
        lambda p: isinstance(p, Always) and not p.strong and (p.low, p.high) == (0, 0),
        lambda p: isinstance(p, Always) and p.strong and (p.low, p.high) == (1, 2),
        lambda p: isinstance(p, Eventually) and not p.strong and (p.low, p.high) == (0, 2),
        lambda p: isinstance(p, Eventually) and p.strong and (p.low, p.high) == (2, 2),
        lambda p: (
            isinstance(p, (Nexttime, Always, Eventually))
            and isinstance(p.operand, (Implication, Not, PropOr, Nexttime, Always, Eventually))
        ),
    ],
)
def test_every_tree_node_kind_is_reachable(predicate: Callable[[Property], bool]) -> None:
    reach = settings(database=None, derandomize=True, max_examples=2000)

    def has(prop: Property) -> bool:
        return any(predicate(node) for node in _tree_nodes(prop))

    assert has(find(property_trees(3), has, settings=reach))


@pytest.mark.extended
@settings(max_examples=200, deadline=None, derandomize=True)
@given(prop=property_trees(3))
def test_temporal_operator_bounds_are_small_and_valid(prop: Property) -> None:
    for node in _tree_nodes(prop):
        match node:
            case Nexttime(count=count):
                assert 0 <= count <= 2
            case Always(low=low, high=high) | Eventually(low=low, high=high):
                assert high is not None
                assert 0 <= low <= high <= 2
            case _:
                pass


@pytest.mark.extended
@settings(max_examples=200, deadline=None, derandomize=True)
@given(directive=attempt_directives())
def test_generated_attempt_directives_have_a_small_span(directive: Directive) -> None:
    assert tree_span(directive.property) <= MAX_ATTEMPT_SPAN


def test_tree_span_adds_offsets_and_antecedents() -> None:
    b = SequenceProperty(A)
    assert tree_span(b) == 1
    assert tree_span(Nexttime(2, False, b)) == 3
    assert tree_span(Always(1, 2, True, Eventually(0, 1, False, b))) == 4
    assert tree_span(Implication(SeqConcat(A, Delay(1, 2), A), b, False)) == 3 + 1 + 1
    assert tree_span(PropOr((b, Nexttime(1, True, b)))) == 2
    assert tree_span(PropIf(Signal("m.a", BIT), b, Nexttime(2, False, b))) == 3


@pytest.mark.extended
@pytest.mark.slow
@pytest.mark.parametrize(
    "predicate",
    [
        lambda d: d.disable is None,
        lambda d: d.disable == Signal("m.d", BIT),
        lambda d: isinstance(d.disable, Sampled),
        lambda d: d.kind is DirectiveKind.COVER,
        lambda d: d.kind is DirectiveKind.ASSERT,
    ],
)
def test_attempt_directive_variants_are_reachable(predicate: Callable[[Directive], bool]) -> None:
    reach = settings(database=None, derandomize=True, max_examples=2000)
    assert predicate(find(attempt_directives(), predicate, settings=reach))


def test_duration_includes_antecedent_and_offset() -> None:
    def matches(d: Directive) -> bool:
        return isinstance(d.property, Implication) and not d.property.overlapping

    directive = find(directives(), matches)
    assert isinstance(directive.property, Implication)
    consequent = directive.property.consequent
    assert isinstance(consequent, SequenceProperty)
    expected = max_length(directive.property.antecedent) + 1 + max_length(consequent.sequence)
    assert duration(directive) == expected


# --- unbounded generators ---------------------------------------------------------

from support.ir_strategies import (  # noqa: E402
    all_signals,
    has_unbounded_part,
    unbounded_directives,
    unbounded_sequences,
)


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(directive=unbounded_directives())
def test_unbounded_directives_are_accepted_by_validation(directive: Directive) -> None:
    assert directive.kind in (DirectiveKind.ASSERT, DirectiveKind.ASSUME)
    assert validation_problems(directive) == ()
    assert all(path.startswith("m.") for path in all_signals(directive))


@pytest.mark.extended
@pytest.mark.parametrize(
    "predicate",
    [
        lambda d: has_unbounded_part(d.property),
        lambda d: isinstance(d.property, Always),
        lambda d: isinstance(d.property, Implication) and isinstance(d.property.consequent, Always),
        lambda d: d.kind is DirectiveKind.ASSUME,
        lambda d: d.disable is not None,
        lambda d: isinstance(d.property, Implication) and not d.property.overlapping,
    ],
)
def test_unbounded_directive_variants_are_reachable(
    predicate: Callable[[Directive], bool],
) -> None:
    find(unbounded_directives(), predicate, settings=settings(max_examples=2000, deadline=None))


def _node_kinds(sequence: Sequence) -> set[type]:
    kinds = {type(sequence)}
    match sequence:
        case SeqConcat(left=left, right=right):
            kinds |= _node_kinds(left) | _node_kinds(right)
        case Repeat(sequence=inner) | Throughout(sequence=inner):
            kinds |= _node_kinds(inner)
        case (
            SeqOr(left=left, right=right)
            | SeqAnd(left=left, right=right)
            | SeqIntersect(left=left, right=right)
            | Within(inner=left, outer=right)
        ):
            kinds |= _node_kinds(left) | _node_kinds(right)
        case _:
            pass
    return kinds


@pytest.mark.extended
@pytest.mark.parametrize("kind", [Goto, NonConsecutive, Repeat, SeqConcat, SeqAnd, Within])
def test_unbounded_sequences_reach_every_construct(kind: type) -> None:
    find(
        unbounded_sequences(2),
        lambda s: kind in _node_kinds(s),
        settings=settings(max_examples=2000, deadline=None),
    )


def test_unbounded_ranges_are_generated() -> None:
    find(
        unbounded_sequences(2),
        lambda s: has_unbounded_part(SequenceProperty(s)),
        settings=settings(max_examples=500, deadline=None),
    )


def test_has_unbounded_part_and_nullable_of_the_new_nodes() -> None:
    b = Signal("m.b", BIT)
    assert has_unbounded_part(SequenceProperty(Goto(b, 1, 1)))
    assert has_unbounded_part(SequenceProperty(SeqConcat(A, Delay(1, None), A)))
    assert has_unbounded_part(Always(0, None, False, SequenceProperty(A)))
    assert not has_unbounded_part(Always(0, 2, False, SequenceProperty(A)))
    assert not has_unbounded_part(SequenceProperty(Repeat(A, 1, 3)))
    assert nullable(Goto(b, 0, 2)) and not nullable(Goto(b, 1, None))
    assert nullable(NonConsecutive(b, 0, 1)) and not nullable(NonConsecutive(b, 2, 2))
    assert nullable(Repeat(A, 0, None)) and not nullable(Repeat(A, 1, None))
    assert nullable(SeqConcat(Repeat(A, 0, 1), Delay(1, None), Repeat(A, 0, 1)))
    assert not nullable(SeqConcat(Repeat(A, 0, 1), Delay(2, None), Repeat(A, 0, 1)))


def test_all_signals_walks_every_node() -> None:
    prop = Implication(
        SeqConcat(A, Delay(1, None), Bool(Signal("m.q", BIT))),
        Always(0, None, False, SequenceProperty(Bool(Signal("m.r", BIT)))),
        True,
    )
    directive = Directive(
        DirectiveKind.ASSERT,
        "p",
        "m",
        Clock(Signal("m.clk", BIT), Edge.POSEDGE),
        Signal("m.z", BIT),
        prop,
        SourceRange("t", 1, 1, 1, 2),
    )
    assert all_signals(directive) == ("m.a", "m.q", "m.r", "m.z")


# --- weak unbounded consequent generators ----------------------------------------

from support.ir_strategies import (  # noqa: E402
    config_directives,
    has_false_constant,
    weak_unbounded_consequents,
)
from sva2yosys.ir.property import Strength  # noqa: E402


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(directive=config_directives())
def test_config_directives_are_accepted_by_validation(directive: Directive) -> None:
    assert directive.kind in (DirectiveKind.ASSERT, DirectiveKind.ASSUME)
    assert validation_problems(directive) == ()
    assert has_unbounded_part(directive.property)
    assert all(path.startswith("m.") for path in all_signals(directive))


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(prop=weak_unbounded_consequents())
def test_weak_unbounded_consequents_are_weak_unbounded_and_not_nullable(
    prop: SequenceProperty,
) -> None:
    assert prop.strength in (Strength.DEFAULT, Strength.WEAK)
    assert has_unbounded_part(prop)
    assert not nullable(prop.sequence)


@pytest.mark.extended
@pytest.mark.parametrize(
    "predicate",
    [
        lambda d: isinstance(d.property, SequenceProperty),
        lambda d: (
            isinstance(d.property, Implication)
            and isinstance(d.property.consequent, SequenceProperty)
        ),
        lambda d: isinstance(d.property, Implication) and not d.property.overlapping,
        lambda d: isinstance(d.property, Always),
        lambda d: isinstance(d.property, Implication) and isinstance(d.property.consequent, Always),
        lambda d: (
            isinstance(d.property, Implication)
            and has_unbounded_part(SequenceProperty(d.property.antecedent))
        ),
        lambda d: d.kind is DirectiveKind.ASSUME,
        lambda d: d.disable is not None,
    ],
)
def test_config_directive_variants_are_reachable(predicate: Callable[[Directive], bool]) -> None:
    find(config_directives(), predicate, settings=settings(max_examples=3000, deadline=None))


# --- constants are generated everywhere --------------------------------------------


def test_weak_unbounded_consequents_include_constant_false_conditions() -> None:
    """A constant-false condition is a legal letter in an unbounded consequent, so the
    consequent generator produces it (it used to filter it out)."""
    find(
        weak_unbounded_consequents(),
        lambda p: has_false_constant(p.sequence),
        settings=settings(max_examples=3000, deadline=None),
    )


def test_config_directives_include_a_constant_false_consequent() -> None:
    def constant_in_consequent(directive: Directive) -> bool:
        prop = directive.property
        while isinstance(prop, Implication):
            prop = prop.consequent
        return isinstance(prop, SequenceProperty) and has_false_constant(prop.sequence)

    find(
        config_directives(),
        constant_in_consequent,
        settings=settings(max_examples=5000, deadline=None),
    )


def test_unbounded_sequences_include_goto_of_constants() -> None:
    def has_counted_constant(sequence: object) -> bool:
        if isinstance(sequence, Goto | NonConsecutive):
            return isinstance(sequence.condition, Const)
        return any(
            has_counted_constant(child)
            for child in vars(sequence).values()
            if hasattr(child, "__dataclass_fields__")
        )

    find(
        unbounded_sequences(2),
        has_counted_constant,
        settings=settings(max_examples=5000, deadline=None),
    )


# --- weak until generators -----------------------------------------------------

from support.ir_strategies import until_directives, until_properties  # noqa: E402


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(prop=until_properties())
def test_until_properties_are_weak_until_nodes(prop: Until) -> None:
    assert isinstance(prop, Until)
    assert not prop.strong
    assert has_unbounded_part(prop)


def test_until_properties_include_both_forms_and_the_interesting_operands() -> None:
    settings_ = settings(max_examples=3000, deadline=None)
    find(until_properties(), lambda p: p.inclusive, settings=settings_)
    find(until_properties(), lambda p: not p.inclusive, settings=settings_)
    find(until_properties(), lambda p: "Sampled" in repr(p.left), settings=settings_)
    find(until_properties(), lambda p: "Sampled" in repr(p.right), settings=settings_)
    find(until_properties(), lambda p: isinstance(p.left, Const), settings=settings_)
    find(until_properties(), lambda p: isinstance(p.right, Const), settings=settings_)


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(directive=until_directives())
def test_until_directives_are_accepted_by_validation(directive: Directive) -> None:
    assert directive.kind in (DirectiveKind.ASSERT, DirectiveKind.ASSUME)
    assert validation_problems(directive) == ()
    assert has_unbounded_part(directive.property)
    assert all(path.startswith("m.") for path in all_signals(directive))


@pytest.mark.extended
@pytest.mark.parametrize(
    "predicate",
    [
        lambda d: isinstance(d.property, Until),
        lambda d: isinstance(d.property, Implication) and isinstance(d.property.consequent, Until),
        lambda d: isinstance(d.property, Implication) and not d.property.overlapping,
        lambda d: isinstance(d.property, Always) and d.property.high is None,
        lambda d: isinstance(d.property, Always) and d.property.high is not None,
        lambda d: isinstance(d.property, Nexttime),
        lambda d: isinstance(d.property, PropIf) and d.property.otherwise is None,
        lambda d: isinstance(d.property, PropIf) and d.property.otherwise is not None,
        lambda d: d.kind is DirectiveKind.ASSUME,
        lambda d: d.disable is not None,
    ],
)
def test_until_directive_variants_are_reachable(predicate: Callable[[Directive], bool]) -> None:
    find(until_directives(), predicate, settings=settings(max_examples=3000, deadline=None))


def test_config_directives_include_an_until() -> None:
    def has_until(directive: Directive) -> bool:
        return "Until(" in repr(directive.property)

    find(config_directives(), has_until, settings=settings(max_examples=3000, deadline=None))


# --- covers of unbounded sequences -------------------------------------------------

from support.ir_strategies import (  # noqa: E402
    cover_config_directives,
    single_length_antecedents,
    strong_unbounded_properties,
)


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(directive=cover_config_directives())
def test_cover_config_directives_are_accepted_by_validation(directive: Directive) -> None:
    assert directive.kind is DirectiveKind.COVER
    assert validation_problems(directive) == ()
    assert has_unbounded_part(directive.property)
    assert all(path.startswith("m.") for path in all_signals(directive))


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(prop=strong_unbounded_properties())
def test_strong_unbounded_properties_are_strong_unbounded_and_not_nullable(
    prop: SequenceProperty,
) -> None:
    assert prop.strength in (Strength.DEFAULT, Strength.STRONG)
    assert has_unbounded_part(prop)
    assert not nullable(prop.sequence)


@pytest.mark.extended
@settings(max_examples=100, deadline=None, derandomize=True)
@given(antecedent=single_length_antecedents())
def test_single_length_antecedents_have_one_length(antecedent: Sequence) -> None:
    lengths = match_lengths(antecedent)
    assert lengths is not None and len(lengths) == 1
    assert not nullable(antecedent)
    assert not has_unbounded_part(SequenceProperty(antecedent))


@pytest.mark.extended
@pytest.mark.parametrize(
    "predicate",
    [
        lambda d: isinstance(d.property, SequenceProperty),
        lambda d: isinstance(d.property, Implication) and d.property.overlapping,
        lambda d: isinstance(d.property, Implication) and not d.property.overlapping,
        lambda d: (
            isinstance(d.property, SequenceProperty) and d.property.strength is Strength.STRONG
        ),
        lambda d: "Goto(" in repr(d.property),
        lambda d: "NonConsecutive(" in repr(d.property),
        lambda d: "SeqOr(" in repr(d.property),
        lambda d: "Sampled(" in repr(d.property),
        lambda d: d.disable is not None,
        lambda d: (
            isinstance(d.property, Implication)
            and len(match_lengths(d.property.antecedent) or ()) == 1
            and max_length(d.property.antecedent) >= 2
        ),
    ],
)
def test_cover_config_directive_variants_are_reachable(
    predicate: Callable[[Directive], bool],
) -> None:
    find(cover_config_directives(), predicate, settings=settings(max_examples=3000, deadline=None))


def test_cover_config_directives_include_a_constant_false_condition() -> None:
    def constant_false(directive: Directive) -> bool:
        prop = directive.property
        while isinstance(prop, Implication):
            prop = prop.consequent
        return isinstance(prop, SequenceProperty) and has_false_constant(prop.sequence)

    find(
        cover_config_directives(),
        constant_false,
        settings=settings(max_examples=5000, deadline=None),
    )
